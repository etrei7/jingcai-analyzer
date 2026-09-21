"""信心分级校准：用已结算的真实推荐（竞彩 1X2）统计各信心等级的历史命中率，
并与该等级的盈亏平衡命中率（1/平均赔率）对比，据此**保守地降级**不可靠的"高信心"，
避免"高信心"名不副实。只降不升，样本不足时不干预。
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

_cache = {}
_lock = threading.Lock()
_TTL = 120
_MIN_N = 50          # 单等级最小样本
_HARD_DOWN_PP = -5.0  # 命中率低于盈亏平衡 5 个百分点 → 降级

_ORDER = {'高': 2, '中': 1, '低': 0}


def _now():
    return time.time()


def get_calibration(force=False):
    """返回 {buckets:{等级:{n,wins,hit_rate,avg_odds,required_rate,edge}}, total, min_n}。"""
    with _lock:
        v = _cache.get('c')
        if v and not force and (_now() - v[0]) < _TTL:
            return v[1]
    data = _compute()
    with _lock:
        _cache['c'] = (_now(), data)
    return data


def _compute():
    try:
        from backtest_models import BtBet
        bets = [b for b in BtBet.query.filter_by(play_type='1X2', jingcai=True).all()
                if b.settled_at]
        buckets = {}
        for b in bets:
            lv = b.confidence_level or '未分级'
            d = buckets.setdefault(lv, {'n': 0, 'wins': 0, 'odds_sum': 0.0})
            if b.outcome in ('win', 'lose'):
                d['n'] += 1
                d['odds_sum'] += (b.odds or 0)
                if b.outcome == 'win':
                    d['wins'] += 1
        out = {}
        for lv, d in buckets.items():
            n = d['n']
            if n <= 0:
                continue
            avg = d['odds_sum'] / n
            hit = d['wins'] / n
            required = (1.0 / avg) if avg > 0 else 0.0
            out[lv] = {
                'n': n, 'wins': d['wins'],
                'hit_rate': round(hit * 100, 1),
                'avg_odds': round(avg, 2),
                'required_rate': round(required * 100, 1),
                'edge': round((hit - required) * 100, 1),
            }
        return {'buckets': out, 'total': sum(d['n'] for d in out.values()),
                'min_n': _MIN_N, 'updated': time.strftime('%Y-%m-%d %H:%M:%S')}
    except Exception as e:
        logger.warning('[calib] compute failed: %s', e)
        return {'buckets': {}, 'total': 0, 'min_n': _MIN_N}


def clear_cache():
    with _lock:
        _cache.clear()


def apply_calibration(matches):
    """对分析结果应用校准：样本足够且该等级命中率显著低于盈亏平衡时降一级。
    仅降不升；返回被降级场次数。"""
    cal = get_calibration()
    buckets = cal.get('buckets') or {}
    n_min = cal.get('min_n') or _MIN_N
    changed = 0
    for m in matches or []:
        lv = m.get('confidence_level')
        st = buckets.get(lv)
        if not st or st.get('n', 0) < n_min:
            m['calib'] = None
            continue
        m['calib'] = {
            'hit_rate': st['hit_rate'], 'required_rate': st['required_rate'],
            'n': st['n'], 'edge': st['edge'],
        }
        if lv in _ORDER and st.get('edge', 0) < _HARD_DOWN_PP:
            new_lv = {2: '中', 1: '低', 0: '低'}[_ORDER[lv]]
            if new_lv != lv:
                m['confidence_level'] = new_lv
                m['calib']['downgraded'] = True
                changed += 1
    return changed
