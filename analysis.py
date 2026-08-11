import random, math, copy


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
    
    # 1. 状态 (0-3分): based on recent form
    form_score = 1.5
    if form_str:
        wins = sum(1 for ch in form_str[-5:] if ch in 'Ww')
        losses = sum(1 for ch in form_str[-5:] if ch in 'Ll')
        form_score = 1.5 + wins * 0.5 - losses * 0.4
    breakdown['状态'] = round(max(0, min(3, form_score)), 1)

    # 2. 历史交手 (0-2分): estimated from odds ratio (stronger team = favored h2h)
    h2h_score = 1.0
    if odds_ratio is not None and odds_ratio > 0:
        if odds_ratio < 0.6:  h2h_score = 2.0          # strong favorite
        elif odds_ratio < 0.8: h2h_score = 1.5
        elif odds_ratio > 1.6: h2h_score = 0.3         # heavy underdog
        elif odds_ratio > 1.2: h2h_score = 0.7
        else: h2h_score = 1.0
    breakdown['历史交手'] = round(h2h_score, 1)

    # 3. 伤病 (0-3分): fewer injuries = higher score
    inj_score = max(0, 3.0 - injuries_count * 0.6)
    breakdown['伤病'] = round(inj_score, 1)

    # 4. 首发轮换 (0-2分): fewer unavailable = better lineup stability
    lineup_score = max(0, 2.0 - unavailable_count * 0.4)
    breakdown['首发轮换'] = round(lineup_score, 1)

    total = round(sum(breakdown.values()), 1)
    return {'score': min(total, 10), 'breakdown': breakdown}


def _compute_handicap(match, prediction):
    """Compute 让球 recommendation from odds and predictions.
    Returns handicap line and estimated odds for 让球胜/平/负."""
    win_odds = match['win_odds']
    draw_odds = match['draw_odds']
    lose_odds = match['lose_odds']

    # Determine handicap direction: negative = home gives ball, positive = home gets ball
    if win_odds > 0 and lose_odds > 0:
        if win_odds < lose_odds * 0.6:
            line = -1    # home team strong favorite
        elif lose_odds < win_odds * 0.6:
            line = +1    # away team strong favorite
        else:
            line = 0     # balanced
    else:
        line = 0

    # Estimate handicap odds from Poisson difference probabilities
    pred = prediction or {}
    home_exp = float(pred.get('expected_home_goals', 0) or 0)
    away_exp = float(pred.get('expected_away_goals', 0) or 0)

    if home_exp <= 0 or away_exp <= 0:
        imp = 1.0 / win_odds if win_odds > 0 else 0.35
        home_exp = imp * 5.0
        away_exp = (1 - imp) * 5.0

    # For handicap line = -1: home must win by 2+, draw if win by 1, away wins if home doesn't win
    # Skellam: P(home - away >= 2), P(home - away == 1), P(home - away <= 0)
    if line == -1:
        prob_hg_win = sum(_skellam_prob(d, home_exp, away_exp) for d in range(2, 11))
        prob_hg_draw = _skellam_prob(1, home_exp, away_exp)
        prob_hg_lose = 1 - prob_hg_win - prob_hg_draw
        odds_win = round(1.0 / max(prob_hg_win, 0.02), 2)
        odds_draw = round(1.0 / max(prob_hg_draw, 0.02), 2)
        odds_lose = round(1.0 / max(prob_hg_lose, 0.02), 2)
    elif line == +1:
        prob_hg_win = sum(_skellam_prob(d, away_exp, home_exp) for d in range(2, 11))
        prob_hg_draw = _skellam_prob(1, away_exp, home_exp)
        prob_hg_lose = 1 - prob_hg_win - prob_hg_draw
        odds_win = round(1.0 / max(prob_hg_win, 0.02), 2)
        odds_draw = round(1.0 / max(prob_hg_draw, 0.02), 2)
        odds_lose = round(1.0 / max(prob_hg_lose, 0.02), 2)
    else:  # line == 0, same as 1x2
        odds_win = win_odds
        odds_draw = draw_odds
        odds_lose = lose_odds
        
    label = f'让球{line:+d}' if line != 0 else '不让球'

    return {
        'handicap_line': line,
        'handicap_label': label,
        'handicap_win_odds': odds_win,
        'handicap_draw_odds': odds_draw,
        'handicap_lose_odds': odds_lose,
    }


def _compute_total_goals(match, prediction):
    expected = 2.4
    pred = prediction or {}

    home_exp = float(pred.get('expected_home_goals', 0) or 0)
    away_exp = float(pred.get('expected_away_goals', 0) or 0)

    if home_exp > 0 and away_exp > 0:
        expected = home_exp + away_exp
    else:
        home_gs = random.uniform(0.8, 2.4)
        home_gc = random.uniform(0.5, 2.0)
        away_gs = random.uniform(0.7, 2.0)
        away_gc = random.uniform(0.5, 2.2)
        expected = (home_gs + home_gc + away_gs + away_gc) / 2

    expected = round(expected * 2) / 2
    dist = {}
    for k in range(8):
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

    # Tendency tag
    if expected >= 2.5:
        tendency = '大球倾向'
    elif expected <= 2.0:
        tendency = '小球倾向'
    else:
        tendency = '大小均衡'

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

    if prediction:
        pred_conf = prediction.get('confidence', 0) or 0
        if pred_conf > 0:
            confidence_score = (confidence_score + pred_conf) / 2
            pr = prediction.get('predicted_result', '')
            predicted_option = '胜' if pr == 'home' else '平' if pr == 'draw' else '负' if pr == 'away' else None
    else:
        predicted_option = None

    confidence_score *= league_quality
    confidence_level = '高' if confidence_score > 0.48 else '中' if confidence_score > 0.32 else '低'

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

    # 4. 排名信息
    home_rank = away_rank = home_form = away_form = ''
    home_pts = away_pts = home_xgd = away_xgd = None

    if standings and match.get('league_id'):
        ls = standings.get(str(match['league_id']), {})
        if ls:
            hk = str(match.get('home_team_id', '')) or match['home_team']
            ak = str(match.get('away_team_id', '')) or match['away_team']
            hi = ls.get(hk, {})
            ai = ls.get(ak, {})
            home_rank = hi.get('position')
            away_rank = ai.get('position')
            home_form = hi.get('form', '')
            away_form = ai.get('form', '')
            home_pts = hi.get('pts')
            away_pts = ai.get('pts')
            home_xgd = hi.get('xgd')
            away_xgd = ai.get('xgd')

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

    # 6. 总进球分析
    tg = _compute_total_goals(match, prediction)
    expected = tg['expected']
    goal_range = tg['goal_range']
    tendency = tg['tendency']
    goal_dist = tg['distribution']
    over25_prob = tg['over25_prob']
    top3_goals = tg['top3_goals']

    # 7. 让球分析
    handicap = _compute_handicap(match, prediction)

    # 8. 推荐比分
    if predicted_option == '胜':
        home_score = random.randint(1, 3)
        away_score = random.randint(0, home_score - 1)
    elif predicted_option == '负':
        away_score = random.randint(1, 3)
        home_score = random.randint(0, away_score - 1)
    elif predicted_option == '平':
        s = random.randint(0, 2)
        home_score = away_score = s
    else:
        home_score = random.randint(0, 4)
        away_score = random.randint(0, 3)
    recommended_score = f'{home_score}-{away_score}'

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
    result['home_xgd'] = home_xgd
    result['away_xgd'] = away_xgd
    result['predicted_option'] = predicted_option
    result['goal_distribution'] = goal_dist
    result['over25_prob'] = over25_prob
    result['injury_impact'] = injury_impact
    result['ref_impact'] = ref_impact

    # Handicap fields
    result['handicap'] = handicap['handicap_label']
    result['handicap_line'] = handicap['handicap_line']
    result['handicap_win_odds'] = handicap['handicap_win_odds']
    result['handicap_draw_odds'] = handicap['handicap_draw_odds']
    result['handicap_lose_odds'] = handicap['handicap_lose_odds']

    # Total goals fields
    result['expected_total'] = expected
    result['top3_goals'] = top3_goals

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
        random.shuffle(pairs)
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
        random.shuffle(pairs)
        for a, b in pairs[:2]:
            co = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f"让球2串1-{a['match']['match_id']}+{b['match']['match_id']}",
                'plan_type': '让球胜平负2串1', 'combo_odds': co, 'risk_level': '中风险',
                'logic': '基于泊松球差模型估算让球赔率，筛选让球盘口最佳选项组合',
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案三：混合高信心2串1
    hc = [m for m in matches if m['confidence_level'] == '高']
    co_match = [m for m in matches if m['over_under_tendency'] in ('大球倾向', '小球倾向')]
    if hc and co_match:
        spf = random.choice(hc)
        opts = [('胜', spf['win_odds']), ('平', spf['draw_odds']), ('负', spf['lose_odds'])]
        bs = min(opts, key=lambda x: x[1])
        ou = random.choice(co_match)
        ou_odds = round(random.uniform(1.50, 1.95), 2)
        co = round(bs[1] * ou_odds, 2)
        recommendations.append({
            'name': f"混合2串1-{spf['match_id']}({bs[0]})+{ou['match_id']}({ou['over_under_tendency']})",
            'plan_type': '混合高信心2串1', 'combo_odds': co, 'risk_level': '中风险',
            'logic': '胜平负高信心场次 + 大小球倾向场次交叉组合，分散风险',
            'matches_detail': [_make_rec_detail({'match': spf, 'option': bs[0], 'odds': bs[1]}),
                               _make_rec_detail({'match': ou, 'option': ou['over_under_tendency'], 'odds': ou_odds})],
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
        random.shuffle(pairs)
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
        random.shuffle(pairs)
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
        rec = {
            'match_id': m['match_id'],
            'league': m['league'],
            'home_team': m['home_team'],
            'away_team': m['away_team'],
            'match_time': m.get('match_time', ''),
            'expected_goals': m.get('expected_goals', ''),
            'tendency': m.get('over_under_tendency', ''),
            'over25_prob': m.get('over25_prob', 0),
            'top3': top3,
            'goal_distribution': m.get('goal_distribution', {}),
        }
        tg_recs.append(rec)
    tg_recs.sort(key=lambda r: r['top3'][0]['prob'] if r['top3'] else 0, reverse=True)
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
