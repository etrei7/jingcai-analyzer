"""信心分级 + 同类场次经验校准。

用已结算的真实推荐（竞彩场次）统计：
1. 各信心等级的历史命中率 vs 盈亏平衡(1/平均赔率)；
2. 同类场次分段：`玩法|赔率区间`（如 1X2|1.50-2.00）与 `联赛`。
样本足够且命中率显著低于盈亏平衡时，**保守地降级**新场次信心并提示；
只降不升，样本不足不干预。让"回测里的比赛"反哺新场次分析。
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

_cache = {}
_lock = threading.Lock()
_TTL = 120
_MIN_N = 50          # 信心等级最小样本
_MIN_SEG = 30        # 分段最小样本
_HARD_DOWN_PP = -5.0  # 命中率低于盈亏平衡 5 个百分点 → 降级

_ORDER = {'高': 2, '中': 1, '低': 0}

# 赔率区间（左闭右开），用于同类场次分段
_ODDS_BUCKETS = [
    (1.0, 1.5, '1.00-1.50'), (1.5, 2.0, '1.50-2.00'), (2.0, 2.5, '2.00-2.50'),
    (2.5, 3.5, '2.50-3.50'), (3.5, 5.0, '3.50-5.00'), (5.0, 10.0, '5.00-10.0'),
    (10.0, 1e9, '10.0+'),
]


def _odds_bucket(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o <= 0:
        return None
    for lo, hi, lab in _ODDS_BUCKETS:
        if lo <= o < hi:
            return lab
    return None


def _now():
    return time.time()


def _add(d, key, b):
    r = d.setdefault(key, {'n': 0, 'wins': 0, 'odds_sum': 0.0})
    r['n'] += 1
    r['odds_sum'] += (b.odds or 0)
    if b.outcome == 'win':
        r['wins'] += 1


def _stat(v):
    n = v['n']
    avg = (v['odds_sum'] / n) if n else 0.0
    hit = (v['wins'] / n) if n else 0.0
    req = (1.0 / avg) if avg > 0 else 0.0
    return {
        'n': n, 'wins': v['wins'],
        'hit_rate': round(hit * 100, 1),
        'avg_odds': round(avg, 2),
        'required_rate': round(req * 100, 1),
        'edge': round((hit - req) * 100, 1),
    }


def get_calibration(force=False):
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
        bets = [b for b in BtBet.query.filter_by(jingcai=True).all()
                if b.settled_at and b.outcome in ('win', 'lose')]
        lv_b, seg_b, lg_b = {}, {}, {}
        for b in bets:
            _add(lv_b, b.confidence_level or '未分级', b)
            bk = _odds_bucket(b.odds)
            if bk:
                _add(seg_b, '%s|%s' % (b.play_type or '?', bk), b)
            if b.league:
                _add(lg_b, b.league, b)
        return {
            'buckets': {k: _stat(v) for k, v in lv_b.items()},
            'segments': {k: _stat(v) for k, v in seg_b.items()},
            'leagues': {k: _stat(v) for k, v in lg_b.items()},
            'total': len(bets), 'min_n': _MIN_N, 'min_seg': _MIN_SEG,
            'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    except Exception as e:
        logger.warning('[calib] compute failed: %s', e)
        return {'buckets': {}, 'segments': {}, 'leagues': {}, 'total': 0,
                'min_n': _MIN_N, 'min_seg': _MIN_SEG}


def clear_cache():
    with _lock:
        _cache.clear()


def apply_calibration(matches):
    """对分析结果应用校准：信心等级或同类场次(赔率区间/联赛)历史不盈利时降一级。
    仅降不升；返回被降级场次数。"""
    cal = get_calibration()
    buckets = cal.get('buckets') or {}
    segs = cal.get('segments') or {}
    lgs = cal.get('leagues') or {}
    n_min = cal.get('min_n') or _MIN_N
    n_seg = cal.get('min_seg') or _MIN_SEG
    changed = 0
    for m in matches or []:
        lv = m.get('confidence_level')
        st = buckets.get(lv)
        if st and st.get('n', 0) >= n_min:
            m['calib'] = {'hit_rate': st['hit_rate'], 'required_rate': st['required_rate'],
                          'n': st['n'], 'edge': st['edge']}
        else:
            m['calib'] = None

        # 同类场次：推荐方向所在赔率区间 + 联赛
        po = m.get('predicted_option')
        odds = {'胜': m.get('win_odds'), '平': m.get('draw_odds'),
                '负': m.get('lose_odds')}.get(po)
        bk = _odds_bucket(odds)
        seg_bad = False
        m['segment'] = None
        seg = segs.get('1X2|' + bk) if bk else None
        if seg and seg.get('n', 0) >= n_seg:
            m['segment'] = {'play': '1X2', 'bucket': bk, 'n': seg['n'],
                            'hit_rate': seg['hit_rate'], 'required_rate': seg['required_rate'],
                            'edge': seg['edge']}
            if seg['edge'] < _HARD_DOWN_PP:
                seg_bad = True
        lg = m.get('league')
        lst = lgs.get(lg) if lg else None
        if lst and lst.get('n', 0) >= n_seg and lst['edge'] < _HARD_DOWN_PP:
            seg_bad = True
            if m['segment'] is None:
                m['segment'] = {'play': '联赛', 'bucket': lg, 'n': lst['n'],
                                'hit_rate': lst['hit_rate'],
                                'required_rate': lst['required_rate'], 'edge': lst['edge']}
        m['seg_bad'] = seg_bad

        lv_bad = bool(st and st.get('n', 0) >= n_min and st.get('edge', 0) < _HARD_DOWN_PP)
        if lv in _ORDER and (lv_bad or seg_bad):
            new_lv = {2: '中', 1: '低', 0: '低'}[_ORDER[lv]]
            if new_lv != lv:
                m['confidence_level'] = new_lv
                changed += 1
        if seg_bad:
            try:
                m['ai_preview'] = (m.get('ai_preview') or '') + \
                    '（同类场次历史胜率不足，已谨慎处理）'
            except Exception:
                pass
    return changed
