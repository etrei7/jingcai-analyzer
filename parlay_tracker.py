"""串关级命中率追踪：独立于单场预测，记录每套串关方案并逐场结算。

单场命中率 ≠ 串关命中率（串关为乘法关系：任一腿未中即整套未中）。
本模块让"串关真实胜率与期望收益"可被独立追踪，写入新增表 bt_parlays，不影响既有表。
"""
import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 串关汇总缓存：前端轮询时避免重复全表聚合
_parlay_cache = {}
_parlay_lock = threading.Lock()
_PARLAY_TTL = 60


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _norm(s):
    """队名规约：小写、去空格/连字符，便于跨源匹配。"""
    return (s or '').replace(' ', '').replace('-', '').lower()


def _leg_signature(legs):
    parts = []
    for l in sorted(legs, key=lambda x: (str(x.get('match_id')), str(x.get('option')))):
        parts.append('%s:%s:%s' % (l.get('match_id'), l.get('play_type'), l.get('pick')))
    return '|'.join(parts)


def record_parlays(recs, source='', date_str=None):
    """记录本次生成的所有串关方案。按 (日期 + 腿部签名) 去重，避免刷新重复入库。
    返回新增条数。"""
    if not recs:
        return 0
    date_str = date_str or datetime.now().strftime('%Y-%m-%d')
    added = 0
    try:
        from backtest_models import BtParlay, db
        existing = {p.signature for p in BtParlay.query.filter_by(plan_date=date_str).all()}
        for r in recs:
            legs = []
            for d in (r.get('matches_detail') or []):
                if not d.get('play_type'):
                    continue
                legs.append({
                    'match_id': d.get('match_id'), 'home_team': d.get('home_team'),
                    'away_team': d.get('away_team'), 'option': d.get('option'),
                    'odds': d.get('odds'), 'play_type': d.get('play_type'),
                    'pick': d.get('pick'), 'match_time': d.get('match_time', ''),
                })
            if len(legs) < 2:
                continue
            sig = date_str + '#' + _leg_signature(legs)
            if sig in existing:
                continue
            existing.add(sig)
            p = BtParlay(
                plan_date=date_str, name=r.get('name', ''), plan_type=r.get('plan_type', ''),
                risk_level=r.get('risk_level', ''), combo_odds=r.get('combo_odds'),
                stake=1.0, stake_pct=r.get('stake_pct', 0) or 0, source=source,
                legs_json=json.dumps(legs, ensure_ascii=False), signature=sig,
                earliest_time=r.get('earliest_time', ''), created_at=_now_str(),
            )
            db.session.add(p)
            added += 1
        if added:
            db.session.commit()
            clear_parlay_cache()
    except Exception as e:
        logger.warning('[parlay] record failed: %s', e)
    return added


def eval_leg(play_type, pick, hs, aw, hht, awt):
    """判定单条串关腿的结果：返回 'win' / 'lose' / 'void'。
    支持 1X2 / AH / HTFT / CS / TG（总进球精确值）。异常兜底 void。"""
    try:
        if hs is None or aw is None:
            return 'void'
        pt = play_type or ''
        actual = 'H' if hs > aw else 'A' if hs < aw else 'D'
        if pt == '1X2':
            return 'win' if pick == actual else 'lose'
        if pt == 'AH':
            if '|' not in (pick or ''):
                return 'void'
            p, line = pick.split('|', 1)
            try:
                line = float(line)
            except (TypeError, ValueError):
                return 'void'
            adj = hs + line - aw
            a2 = 'H' if adj > 0 else 'A' if adj < 0 else 'D'
            return 'win' if p == a2 else 'lose'
        if pt == 'HTFT':
            if hht is None or awt is None:
                return 'void'
            ht = 'H' if hht > awt else 'A' if hht < awt else 'D'
            return 'win' if pick == (ht + actual) else 'lose'
        if pt == 'CS':
            return 'win' if pick == ('%s-%s' % (hs, aw)) else 'lose'
        if pt == 'TG':
            total = hs + aw
            if pick == '7+':
                return 'win' if total >= 7 else 'lose'
            if str(pick).isdigit():
                return 'win' if total == int(pick) else 'lose'
            return 'void'
        return 'void'
    except Exception:
        return 'void'


def settle_parlays(results):
    """结算所有待结算串关。
    results: {match_id(str): {'home_score','away_score','home_score_ht','away_score_ht','home_team','away_team'}}
    规则：任一腿 lose → 整套 lose；全部 win → win；存在 pending/void → 留待下次。
    按比赛 ID 精确定位；定位不到时用队名规约兜底（竞彩编号 vs Bzzoiro 事件的跨源匹配）。
    返回本次结算套数。
    """
    settled = 0
    try:
        from backtest_models import BtParlay, db
        results = results or {}
        name_idx = {}
        for _mid, ev in results.items():
            h, a = _norm(ev.get('home_team')), _norm(ev.get('away_team'))
            if h and a:
                name_idx[(h, a)] = ev
        pending = BtParlay.query.filter_by(settled_at=None).all()
        for p in pending:
            try:
                legs = json.loads(p.legs_json or '[]')
            except Exception:
                legs = []
            if not legs:
                continue
            outcomes = []
            for l in legs:
                mid = str(l.get('match_id'))
                ev = results.get(mid)
                if ev is None:
                    ev = name_idx.get((_norm(l.get('home_team')), _norm(l.get('away_team'))))
                if ev is None:
                    outcomes.append('pending')
                    continue
                outcomes.append(eval_leg(l.get('play_type'), l.get('pick'),
                                         ev.get('home_score'), ev.get('away_score'),
                                         ev.get('home_score_ht'), ev.get('away_score_ht')))
            if any(o == 'lose' for o in outcomes):
                p.outcome = 'lose'
                p.pnl = round(-(p.stake or 1.0), 2)
            elif any(o in ('pending', 'void') for o in outcomes):
                continue
            else:
                p.outcome = 'win'
                p.pnl = round(((p.combo_odds or 0) - 1) * (p.stake or 1.0), 2)
            p.settled_at = _now_str()
            settled += 1
        if settled:
            db.session.commit()
            clear_parlay_cache()
        return settled
    except Exception as e:
        logger.warning('[parlay] settle failed: %s', e)
        return 0


def parlay_summary():
    """串关命中率 / ROI / 累计盈亏 汇总（供面板读取）。"""
    try:
        from backtest_models import BtParlay
        rows = BtParlay.query.all()
        settled = [p for p in rows if p.outcome in ('win', 'lose')]
        wins = sum(1 for p in settled if p.outcome == 'win')
        losses = len(settled) - wins
        stake = sum((p.stake or 1.0) for p in settled)
        pnl = sum((p.pnl or 0.0) for p in settled)
        hit_rate = round(wins / len(settled) * 100, 1) if settled else 0.0
        roi = round(pnl / stake * 100, 1) if stake else 0.0
        records = []
        for p in sorted(rows, key=lambda x: (x.plan_date or '', x.id or 0), reverse=True):
            try:
                legs = json.loads(p.legs_json or '[]')
            except Exception:
                legs = []
            records.append({
                'name': p.name, 'plan_type': p.plan_type, 'risk_level': p.risk_level,
                'combo_odds': p.combo_odds, 'stake_pct': p.stake_pct,
                'plan_date': p.plan_date, 'outcome': p.outcome, 'pnl': p.pnl,
                'earliest_time': p.earliest_time,
                'result_cn': '命中' if p.outcome == 'win' else '未中' if p.outcome == 'lose' else '待结算',
                'legs': [{'home_team': l.get('home_team'), 'away_team': l.get('away_team'),
                          'option': l.get('option'), 'odds': l.get('odds'),
                          'match_time': l.get('match_time')} for l in legs],
            })
        return {
            'total': len(rows),
            'settled': len(settled),
            'pending': len(rows) - len(settled),
            'wins': wins,
            'losses': losses,
            'hit_rate': hit_rate,
            'total_stake': round(stake, 2),
            'total_pnl': round(pnl, 2),
            'roi': roi,
            'computed_at': _now_str(),
            'records': records,
        }
    except Exception as e:
        logger.warning('[parlay] summary failed: %s', e)
        return {'total': 0, 'settled': 0, 'pending': 0, 'wins': 0, 'losses': 0,
                'hit_rate': 0, 'total_stake': 0, 'total_pnl': 0, 'roi': 0, 'records': []}


def parlay_summary_cached():
    """带 TTL（60s）的 parlay_summary。"""
    with _parlay_lock:
        v = _parlay_cache.get('s')
        if v and (time.time() - v[0]) < _PARLAY_TTL:
            return v[1]
    data = parlay_summary()
    with _parlay_lock:
        _parlay_cache['s'] = (time.time(), data)
    return data


def clear_parlay_cache():
    with _parlay_lock:
        _parlay_cache.clear()
