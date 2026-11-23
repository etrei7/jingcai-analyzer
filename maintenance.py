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


_FLAG_NAME = 'RESETTLE'


def _flag_path(app):
    return os.path.join(app.instance_path, _FLAG_NAME)


def _consume_resettle(app):
    """如果 instance/RESETTLE 存在，按其内容的天数重算历史结算，然后删掉标记。

    为什么用文件标记：重算必须跑在有 BZZOIRO_API_KEY 的 webapp 进程里，而控制台
    （PA console）的 env 是另一套、没有这个 key。用共享文件系统传信号最省事，
    也避免为一次性维护开一个公网可写的接口。
    """
    path = _flag_path(app)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            days = int((fh.read() or '14').strip() or 14)
    except Exception:
        days = 14
    try:
        os.remove(path)
    except Exception:
        pass
    try:
        from data_pipeline import resettle_recent
        stats = resettle_recent(days=days)
        logger.info('[维护] 重算历史结算: %s', stats)
        try:
            import json
            with open(os.path.join(app.instance_path, 'resettle_report.json'), 'w', encoding='utf-8') as fh:
                json.dump({'days': days, 'stats': stats}, fh, ensure_ascii=False)
        except Exception:
            pass
        return stats
    except Exception as e:
        logger.warning('[维护] 重算失败: %s', e)
        return {'error': str(e)[:200]}


def _work(app):
    try:
        with app.app_context():
            _consume_resettle(app)
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
    forced = os.path.exists(_flag_path(app))
    if not _ENABLED or (_INTERVAL_HOURS <= 0 and not forced):
        return False
    now = time.time()
    forced = os.path.exists(_flag_path(app))
    if not forced and now - _STATE['ts'] < _INTERVAL_HOURS * 3600:
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
