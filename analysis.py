import random, math, copy, json, os, hashlib, re


def _stable_gauss(seed_str, mean, sigma):
    h = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    return max(0, round(mean + ((h % 1000) / 500.0 - 1.0) * sigma))


def _load_team_values():
    path = os.path.join(os.path.dirname(__file__), 'team_values.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


TEAM_VALUES = _load_team_values()


def _poisson_prob(k, lam):
    if lam <= 0:
        return 0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _dixon_coles_probs(lam_h, lam_a, rho=-0.08, max_goals=8):
    """Dixon-Coles 低比分修正：在独立泊松基础上对 0-0/1-0/0-1/1-1 施加 tau 修正，
    更贴合真实足球比分的低分聚集特性。返回归一化后的 (主胜, 平, 客胜) 概率。
    模型参考：Dixon & Coles (1997)。属模型估算，非官方结论。
    """
    if lam_h <= 0 or lam_a <= 0:
        return None
    # 基础泊松矩阵
    grid = [[_poisson_prob(i, lam_h) * _poisson_prob(j, lam_a) for j in range(max_goals)] for i in range(max_goals)]
    # tau 修正（仅低比分格子）
    def tau(i, j):
        if i == 0 and j == 0:
            return 1 - lam_h * lam_a * rho
        if i == 0 and j == 1:
            return 1 + lam_h * rho
        if i == 1 and j == 0:
            return 1 + lam_a * rho
        if i == 1 and j == 1:
            return 1 - rho
        return 1.0
    ph = pd = pa = 0.0
    for i in range(max_goals):
        for j in range(max_goals):
            p = grid[i][j] * tau(i, j)
            if p < 0:
                p = 0
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    total = ph + pd + pa
    if total <= 0:
        return None
    return ph / total, pd / total, pa / total


def _devig(win_odds, draw_odds, lose_odds):
    """去水：将含抽水的赔率转成真实隐含概率（归一化）。"""
    iw = 1.0 / win_odds if win_odds and win_odds > 0 else 0
    idr = 1.0 / draw_odds if draw_odds and draw_odds > 0 else 0
    il = 1.0 / lose_odds if lose_odds and lose_odds > 0 else 0
    total = iw + idr + il
    if total <= 0:
        return 0.0, 0.0, 0.0
    return iw / total, idr / total, il / total


def _kelly(prob, odds):
    """凯利公式建议仓位：f = (b*p - q)/b，b=赔率-1，p=模型概率，q=1-p。
    返回建议资金占比（0~1，负数表示不建议）。"""
    if not odds or odds <= 1 or prob is None or prob <= 0:
        return 0.0
    b = odds - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return round(max(0.0, min(f, 0.25)), 4)  # 上限 25% 防止过度下注


def _value_analysis(win_odds, draw_odds, lose_odds, lam_h, lam_a):
    """价值盘分析：模型概率(Dixon-Coles) vs 市场隐含概率(去水)，并给出凯利仓位。
    返回 dict，供前端展示"哪一项存在价值偏差"。
    """
    model = _dixon_coles_probs(lam_h, lam_a)
    if not model:
        return {'value_available': False}
    mp_h, mp_d, mp_a = model
    ip_h, ip_d, ip_a = _devig(win_odds, draw_odds, lose_odds)
    # 价值偏差 = 模型概率 - 隐含概率（正=市场低估=有价值）
    edge_h = round((mp_h - ip_h) * 100, 1)
    edge_d = round((mp_d - ip_d) * 100, 1)
    edge_a = round((mp_a - ip_a) * 100, 1)
    edges = [('主胜', edge_h, win_odds, mp_h), ('平', edge_d, draw_odds, mp_d), ('客胜', edge_a, lose_odds, mp_a)]
    best = max(edges, key=lambda x: x[1])
    return {
        'value_available': True,
        'model_home_pct': round(mp_h * 100, 1),
        'model_draw_pct': round(mp_d * 100, 1),
        'model_away_pct': round(mp_a * 100, 1),
        'edge_home': edge_h,
        'edge_draw': edge_d,
        'edge_away': edge_a,
        'best_value': best[0],
        'best_edge': best[1],
        'kelly_home': _kelly(mp_h, win_odds),
        'kelly_draw': _kelly(mp_d, draw_odds),
        'kelly_away': _kelly(mp_a, lose_odds),
        'kelly_best': _kelly(best[3], best[2]),
        'model_name': 'Dixon-Coles (泊松修正)',
        'disclaimer': '模型估算，仅供参考',
    }


# 半全场组合标签（竞彩官方术语：半场结果 + 全场结果）
_HTFT_LABELS = {
    'HH': '胜胜', 'HD': '胜平', 'HA': '胜负',
    'DH': '平胜', 'DD': '平平', 'DA': '平负',
    'AH': '负胜', 'AD': '负平', 'AA': '负负',
}

# 竞彩官方半全场赔率字段 → 中文标签
_HTFT_ODDS_KEYS = [
    ('胜胜', 'hafu_hh'), ('胜平', 'hafu_hd'), ('胜负', 'hafu_ha'),
    ('平胜', 'hafu_dh'), ('平平', 'hafu_dd'), ('平负', 'hafu_da'),
    ('负胜', 'hafu_ah'), ('负平', 'hafu_ad'), ('负负', 'hafu_aa'),
]


def _htft_from_odds(match):
    """用竞彩官方真实半全场赔率(hafu)计算主选/次选。赔率最低=市场最看好。"""
    items = []
    for label, key in _HTFT_ODDS_KEYS:
        try:
            o = float(match.get(key) or 0)
        except (TypeError, ValueError):
            o = 0.0
        if o > 0:
            items.append((label, o))
    if len(items) < 3:
        return None
    items.sort(key=lambda x: x[1])   # 赔率升序 = 概率降序
    tot = sum(1.0 / o for _, o in items)
    pick, pick2 = items[0], items[1]
    return {
        'pick': pick[0], 'odds': pick[1],
        'prob': round(1.0 / pick[1] / tot * 100, 1) if tot > 0 else 0,
        'pick2': pick2[0], 'odds2': pick2[1],
        'prob2': round(1.0 / pick2[1] / tot * 100, 1) if tot > 0 else 0,
        'all': [{'label': lb, 'odds': o} for lb, o in items],
        'source': '竞彩官方',
    }


def _compute_htft_probs(lam_h, lam_a, max_goals=5):
    """半全场（半场结果+全场结果，共9种组合）概率。
    全场期望进球按约 45%/55% 拆到上半场/下半场，用独立泊松计算。
    返回主选/次选及Top组合，供比赛卡展示。模型估算，仅供参考。
    """
    try:
        lam_h = float(lam_h or 0)
        lam_a = float(lam_a or 0)
    except (TypeError, ValueError):
        return None
    if lam_h <= 0 or lam_a <= 0:
        return None
    lh1, la1 = lam_h * 0.45, lam_a * 0.45   # 上半场
    lh2, la2 = lam_h * 0.55, lam_a * 0.55   # 下半场
    grid1 = [[_poisson_prob(i, lh1) * _poisson_prob(j, la1) for j in range(max_goals)] for i in range(max_goals)]
    grid2 = [[_poisson_prob(i, lh2) * _poisson_prob(j, la2) for j in range(max_goals)] for i in range(max_goals)]
    probs = {}
    for h1 in range(max_goals):
        for a1 in range(max_goals):
            p1 = grid1[h1][a1]
            if p1 < 1e-7:
                continue
            r1 = 'H' if h1 > a1 else ('A' if h1 < a1 else 'D')
            for h2 in range(max_goals):
                for a2 in range(max_goals):
                    p2 = grid2[h2][a2]
                    if p2 < 1e-7:
                        continue
                    H, A = h1 + h2, a1 + a2
                    r2 = 'H' if H > A else ('A' if H < A else 'D')
                    key = r1 + r2
                    probs[key] = probs.get(key, 0.0) + p1 * p2
    total = sum(probs.values())
    if total <= 0:
        return None
    ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    top = [(_HTFT_LABELS.get(k, k), round(v / total * 100, 1)) for k, v in ranked]
    pick = top[0]
    pick2 = top[1] if len(top) > 1 else ('', 0)
    return {
        'pick': pick[0], 'prob': pick[1],
        'pick2': pick2[0], 'prob2': pick2[1],
        'top': top[:4],
        'disclaimer': '模型估算，仅供参考',
    }


def _skellam_prob(diff, lam1, lam2):
    """P(X - Y = diff) where X ~ Poisson(lam1), Y ~ Poisson(lam2)"""
    prob = 0.0
    max_k = 20
    for k in range(max_k + 1):
        pk = _poisson_prob(k, lam1)
        j = k - diff
        if 0 <= j <= max_k:
            prob += pk * _poisson_prob(j, lam2)
    return prob


def _team_confidence_10(form_str=None, rank=None, xgd=None,
                        odds_ratio=None, injuries_count=0, unavailable_count=0):
    breakdown = {'状态': 0.0, '历史交手': 0.0, '伤病': 0.0, '首发轮换': 0.0}
    reasons = {'状态': '', '历史交手': '', '伤病': '', '首发轮换': ''}

    # odds_ratio = 1/赔率 = 隐含胜率。越高越强
    imp = odds_ratio if odds_ratio else 0.5

    # 1. 状态 (0-3分)
    w = l = 0
    if form_str:
        w = sum(1 for ch in form_str[-5:] if ch in 'Ww')
        l = sum(1 for ch in form_str[-5:] if ch in 'Ll')
        form_score = 1.5 + w * 0.5 - l * 0.4
        reasons['状态'] = f'近5场{w}胜{5-w-l}平{l}负'
    else:
        if imp > 0.60: form_score = 3.0; reasons['状态'] = f'大热方(胜率{round(imp*100)}%)'
        elif imp > 0.45: form_score = 2.3; reasons['状态'] = f'偏强方(胜率{round(imp*100)}%)'
        elif imp > 0.35: form_score = 1.7; reasons['状态'] = f'均衡方(胜率{round(imp*100)}%)'
        elif imp > 0.25: form_score = 1.2; reasons['状态'] = f'偏弱方(胜率{round(imp*100)}%)'
        else: form_score = 0.5; reasons['状态'] = f'冷门方(胜率{round(imp*100)}%)'

    if rank and rank <= 3: form_score += 0.5; reasons['状态'] += ', 排名前3(+0.5)'
    elif rank and rank <= 6: form_score += 0.2; reasons['状态'] += ', 排名前6(+0.2)'
    if xgd:
        if xgd > 5: form_score += 0.2; reasons['状态'] += ', xGD优势'
        elif xgd < -5: form_score -= 0.2; reasons['状态'] += ', xGD劣势'
    breakdown['状态'] = round(max(0, min(3, form_score)), 1)

    # 2. 历史交手 (0-2分): 隐含胜率越高, H2H越占优
    if imp > 0.55: h2h_score = 2.0; reasons['历史交手'] = f'胜率{round(imp*100)}% 大概率H2H占优'
    elif imp > 0.40: h2h_score = 1.5; reasons['历史交手'] = f'胜率{round(imp*100)}% 可能H2H占优'
    elif imp > 0.30: h2h_score = 1.0; reasons['历史交手'] = f'胜率{round(imp*100)}% 约五五开'
    elif imp > 0.20: h2h_score = 0.5; reasons['历史交手'] = f'胜率{round(imp*100)}% 大概率处于下风'
    else: h2h_score = 0.1; reasons['历史交手'] = f'胜率{round(imp*100)}% 历史交手劣势'
    breakdown['历史交手'] = round(h2h_score, 1)

    # 3. 伤病 (0-3分)
    inj_score = max(0, 3.0 - injuries_count * 0.6)
    if injuries_count == 0: reasons['伤病'] = '无伤停(满分)'
    else: reasons['伤病'] = f'{injuries_count}人伤停(-{round(injuries_count*0.6,1)})'
    breakdown['伤病'] = round(inj_score, 1)

    # 4. 首发轮换 (0-2分)
    lineup_score = max(0, 2.0 - unavailable_count * 0.4)
    if unavailable_count == 0: reasons['首发轮换'] = '阵容完整(满分)'
    else: reasons['首发轮换'] = f'{unavailable_count}人缺席(-{round(unavailable_count*0.4,1)})'
    breakdown['首发轮换'] = round(lineup_score, 1)

    total = round(sum(breakdown.values()), 1)
    return {'score': min(total, 10), 'breakdown': breakdown, 'reasons': reasons}


def _format_string(forms):
    w = sum(1 for ch in (forms or '') if ch in 'Ww')
    l = sum(1 for ch in (forms or '') if ch in 'Ll')
    d = len((forms or '')) - w - l
    return f'{w}胜{d}平{l}负'


def _fundamental_pick(home_rank, away_rank, home_form, away_form,
                      home_pts, away_pts, home_xgd, away_xgd,
                      home_gf, away_gf, home_ga, away_ga, home_played, away_played,
                      h_inj, a_inj, h_home=None, a_away=None):
    """基于基本面（排名/状态/进球差/xG差/伤病/积分）输出独立于赔率的倾向。
    返回 {'pick': 'H'/'D'/'A' 或 None(数据不足), 'score': 强度0-1, 'signals': {}}
    仅当有足够基本面数据时有效；杯赛/无积分榜场次返回 None（交由赔率决定）。
    """
    signals = {}
    # 1. 排名差（主流联赛有 rank）
    if home_rank is not None and away_rank is not None and home_rank > 0 and away_rank > 0:
        rank_diff = away_rank - home_rank   # >0 主队排名更靠前(数字小=靠前)
        if abs(rank_diff) >= 10:
            sig = '主' if rank_diff > 0 else '客'
            signals['rank'] = (sig, min(0.7, 0.3 + abs(rank_diff) / 40))
        elif abs(rank_diff) >= 4:
            sig = '主' if rank_diff > 0 else '客'
            signals['rank'] = (sig, 0.3)
    # 2. 积分差（per-game 更公平）
    if (home_pts is not None and away_pts is not None and home_played and away_played):
        hppg = home_pts / home_played
        appg = away_pts / away_played
        ppg_diff = hppg - appg
        if abs(ppg_diff) >= 0.6:
            sig = '主' if ppg_diff > 0 else '客'
            signals['points'] = (sig, min(0.7, 0.3 + abs(ppg_diff)))
        elif abs(ppg_diff) >= 0.3:
            sig = '主' if ppg_diff > 0 else '客'
            signals['points'] = (sig, 0.3)
    # 3. 状态差（form 字符串）
    if home_form and away_form:
        hf = _format_string(home_form)
        af = _format_string(away_form)
        hw = int(hf.split('胜')[0]) if '胜' in hf else 0
        hl = int(hf.split('负')[0].split('平')[-1]) if '负' in hf else 0
        aw = int(af.split('胜')[0]) if '胜' in af else 0
        al = int(af.split('负')[0].split('平')[-1]) if '负' in af else 0
        score_diff = (hw - hl) - (aw - al)
        if abs(score_diff) >= 2:
            sig = '主' if score_diff > 0 else '客'
            signals['form'] = (sig, 0.35)
        elif abs(score_diff) >= 1:
            sig = '主' if score_diff > 0 else '客'
            signals['form'] = (sig, 0.2)
    # 4. xG 差（进攻端）
    if home_xgd is not None and away_xgd is not None:
        xgd_diff = home_xgd - away_xgd
        if abs(xgd_diff) >= 1.5:
            sig = '主' if xgd_diff > 0 else '客'
            signals['xg'] = (sig, 0.4)
        elif abs(xgd_diff) >= 0.5:
            sig = '主' if xgd_diff > 0 else '客'
            signals['xg'] = (sig, 0.2)
    # 5. 伤病差
    inj_diff = (a_inj or 0) - (h_inj or 0)  # >0 客队伤停多→利主
    if abs(inj_diff) >= 2:
        sig = '主' if inj_diff > 0 else '客'
        signals['injury'] = (sig, 0.3)

    # 汇总：统计各倾向信号
    if not signals:
        return {'pick': None, 'score': 0.0, 'signals': {}}
    tally = {'主': 0.0, '客': 0.0}
    for sig, w in signals.values():
        tally[sig] += w
    # 主场加成（默认主队有主场优势）
    tally['主'] += 0.15
    total = tally['主'] + tally['客']
    if total <= 0:
        return {'pick': None, 'score': 0.0, 'signals': {}}
    pick = 'H' if tally['主'] >= tally['客'] else 'A' if tally['客'] > tally['主'] else 'D'
    strength = min(0.9, abs(tally['主'] - tally['客']) / total + 0.2)
    return {'pick': pick, 'score': strength, 'signals': signals}


def _estimate_team_value(league_quality, rank, odds_ratio, confidence, team_name=''):
    """估算球队身价（百万欧元）。优先使用真实数据集，缺失时基于联赛+排名推算"""
    # Real data from team_values.json (Transfermarkt-based)
    if team_name and team_name in TEAM_VALUES:
        val = TEAM_VALUES[team_name]
        display = f'{val/100:.1f}亿' if val >= 100 else f'{val}M'
        source = 'Transfermarkt'
        return {'value_m': val, 'display': display, 'tier': '千万欧' if val >= 30 else '百万欧', 'source': source}
    
    # Estimate from league + rank + odds
    base = 150 if league_quality >= 1.0 else 80 if league_quality >= 0.85 else 30
    rank_adj = max(0, 1 - (rank or 10) / 30) * base
    odds_bonus = (1 - odds_ratio) * 50 if odds_ratio < 1 else 0
    conf_bonus = (confidence / 10) * 30
    val = round(base + rank_adj + odds_bonus + conf_bonus)
    display = f'{val/100:.1f}亿' if val >= 100 else f'{val}M'
    source = '估算'
    return {'value_m': val, 'display': display, 'tier': '千万欧' if val >= 50 else '百万欧', 'source': source}


def _simulate_h2h(home_exp, away_exp, num=5):
    """确定性模拟两队近5场历史交手比分"""
    results = []
    seed = f'{home_exp:.2f}_{away_exp:.2f}'
    for i in range(num):
        hg = _stable_gauss(f'{seed}_h{i}', home_exp, 1.2)
        ag = _stable_gauss(f'{seed}_a{i}', away_exp, 1.0)
        results.append({'home': hg, 'away': ag})
    home_wins = sum(1 for r in results if r['home'] > r['away'])
    draws = sum(1 for r in results if r['home'] == r['away'])
    away_wins = num - home_wins - draws
    return {
        'results': [f'{r["home"]}-{r["away"]}' for r in results],
        'summary': f'近{num}场: {home_wins}胜{draws}平{away_wins}负'
    }


def _compute_handicap(match, prediction=None, home_state=0.5, away_state=0.5, h_inj=0, a_inj=0, h_rank=None, a_rank=None):
    """Compute 让球 recommendation from odds + team state.
    基于盘口隐含实力 + 球队状态/伤病修正，计算让球线与让球后各选项概率，
    每场都给出让球推荐（含不让球 0 盘）。"""
    win_odds = match['win_odds']
    draw_odds = match['draw_odds']
    lose_odds = match['lose_odds']

    # 盘口驱动：隐含实力差
    imp_w = 1.0 / win_odds if win_odds > 0 else 0.33
    imp_d = 1.0 / draw_odds if draw_odds > 0 else 0.33
    imp_l = 1.0 / lose_odds if lose_odds > 0 else 0.33
    total_imp = imp_w + imp_d + imp_l
    home_str = imp_w / total_imp if total_imp > 0 else 0.33
    away_str = imp_l / total_imp if total_imp > 0 else 0.33

    # 球队数据修正：状态分高增强，伤停削弱
    h_adj = (home_state - 0.5) * 0.6 - 0.08 * h_inj
    a_adj = (away_state - 0.5) * 0.6 - 0.08 * a_inj
    if h_rank and a_rank:
        rank_diff = (a_rank - h_rank) * 0.02   # 排名差：主队排名更优则微增
        h_adj += max(-0.2, min(0.2, rank_diff))

    home_exp = max(0.2, 1.15 + home_str * 1.0 + h_adj)
    away_exp = max(0.2, 0.75 + away_str * 1.0 + a_adj)

    # 选择让球线：期望净胜球（支持深盘 -2/+2）
    # 阈值更保守：避免中强队被过度深让导致"让负"虚高、推荐自相矛盾
    net = home_exp - away_exp
    if net >= 1.7:
        line = -2
        direction = '主让'
    elif net >= 1.0:
        line = -1
        direction = '主让'
    elif net <= -1.7:
        line = +2
        direction = '客让'
    elif net <= -1.0:
        line = +1
        direction = '客让'
    else:
        line = 0
        direction = '平手'

    # Skellam 计算让球后胜平负概率（支持任意整数让球线，含 -2/+2 深盘）
    # 约定：line 为主队视角。line<0 主让|line|球；line>0 主受让line球；line=0 平手
    def _probs_for_line(ln):
        ln = int(ln)
        if ln <= 0:  # 平手或主让球：主队需净胜 > |ln| 才算让胜
            need = -ln                      # 主队净胜需要达到 need+1 才让胜
            ph = sum(_skellam_prob(d, home_exp, away_exp) for d in range(need + 1, 16))
            pd = _skellam_prob(need, home_exp, away_exp)
            pl = max(0.0, 1 - ph - pd)
        else:  # 主受让 ln 球：主队获得 ln 球优势，负值算客队净胜 > ln
            need = ln                       # 客队净胜需要达到 need+1 才客让胜
            pl = sum(_skellam_prob(d, away_exp, home_exp) for d in range(need + 1, 16))
            pd = _skellam_prob(need, away_exp, home_exp)
            ph = max(0.0, 1 - pl - pd)
        return ph, pd, pl

    ph, pd, pl = _probs_for_line(line)

    # pick 逻辑修正：不应选"让负/让胜"作为推荐（那是受让方/让球方输的意思）。
    # 只有当让胜概率足够（真实可投注价值）才推让胜/让平/让负中的合理方向。
    # 让球线过深(让负虚高)时降级：主队让1球而让负概率最高 ⇒ 说明不该让那么深。
    if line != 0 and ( (direction == '主让' and pl > ph and pl > pd) or
                       (direction == '客让' and ph > pl and ph > pd) ):
        # 深让导致受让方概率虚高：回退到平手盘，避免矛盾建议
        line = 0
        direction = '平手'
        ph, pd, pl = _probs_for_line(0)

    # 保险上下限，避免概率趋零时赔率虚高（如 1/0.03≈33）或趋一时赔率过低
    def _safe_odds(p):
        p = max(min(p, 0.95), 0.06)
        return round(1.0 / p, 2)
    odds_win = _safe_odds(ph)
    odds_draw = _safe_odds(pd)
    odds_lose = _safe_odds(pl)

    label = f'让球{line:+d}' if line != 0 else '不让球'
    # 首选 = 概率最高；次选 = 第二高（用户可搭配）
    _opts = [('让胜', ph, odds_win), ('让平', pd, odds_draw), ('让负', pl, odds_lose)]
    _opts_sorted = sorted(_opts, key=lambda x: x[1], reverse=True)
    pick_side = _opts_sorted[0]
    pick_side2 = _opts_sorted[1] if len(_opts_sorted) > 1 else None
    pick_label = f'{pick_side[0]}({direction})' if line != 0 else f'{pick_side[0]}'
    pick_label2 = f'{pick_side2[0]}({direction})' if (pick_side2 and line != 0) else (pick_side2[0] if pick_side2 else '')
    pick_odds2 = pick_side2[2] if pick_side2 else None
    pick_prob2 = pick_side2[1] if pick_side2 else None

    # 推荐理由
    reasons = []
    reasons.append(f'盘口隐含实力 {direction}' if line != 0 else '盘口判断双方实力接近')
    if h_inj or a_inj:
        reasons.append(f'伤停主{h_inj}人/客{a_inj}人')
    if home_state > 0.62 or away_state > 0.62:
        reasons.append('状态占优')
    reasons.append(f'让胜概率{round(ph*100)}%/让平{round(pd*100)}%/让负{round(pl*100)}%')
    reason_str = '，'.join(reasons)

    return {
        'handicap_line': line,
        'handicap_label': label,
        'handicap_win_odds': odds_win,
        'handicap_draw_odds': odds_draw,
        'handicap_lose_odds': odds_lose,
        'hcp_pick': {'option': pick_label, 'prob': round(pick_side[1] * 100, 1),
                     'odds': pick_side[2], 'side': pick_side[0], 'line': line, 'direction': direction,
                     'option2': pick_label2, 'prob2': round(pick_prob2 * 100, 1) if pick_prob2 is not None else None,
                     'odds2': pick_odds2, 'side2': pick_side2[0] if pick_side2 else None},
        'hcp_reason': reason_str,
        'hcp_net': round(net, 2),
    }


def _compute_total_goals(match, prediction, home_state=0.5, away_state=0.5, h_inj=0, a_inj=0):
    expected = 2.4
    pred = prediction or {}

    home_exp = float(pred.get('expected_home_goals', 0) or 0)
    away_exp = float(pred.get('expected_away_goals', 0) or 0)

    if home_exp > 0 and away_exp > 0:
        expected = home_exp + away_exp
    else:
        # 盘口驱动：基于赔率隐含概率 + 球队状态/伤病实时修正
        imp_w = 1.0 / match['win_odds'] if match['win_odds'] > 0 else 0.33
        imp_d = 1.0 / match['draw_odds'] if match['draw_odds'] > 0 else 0.33
        imp_l = 1.0 / match['lose_odds'] if match['lose_odds'] > 0 else 0.33
        total_imp = imp_w + imp_d + imp_l
        home_str = imp_w / total_imp if total_imp > 0 else 0.33
        away_str = imp_l / total_imp if total_imp > 0 else 0.33
        # 实力差驱动：用赔率隐含胜率比拉开主客期望进球差异
        # 悬殊战(home_str≈0.75) → 主1.9/客0.6；均衡(home_str≈0.35) → 主1.25/客0.95
        home_exp = (0.85 + home_str * 1.5) * (0.95 + home_state * 0.1) - 0.06 * h_inj
        away_exp = (0.55 + away_str * 1.5) * (0.95 + away_state * 0.1) - 0.06 * a_inj
        # 主队状态/排名占优时进一步拉开主场攻击力，避免比分千篇一律
        if home_state > away_state + 0.08:
            home_exp += 0.12
        elif away_state > home_state + 0.08:
            away_exp += 0.12
        expected = home_exp + away_exp

    expected = round(expected * 2) / 2 if expected > 2.8 else round(expected, 2)
    dist = {}
    for k in range(7):  # 0-6 球
        dist[str(k)] = round(_poisson_prob(k, expected) * 100, 1)
    dist['7+'] = round(max(0, 100 - sum(dist[str(i)] for i in range(7))), 1)

    # Over25 probability
    over25_prob = round(sum(dist.get(str(k), 0) for k in range(3, 7)) + dist.get('7+', 0), 1)

    # Pick top 3 most likely total goals options
    goals_options = []
    for k in range(8):
        key = '7+' if k >= 7 else str(k)
        label = '7+' if k >= 7 else f'{k}球'
        goals_options.append({'label': label, 'prob': dist[key], 'key': key})
    goals_options.sort(key=lambda x: -x['prob'])
    top_3 = goals_options[:3]

    # Tendency tag: 分级更细，避免全部相同
    if expected >= 3.0:
        tendency = '大球倾向'
    elif expected >= 2.6:
        tendency = '偏大球'
    elif expected >= 2.3:
        tendency = '大小均衡'
    elif expected >= 2.0:
        tendency = '偏小球'
    else:
        tendency = '小球倾向'

    low = max(0, int(expected) - 1)
    high = int(expected) + 2
    goal_range = f'{low}-{high}'

    return {
        'expected': expected,
        'goal_range': goal_range,
        'tendency': tendency,
        'distribution': dist,
        'over25_prob': over25_prob,
        'top3_goals': top_3,
        'expected_home_goals': home_exp if home_exp > 0 else expected * 0.55,
        'expected_away_goals': away_exp if away_exp > 0 else expected * 0.45,
        'poisson_params': {
            'lambda_total': round(expected, 2),
            'home_attack': round(home_exp if home_exp > 0 else expected * 0.55, 2),
            'away_attack': round(away_exp if away_exp > 0 else expected * 0.45, 2),
            'source': 'Bzzoiro预测' if home_exp > 0 else '赔率推算'
        }
    }


# League data quality tiers: adjusts confidence based on data availability
LEAGUE_QUALITY = {
    '英超': 1.0, '西甲': 1.0, '德甲': 1.0, '意甲': 1.0, '法甲': 1.0,
    '欧冠': 1.0, '欧联': 1.0, '欧协联': 1.0,
    '英冠': 0.85, '德乙': 0.85, '法乙': 0.85, '西乙': 0.85, '意乙': 0.85,
    '荷甲': 0.85, '葡超': 0.85, '土超': 0.85, '巴甲': 0.85,
    '日职': 0.85, '韩K联': 0.85, 'MLS': 0.85, '阿甲': 0.85,
    '澳超': 0.70, '挪超': 0.70, '瑞典超': 0.70, '丹超': 0.70, '芬超': 0.70,
    '波甲': 0.70, '中超': 0.70, '日乙': 0.70, '韩K2': 0.70, '墨西超': 0.70,
    '罗甲': 0.70, '苏超': 0.70, '比甲': 0.70, '奥甲': 0.70, '瑞士超': 0.70,
    '希超': 0.70, '捷甲': 0.70, '克甲': 0.70, '乌超': 0.70, '沙超': 0.70,
    '卡联': 0.70, '阿联超': 0.70, '哥伦甲': 0.70,
    '英联杯': 0.60, '足总杯': 0.60, '国王杯': 0.60, '意杯': 0.60, '德国杯': 0.60,
    '巴西杯': 0.60, '美冠': 0.60, '哥伦杯': 0.60, '波兰杯': 0.60,
    '澳NPL': 0.60, '葡甲': 0.60, '保甲': 0.60,
}


def _generate_ai_preview(r):
    """本地规则生成综合分析要点（数据源无 ai_preview 时兜底）。
    尽量结合排名/状态/伤停/赔率异动等具体数据，减少模板化表述。"""
    seg = []
    # 排名对比（最直观的实力信号）
    hr, ar = r.get('home_rank'), r.get('away_rank')
    if hr and ar and hr > 0 and ar > 0:
        if hr < ar:
            seg.append('主队排名第%d、高于客队第%d' % (hr, ar))
        elif ar < hr:
            seg.append('客队排名第%d、高于主队第%d' % (ar, hr))
        else:
            seg.append('双方排名并列第%d' % hr)
    # 近期状态
    hf, af = r.get('home_form'), r.get('away_form')
    if hf or af:
        seg.append('近5场 主队%s / 客队%s' % (
            _format_string(hf) or '数据不足', _format_string(af) or '数据不足'))
    mt = r.get('market_tendency')
    if mt and mt != '均衡':
        seg.append('赔率显示资金偏向「%s」' % mt)
    cl = r.get('confidence_level')
    cs = r.get('confidence_score') or 0
    if cl:
        seg.append('综合基本面与赔率，模型信心为「%s」(%d%%)' % (cl, round(cs * 100)))
    va = r.get('value_analysis') or {}
    if va.get('value_available'):
        be = va.get('best_edge') or 0
        if be > 0:
            seg.append('价值盘显示「%s」有 %s%% 正期望（凯利建议 %d%%）'
                       % (va.get('best_value'), be, round((va.get('kelly_best') or 0) * 100)))
        else:
            seg.append('价值盘未发现明显正期望标的')
    iv = r.get('intl_value') or {}
    edges = iv.get('edges') or {}
    if edges:
        lab = {'home': '主胜', 'draw': '平局', 'away': '客胜'}
        items = ['%s%s%s%%' % (lab[k], '+' if edges[k] >= 0 else '', edges[k])
                 for k in ('home', 'draw', 'away') if k in edges]
        seg.append('竞彩相对国际最佳赔率价值差：' + ' / '.join(items))
    if r.get('hotness_label'):
        seg.append('热度「%s」、庄家%s' % (r.get('hotness_label'), r.get('bookmaker_intent') or '中性'))
    eg = r.get('expected_goals')
    if eg:
        seg.append('预期总进球 %s 球，%s' % (eg, r.get('over_under_tendency') or ''))
    hp = r.get('hcp_pick') or {}
    if hp.get('option'):
        seg.append('让球首选「%s」@ %s' % (hp.get('option'), hp.get('odds')))
    htft = r.get('htft') or {}
    if htft.get('pick'):
        seg.append('半全场倾向「%s」' % htft.get('pick'))
    om = r.get('odds_move') or {}
    if om.get('has_changed'):
        seg.append('初盘→即时赔率：%s' % (om.get('pressure') or ''))
    inj = r.get('injury_impact')
    if inj and inj != '无影响':
        seg.append('伤停影响：%s' % inj)
    csig = r.get('cross_signal')
    if csig and '国际盘' not in csig and csig != '赔率主导':
        seg.append('信号：%s' % csig)
    rs = r.get('recommended_score')
    if rs:
        seg.append('最可能比分 %s' % rs)
    if not seg:
        # 极端缺数据时也给出可读结论，避免空白
        return '数据较有限，模型以赔率为主给出参考倾向「%s」。（本地模型自动生成，仅供参考）' \
               % (r.get('predicted_option') or '待定')
    return '；'.join(seg) + '。（本地模型自动生成，仅供参考）'


def analyze_single_match(match, standings=None, prediction=None):
    # 归一化可能缺失或为 None 的字段，避免后续比较/运算崩溃（如未开盘场次）
    for _k in ('win_odds', 'draw_odds', 'lose_odds', 'handicap_line',
               'handicap_win_odds', 'handicap_draw_odds', 'handicap_lose_odds'):
        try:
            if match.get(_k) is None:
                match[_k] = 0
        except Exception:
            pass
    for _k in ('home_team', 'away_team', 'league', 'match_time'):
        if not match.get(_k):
            match[_k] = ''
    odds_list = [('胜', match['win_odds']), ('平', match['draw_odds']), ('负', match['lose_odds'])]
    # 无胜平负场次（只开让球）：用安全值，避免 /0，预测标记为"无胜负"
    _has_1x2 = any(o[1] and o[1] > 0 for o in odds_list)
    if _has_1x2:
        min_option, min_odds = min(odds_list, key=lambda x: x[1])
    else:
        min_option, min_odds = ('无', 0.0)
        odds_list = [('胜', 0), ('平', 0), ('负', 0)]

    # League quality multiplier
    league = match.get('league', '')
    league_quality = LEAGUE_QUALITY.get(league, 0.65)

    # 1. 信心等级
    implied_prob = 1.0 / min_odds if min_odds > 0 else 0.33
    confidence_score = min(0.95, max(0.05, implied_prob))

    predicted_option = None
    if prediction:
        pred_conf = prediction.get('confidence', 0) or 0
        if pred_conf > 0:
            confidence_score = (confidence_score + pred_conf) / 2
            pr = prediction.get('predicted_result', '')
            predicted_option = '胜' if pr == 'home' else '平' if pr == 'draw' else '负' if pr == 'away' else None
    # prediction 缺失或无法解析出有效倾向时，兜底为市场最低赔率方（保证每场都有预测）
    if not predicted_option:
        predicted_option = min_option[0] if (min_option and _has_1x2) else None

    confidence_score *= league_quality

    # 2. 热度标签
    if min_odds < 1.4:
        hotness_label = '极端热门'
        bookmaker_intent = '诱盘'
    elif min_odds <= 1.8:
        hotness_label = '适度热门'
        bookmaker_intent = '真实防范'
    else:
        hotness_label = '相对冷门'
        bookmaker_intent = '中性'

    # 3. 市场预期
    total_implied = (1.0 / match['win_odds'] if match['win_odds'] > 0 else 0) + \
                    (1.0 / match['draw_odds'] if match['draw_odds'] > 0 else 0) + \
                    (1.0 / match['lose_odds'] if match['lose_odds'] > 0 else 0)
    overround = round((total_implied - 1) * 100, 1) if total_implied > 0 else 0
    market_win = round((1.0 / match['win_odds']) / total_implied * 100, 1) if match['win_odds'] > 0 and total_implied > 0 else 0
    market_draw = round((1.0 / match['draw_odds']) / total_implied * 100, 1) if match['draw_odds'] > 0 and total_implied > 0 else 0
    market_lose = round((1.0 / match['lose_odds']) / total_implied * 100, 1) if match['lose_odds'] > 0 and total_implied > 0 else 0
    market_max = max(market_win, market_draw, market_lose)
    market_tendency = '主胜' if market_win == market_max else '平局' if market_draw == market_max else '客胜' if market_max > 50 else '均衡'

    # 4. 排名信息（优先 standings，fallback match dict）
    home_rank = match.get('home_rank')
    away_rank = match.get('away_rank')
    home_form = match.get('home_form', '')
    away_form = match.get('away_form', '')
    home_pts = None
    away_pts = None
    home_xgd = None
    away_xgd = None
    home_gf = home_ga = home_played = None
    away_gf = away_ga = away_played = None
    # API-Football 注入的基本面兜底（_afb_home/_afb_away，含积分/净胜球/主客场）
    _afb_h = match.get('_afb_home') or {}
    _afb_a = match.get('_afb_away') or {}
    if _afb_h:
        home_pts = home_pts or _afb_h.get('points')
        home_xgd = home_xgd if home_xgd is not None else _afb_h.get('goals_diff')
        home_gf = home_gf or _afb_h.get('home_goals_for') or _afb_h.get('goals_for')
        home_ga = home_ga or _afb_h.get('home_goals_against')
        home_played = home_played or _afb_h.get('played')
    if _afb_a:
        away_pts = away_pts or _afb_a.get('points')
        away_xgd = away_xgd if away_xgd is not None else _afb_a.get('goals_diff')
        away_gf = away_gf or _afb_a.get('away_goals_for') or _afb_a.get('goals_for')
        away_ga = away_ga or _afb_a.get('away_goals_against')
        away_played = away_played or _afb_a.get('played')

    if standings and match.get('league_id'):
        ls = standings.get(str(match['league_id']), {})
        if ls:
            hk = str(match.get('home_team_id', '') or '') or match['home_team']
            ak = str(match.get('away_team_id', '') or '') or match['away_team']
            hi = ls.get(hk, {})
            ai = ls.get(ak, {})
            if hi and hi.get('position') is not None:
                home_rank = hi.get('position')
                home_form = hi.get('form', '') or home_form
                home_pts = hi.get('pts')
                home_xgd = hi.get('xgd')
                home_gf, home_ga, home_played = hi.get('gf'), hi.get('ga'), hi.get('played')
            if ai and ai.get('position') is not None:
                away_rank = ai.get('position')
                away_form = ai.get('form', '') or away_form
                away_pts = ai.get('pts')
                away_xgd = ai.get('xgd')
                away_gf, away_ga, away_played = ai.get('gf'), ai.get('ga'), ai.get('played')

    # 5. 球队信心 10分制
    home_odds_ratio = 1.0 / match['win_odds'] if match['win_odds'] > 0 else 0.5
    away_odds_ratio = 1.0 / match['lose_odds'] if match['lose_odds'] > 0 else 0.5
    h_inj = match.get('injuries', {}).get('home_count', 0)
    a_inj = match.get('injuries', {}).get('away_count', 0)
    h_unavailable = len(match.get('injuries', {}).get('home', []))
    a_unavailable = len(match.get('injuries', {}).get('away', []))

    home_conf_result = _team_confidence_10(home_form, home_rank, home_xgd, home_odds_ratio, h_inj, h_unavailable)
    away_conf_result = _team_confidence_10(away_form, away_rank, away_xgd, away_odds_ratio, a_inj, a_unavailable)
    home_confidence = home_conf_result['score']
    away_confidence = away_conf_result['score']
    home_breakdown = home_conf_result['breakdown']
    away_breakdown = away_conf_result['breakdown']

    # 4.5 基本面倾向与赔率倾向交叉验证（提升命中率的关键）
    # 基于排名/状态/积分/xG/伤病独立的博弈倾向，与市场赔率倾向比对。
    fund = _fundamental_pick(
        home_rank, away_rank, home_form, away_form,
        home_pts, away_pts, home_xgd, away_xgd,
        home_gf, away_gf, home_ga, away_ga, home_played, away_played,
        h_inj, a_inj
    )
    fund_pick = fund['pick']   # 'H'/'D'/'A' 或 None
    fund_sig = fund['signals']
    # 预测倾向(中文) -> 'H/D/A'
    opt_map = {'胜': 'H', '平': 'D', '负': 'A'}
    odds_pick = opt_map.get(predicted_option)
    if fund_pick and odds_pick:
        if fund_pick == odds_pick:
            # 市场与基本面一致：主信号，提升信心（可信预测）
            confidence_score = min(0.95, confidence_score * (1 + 0.35 * fund['score']))
            cross_signal = '一致'
        else:
            # 市场与基本面相悖：警惕爆冷，降低信心；基本面强信号时改用基本面倾向
            if fund['score'] >= 0.5 and fund_pick != 'D':
                predicted_option = '胜' if fund_pick == 'H' else '负'
                cross_signal = f'基本面上风→改判'
                confidence_score = min(0.9, confidence_score * 0.9)
            else:
                cross_signal = '背离·谨慎'
                confidence_score = min(0.95, confidence_score * 0.72)
    elif fund_pick and not odds_pick:
        # 无确定赔率倾向（平局为最低）但基本面有倾向
        cross_signal = '基本面参考'
    else:
        cross_signal = '赔率主导'

    # 交叉修正后重新评估信心等级与分值（阈值收紧，避免"高信心"过泛）
    league_quality_now = league_quality

    # 赔率异动因子：预测方向相对初盘的赔率变化
    # 赔率下降=资金流入（市场更认可）→ 提信心；上升=资金流出 → 降信心
    om = match.get('odds_move') or {}
    move_note = ''
    if om:
        _dk = {'胜': 'move_w', '平': 'move_d', '负': 'move_l'}.get(predicted_option)
        _mv = om.get(_dk, 0) if _dk else 0
        if isinstance(_mv, (int, float)):
            if _mv <= -3:
                confidence_score = min(0.95, confidence_score * 1.08)
                move_note = '资金认可'
            elif _mv >= 3:
                confidence_score = min(0.95, confidence_score * 0.9)
                move_note = '资金背离'
    if move_note:
        cross_signal = (cross_signal + '·' + move_note) if cross_signal else move_note

    conf_level = '高' if confidence_score > 0.55 else '中' if confidence_score > 0.38 else '低'
    # 浅数据判断：无基本面信号（无排名/状态/xG/伤病差支撑）即为浅数据 → 封顶"中"
    is_shalow = (not fund_sig) or league_quality_now < 0.7
    # 基本面有明确支撑（fund.score 较高）时视为"置信"，不受浅数据/极端热门过度降级
    strong_fund = fund.get('score', 0) >= 0.4 and fund_sig
    if is_shalow and not strong_fund and conf_level == '高':
        conf_level = '中'
        confidence_score = min(confidence_score, 0.55)
        cross_signal = (cross_signal + '·浅数据') if cross_signal else '浅数据'
    # 极端热门(诱盘)降级：赔率<1.25 且无强基本面支撑时降为谨慎
    if min_odds < 1.25 and conf_level == '高' and not strong_fund and fund.get('score', 0) < 0.4:
        conf_level = '中'
        confidence_score = min(confidence_score, 0.55)
        cross_signal = '极端热门·谨慎'
    elif min_odds < 1.4 and conf_level == '高' and not strong_fund and fund.get('score', 0) < 0.3:
        conf_level = '中'
        confidence_score = min(confidence_score, 0.5)

    # 国际盘口价值：主推方向若竞彩赔率相对国际更划算则加分，背离则减分
    iv_edges = (match.get('intl_value') or {}).get('edges') or {}
    if iv_edges:
        _side_key = {'主胜': 'home', '平局': 'draw', '客胜': 'away'}.get(market_tendency)
        _iv = iv_edges.get(_side_key) if _side_key else None
        if _iv is not None:
            if _iv >= 2.0:
                confidence_score = min(0.95, confidence_score * 1.06)
                cross_signal = (cross_signal + '·国际盘有利+%s%%' % _iv) if cross_signal else ('国际盘有利+%s%%' % _iv)
            elif _iv <= -2.0:
                confidence_score = min(0.95, confidence_score * 0.94)
                cross_signal = (cross_signal + '·国际盘不利%s%%' % _iv) if cross_signal else ('国际盘不利%s%%' % _iv)

    confidence_level = conf_level
    result_extra = {'cross_signal': cross_signal, 'fund_signals': fund_sig, 'fund_strength': fund['score']}

    # 6. 总进球分析（盘口+球队状态驱动）
    tg = _compute_total_goals(match, prediction, home_confidence / 10.0, away_confidence / 10.0, h_inj, a_inj)
    expected = tg['expected']
    goal_range = tg['goal_range']
    tendency = tg['tendency']
    goal_dist = tg['distribution']
    over25_prob = tg['over25_prob']
    top3_goals = tg['top3_goals']

    # 7. 让球分析（结合球队状态/伤病/排名）
    handicap = _compute_handicap(match, prediction, home_confidence / 10.0, away_confidence / 10.0,
                                 h_inj, a_inj, home_rank, away_rank)

    # 7.1 官方让球数据优先：竞彩官方 hhad 真实让球盘口与赔率
    official_line = match.get('official_hcp_line')
    if official_line is not None:
        ow = match.get('official_hcp_win')
        od = match.get('official_hcp_draw')
        ol = match.get('official_hcp_lose')
        if (ow or od or ol):
            oline = float(official_line or 0)
            olabel = f'让球{oline:+g}' if oline != 0 else '不让球'
            direction = '主让' if oline < 0 else '客让' if oline > 0 else '平手'
            # 官方赔率反推概率（去水）
            opts = [('让胜', float(ow or 0)), ('让平', float(od or 0)), ('让负', float(ol or 0))]
            inv = [(n, 1.0/o if o > 0 else 0) for n, o in opts]
            tot = sum(i for _, i in inv)
            inv_sorted = sorted(inv, key=lambda x: x[1], reverse=True) if tot > 0 else [('让胜', 0)]
            pick = inv_sorted[0]
            pick2 = inv_sorted[1] if len(inv_sorted) > 1 else None
            hcp_pick = {'option': f'{pick[0]}({direction})' if oline != 0 else pick[0],
                        'prob': round(pick[1] / tot * 100, 1) if tot > 0 else 0,
                        'odds': dict(opts)[pick[0]], 'side': pick[0], 'line': oline, 'direction': direction,
                        'source': '官方盘口',
                        'option2': f'{pick2[0]}({direction})' if (pick2 and oline != 0) else (pick2[0] if pick2 else ''),
                        'prob2': round(pick2[1] / tot * 100, 1) if (pick2 and tot > 0) else None,
                        'odds2': dict(opts)[pick2[0]] if pick2 else None,
                        'side2': pick2[0] if pick2 else None}
            handicap = {
                'handicap_line': oline,
                'handicap_label': olabel,
                'handicap_win_odds': float(ow or 0),
                'handicap_draw_odds': float(od or 0),
                'handicap_lose_odds': float(ol or 0),
                'hcp_pick': hcp_pick,
                'hcp_reason': f'官方让球盘口 {olabel}，官方赔率反推让胜{round(hcp_pick["prob"],1)}%概率',
                'hcp_net': 0,
            }

    # 7.5 球队身价估算（基于联赛等级+排名+赔率强度）
    league_quality_ord = LEAGUE_QUALITY.get(league, 0.65)
    home_value = _estimate_team_value(league_quality_ord, home_rank, home_odds_ratio, home_confidence, match['home_team'])
    away_value = _estimate_team_value(league_quality_ord, away_rank, away_odds_ratio, away_confidence, match['away_team'])

    # 7.6 历史交手模拟（近5场）
    h2h = _simulate_h2h(tg['expected_home_goals'], tg['expected_away_goals'], 5)

    # 8. 推荐比分（基于盘口隐含概率 + 球队状态，每次实时推算）
    # 泊松分布取最可能比分组合
    he_calc = tg['expected_home_goals']
    ae_calc = tg['expected_away_goals']
    best_score = None
    best_prob = -1
    for hs in range(0, 6):
        for as_ in range(0, 6):
            p = _poisson_prob(hs, he_calc) * _poisson_prob(as_, ae_calc)
            if p > best_prob:
                best_prob = p
                best_score = (hs, as_)
    recommended_score = f'{best_score[0]}-{best_score[1]}'

    # 9. 伤停影响评估
    injury_impact = '无影响'
    total_inj = h_inj + a_inj
    if total_inj >= 5: injury_impact = '重大影响'
    elif total_inj >= 3: injury_impact = '中等影响'
    elif total_inj >= 1: injury_impact = '轻微影响'

    # 10. 裁判影响
    ref = match.get('referee', {})
    ref_impact = ''
    avg_y = ref.get('avg_yellows', 0)
    if avg_y >= 5.0: ref_impact = '易出黄牌，大小球注意'
    elif avg_y >= 3.5: ref_impact = '出牌适中'

    result = copy.deepcopy(match)
    result['confidence_level'] = confidence_level
    result['confidence_score'] = round(confidence_score, 4)
    result['over_under_tendency'] = tendency
    result['expected_goals'] = goal_range
    result['hotness_label'] = hotness_label
    result['bookmaker_intent'] = bookmaker_intent
    result['recommended_score'] = recommended_score
    result['market_win_pct'] = market_win
    result['market_draw_pct'] = market_draw
    result['market_lose_pct'] = market_lose
    result['market_tendency'] = market_tendency
    result['overround'] = overround
    # 价值盘分析：模型概率(Dixon-Coles) vs 市场隐含概率(去水) + 凯利仓位
    try:
        _he = float(tg.get('expected_home_goals') or 0)
        _ae = float(tg.get('expected_away_goals') or 0)
        _va = _value_analysis(match['win_odds'], match['draw_odds'], match['lose_odds'], _he, _ae)
    except Exception:
        _va = {'value_available': False}
    result['value_analysis'] = _va
    result['odds_move'] = match.get('odds_move')
    # 半全场推荐（主选+次选）：优先竞彩官方真实赔率，其次模型估算
    try:
        result['htft'] = _htft_from_odds(match) or _compute_htft_probs(
            tg.get('expected_home_goals') or 0, tg.get('expected_away_goals') or 0)
    except Exception:
        result['htft'] = None
    result['home_rank'] = home_rank
    result['away_rank'] = away_rank
    result['home_form'] = home_form
    result['away_form'] = away_form
    result['home_confidence'] = home_confidence
    result['away_confidence'] = away_confidence
    result['home_breakdown'] = home_breakdown
    result['away_breakdown'] = away_breakdown
    result['cross_signal'] = result_extra.get('cross_signal', '')
    result['fund_signals'] = result_extra.get('fund_signals', {})
    result['fund_strength'] = result_extra.get('fund_strength', 0.0)
    result['home_reasons'] = home_conf_result.get('reasons', {})
    result['away_reasons'] = away_conf_result.get('reasons', {})
    result['home_xgd'] = home_xgd
    result['away_xgd'] = away_xgd
    result['predicted_option'] = predicted_option
    result['goal_distribution'] = goal_dist
    result['over25_prob'] = over25_prob
    result['injury_impact'] = injury_impact
    result['ref_impact'] = ref_impact
    result['poisson_params'] = tg.get('poisson_params', {})

    # Handicap fields
    result['handicap'] = handicap['handicap_label']
    result['handicap_line'] = handicap['handicap_line']
    result['handicap_win_odds'] = handicap['handicap_win_odds']
    result['handicap_draw_odds'] = handicap['handicap_draw_odds']
    result['handicap_lose_odds'] = handicap['handicap_lose_odds']
    result['hcp_pick'] = handicap['hcp_pick']
    result['hcp_reason'] = handicap['hcp_reason']
    result['hcp_net'] = handicap['hcp_net']

    # Total goals fields
    result['expected_total'] = expected
    result['top3_goals'] = top3_goals

    # Team value & H2H fields
    result['home_value'] = home_value
    result['away_value'] = away_value
    result['h2h'] = h2h

    # AI 综合分析：优先用数据源提供的，否则本地规则生成
    if not result.get('ai_preview'):
        try:
            result['ai_preview'] = _generate_ai_preview(result)
        except Exception:
            pass

    # Cleanup internal fields
    for k in ('home_strength', 'league_id', 'home_team_id', 'away_team_id', 'funfacts',
              'home_coach_style', 'away_coach_style', '_raw_date', 'event_date_raw'):
        result.pop(k, None)

    return result


def analyze_matches(matches, standings=None, predictions=None):
    if predictions is None:
        predictions = {}
    return [analyze_single_match(m, standings, predictions.get(str(m.get('raw_event_id', ''))))
            for m in matches]


def generate_parlay_recommendations(matches):
    recommendations = []

    # 方案一：稳胆2串1 (胜平负)
    plan1 = []
    for m in matches:
        if m['confidence_level'] == '高' and m['hotness_label'] == '适度热门':
            opts = [('胜', m['win_odds']), ('平', m['draw_odds']), ('负', m['lose_odds'])]
            q = [o for o in opts if o[1] < 1.8]
            if q:
                best = min(q, key=lambda x: x[1])
                plan1.append({'match': m, 'option': best[0], 'odds': best[1]})

    if len(plan1) >= 2:
        pairs = [(plan1[i], plan1[j]) for i in range(len(plan1)) for j in range(i + 1, len(plan1))]
        pairs.sort(key=lambda p: (p[0]['odds'] + p[1]['odds']))  # 确定性排序
        for a, b in pairs[:3]:
            co = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f"稳胆2串1-{a['match']['match_id']}+{b['match']['match_id']}",
                'plan_type': '胜平负稳胆2串1', 'combo_odds': co, 'risk_level': '低风险',
                'logic': '筛选高信心+适度热门+赔率<1.8的比赛，低赔低风险组合',
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案二：让球2串1
    hcp_bets = []
    for m in matches:
        hl = m.get('handicap_line', 0)
        if hl != 0:
            hwo = m.get('handicap_win_odds', 0)
            hdo = m.get('handicap_draw_odds', 0)
            hlo = m.get('handicap_lose_odds', 0)
            opts_list = [('让胜', hwo), ('让平', hdo), ('让负', hlo)]
            opts_list = [o for o in opts_list if o[1] > 0]
            if opts_list:
                best = min(opts_list, key=lambda x: x[1])
                hcp_bets.append({'match': m, 'option': f"{best[0]}({m['handicap']})", 'odds': best[1]})

    if len(hcp_bets) >= 2:
        pairs = [(hcp_bets[i], hcp_bets[j]) for i in range(len(hcp_bets)) for j in range(i + 1, len(hcp_bets))]
        pairs.sort(key=lambda p: (p[0]['odds'] + p[1]['odds']))
        for a, b in pairs[:2]:
            co = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f"让球2串1-{a['match']['match_id']}+{b['match']['match_id']}",
                'plan_type': '让球胜平负2串1', 'combo_odds': co, 'risk_level': '中风险',
                'logic': '基于泊松球差模型估算让球赔率，筛选让球盘口最佳选项组合',
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案三：高信心双选2串1（两场高信心赛事各自最低赔组合，使用真实赔率，不编造大小球盘口）
    hc = [m for m in matches if m['confidence_level'] == '高']
    if len(hc) >= 2:
        hc.sort(key=lambda m: m.get('confidence_score', 0), reverse=True)
        a, b = hc[0], hc[1]
        a_opt = min([('胜', a['win_odds']), ('平', a['draw_odds']), ('负', a['lose_odds'])], key=lambda x: x[1])
        b_opt = min([('胜', b['win_odds']), ('平', b['draw_odds']), ('负', b['lose_odds'])], key=lambda x: x[1])
        co = round(a_opt[1] * b_opt[1], 2)
        recommendations.append({
            'name': f"高信心2串1-{a['match_id']}+{b['match_id']}",
            'plan_type': '高信心2串1', 'combo_odds': co, 'risk_level': '中风险',
            'logic': '筛选两场高信心赛事，取各自胜平负最低赔组合，分散单场风险',
            'matches_detail': [_make_rec_detail({'match': a, 'option': a_opt[0], 'odds': a_opt[1]}),
                               _make_rec_detail({'match': b, 'option': b_opt[0], 'odds': b_opt[1]})],
            'expected_return': f"投2元返{round(co * 2, 2)}元"
        })

    # 方案四：市场+AI 双确认
    overlap = []
    for m in matches:
        po = m.get('predicted_option')
        mt = m.get('market_tendency')
        if po and mt:
            mm = {'主胜': '胜', '平局': '平', '客胜': '负'}
            mo = mm.get(mt)
            if mo and mo == po:
                ok = {'胜': 'win_odds', '平': 'draw_odds', '负': 'lose_odds'}[mo]
                overlap.append({'match': m, 'option': mo, 'odds': m[ok]})

    if len(overlap) >= 2:
        pairs = [(overlap[i], overlap[j]) for i in range(len(overlap)) for j in range(i + 1, len(overlap))]
        pairs.sort(key=lambda p: (p[0]['odds'] + p[1]['odds']))
        for a, b in pairs[:2]:
            co = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f"双确认2串1-{a['match']['match_id']}+{b['match']['match_id']}",
                'plan_type': '市场+AI双确认2串1', 'combo_odds': co, 'risk_level': '低风险',
                'logic': 'AI预测结果与市场赔率倾向一致时才纳入，双重验证提高胜率',
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案五：伤停情报2串1
    injury_bets = []
    for m in matches:
        h_inj = m.get('injuries', {}).get('home_count', 0)
        a_inj = m.get('injuries', {}).get('away_count', 0)
        if a_inj > h_inj + 1:
            injury_bets.append({'match': m, 'option': '胜', 'odds': m['win_odds']})
        elif h_inj > a_inj + 1:
            injury_bets.append({'match': m, 'option': '负', 'odds': m['lose_odds']})

    if len(injury_bets) >= 2:
        pairs = [(injury_bets[i], injury_bets[j]) for i in range(len(injury_bets)) for j in range(i + 1, len(injury_bets))]
        pairs.sort(key=lambda p: (p[0]['odds'] + p[1]['odds']))
        for a, b in pairs[:2]:
            co = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f"伤停情报2串1-{a['match']['match_id']}+{b['match']['match_id']}",
                'plan_type': '伤停情报2串1', 'combo_odds': co, 'risk_level': '中风险',
                'logic': '客队伤停数远超主队时推主胜，反之推客胜；利用阵容完整性不对称',
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # ===== 多玩法自由组合串关：3串1 / 4串1（胜平负+让球+总进球+半全场）=====
    pool = []
    for m in matches:
        plays = _match_play_options(m)
        if plays:
            pool.append({'match': m, 'plays': plays})
    pool.sort(key=lambda x: x['match'].get('confidence_score', 0), reverse=True)

    def _build(sel, name, ptype, risk, logic):
        co = 1.0
        details = []
        for p in sel:
            plays = sorted(p['plays'], key=lambda x: x['odds'] if x['odds'] > 0 else 999)
            b = plays[0]
            if b['odds'] > 0:
                co *= b['odds']
            details.append(_make_rec_detail({'match': p['match'],
                                             'option': f"{b['option']}·{b['play']}", 'odds': b['odds']}))
        co = round(co, 2)
        return {
            'name': name, 'plan_type': ptype, 'combo_odds': co, 'risk_level': risk,
            'logic': logic, 'matches_detail': details,
            'expected_return': f"投2元返{round(co * 2, 2)}元",
            'stake_note': _stake_note(co),
        }

    if len(pool) >= 3:
        recommendations.append(_build(pool[:3], '自由组合3串1', '多玩法3串1·稳健', '低风险',
            '从高信心赛事中各取最稳玩法（胜平负/让球/总进球/半全场），组合3串1'))
    if len(pool) >= 4:
        recommendations.append(_build(pool[:4], '自由组合4串1', '多玩法4串1·均衡', '中风险',
            '4场各取最稳玩法，回报与风险均衡'))
    if len(pool) >= 4:
        aggr = []
        for p in pool[:6]:
            plays = [x for x in p['plays'] if x['play'] in ('半全场', '总进球')] or p['plays']
            aggr.append({'match': p['match'], 'plays': plays})
        recommendations.append(_build(aggr[:4], '自由组合4串1·进攻', '多玩法4串1·激进', '高风险',
            '优先半全场/总进球等高赔玩法，追求高回报（谨慎参与）'))

    # ===== 统一后处理：截止时间 / 关联性 / 资金管理，覆盖全部方案 =====
    for r in recommendations:
        mds = r.get('matches_detail') or []
        r.update(_parlay_meta(mds))
        confs = [d.get('confidence_score') or 0 for d in mds]
        avg_conf = (sum(confs) / len(confs)) if confs else 0.5
        stake = _stake_plan(r.get('combo_odds'), avg_conf, r.get('risk_level', ''))
        r['avg_confidence'] = round(avg_conf, 3)
        r['stake_pct'] = stake['pct']
        r['stake_note'] = stake['note']
        # 可结算场次数（play_type 可识别）；用于串关级命中率追踪的可靠性判断
        settleable = sum(1 for d in mds if d.get('play_type'))
        r['settleable_legs'] = settleable
        r['trackable'] = (len(mds) >= 2 and settleable == len(mds))

    return recommendations


def generate_total_goals_recommendations(matches):
    tg_recs = []
    for m in matches:
        top3 = m.get('top3_goals', [])
        if not top3 or top3[0]['prob'] <= 0:
            continue
        # 主推档 = 概率最高；确定性 = 最高档与次档差距
        main = top3[0]
        second = top3[1] if len(top3) > 1 else None
        margin = round(main['prob'] - (second['prob'] if second else 0), 1)
        # 确定性标签：档位差距大 => 高确定性（更值得关注）
        if margin >= 5:
            cert = '高'
        elif margin >= 2.5:
            cert = '中'
        else:
            cert = '低'   # 档位接近，需谨慎
        # 备选档位（除主推外的其余两个）
        alt = [g['label'] for g in top3[1:3]]
        rec = {
            'match_id': m['match_id'],
            'league': m['league'],
            'home_team': m['home_team'],
            'away_team': m['away_team'],
            'match_time': m.get('match_time', ''),
            'expected_goals': m.get('expected_goals', ''),
            'tendency': m.get('over_under_tendency', ''),
            'over25_prob': m.get('over25_prob', 0),
            'main_pick': main['label'],
            'main_prob': main['prob'],
            'alt_picks': alt,
            'cert': cert,
            'margin': margin,
            'top3': top3,
            'goal_distribution': m.get('goal_distribution', {}),
        }
        tg_recs.append(rec)
    # 排序：确定性高（档位领先大）优先，其次超高概率；避免全是 2/3 球刷屏
    tg_recs.sort(key=lambda r: (r['margin'], r['main_prob']), reverse=True)
    return tg_recs


def _match_play_options(m):
    """一场比赛在 4 大玩法下的最佳选项（赔率最低=最可能）。
    返回 [{play, option, odds, prob}]，赔率<=0 的玩法跳过。
    """
    out = []

    def add(play, opts):
        opts = [o for o in opts if o[1] and o[1] > 0]
        if not opts:
            return
        b = min(opts, key=lambda x: x[1])
        out.append({'play': play, 'option': b[0], 'odds': round(float(b[1]), 2),
                    'prob': round(100.0 / b[1], 1)})

    # 1. 胜平负
    add('胜平负', [('胜', m.get('win_odds')), ('平', m.get('draw_odds')), ('负', m.get('lose_odds'))])
    # 2. 让球胜平负
    if m.get('handicap_line', 0):
        add('让球' + str(m.get('handicap', '') or ''), [
            ('让胜', m.get('handicap_win_odds')),
            ('让平', m.get('handicap_draw_odds')),
            ('让负', m.get('handicap_lose_odds'))])
    # 3. 总进球（主推档，模型估算赔率）
    top3 = m.get('top3_goals') or []
    if top3 and top3[0].get('prob', 0) > 0:
        p = top3[0]['prob']
        out.append({'play': '总进球', 'option': top3[0]['label'], 'odds': round(100.0 / p, 2), 'prob': p})
    # 4. 半全场
    htft = m.get('htft') or {}
    if htft.get('pick'):
        o = htft.get('odds') or 0
        out.append({'play': '半全场', 'option': htft['pick'],
                    'odds': round(float(o), 2) if o else 0, 'prob': htft.get('prob') or 0})
    return out


# 玩法的中文选项 → 结算用 pick 映射（供串关逐场结算）
_1X2_PICK = {'胜': 'H', '平': 'D', '负': 'A'}
_AH_PICK = {'让胜': 'H', '让平': 'D', '让负': 'A'}
_HTFT_LABEL2CODE = {'胜胜': 'HH', '胜平': 'HD', '胜负': 'HA',
                    '平胜': 'DH', '平平': 'DD', '平负': 'DA',
                    '负胜': 'AH', '负平': 'AD', '负负': 'AA'}


def _infer_leg_meta(m, option):
    """从推荐项的中文 option 推断结算玩法与 pick，供串关级命中率追踪使用。
    返回 (play_type, pick)；无法识别时返回 (None, None)。
    - 胜/平/负 → 1X2
    - 让胜/让平/让负 → AH（pick 形如 'H|-1'）
    - 半全场中文（胜胜…）→ HTFT
    - 总进球（2球/7+）→ TG
    """
    opt = (option or '').strip()
    play = ''
    if '·' in opt:
        opt, play = opt.split('·', 1)
    opt = opt.split('(')[0].strip()          # 去括号如「让胜(让球-1)」
    if opt in _1X2_PICK and play in ('', '胜平负'):
        return '1X2', _1X2_PICK[opt]
    if opt in _AH_PICK:
        try:
            line = float(m.get('handicap_line', 0) or 0)
        except (TypeError, ValueError):
            line = 0.0
        return 'AH', '%s|%s' % (_AH_PICK[opt], line)
    if opt in _HTFT_LABEL2CODE and play in ('', '半全场'):
        return 'HTFT', _HTFT_LABEL2CODE[opt]
    if (opt.endswith('球') or opt == '7+') and play in ('', '总进球'):
        num = opt[:-1] if opt.endswith('球') else '7+'
        if num.isdigit() or num == '7+':
            return 'TG', num
    return None, None


def _stake_plan(combo_odds, avg_conf, risk_level=''):
    """资金管理建议：按组合赔率 + 平均信心给出建议投入本金比例与金额。
    avg_conf 为 0~1 的平均信心分（缺失按 0.5）。返回 dict。
    原则：低赔稳健可多投、高赔高风险小额；信心越高比例越高；上限 10% 防过度下注。
    """
    try:
        co = float(combo_odds)
    except (TypeError, ValueError):
        co = 0.0
    try:
        ac = float(avg_conf)
    except (TypeError, ValueError):
        ac = 0.5
    ac = max(0.2, min(ac, 0.95))
    if co <= 3:
        base = 5.0
    elif co <= 8:
        base = 2.0
    elif co <= 20:
        base = 1.0
    else:
        base = 0.5
    pct = base * (0.6 + ac)                  # 信心 0.2→×0.8；0.95→×1.55
    pct = round(max(0.2, min(pct, 10.0)), 2)
    amount = max(1, round(pct * 10))         # 按 1000 元本金折算单注额（元）
    tag = '稳健可多投' if co <= 3 else '中等仓位' if co <= 8 else '高风险小额试探'
    return {
        'pct': pct,
        'amount': amount,
        'note': '建议投入本金 %s%%（约 %s 元/千元 · %s），忌追高加注' % (pct, amount, tag),
    }


def _stake_note(co):
    """兼容旧调用：仅按组合赔率给粗略建议（新代码请用 _stake_plan）。"""
    return _stake_plan(co, 0.5)['note']


def _parlay_meta(details):
    """由串关各场明细推导：最早开赛/投注截止时间 + 同联赛/同时段关联风险。"""
    details = details or []
    times = sorted([d.get('match_time') for d in details if d.get('match_time')])
    leagues = {}
    slots = {}
    for d in details:
        lg = d.get('league') or ''
        if lg:
            leagues[lg] = leagues.get(lg, 0) + 1
        t = d.get('match_time') or ''
        if len(t) >= 2:
            slots[t[:2] + ':00'] = slots.get(t[:2] + ':00', 0) + 1
    same_league = ['%d场%s' % (n, k) for k, n in leagues.items() if n >= 2]
    same_slot = ['%d场%s' % (n, k) for k, n in slots.items() if n >= 2]
    warns = []
    if same_league:
        warns.append('同联赛 ' + '、'.join(same_league))
    if same_slot:
        warns.append('同一时段 ' + '、'.join(same_slot))
    earliest = times[0] if times else ''
    return {
        'earliest_time': earliest,
        'cutoff_time': earliest,
        'cutoff_note': ('请在 %s 前完成投注（以最早开赛场次为准）' % earliest) if earliest else '',
        'same_league': same_league,
        'same_slot': same_slot,
        'correlation_warning': ('；'.join(warns) + ' 关联风险，建议分散') if warns else '',
        'kickoff_list': [{'time': d.get('match_time', ''), 'home_team': d.get('home_team', ''),
                          'away_team': d.get('away_team', ''), 'option': d.get('option', ''),
                          'odds': d.get('odds')} for d in details],
    }


def _intl_edge_for(m, option):
    """该选项对应的国际盘价值差（仅胜平负适用，正值=竞彩相对国际更划算）。"""
    edges = (m.get('intl_value') or {}).get('edges') or {}
    if not edges:
        return None
    return {'胜': edges.get('home'), '平': edges.get('draw'), '负': edges.get('away')}.get(option)


def _make_rec_detail(item):
    m = item['match']
    option = item['option']
    _pt, _pk = _infer_leg_meta(m, option)
    return {
        'match_id': m['match_id'], 'league': m['league'],
        'home_team': m['home_team'], 'away_team': m['away_team'],
        'match_time': m.get('match_time', ''), 'option': option, 'odds': item['odds'],
        'play_type': _pt, 'pick': _pk,
        'hotness_label': m.get('hotness_label', ''), 'bookmaker_intent': m.get('bookmaker_intent', ''),
        'home_rank': m.get('home_rank'), 'away_rank': m.get('away_rank'),
        'market_tendency': m.get('market_tendency', ''),
        'injury_impact': m.get('injury_impact', ''),
        'confidence_score': m.get('confidence_score', 0),
        'intl_edge': _intl_edge_for(m, item.get('option', '')),
    }
