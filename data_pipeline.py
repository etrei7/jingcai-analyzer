"""数据流水线：定时拉取赛事+赔率并落库，赛后回填结果，形成回测闭环。
基于 Bzzoiro（后端可访问）；sporttery 在服务器端 403，故竞彩官方数据走前端，此处仅回测。
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run_pipeline():
    """拉取未来赛事+赔率快照 + 生成价值盘预测落库。由 APScheduler 定时调用。"""
    try:
        from bizzoiro_client import API_KEY, fetch_events
        if not API_KEY:
            logger.info('[pipeline] no API key, skip')
            return 0
        from analysis import analyze_matches
        import backtest as bt

        matches = fetch_events(limit=15)
        if not matches:
            return 0

        analyzed = analyze_matches(matches, None, {})
        saved = 0
        for m in analyzed:
            mid = m.get('raw_event_id') or m.get('match_id')
            if not mid:
                continue

            bt.record_odds_snapshot(
                match_id=str(mid), market='1X2',
                home=m.get('win_odds'), draw=m.get('draw_odds'), away=m.get('lose_odds'),
                source='Bzzoiro'
            )

            win, draw, loss = m.get('win_odds'), m.get('draw_odds'), m.get('lose_odds')
            opts = [('H', win), ('D', draw), ('A', loss)]
            best = min((o for o in opts if o[1] and o[1] > 0), key=lambda x: x[1])
            pick, odds = best
            imp = bt.implied_prob(odds)
            conf_level = m.get('confidence_level', '')
            conf = 0.8 if conf_level == '高' else 0.6 if conf_level == '中' else 0.4
            predicted_prob = round(imp * conf, 4)

            bt.record_prediction(
                match_id=str(mid), play_type='1X2', pick=pick,
                predicted_prob=predicted_prob, odds=odds,
                model_name='jingcai-value', confidence=conf, combo='single',
                home_team=m.get('home_team'), away_team=m.get('away_team')
            )
            saved += 1
        logger.info('[pipeline] processed %d matches (snapshot+prediction)', saved)
        return saved
    except Exception as e:
        logger.warning('[pipeline] run error: %s', e)
        return 0


def settle_finished():
    """赛后结算：拉取近7天已完赛，按队名回填 bt_bets。"""
    try:
        from bizzoiro_client import API_KEY, fetch_actionable_results
        if not API_KEY:
            return 0
        import backtest as bt
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        from_today = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
        results = fetch_actionable_results(from_today, today)
        if not results:
            return 0
        settled = 0
        seen = set()
        for key, val in results.items():
            parts = key.split('|')
            if len(parts) != 2:
                continue
            if key in seen:
                continue
            seen.add(key)
            settled += bt.settle_bet(
                match_id='', home_score=val['home'], away_score=val['away'],
                home_team=parts[0], away_team=parts[1]
            ) or 0
        logger.info('[pipeline] settled %d', settled)
        return settled
    except Exception as e:
        logger.warning('[pipeline] settle error: %s', e)
        return 0


def run_full():
    """完整流水线（定时任务入口）。"""
    n1 = run_pipeline()
    n2 = settle_finished()
    return {'snapshots': n1, 'settled': n2}
