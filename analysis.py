import random
import copy


def analyze_single_match(match):
    """对单场比赛进行 AI 分析，返回带分析字段的完整数据"""
    odds_list = [
        ('胜', match['win_odds']),
        ('平', match['draw_odds']),
        ('负', match['lose_odds'])
    ]
    min_option, min_odds = min(odds_list, key=lambda x: x[1])

    # 1. 信心等级：基于赔率隐含概率（1/赔率）结合实力差
    implied_prob = 1.0 / min_odds
    strength_adj = match.get('home_strength', 0.5) - 0.5
    if min_option == '负':
        strength_adj = -strength_adj
    confidence_score = min(0.95, max(0.05, implied_prob + strength_adj * 0.2))

    if confidence_score > 0.55:
        confidence_level = '高'
    elif confidence_score > 0.40:
        confidence_level = '中'
    else:
        confidence_level = '低'

    # 2. 热度标签与庄家意图
    if min_odds < 1.4:
        hotness_label = '极端热门'
        bookmaker_intent = '诱盘'
    elif min_odds <= 1.8:
        hotness_label = '适度热门'
        bookmaker_intent = '真实防范'
    else:
        hotness_label = '相对冷门'
        bookmaker_intent = '中性'

    # 3. 大小球倾向：基于两队模拟场均进球/失球
    home_gs = random.uniform(0.8, 2.4)
    home_gc = random.uniform(0.5, 2.0)
    away_gs = random.uniform(0.7, 2.0)
    away_gc = random.uniform(0.5, 2.2)
    expected = (home_gs + home_gc + away_gs + away_gc) / 2
    expected = round(expected * 2) / 2

    if expected >= 2.5:
        tendency = '大球倾向'
    elif expected <= 2.0:
        tendency = '小球倾向'
    else:
        tendency = '大小均衡'

    low = max(0, int(expected) - 1)
    high = int(expected) + 2
    goal_range = f'{low}-{high}'

    # 4. 推荐比分：随机生成
    home_score = random.randint(0, 4)
    away_score = random.randint(0, 3)
    recommended_score = f'{home_score}-{away_score}'

    result = copy.deepcopy(match)
    result['confidence_level'] = confidence_level
    result['confidence_score'] = round(confidence_score, 4)
    result['over_under_tendency'] = tendency
    result['expected_goals'] = goal_range
    result['hotness_label'] = hotness_label
    result['bookmaker_intent'] = bookmaker_intent
    result['recommended_score'] = recommended_score

    for k in ('home_strength',):
        result.pop(k, None)

    return result


def analyze_matches(matches):
    """批量分析比赛"""
    return [analyze_single_match(m) for m in matches]


def generate_parlay_recommendations(matches):
    """生成 AI 串关推荐方案"""
    recommendations = []

    # 方案一：胜平负稳胆2串1
    # 筛选: 信心高 + 热度适度热门，取赔率最低且<1.8的选项
    plan1_candidates = []
    for m in matches:
        if m['confidence_level'] == '高' and m['hotness_label'] == '适度热门':
            options = [
                ('胜', m['win_odds']),
                ('平', m['draw_odds']),
                ('负', m['lose_odds'])
            ]
            qualified = [o for o in options if o[1] < 1.8]
            if qualified:
                best = min(qualified, key=lambda x: x[1])
                plan1_candidates.append({
                    'match': m,
                    'option': best[0],
                    'odds': best[1]
                })

    if len(plan1_candidates) >= 2:
        pairs = []
        for i in range(len(plan1_candidates)):
            for j in range(i + 1, len(plan1_candidates)):
                pairs.append((plan1_candidates[i], plan1_candidates[j]))

        random.shuffle(pairs)
        for idx, (a, b) in enumerate(pairs[:3]):
            combo_odds = round(a['odds'] * b['odds'], 2)
            recommendations.append({
                'name': f'稳胆2串1-{a["match"]["match_id"]}+{b["match"]["match_id"]}',
                'plan_type': '胜平负稳胆2串1',
                'combo_odds': combo_odds,
                'risk_level': '低风险',
                'matches_detail': [
                    _make_rec_match_detail(a['match'], a['option'], a['odds']),
                    _make_rec_match_detail(b['match'], b['option'], b['odds'])
                ],
                'expected_return': f'投2元返{round(combo_odds * 2, 2)}元'
            })

    # 方案二：混合高信心2串1
    # 一场胜平负（信心高）+ 一场大小球（倾向明显）
    high_conf = [m for m in matches if m['confidence_level'] == '高']
    clear_ou = [m for m in matches if m['over_under_tendency'] in ('大球倾向', '小球倾向')]

    if high_conf and clear_ou:
        spf_match = random.choice(high_conf)
        options = [
            ('胜', spf_match['win_odds']),
            ('平', spf_match['draw_odds']),
            ('负', spf_match['lose_odds'])
        ]
        best_spf = min(options, key=lambda x: x[1])

        ou_match = random.choice(clear_ou)
        ou_odds = round(random.uniform(1.50, 1.95), 2)

        combo_odds = round(best_spf[1] * ou_odds, 2)
        recommendations.append({
            'name': f'混合2串1-{spf_match["match_id"]}({best_spf[0]})+{ou_match["match_id"]}({ou_match["over_under_tendency"]})',
            'plan_type': '混合高信心2串1',
            'combo_odds': combo_odds,
            'risk_level': '中风险',
            'matches_detail': [
                _make_rec_match_detail(spf_match, best_spf[0], best_spf[1]),
                _make_rec_match_detail(
                    ou_match,
                    ou_match['over_under_tendency'],
                    ou_odds
                )
            ],
            'expected_return': f'投2元返{round(combo_odds * 2, 2)}元'
        })

    return recommendations


def _make_rec_match_detail(match, option, odds):
    return {
        'match_id': match['match_id'],
        'league': match['league'],
        'home_team': match['home_team'],
        'away_team': match['away_team'],
        'match_time': match['match_time'],
        'option': option,
        'odds': odds,
        'hotness_label': match['hotness_label'],
        'bookmaker_intent': match['bookmaker_intent']
    }
