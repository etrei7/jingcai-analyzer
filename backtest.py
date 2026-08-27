"""回测闭环业务逻辑：价值盘计算、赛事/赔率快照、赛后结算、ROI 汇总。
设计目标：让"预测战绩面板"真正可用——区分命中率与盈利 ROI，形成可回测闭环。
全部操作写入新增表（bt_*），不改变原有数据。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CST_HOURS = 8
# 是否启用持久化回测（无则仅计算不入库，兼容测试环境）
USE_DB = True


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def implied_prob(odds):
    """赔率隐含概率 = 1/odds"""
    return (1.0 / odds) if odds and odds > 0 else 0.0


def is_value_bet(predicted_prob, odds, threshold=0.0):
    """价值盘判断：模型预测概率 > 赔率隐含概率 + 阈值才算正期望。
    返回 (is_value, edge)：edge = predicted_prob - implied_prob
    """
    ip = implied_prob(odds)
    edge = predicted_prob - ip
    return edge > threshold, edge


def record_odds_snapshot(match_id, market, home, draw, away, line=None, source=''):
    """写入赔率快照（带时间戳）。"""
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
        logger.warning('[backtest] odds_snapshot write failed: %s', e)
        return None


def record_prediction(match_id, play_type, pick, predicted_prob, odds,
                      model_name='jingcai-model', confidence=None, combo='single',
                      home_team=None, away_team=None):
    """记录一条 AI 推荐，并评估是否为价值盘。"""
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
        logger.warning('[backtest] prediction record failed: %s', e)
        return {'value': value, 'edge': round(edge, 4)}


def settle_bet(match_id, home_score, away_score, home_team=None, away_team=None):
    """赛后结算：根据 1X2 结果回填投注 outcome 与 pnl。
    优先按 match_id 匹配；若 match_id 匹配不到（竞彩 vs Bzzoiro 编号差异），
    则按 (home_team, away_team) 规约匹配，兼容回测闭环。
    pnl = (odds-1)*stake if win else -stake if lose else 0 (void)
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
        logger.warning('[backtest] settle failed: %s', e)
        return 0


def compute_summary(period='all', model_name=None, play_type=None):
    """聚合战绩：命中率、ROI、累计盈亏。供面板读取。"""
    try:
        from backtest_models import BtBet, db
        q = BtBet.query
        if play_type and play_type != 'all':
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

        # 预测明细：供面板展示历史战绩
        pick_map = {'H': '主胜', 'D': '平', 'A': '客胜'}
        records = []
        for b in bets:
            rec = {
                'match_id': b.match_id,
                'home_team': b.home_team,
                'away_team': b.away_team,
                'play_type': b.play_type,
                'pick': b.pick,
                'pick_cn': pick_map.get(b.pick, b.pick),
                'odds': b.odds,
                'stake': b.stake,
                'outcome': b.outcome,
                'pnl': b.pnl,
                'settled_at': b.settled_at,
                'hit': (b.outcome == 'win'),
                'result_cn': ('命中' if b.outcome == 'win' else '未中' if b.outcome == 'lose' else '待结算') if b.outcome else '待结算',
            }
            records.append(rec)

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
            'records': records,
        }
    except Exception as e:
        logger.warning('[backtest] summary failed: %s', e)
        return {'period': period, 'total_bets': 0, 'total_pnl': 0, 'roi': 0, 'hit_rate': 0, 'pending': 0, 'records': []}


def persist_summary(period='all', model_name=None, play_type=None):
    """物化战绩汇总到 bt_backtest_summary，供面板快速读取。"""
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
        logger.warning('[backtest] persist failed: %s', e)
        return None
