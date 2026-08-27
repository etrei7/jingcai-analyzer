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
        save_plays = []
        for m in analyzed:
            mid = m.get('raw_event_id') or m.get('match_id')
            if not mid:
                continue

            bt.record_odds_snapshot(
                match_id=str(mid), market='1X2',
                home=m.get('win_odds'), draw=m.get('draw_odds'), away=m.get('lose_odds'),
                source='Bzzoiro'
            )

            home, away = m.get('home_team'), m.get('away_team')
            conf_level = m.get('confidence_level', '')
            conf = 0.8 if conf_level == '高' else 0.6 if conf_level == '中' else 0.4
            win, draw, loss = m.get('win_odds'), m.get('draw_odds'), m.get('lose_odds')

            # 1X2 胜平负
            best = min((o for o in [('H', win), ('D', draw), ('A', loss)] if o[1] and o[1] > 0),
                       key=lambda x: x[1])
            pick1x2, odds1x2 = best
            bt.record_prediction(str(mid), '1X2', pick1x2,
                                 round(bt.implied_prob(odds1x2) * conf, 4), odds1x2,
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away)

            # AH 让胜平负（有盘口则记录盘口+推荐）
            hp = m.get('handicap', {}) or {}
            line = hp.get('handicap_line', 0)
            hwin, hdraw, hloss = hp.get('handicap_win_odds'), hp.get('handicap_draw_odds'), hp.get('handicap_lose_odds')
            hcp_pick = hp.get('hcp_pick', {})
            if hcp_pick and hcp_pick.get('odds'):
                hside = {'让胜': 'H', '让平': 'D', '让负': 'A'}.get(hcp_pick.get('side'), 'H')
                # pick 编码：让球结果 | 让球线（主让为负，主受让为正）
                hline = line if line else 0
                pick_ah = f'{hside}|{hline}'
                bt.record_prediction(str(mid), 'AH', pick_ah,
                                     round((hcp_pick.get('prob', 0) or 0) / 100 * conf, 4),
                                     hcp_pick.get('odds'),
                                     model_name='jingcai-value', confidence=conf,
                                     home_team=home, away_team=away)

            # CS 正确比分（推荐的比分）
            rec_score = m.get('recommended_score', '')
            if rec_score and '-' in rec_score:
                h, a = rec_score.split('-', 1)
                # 用模型概率估算比分赔率（隐含：1/概率）
                bt.record_prediction(str(mid), 'CS', rec_score,
                                     round(0.12 * conf, 4), None,
                                     model_name='jingcai-value', confidence=conf,
                                     home_team=home, away_team=away)

            # HTFT 半全场（半场期望≈全场*0.45 简化模拟，取最可能组合）
            htft = _predict_htft(m, conf)
            if htft:
                pick_htft, prob_htft, odds_htft = htft
                bt.record_prediction(str(mid), 'HTFT', pick_htft, prob_htft, odds_htft,
                                     model_name='jingcai-value', confidence=conf,
                                     home_team=home, away_team=away)
            saved += 1
        logger.info('[pipeline] processed %d matches with multi-play predictions', saved)
        return saved
    except Exception as e:
        logger.warning('[pipeline] run error: %s', e)
        return 0


def _predict_htft(m, conf, max_goals=5):
    """用泊松模型估算半全场最可能组合（半场期望=全场期望*0.45）。
    返回 (pick, prob, odds) 或 None。pick 如 'HH'（半场胜+全场胜）。
    """
    try:
        import math
        # 全场期望进球
        he = m.get('expected_total')
        if not he:
            return None
        he = float(he)
        h_exp = he * 0.55
        a_exp = he * (1 - 0.55)
        # 半场期望
        hht_exp = h_exp * 0.45
        awt_exp = a_exp * 0.45

        def poisson(k, lam):
            if lam <= 0:
                return 1.0 if k == 0 else 0.0
            return (lam ** k) * math.exp(-lam) / math.factorial(k)

        def result(gh, ga):
            return 'H' if gh > ga else 'A' if gh < ga else 'D'

        best = None
        best_prob = 0
        for hhg in range(max_goals):
            for awg in range(max_goals):
                for fhg in range(max_goals):
                    for fwg in range(max_goals):
                        if fhg < hhg or fwg < awg:
                            continue
                        p = poisson(hhg, hht_exp) * poisson(awg, awt_exp) * \
                            poisson(fhg - hhg, h_exp - hht_exp) * poisson(fwg - awg, a_exp - awt_exp)
                        pt = result(hhg, awg) + result(fhg, fwg)
                        if p > best_prob:
                            best_prob = p
                            best = pt
        if best and best_prob > 0:
            # 用模型概率估算赔率（9种半全场概率归一）
            odds = round(1.0 / max(best_prob, 0.05), 2)
            return best, round(min(best_prob, 0.9), 4), odds
        return None
    except Exception:
        return None


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
                home_team=parts[0], away_team=parts[1],
                home_score_ht=val.get('home_ht'), away_score_ht=val.get('away_ht')
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
