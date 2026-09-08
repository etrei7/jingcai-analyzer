import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))


def daily_settlement():
    """每日结算：尝试从 Bzzoiro 获取已完成比赛结果，验证预测命中率"""
    logger.info('[定时任务] 每日结算开始...')
    try:
        api_key = os.environ.get('BZZOIRO_API_KEY', '')
        if not api_key:
            logger.info('[定时任务] 无 API Key，跳过结算')
            return

        # 检查最近3天的比赛
        from history import _load_history, _save_history, _recalc_stats
        hist = _load_history()
        unverified = [p for p in hist['predictions'] if not p.get('verified')]
        if not unverified:
            logger.info('[定时任务] 无待验证预测')
            return

        today = datetime.now(CST).strftime('%Y-%m-%d')
        yesterday = (datetime.now(CST) - timedelta(days=1)).strftime('%Y-%m-%d')

        # 尝试从 Bzzoiro 获取已完成比赛
        BASE_URL = os.environ.get('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api')
        headers = {'Authorization': f'Token {api_key}'}

        def _norm(s):
            return (s or '').replace(' ', '').replace('-', '').lower()

        verified = 0
        # 按 raw_event_id 精确逐个查 Bzzoiro 单场（预测记录的 raw_event_id 即 Bzzoiro 事件ID）
        # 不做 50 条限制、不限定 7 天窗口，确保全部未验证都能结转到结果。
        for p in unverified:
            eid = p.get('raw_event_id', '')
            if not eid:
                continue
            hs = aw = None
            try:
                r = requests.get(f'{BASE_URL}/events/{eid}/', headers=headers, timeout=10)
                if r.status_code == 200:
                    ev = r.json()
                    hs = ev.get('home_score')
                    aw = ev.get('away_score')
            except Exception:
                pass

            if hs is not None and aw is not None:
                if hs > aw: actual = '胜'
                elif hs == aw: actual = '平'
                else: actual = '负'
                p['verified'] = True
                p['actual'] = actual
                p['score'] = f'{hs}-{aw}'
                p['hit'] = (p['predicted'] == actual)
                verified += 1
                logger.info(f'[结算] {p["match_id"]} {p["home_team"]}vs{p["away_team"]}: {p["predicted"]}→{actual} hit={p["hit"]}')

        _recalc_stats(hist)
        _save_history(hist)
        logger.info(f'[定时任务] 结算完成，本次验证 {verified} 条')
    except Exception as e:
        logger.warning(f'[定时任务] 结算异常: {e}')


def init_scheduler(app):
    set_app(app)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        daily_settlement,
        'cron',
        hour=2,
        minute=30,
        id='daily_settlement',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # 回测流水线：定时采集快照 + 生成预测 + 赛后结算（内部有 API 超时保护,不阻塞 worker）
    scheduler.add_job(
        backtest_pipeline,
        'interval',
        minutes=30,
        id='backtest_pipeline',
        max_instances=1,
        coalesce=True,
    )
    # 说明：不额外加定时缓存预热任务。uWSGI 单 worker 环境下，后台高频拉取
    # 会阻塞请求处理；缓存改为「请求时按需构建 + 手动 /cache-refresh 强制刷新」。
    scheduler.start()
    app.extensions['scheduler'] = scheduler
    logger.info('[定时任务] APScheduler 已启动：每日 2:30 结算，每 30 分钟回测流水线')


def backtest_pipeline():
    """回测流水线定时任务：采集预测与赔率快照 + 结算已完赛。"""
    try:
        from data_pipeline import run_full
        app = _get_app()
        if app is None:
            logger.warning('[定时任务] 无 app 引用，跳过回测流水线')
            return
        with app.app_context():
            res = run_full()
        logger.info('[定时任务] 回测流水线完成: %s', res)
    except Exception as e:
        logger.warning('[定时任务] 回测流水线异常: %s', e)


_app_ref = None


def _get_app():
    return _app_ref


def set_app(app):
    global _app_ref
    _app_ref = app


def warmup_cache():
    """定时预拉取赛事数据到内存缓存（保留入口，供需要时手动调用）。"""
    try:
        from cache import warmup
        warmup()
    except Exception as e:
        logger.warning('[定时任务] 缓存预拉取异常: %s', e)
