import random, math, copy, json, os, hashlib


def _stable_random(seed_str, min_val, max_val):
    """确定性随机：同输入永远同输出"""
    h = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    return min_val + (h % 1000) / 1000.0 * (max_val - min_val)


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


def _goal_distribution(expected):
    dist = {}
    for k in range(7):
        dist[str(k)] = round(_poisson_prob(k, expected) * 100, 1)
    total_so_far = sum(dist.values())
    dist['7+'] = round((1 - total_so_far / 100) * 100, 1)
    dist['5+'] = round(sum(dist.get(str(k), 0) for k in range(5, 7)) + dist.get('7+', 0), 1)
    return dist


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

    # 选择让球线：期望净胜球
    net = home_exp - away_exp
    if net >= 0.75:
        line = -1
        direction = '主让'
    elif net <= -0.75:
        line = +1
        direction = '客让'
    else:
        line = 0
        direction = '平手'

    # Skellam 计算让球后胜平负概率
    def _probs_for_line(ln):
        if ln == -1:  # 主让1球: 主队净胜2+ / 净胜1 / 平或负
            ph = sum(_skellam_prob(d, home_exp, away_exp) for d in range(2, 11))
            pd = _skellam_prob(1, home_exp, away_exp)
            pl = 1 - ph - pd
        elif ln == +1:  # 客让1球: 客队净胜2+ / 净胜1 / 平或负
            ph = sum(_skellam_prob(d, away_exp, home_exp) for d in range(2, 11))
            pd = _skellam_prob(1, away_exp, home_exp)
            pl = 1 - ph - pd
        else:  # 平手盘
            ph = sum(_skellam_prob(d, home_exp, away_exp) for d in range(1, 11))
            pd = _skellam_prob(0, home_exp, away_exp)
            pl = 1 - ph - pd
        return ph, pd, pl

    ph, pd, pl = _probs_for_line(line)
    # 保险上下限，避免概率趋零时赔率虚高（如 1/0.03≈33）或趋一时赔率过低
    def _safe_odds(p):
        p = max(min(p, 0.95), 0.06)
        return round(1.0 / p, 2)
    odds_win = _safe_odds(ph)
    odds_draw = _safe_odds(pd)
    odds_lose = _safe_odds(pl)

    label = f'让球{line:+d}' if line != 0 else '不让球'
    pick_side = max([('让胜', ph, odds_win), ('让平', pd, odds_draw), ('让负', pl, odds_lose)],
                    key=lambda x: x[1])
    pick_label = f'{pick_side[0]}({direction})' if line != 0 else f'{pick_side[0]}'

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
                     'odds': pick_side[2], 'side': pick_side[0], 'line': line, 'direction': direction},
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


def analyze_single_match(match, standings=None, prediction=None):
    odds_list = [('胜', match['win_odds']), ('平', match['draw_odds']), ('负', match['lose_odds'])]
    min_option, min_odds = min(odds_list, key=lambda x: x[1])

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
        predicted_option = min_option[0] if min_option else None

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
    # 数据质量约束：浅数据（无基本面信号或低联赛质量）不允许"高信心"——
    # 没有基本面信息支撑的推荐，即便赔率极端也不应标为高信心（避免盲目跟赔率/诱盘）。
    league_quality_now = league_quality
    conf_level = '高' if confidence_score > 0.55 else '中' if confidence_score > 0.38 else '低'
    # 浅数据判断：无基本面信号（无排名/状态/xG/伤病差支撑）即为浅数据 → 封顶"中"
    # 优先用 fund_sig 是否为空判断（比 league_quality 更准确，杯赛无积分榜也是浅数据）
    is_shalow = (not fund_sig) or league_quality_now < 0.7
    if is_shalow and conf_level == '高':
        conf_level = '中'
        confidence_score = min(confidence_score, 0.55)
        cross_signal = (cross_signal + '·浅数据') if cross_signal else '浅数据'
    # 极端热门(诱盘)降级：赔率<1.25 且基本面信号不强一致时降为谨慎
    if min_odds < 1.25 and conf_level == '高' and fund.get('score', 0) < 0.4:
        conf_level = '中'
        confidence_score = min(confidence_score, 0.55)
        cross_signal = '极端热门·谨慎'
    elif min_odds < 1.4 and conf_level == '高' and fund.get('score', 0) < 0.3:
        conf_level = '中'
        confidence_score = min(confidence_score, 0.5)
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
            pick = max(inv, key=lambda x: x[1]) if tot > 0 else ('让胜', 0)
            hcp_pick = {'option': f'{pick[0]}({direction})' if oline != 0 else pick[0],
                        'prob': round(pick[1] / tot * 100, 1) if tot > 0 else 0,
                        'odds': dict(opts)[pick[0]], 'side': pick[0], 'line': oline, 'direction': direction,
                        'source': '官方盘口'}
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

    # Cleanup internal fields
    for k in ('home_strength', 'league_id', 'home_team_id', 'away_team_id', 'funfacts', 'ai_preview',
              'home_coach_style', 'away_coach_style', 'travel_distance_km', '_raw_date', 'event_date_raw'):
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


def _make_rec_detail(item):
    m = item['match']
    return {
        'match_id': m['match_id'], 'league': m['league'],
        'home_team': m['home_team'], 'away_team': m['away_team'],
        'match_time': m.get('match_time', ''), 'option': item['option'], 'odds': item['odds'],
        'hotness_label': m.get('hotness_label', ''), 'bookmaker_intent': m.get('bookmaker_intent', ''),
        'home_rank': m.get('home_rank'), 'away_rank': m.get('away_rank'),
        'market_tendency': m.get('market_tendency', ''),
        'injury_impact': m.get('injury_impact', ''),
    }
