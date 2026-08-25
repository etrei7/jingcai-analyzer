"""鍥炴祴闂幆涓氬姟閫昏緫锛氫环鍊肩洏璁＄畻銆佽禌浜?璧旂巼蹇収銆佽禌鍚庣粨绠椼€丷OI 姹囨€汇€?璁捐鐩爣锛氳"棰勬祴鎴樼哗闈㈡澘"鐪熸鍙敤鈥斺€斿尯鍒嗗懡涓巼涓庣泩鍒?ROI锛屽舰鎴愬彲鍥炴祴闂幆銆?鍏ㄩ儴鎿嶄綔鍐欏叆鏂板琛紙bt_*锛夛紝涓嶆敼鍙樺師鏈夋暟鎹€?"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CST_HOURS = 8
# 鏄惁鍚敤鎸佷箙鍖栧洖娴嬶紙鏃犲垯浠呰绠椾笉鍏ュ簱锛屽吋瀹规祴璇曠幆澧冿級
USE_DB = True


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def implied_prob(odds):
    """璧旂巼闅愬惈姒傜巼 = 1/odds"""
    return (1.0 / odds) if odds and odds > 0 else 0.0


def is_value_bet(predicted_prob, odds, threshold=0.0):
    """浠峰€肩洏鍒ゆ柇锛氭ā鍨嬮娴嬫鐜?> 璧旂巼闅愬惈姒傜巼 + 闃堝€兼墠绠楁鏈熸湜銆?    杩斿洖 (is_value, edge)锛歟dge = predicted_prob - implied_prob
    """
    ip = implied_prob(odds)
    edge = predicted_prob - ip
    return edge > threshold, edge


def record_odds_snapshot(match_id, market, home, draw, away, line=None, source=''):
    """鍐欏叆璧旂巼蹇収锛堝甫鏃堕棿鎴筹級銆?""
    if not USE_DB:
        return None
    try:
        from backtest_models import BtOddsSnapshot, db
        snap = BtOddsSnapshot(
            match_id=match_id, market=market,
            snapshot_time=_now_str(),
            home_odds=home, draw_odds=draw, away_odds=away,
            line=line, source=source,
        )
        db.session.add(snap)
        db.session.commit()
        return snap
    except Exception as e:
        logger.warning('[backtest] 璧旂巼蹇収鍐欏叆澶辫触: %s', e)
        return None


def record_prediction(match_id, play_type, pick, predicted_prob, odds,
                      model_name='jingcai-model', confidence=None, combo='single',
                      home_team=None, away_team=None):
    """璁板綍涓€鏉?AI 鎺ㄨ崘锛屽苟璇勪及鏄惁涓轰环鍊肩洏銆?""
    value, edge = (False, 0.0)
    if predicted_prob is not None and odds:
        value, edge = is_value_bet(predicted_prob, odds)
    if not USE_DB:
        return {'value': value, 'edge': round(edge, 4)}

    try:
        from backtest_models import BtPrediction, BtBet, db
        pred = BtPrediction(
            match_id=match_id, play_type=play_type, pick=pick,
            model_name=model_name, confidence=confidence,
            predicted_prob=predicted_prob,
            odds_at_prediction=odds, combo=combo,
        )
        db.session.add(pred)
        db.session.flush()
        bet = BtBet(
            prediction_id=pred.id, match_id=match_id, play_type=play_type,
            pick=pick, odds=odds, stake=1.0,
            home_team=home_team, away_team=away_team,
        )
        db.session.add(bet)
        db.session.commit()
        return {'value': value, 'edge': round(edge, 4), 'prediction_id': pred.id}
    except Exception as e:
        logger.warning('[backtest] 棰勬祴璁板綍澶辫触: %s', e)
        return {'value': value, 'edge': round(edge, 4)}


def settle_bet(match_id, home_score, away_score, home_team=None, away_team=None):
    """璧涘悗缁撶畻锛氭牴鎹?1X2 缁撴灉鍥炲～鎶曟敞 outcome 涓?pnl銆?    浼樺厛鎸?match_id 鍖归厤锛涜嫢 match_id 鍖归厤涓嶅埌锛堢珵褰?vs Bzzoiro 缂栧彿宸紓锛夛紝
    鍒欐寜 (home_team, away_team) 瑙勭害鍖归厤锛屽吋瀹瑰洖娴嬮棴鐜€?    pnl = (odds-1)*stake if win else -stake if lose else 0 (void)
    """
    if not USE_DB:
        return None
    try:
        from backtest_models import BtBet, db
        if home_score is None or away_score is None:
            return None
        if home_score > away_score:
            actual = 'H'
        elif home_score < away_score:
            actual = 'A'
        else:
            actual = 'D'

        bets = BtBet.query.filter_by(match_id=match_id).all()
        if not bets and home_team and away_team:
            def norm(s):
                return (s or '').replace(' ', '').replace('-', '').lower()
            hk, ak = norm(home_team), norm(away_team)
            for b in BtBet.query.all():
                if norm(b.home_team) == hk and norm(b.away_team) == ak:
                    bets.append(b)

        settled = 0
        seen = set()
        for b in (bets or []):
            if b.id in seen or b.settled_at:
                continue
            seen.add(b.id)
            stake = b.stake or 1.0
            if b.pick == actual:
                outcome = 'win'
                pnl = round((b.odds - 1) * stake, 4)
            elif b.pick in ('H', 'D', 'A'):
                outcome = 'lose'
                pnl = round(-stake, 4)
            else:
                outcome = 'void'
                pnl = 0.0
            b.outcome = outcome
            b.pnl = pnl
            b.settled_at = _now_str()
            settled += 1
        if settled:
            db.session.commit()
        return settled
    except Exception as e:
        logger.warning('[backtest] 缁撶畻澶辫触: %s', e)
        return 0


def compute_summary(period='all', model_name=None, play_type=None):
    """鑱氬悎鎴樼哗锛氬懡涓巼銆丷OI銆佺疮璁＄泩浜忋€備緵闈㈡澘璇诲彇銆?""
    try:
        from backtest_models import BtBet, db
        q = BtBet.query
        if play_type:
            q = q.filter_by(play_type=play_type)
        bets = q.all()
        settled = [b for b in bets if b.settled_at]
        total = len(settled)
        wins = sum(1 for b in settled if b.outcome == 'win')
        losses = sum(1 for b in settled if b.outcome == 'lose')
        voids = sum(1 for b in settled if b.outcome == 'void')
        total_stake = sum((b.stake or 1.0) for b in settled if b.outcome in ('win', 'lose'))
        total_pnl = sum((b.pnl or 0.0) for b in settled)
        hit_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0
        roi = round(total_pnl / total_stake * 100, 1) if total_stake else 0.0
        odds_list = [b.odds for b in settled if b.odds]
        avg_odds = round(sum(odds_list) / len(odds_list), 2) if odds_list else 0.0

        return {
            'period': period,
            'model_name': model_name,
            'play_type': play_type or 'all',
            'total_bets': total,
            'wins': wins,
            'losses': losses,
            'voids': voids,
            'hit_rate': hit_rate,
            'total_stake': round(total_stake, 2),
            'total_pnl': round(total_pnl, 2),
            'roi': roi,
            'avg_odds': avg_odds,
            'computed_at': _now_str(),
            'pending': len(bets) - total,
        }
    except Exception as e:
        logger.warning('[backtest] 姹囨€诲け璐? %s', e)
        return {'period': period, 'total_bets': 0, 'total_pnl': 0, 'roi': 0, 'hit_rate': 0, 'pending': 0}


def persist_summary(period='all', model_name=None, play_type=None):
    """鐗╁寲鎴樼哗姹囨€诲埌 bt_backtest_summary锛屼緵闈㈡澘蹇€熻鍙栥€?""
    if not USE_DB:
        return None
    try:
        from backtest_models import BtBacktestSummary, db
        s = compute_summary(period, model_name, play_type)
        row = BtBacktestSummary(
            period=period, model_name=model_name, play_type=play_type or 'all',
            total_bets=s['total_bets'], wins=s['wins'], losses=s['losses'], voids=s['voids'],
            hit_rate=s['hit_rate'], total_stake=s['total_stake'], total_pnl=s['total_pnl'],
            roi=s['roi'], avg_odds=s['avg_odds'], computed_at=s['computed_at'],
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception as e:
        logger.warning('[backtest] 姹囨€荤墿鍖栧け璐? %s', e)
        return None
