"""鏁版嵁娴佹按绾匡細瀹氭椂鎷夊彇璧涗簨+璧旂巼骞惰惤搴擄紝璧涘悗鍥炲～缁撴灉锛屽舰鎴愬洖娴嬮棴鐜€?鍩轰簬 Bzzoiro锛堝悗绔彲璁块棶锛夛紱sporttery 鍦ㄦ湇鍔″櫒绔?403锛屾晠绔炲僵瀹樻柟鏁版嵁璧板墠绔紝姝ゅ浠呭洖娴嬨€?"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run_pipeline():
    """鎷夊彇鏈潵璧涗簨+璧旂巼蹇収 + 鐢熸垚浠峰€肩洏棰勬祴钀藉簱銆傜敱 APScheduler 瀹氭椂璋冪敤銆?""
    try:
        from bizzoiro_client import API_KEY, fetch_events
        if not API_KEY:
            logger.info('[pipeline] 鏃?API Key锛岃烦杩?)
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
            # 璧旂巼蹇収
            bt.record_odds_snapshot(
                match_id=str(mid), market='1X2',
                home=m.get('win_odds'), draw=m.get('draw_odds'), away=m.get('lose_odds'),
                source='Bzzoiro'
            )

            # 鐢卞競鍦烘渶浣庤禂鐜囦富鎺?1X2锛岀敤闅愬惈姒傜巼 + 杞诲井涓婃诞浣滀负妯″瀷姒傜巼锛屽垽鏂环鍊肩洏
            win, draw, loss = m.get('win_odds'), m.get('draw_odds'), m.get('lose_odds')
            opts = [('H', win), ('D', draw), ('A', loss)]
            best = min((o for o in opts if o[1] and o[1] > 0), key=lambda x: x[1])
            pick, odds = best
            # 妯″瀷姒傜巼锛氶殣鍚鐜囷紙甯傚満浠凤級浣滀负鍩哄噯
            imp = bt.implied_prob(odds)
            conf_level = m.get('confidence_level', '')
            conf = 0.8 if conf_level == '楂? else 0.6 if conf_level == '涓? else 0.4
            predicted_prob = round(imp * conf, 4)  # 淇濆畧涓嬩慨锛岄伩鍏嶉珮浼?
            bt.record_prediction(
                match_id=str(mid), play_type='1X2', pick=pick,
                predicted_prob=predicted_prob, odds=odds,
                model_name='jingcai-value', confidence=conf, combo='single',
                home_team=m.get('home_team'), away_team=m.get('away_team')
            )
            saved += 1
        logger.info('[pipeline] 澶勭悊 %d 鍦猴紙蹇収+棰勬祴锛?, saved)
        return saved
    except Exception as e:
        logger.warning('[pipeline] 寮傚父: %s', e)
        return 0


def settle_finished():
    """璧涘悗缁撶畻锛氭媺鍙栬繎7澶╁凡瀹岃禌锛屾寜闃熷悕鍥炲～ bt_bets銆?""
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
            # key 褰㈠ 'home|away'锛堝惈鍙嶅悜 key锛夛紝鍙鐞嗘鍚戜竴娆★紝閬垮厤閲嶅
            parts = key.split('|')
            if len(parts) != 2:
                continue
            norm_key = key
            if norm_key in seen:
                continue
            seen.add(norm_key)
            settled += bt.settle_bet(
                match_id='', home_score=val['home'], away_score=val['away'],
                home_team=parts[0], away_team=parts[1]
            ) or 0
        logger.info('[pipeline] 缁撶畻瀹屾垚 %d', settled)
        return settled
    except Exception as e:
        logger.warning('[pipeline] 缁撶畻寮傚父: %s', e)
        return 0


def run_full():
    """瀹屾暣娴佹按绾匡紙瀹氭椂浠诲姟鍏ュ彛锛夈€?""
    n1 = run_pipeline()
    n2 = settle_finished()
    return {'snapshots': n1, 'settled': n2}
