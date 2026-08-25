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
        api_key = os.environ.get('BZZOIRO_API_KEY', '2d8a09f4eaac8ce462729d3a7b82cd489bcf4b8e')
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

        # 预取近期完赛结果表（按队名匹配，兼容竞彩场次）
        results_map = {}
        try:
            from bizzoiro_client import fetch_actionable_results
            results_map = fetch_actionable_results(
                (datetime.now(CST) - timedelta(days=7)).strftime('%Y-%m-%d'),
                today
            )
        except Exception:
            results_map = {}

        def _norm(s):
            return (s or '').replace(' ', '').replace('-', '').lower()

        verified = 0
        for p in unverified[:50]:  # 最多验证50条
            eid = p.get('raw_event_id', '')
            home = p.get('home_team', '')
            away = p.get('away_team', '')
            hs = aw = None
            # 优先按队名匹配（竞彩/任意来源通用）
            if home and away:
                key = f'{_norm(home)}|{_norm(away)}'
                m = results_map.get(key)
                if m:
                    hs, aw = m['home'], m['away']
            # 其次按 Bzzoiro eid 精确匹配
            if (hs is None or aw is None) and eid:
                try:
                    r = requests.get(f'{BASE_URL}/events/{eid}/', headers=headers, timeout=15)
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
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        daily_settlement,
        'cron',
        hour=2,
        minute=30,
        id='daily_settlement'
    )
    # 说明：不额外加定时缓存预热任务。uWSGI 单 worker 环境下，后台高频拉取
    # 会阻塞请求处理；缓存改为「请求时按需构建 + 手动 /cache-refresh 强制刷新」。
    scheduler.start()
    app.extensions['scheduler'] = scheduler
    logger.info('[定时任务] APScheduler 已启动：每日 2:30 执行结算')


def warmup_cache():
    """定时预拉取赛事数据到内存缓存（保留入口，供需要时手动调用）。"""
    try:
        from cache import warmup
        warmup()
    except Exception as e:
        logger.warning('[定时任务] 缓存预拉取异常: %s', e)
