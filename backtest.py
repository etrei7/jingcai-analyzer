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
                      home_team=None, away_team=None, estimated=False):
    """记录一条 AI 推荐，并评估是否为价值盘。
    estimated=True 表示赔率为模型估算（如比分/半全场），非真实市场赔率，
    不参与"可投注价值"的 ROI 统计，避免虚构高赔率撑高盈利。"""
    value, edge = (False, 0.0)
    if not estimated and predicted_prob is not None and odds:
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
            estimated=estimated,
        )
        db.session.add(bet)
        db.session.commit()
        return {'value': value, 'edge': round(edge, 4), 'prediction_id': pred.id}
    except Exception as e:
        logger.warning('[backtest] prediction record failed: %s', e)
        return {'value': value, 'edge': round(edge, 4)}


def settle_bet(match_id, home_score, away_score, home_team=None, away_team=None,
               home_score_ht=None, away_score_ht=None):
    """赛后结算：按玩法类型回填投注 outcome 与 pnl。
    支持 1X2(胜平负)、AH(让胜平负)、CS(正确比分)、HTFT(半全场)、OU(大小球)。
    优先按 match_id 匹配；匹配不到则按 (home_team, away_team) 规约匹配。
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
            outcome, pnl = _eval_play(b, actual, home_score, away_score,
                                     home_score_ht, away_score_ht, stake)
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


def _eval_play(b, actual, hs, aw, hht, awt, stake):
    """按玩法判定单条投注结果。返回 (outcome, pnl)。"""
    pt = b.play_type or '1X2'
    pick = b.pick or ''

    # 1X2 胜平负
    if pt == '1X2':
        if pick == actual:
            return 'win', round((b.odds - 1) * stake, 4)
        elif pick in ('H', 'D', 'A'):
            return 'lose', round(-stake, 4)
        return 'void', 0.0

    # AH 让胜平负（pick 形如 'H', 'D', 'A'，按实际让球后结果判定；无让球线时用 1X2）
    if pt == 'AH':
        # pick 以 '|' 分隔：如 'H|-1' 表示主让1球，实际让球结果 = 主净胜 - line
        if '|' in pick:
            p, line = pick.split('|', 1)
            try:
                line = float(line)
            except Exception:
                line = 0
            diff = hs - aw
            # 让球后：主队实际盘口净胜 = diff - line（主让负line，受让加）
            adj = diff - line
            ah_actual = 'H' if adj > 0 else 'A' if adj < 0 else 'D'
            if p == ah_actual:
                return 'win', round((b.odds - 1) * stake, 4)
            elif p in ('H', 'D', 'A'):
                return 'lose', round(-stake, 4)
            return 'void', 0.0
        # 无让球线，按 1X2
        if pick == actual:
            return 'win', round((b.odds - 1) * stake, 4)
        elif pick in ('H', 'D', 'A'):
            return 'lose', round(-stake, 4)
        return 'void', 0.0

    # CS 正确比分（pick 形如 '2-1'）
    if pt == 'CS':
        if pick == f'{hs}-{aw}':
            return 'win', round((b.odds - 1) * stake, 4)
        return 'lose', round(-stake, 4)

    # HTFT 半全场（pick 形如 'HH','HD','HA','DH'...，即 半场结果+全场结果）
    if pt == 'HTFT':
        hh, awt_h = hht, awt
        ht_actual = 'H' if (hh is not None and awt_h is not None and hh > awt_h) else \
                    'D' if (hh is not None and awt_h is not None and hh == awt_h) else \
                    'A' if (hh is not None and awt_h is not None and hh < awt_h) else None
        if ht_actual is None:
            return 'void', 0.0
        if pick == (ht_actual + actual):
            return 'win', round((b.odds - 1) * stake, 4)
        return 'lose', round(-stake, 4)

    # OU 大小球（pick 形如 'O25' 表示大2.5，'U25' 表示小2.5）
    if pt == 'OU':
        total = hs + aw
        try:
            if pick.startswith('O'):
                line = float(pick[1:]) / 10.0
                return ('win', round((b.odds - 1) * stake, 4)) if total > line else ('lose', round(-stake, 4))
            elif pick.startswith('U'):
                line = float(pick[1:]) / 10.0
                return ('win', round((b.odds - 1) * stake, 4)) if total < line else ('lose', round(-stake, 4))
        except Exception:
            return 'void', 0.0
        return 'void', 0.0

    # 未知玩法兜底
    return 'void', 0.0


def compute_summary(period='all', model_name=None, play_type=None):
    """聚合战绩：命中率、ROI、累计盈亏。供面板读取。"""
    try:
        from backtest_models import BtBet, db
        q = BtBet.query
        if play_type and play_type != 'all':
            q = q.filter_by(play_type=play_type)
        bets = q.all()
        settled = [b for b in bets if b.settled_at]
        # 区分"可投注价值"（真实赔率）与"模型估算赔率"（比分/半全场）
        market_settled = [b for b in settled if not getattr(b, 'estimated', False)]
        est_settled = [b for b in settled if getattr(b, 'estimated', False)]
        total = len(settled)
        # 整体命中率：基于真实赔率玩法（可投注），估算玩法仅作参考看板
        wins = sum(1 for b in market_settled if b.outcome == 'win')
        losses = sum(1 for b in market_settled if b.outcome == 'lose')
        voids = sum(1 for b in settled if b.outcome == 'void')
        total_stake = sum((b.stake or 1.0) for b in market_settled if b.outcome in ('win', 'lose'))
        total_pnl = sum((b.pnl or 0.0) for b in market_settled)
        hit_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0
        roi = round(total_pnl / total_stake * 100, 1) if total_stake else 0.0
        odds_list = [b.odds for b in market_settled if b.odds]
        avg_odds = round(sum(odds_list) / len(odds_list), 2) if odds_list else 0.0
        # 估算赔率玩法样本（比分/半全场）——不计入 ROI，单独给面板提示
        est_total = len(est_settled)
        est_wins = sum(1 for b in est_settled if b.outcome == 'win')

        # 预测明细：供面板展示历史战绩（按玩法解释 pick）
        def describe(play_type, pick):
            pt = play_type or '1X2'
            if pt == '1X2':
                p = {'H': '主胜', 'D': '平', 'A': '客胜'}.get(pick, pick)
                return p
            if pt == 'AH':
                if '|' in pick:
                    p, line = pick.split('|', 1)
                    side = {'H': '主', 'D': '平', 'A': '客'}.get(p, p)
                    try:
                        line = float(line)
                        lab = (-line) if line > 0 else abs(line)
                        return f'{side}让{lab:.0f}' if line < 0 else f'{side}受让{lab:.0f}'
                    except Exception:
                        return side
                return {'H': '主胜', 'D': '平', 'A': '客胜'}.get(pick, pick)
            if pt == 'CS':
                return f'比分{pick}'
            if pt == 'HTFT':
                m = {'H': '胜', 'D': '平', 'A': '负'}
                if len(pick) >= 2:
                    return '半场' + m.get(pick[0], pick[0]) + '/全场' + m.get(pick[1], pick[1])
                return pick
            if pt == 'OU':
                return ('大' + str(float(pick[1:]) / 10.0) + '球') if pick.startswith('O') else \
                       ('小' + str(float(pick[1:]) / 10.0) + '球') if pick.startswith('U') else pick
            return pick

        play_cn = {'1X2': '胜平负', 'AH': '让胜平负', 'CS': '比分', 'HTFT': '半全场', 'OU': '大小球'}
        records = []
        for b in bets:
            rec = {
                'match_id': b.match_id,
                'home_team': b.home_team,
                'away_team': b.away_team,
                'play_type': b.play_type,
                'play_type_cn': play_cn.get(b.play_type, b.play_type or '胜平负'),
                'pick': b.pick,
                'pick_cn': describe(b.play_type, b.pick),
                'odds': b.odds,
                'stake': b.stake,
                'outcome': b.outcome,
                'pnl': b.pnl,
                'settled_at': b.settled_at,
                'hit': (b.outcome == 'win'),
                'estimated': getattr(b, 'estimated', False),
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
            # 估算赔率玩法（比分/半全场）不计入 ROI，仅作参考
            'estimated_total': est_total,
            'estimated_wins': est_wins,
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
