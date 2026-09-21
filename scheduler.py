import logging
import requests
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def daily_settlement():
    """每日结算：验证历史预测命中率。
    两条路径：
    1) raw_event_id 为 Bzzoiro 数字事件ID → 按ID精确取赛果；
    2) 竞彩编号（如「周日001」）→ 拉取近几日已完赛事件，按队名规约匹配。
    解决竞彩预测 raw_event_id 非 Bzzoiro ID、导致永远「待验证」的问题。"""
    logger.info('[定时任务] 每日结算开始...')
    try:
        from bizzoiro_client import API_KEY, BASE_URL, fetch_finished_events
        if not API_KEY:
            logger.info('[定时任务] 无 API Key，跳过结算')
            return

        from history import _load_history, _save_history, _recalc_stats
        hist = _load_history()
        unverified = [p for p in hist.get('predictions', []) if not p.get('verified')]
        if not unverified:
            logger.info('[定时任务] 无待验证预测')
            return

        def _norm(s):
            return (s or '').replace(' ', '').replace('-', '').lower()

        def _cn(s):
            try:
                from team_alias import canon
                return canon(s)
            except Exception:
                return s or ''

        # 近几日已完赛事件：建立 事件ID索引 与 (主队,客队)队名索引
        finished = fetch_finished_events()
        by_id = {}
        by_name = {}
        for ev in finished:
            eid = str(ev.get('id') or '')
            if eid:
                by_id[eid] = ev
            h, a = _norm(_cn(ev.get('home_team'))), _norm(_cn(ev.get('away_team')))
            if h and a:
                by_name.setdefault((h, a), ev)

        # 数字ID但列表未覆盖的，逐个补查（限20个，避免拖慢定时任务）
        headers = {'Authorization': f'Token {API_KEY}'}
        numeric_eids = [str(p.get('raw_event_id')) for p in unverified
                        if str(p.get('raw_event_id', '')).isdigit()]
        for eid in numeric_eids[:20]:
            if eid in by_id:
                continue
            try:
                r = requests.get(f'{BASE_URL}/events/{eid}/', headers=headers, timeout=10)
                if r.status_code == 200:
                    by_id[eid] = r.json()
            except Exception:
                pass

        verified = 0
        for p in unverified:
            eid = str(p.get('raw_event_id', ''))
            ev = by_id.get(eid) if eid.isdigit() else None
            if ev is None:
                ev = by_name.get((_norm(_cn(p.get('home_team'))), _norm(_cn(p.get('away_team')))))
            if not ev:
                continue
            # 学习别名（命中同场时，预测名 与 事件名 可能不同）
            try:
                from team_alias import learn
                learn(ev.get('home_team'), p.get('home_team'))
                learn(ev.get('away_team'), p.get('away_team'))
            except Exception:
                pass
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                continue
            actual = '胜' if hs > aw else '平' if hs == aw else '负'
            p['verified'] = True
            p['actual'] = actual
            p['score'] = f'{hs}-{aw}'
            p['hit'] = (p.get('predicted') == actual)
            verified += 1
            logger.info('[结算] %s %svs%s: %s→%s hit=%s',
                        p.get('match_id'), p.get('home_team'), p.get('away_team'),
                        p.get('predicted'), actual, p['hit'])

        _recalc_stats(hist)
        _save_history(hist)
        logger.info('[定时任务] 结算完成，本次验证 %d 条', verified)
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
    # 资讯刷新：RSS 抓取+分类+队名匹配（只读文件，轻量，不阻塞）
    scheduler.add_job(
        refresh_news,
        'interval',
        minutes=15,
        id='news_refresh',
        max_instances=1,
        coalesce=True,
    )
    # 说明：不额外加定时缓存预热任务。uWSGI 单 worker 环境下，后台高频拉取
    # 会阻塞请求处理；缓存改为「请求时按需构建 + 手动 /cache-refresh 强制刷新」。
    scheduler.start()
    app.extensions['scheduler'] = scheduler
    logger.info('[定时任务] APScheduler 已启动：每日 2:30 结算，每 30 分钟回测流水线')


def refresh_news():
    """定时资讯刷新：RSS → 分类 → 队名匹配（失败静默）。"""
    try:
        from news_ingest import refresh
        n = refresh()
        logger.info('[定时任务] 资讯刷新完成：%d 条', n)
    except Exception as e:
        logger.warning('[定时任务] 资讯刷新异常: %s', e)


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
        try:
            from backtest import clear_summary_cache
            clear_summary_cache()
        except Exception:
            pass
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
