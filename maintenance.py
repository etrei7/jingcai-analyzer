"""请求驱动的轻量维护（给没有 Scheduled Tasks 的免费版 PythonAnywhere 用）。

背景：免费版 PA 的 web worker 会被卸载，进程内 APScheduler 会随之停摆；
而免费账户又没有 Scheduled Tasks 功能（API 直接返回
`{"detail": "Scheduled tasks are not available on free accounts"}`）。
折中方案：只要有人访问首页或 /api/data，且距上次维护超过 MAINT_INTERVAL_HOURS
小时，就在后台线程里补跑一次「结算已完赛 + 刷新资讯」。这样既不需要付费任务，
也不依赖 worker 常驻；没人访问时也没有人需要新数据。
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_INTERVAL_HOURS = float(os.environ.get('MAINT_INTERVAL_HOURS', '6') or 0)
_ENABLED = str(os.environ.get('MAINT_ENABLED', '1')).lower() not in ('0', 'false', 'no', 'off')

_LOCK = threading.Lock()
_STATE = {'ts': 0.0, 'running': False}


def status():
    return {
        'enabled': _ENABLED,
        'interval_hours': _INTERVAL_HOURS,
        'running': _STATE['running'],
        'last_run': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_STATE['ts'])) if _STATE['ts'] else None,
    }


def _work(app):
    try:
        with app.app_context():
            from data_pipeline import settle_finished
            res = settle_finished()
            logger.info('[维护] 结算完成: %s', res)
    except Exception as e:
        logger.warning('[维护] 结算失败: %s', e)
    try:
        from news_ingest import refresh
        logger.info('[维护] 资讯刷新: %s', refresh())
    except Exception as e:
        logger.warning('[维护] 资讯刷新失败: %s', e)
    finally:
        _STATE['running'] = False
        _LOCK.release()


def maybe_run(app):
    """满足条件时启动一次后台维护；返回是否真的启动。"""
    if not _ENABLED or _INTERVAL_HOURS <= 0:
        return False
    now = time.time()
    if now - _STATE['ts'] < _INTERVAL_HOURS * 3600:
        return False
    if not _LOCK.acquire(blocking=False):
        return False
    _STATE['ts'] = now
    _STATE['running'] = True
    try:
        threading.Thread(target=_work, args=(app,), daemon=True, name='lazy-maint').start()
        logger.info('[维护] 触发后台维护（间隔 %.1f 小时）', _INTERVAL_HOURS)
        return True
    except Exception as e:
        logger.warning('[维护] 线程启动失败: %s', e)
        _STATE['running'] = False
        _LOCK.release()
        return False
