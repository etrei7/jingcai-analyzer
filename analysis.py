import random, math, copy


def _poisson_prob(k, lam):
    """泊松分布概率 P(X=k)"""
    if lam <= 0:
        return 0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _goal_distribution(expected):
    dist = {}
    for k in range(6):
        dist[str(k)] = round(_poisson_prob(k, expected) * 100, 1)
    dist['5+'] = round((1 - sum(dist.values()) / 100) * 100, 1)
    return dist


def analyze_single_match(match, standings=None, prediction=None):
    odds_list = [('胜', match['win_odds']), ('平', match['draw_odds']), ('负', match['lose_odds'])]
    min_option, min_odds = min(odds_list, key=lambda x: x[1])

    # 1. 信心等级
    implied_prob = 1.0 / min_odds
    confidence_score = min(0.95, max(0.05, implied_prob))
    predicted_option = None

    if prediction:
        pred_conf = prediction.get('confidence', 0) or 0
        if pred_conf > 0:
            confidence_score = (confidence_score + pred_conf) / 2
            pr = prediction.get('predicted_result', '')
            predicted_option = '胜' if pr == 'home' else '平' if pr == 'draw' else '负' if pr == 'away' else None

    confidence_level = '高' if confidence_score > 0.55 else '中' if confidence_score > 0.40 else '低'

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

    # 4. 排名与球队信心
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

    def _team_confidence(form_str, rank=None, pts=None, xgd=None):
        score = 3.0
        if form_str:
            for ch in form_str[-5:]:
                if ch in 'Ww': score += 0.6
                elif ch in 'Ll': score -= 0.5
                elif ch in 'Dd': score += 0.1
        if rank and rank <= 3: score += 0.8
        elif rank and rank <= 6: score += 0.3
        if xgd and xgd > 5: score += 0.3
        elif xgd and xgd < -5: score -= 0.3
        return round(max(0, min(5, score)), 1)

    home_confidence = _team_confidence(home_form, home_rank, home_pts, home_xgd)
    away_confidence = _team_confidence(away_form, away_rank, away_pts, away_xgd)

    # 5. 大小球倾向 + 泊松分布
    expected = 2.4
    if prediction and prediction.get('expected_goals'):
        pred_goals = prediction['expected_goals']
        if pred_goals > 0:
            expected = pred_goals
    else:
        home_gs = random.uniform(0.8, 2.4)
        home_gc = random.uniform(0.5, 2.0)
        away_gs = random.uniform(0.7, 2.0)
        away_gc = random.uniform(0.5, 2.2)
        expected = (home_gs + home_gc + away_gs + away_gc) / 2
    expected = round(expected * 2) / 2

    goal_dist = _goal_distribution(expected)
    over25_prob = round((goal_dist.get(3, 0) + goal_dist.get(4, 0) + goal_dist.get('5+', 0)), 1)

    if expected >= 2.5:
        tendency = '大球倾向'
    elif expected <= 2.0:
        tendency = '小球倾向'
    else:
        tendency = '大小均衡'

    low = max(0, int(expected) - 1)
    high = int(expected) + 2
    goal_range = f'{low}-{high}'

    # 6. 推荐比分
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

    # 7. 伤停影响评估
    injury_impact = '无影响'
    h_inj = match.get('injuries', {}).get('home_count', 0)
    a_inj = match.get('injuries', {}).get('away_count', 0)
    total_inj = h_inj + a_inj
    if total_inj >= 5: injury_impact = '重大影响'
    elif total_inj >= 3: injury_impact = '中等影响'
    elif total_inj >= 1: injury_impact = '轻微影响'

    # 8. 裁判影响
    ref = match.get('referee', {})
    ref_impact = ''
    avg_y = ref.get('avg_yellows', 0)
    if avg_y >= 5.0: ref_impact = '易出黄牌，大小球注意'
    elif avg_y >= 3.5: ref_impact = '出牌适中'

    # 构建结果
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
    result['home_xgd'] = home_xgd
    result['away_xgd'] = away_xgd
    result['predicted_option'] = predicted_option
    result['goal_distribution'] = goal_dist
    result['over25_prob'] = over25_prob
    result['injury_impact'] = injury_impact
    result['ref_impact'] = ref_impact

    for k in ('home_strength', 'league_id', 'home_team_id', 'away_team_id', 'funfacts', 'ai_preview',
              'home_coach_style', 'away_coach_style', 'travel_distance_km'):
        result.pop(k, None)

    return result


def analyze_matches(matches, standings=None, predictions=None):
    if predictions is None:
        predictions = {}
    return [analyze_single_match(m, standings, predictions.get(m.get('match_id', ''))) for m in matches]


def generate_parlay_recommendations(matches):
    recommendations = []

    # 方案一：稳胆2串1
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
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案二：混合高信心2串1
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
            'matches_detail': [_make_rec_detail({'match': spf, 'option': bs[0], 'odds': bs[1]}),
                               _make_rec_detail({'match': ou, 'option': ou['over_under_tendency'], 'odds': ou_odds})],
            'expected_return': f"投2元返{round(co * 2, 2)}元"
        })

    # 方案三：市场+AI 双确认
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
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    # 方案四：伤停情报 2串1（客队伤停多时选主队）
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
                'matches_detail': [_make_rec_detail(a), _make_rec_detail(b)],
                'expected_return': f"投2元返{round(co * 2, 2)}元"
            })

    return recommendations


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
