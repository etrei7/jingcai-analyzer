# -*- coding: utf-8 -*-
"""Edge 差值榜：把当日场次里「模型概率 − 市场隐含概率」分歧最大的选项排成一张表，
并把「同类场次（胜平负 × 赔率区间）历史命中率」贴在每一行旁边。

为什么单独做这张榜：
- 结算口径复核期，闸门把战绩存疑的玩法全部暂停，页面只给结论不给候选，
  用户无法判断"今天到底有没有一场值得看"；
- 单场卡片的 Edge 只是角落一行小字，散在 1.7 万像素的长列表里，无法横向比较；
- 校准早就把同类场次历史命中率算出来了，但只用于悄悄降级信心等级，从未展示，
  用户看不到"这个赔率区间历史上根本不赚钱"这条证据。

口径（与 analysis._pick_by_ev / calibration 完全一致，不引入新算法）：
- edge = 模型概率 − 市场隐含概率（两者同为 100% 归一化口径，单位百分点）；
- EV = p·odds − 1；凯利 f = (p·odds − 1) / (odds − 1)；
- 可下注门槛：1.01 < odds ≤ 8、p ≥ 10%、f > 1%，与 _pick_by_ev 同参；
- 已开赛、模型概率缺失、赔率缺失的场次不进榜；
- 排序：可下注优先 → 凯利降序 → edge 降序。

本模块只做聚合展示，不改结论、不写数据库。
"""

MIN_PROB = 0.10      # 概率下限：低于 10% 的选项样本太稀，无法长期验证
MAX_ODDS = 8.0       # 赔率上限：再往上分歧多半是信息差或状态突变，不是实力差
MIN_KELLY = 0.01     # 凯利分数下限：低于 1% 不值得占用资金

DEFAULT_LIMIT = 8

_OPTIONS = (
    ('win', '胜', 'win_odds'),
    ('draw', '平', 'draw_odds'),
    ('lose', '负', 'lose_odds'),
)

_OPTION_ORDER = {'胜': 0, '平': 1, '负': 2}


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _segment_for(odds):
    """同类场次历史命中率（胜平负 × 赔率区间）。样本不足时返回 None。"""
    try:
        from calibration import _odds_bucket, get_calibration
    except Exception:
        return None
    try:
        bucket = _odds_bucket(odds)
        if not bucket:
            return None
        cal = get_calibration() or {}
        seg = (cal.get('segments') or {}).get('1X2|' + bucket)
        n_min = cal.get('min_seg') or 30
        if not seg or seg.get('n', 0) < n_min:
            return None
        return {
            'play': '1X2',
            'bucket': bucket,
            'n': seg['n'],
            'hit_rate': seg['hit_rate'],
            'ci_low': seg.get('ci_low'),
            'ci_high': seg.get('ci_high'),
            'required_rate': seg['required_rate'],
            'edge': seg['edge'],
            'roi': seg.get('roi'),
            'roi_low': seg.get('roi_low'),
            'roi_high': seg.get('roi_high'),
        }
    except Exception:
        return None


def _candidates(match):
    """把一场比赛拆成 胜/平/负 三个候选，带上展示与筛选需要的字段。"""
    mp = match.get('model_prob') or {}
    mk = match.get('market_prob') or {}
    kelly = match.get('kelly') or {}
    out = []
    for key, label, odds_key in _OPTIONS:
        odds = _num(match.get(odds_key))
        p_model = _num(mp.get(key))
        p_market = _num(mk.get(key))
        if odds <= 1.01 or p_model <= 0:
            continue
        p = p_model / 100.0
        ev = round(p * odds - 1.0, 4)
        f = round(ev / (odds - 1.0), 4) if odds > 1.01 else -9.0
        if f <= -9.0:
            continue
        reasons = []
        if odds > MAX_ODDS:
            reasons.append('赔率超过 %g 倍，分歧无法用实力解释' % MAX_ODDS)
        if p_model < MIN_PROB * 100:
            reasons.append('模型概率低于 %d%%，样本太稀' % round(MIN_PROB * 100))
        if f <= MIN_KELLY:
            reasons.append('凯利分数 ≤ %g%%，不值得占用资金' % round(MIN_KELLY * 100))
        out.append({
            'option': label,
            'option_key': key,
            'model_prob': round(p_model, 1),
            'market_prob': round(p_market, 1),
            'edge_pp': round(p_model - p_market, 1),
            'odds': round(odds, 2),
            'ev': ev,
            'kelly': f,
            'actionable': not reasons,
            'blocked_reasons': reasons,
        })
    return out


def _match_row(match, cand):
    """一行榜：比赛信息 + 选中候选 + 同类场次命中率。"""
    seg = _segment_for(cand['odds'])
    return {
        'match_id': match.get('match_id') or '',
        'home_team': match.get('home_team') or '',
        'away_team': match.get('away_team') or '',
        'league': match.get('league') or '',
        'match_time': match.get('match_time') or match.get('time') or '',
        'confidence_level': match.get('confidence_level') or '',
        'option': cand['option'],
        'option_key': cand['option_key'],
        'model_prob': cand['model_prob'],
        'market_prob': cand['market_prob'],
        'edge_pp': cand['edge_pp'],
        'odds': cand['odds'],
        'ev': cand['ev'],
        'kelly': cand['kelly'],
        'actionable': cand['actionable'],
        'blocked_reasons': cand['blocked_reasons'],
        'segment': seg,
        # 同类场次历史命中率是否低于盈亏平衡（展示用，不改结论）
        'segment_warn': bool(seg and seg.get('edge', 0) < 0),
        'model_version': (match.get('model') or {}).get('version') or '',
    }


def build_board(matches, limit=DEFAULT_LIMIT, include_blocked=True):
    """构造 Edge 差值榜。

    matches: analyze_matches() 的结果列表（已含 model_prob / market_prob / kelly）
    limit:   榜单长度上限（可下注的优先占位）
    include_blocked: 是否把不合格但分歧大的场次也列出来（标注"仅观察"）

    返回 {'rows': [...], 'summary': {...}, 'note': '...'}
    """
    rows = []
    seen = 0
    for match in matches or []:
        if not isinstance(match, dict):
            continue
        if match.get('started'):
            continue
        if not (match.get('model_prob') and match.get('market_prob')):
            continue
        cands = _candidates(match)
        if not cands:
            continue
        seen += 1
        # 每场只占一行：优先「可下注且凯利最高」，否则退化为凯利最高者
        ok = [c for c in cands if c['actionable']]
        pool = ok or cands
        cand = max(pool, key=lambda c: (c['kelly'], c['edge_pp']))
        if not ok and not include_blocked:
            continue
        rows.append(_match_row(match, cand))

    actionable = [r for r in rows if r['actionable']]
    if actionable:
        rows.sort(key=lambda r: (not r['actionable'],
                                 -r['kelly'],
                                 -r['edge_pp'],
                                 _OPTION_ORDER.get(r['option'], 9)))
    else:
        rows.sort(key=lambda r: (-r['kelly'], -r['edge_pp']))
    rows = rows[:max(1, int(limit or DEFAULT_LIMIT))]

    n_act = len([r for r in rows if r['actionable']])
    avg_edge = (round(sum(r['edge_pp'] for r in actionable) / len(actionable), 1)
                if actionable else 0.0)
    return {
        'rows': rows,
        'summary': {
            'evaluated': seen,
            'listed': len(rows),
            'actionable': n_act,
            'avg_edge_pp': avg_edge,
            'min_prob': round(MIN_PROB * 100, 1),
            'max_odds': MAX_ODDS,
            'min_kelly': MIN_KELLY,
        },
        # 2026-11-23 之前的同类场次样本含前视风险（当时赔率未按开赛时点落库），
        # 复盘闸门对此已做暂停处理；榜单沿用同一口径，必须把风险写在明面上。
        'note': '同类场次命中率含 2026-11-23 之前的样本，存在前视风险，仅作参考；'
                '榜单不含已开赛场次。本榜只表示模型与市场的分歧大小，不是投注建议；'
                '玩法是否可用以首屏决策条为准。',
    }
