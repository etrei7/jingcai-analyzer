import random
from datetime import datetime, timedelta, timezone

LEAGUES = [
    '英超', '西甲', '德甲', '意甲', '法甲',
    '中超', '日职', '韩K联', '澳超', '挪超', '瑞典超', '丹超',
    '英冠', '德乙', '意乙', '荷甲', '葡超', '土超', '巴甲',
]

CST = timezone(timedelta(hours=8))
DAY_NAMES = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

TEAMS = {
    '英超': ['曼城', '利物浦', '阿森纳', '曼联', '切尔西', '热刺', '纽卡斯尔', '布莱顿', '阿斯顿维拉', '西汉姆联'],
    '西甲': ['皇马', '巴萨', '马竞', '皇家社会', '毕尔巴鄂', '贝蒂斯', '比利亚雷亚尔', '塞维利亚', '瓦伦西亚', '奥萨苏纳'],
    '德甲': ['拜仁', '多特蒙德', '莱比锡', '勒沃库森', '法兰克福', '弗赖堡', '沃尔夫斯堡', '门兴', '斯图加特', '霍芬海姆'],
    '意甲': ['国米', 'AC米兰', '尤文图斯', '那不勒斯', '拉齐奥', '罗马', '亚特兰大', '佛罗伦萨', '博洛尼亚', '都灵'],
    '法甲': ['巴黎', '摩纳哥', '马赛', '里昂', '尼斯', '里尔', '雷恩', '朗斯', '斯特拉斯堡', '蒙彼利埃'],
    '中超': ['上海海港', '山东泰山', '北京国安', '成都蓉城', '上海申花', '武汉三镇', '浙江队', '天津津门虎', '河南队', '长春亚泰'],
    '日职': ['横滨水手', '川崎前锋', '浦和红钻', '鹿岛鹿角', '大阪钢巴', '名古屋鲸八', '广岛三箭', '东京FC', '柏太阳神', '新泻天鹅'],
    '韩K联': ['蔚山现代', '全北现代', '浦项制铁', '首尔FC', '济州联', '大邱FC', '水原三星', '仁川联', '光州FC', '大田市民'],
    '澳超': ['墨尔本城', '中央海岸', '悉尼FC', '西悉尼', '阿德莱德', '墨尔本胜利', '布里斯班', '珀斯光荣', '纽卡斯尔', '惠灵顿凤凰'],
    '挪超': ['博德闪耀', '莫尔德', '罗森博格', '维京', '布兰', '利勒斯特罗姆', '奥德', '斯特罗姆加斯特', '萨普斯堡', '特罗姆瑟'],
    '瑞典超': ['马尔默', '埃尔夫斯堡', '赫根', '尤尔加登', '索尔纳', '哈马比', '卡尔马', '北雪平', '米亚尔比', '天狼星'],
    '丹超': ['哥本哈根', '中日德兰', '布隆德比', '奥胡斯', '北西兰', '奥尔堡', '锡尔克堡', '兰德斯', '瓦埃勒', '维堡'],
    '英冠': ['利兹联', '莱斯特城', '南安普顿', '西布朗', '诺维奇', '沃特福德', '米德尔斯堡', '考文垂', '桑德兰', '卡迪夫城'],
    '德乙': ['汉堡', '杜塞尔多夫', '汉诺威', '圣保利', '基尔', '帕德博恩', '纽伦堡', '凯泽斯劳滕', '菲尔特', '柏林赫塔'],
    '意乙': ['帕尔马', '威尼斯', '克雷莫纳', '桑普多利亚', '巴勒莫', '布雷西亚', '比萨', '巴里', '南蒂罗尔', '科莫'],
    '荷甲': ['阿贾克斯', '埃因霍温', '费耶诺德', '阿尔克马尔', '特温特', '乌德勒支', '奈梅亨', '海伦芬', '福图纳', '瓦尔韦克'],
    '葡超': ['本菲卡', '波尔图', '里斯本竞技', '布拉加', '吉马良斯', '法马利康', '博阿维斯塔', '阿罗卡', '卡萨皮亚', '埃斯托里尔'],
    '土超': ['加拉塔萨雷', '费内巴切', '贝西克塔斯', '特拉布宗', '伊斯坦布尔', '科尼亚', '阿拉尼亚', '开塞利', '安塔利亚', '锡瓦斯'],
    '巴甲': ['弗拉门戈', '帕尔梅拉斯', '圣保罗', '科林蒂安', '巴西国际', '格雷米奥', '弗鲁米嫩塞', '米内罗竞技', '桑托斯', '博塔弗戈'],
}


def _random_form(num=5):
    """生成近期状态字符串，胜率反映球队实力"""
    w = random.randint(0, num)
    l = random.randint(0, num - w)
    d = num - w - l
    chars = ['W'] * w + ['L'] * l + ['D'] * d
    random.shuffle(chars)
    return ''.join(chars)


def _random_odds(home_strength):
    """根据主队实力生成逻辑一致的赔率"""
    if home_strength > 0.65:
        win = round(random.uniform(1.25, 1.85), 2)
        draw = round(random.uniform(3.2, 5.5), 2)
        lose = round(random.uniform(4.0, 7.5), 2)
        handicap = random.choice(['-2', '-1.5', '-1'])
    elif home_strength > 0.52:
        win = round(random.uniform(1.70, 2.60), 2)
        draw = round(random.uniform(2.80, 3.80), 2)
        lose = round(random.uniform(2.60, 4.20), 2)
        handicap = random.choice(['-0.5', '0', '+0.5'])
    elif home_strength > 0.38:
        win = round(random.uniform(2.40, 3.20), 2)
        draw = round(random.uniform(2.80, 3.60), 2)
        lose = round(random.uniform(2.30, 3.50), 2)
        handicap = '0'
    else:
        win = round(random.uniform(2.80, 5.50), 2)
        draw = round(random.uniform(2.80, 4.50), 2)
        lose = round(random.uniform(1.35, 2.40), 2)
        handicap = random.choice(['+0.5', '+1', '+1.5', '+2'])
    return win, draw, lose, handicap


def generate_single_match():
    """生成单场比赛的模拟数据（含真实差异化的排名、状态、伤停）"""
    league = random.choice(LEAGUES)
    teams = TEAMS[league]
    home_team, away_team = random.sample(teams, 2)

    match_id = str(random.randint(7001, 7099))

    hour = random.randint(11, 23)
    minute = random.choice(['00', '15', '30', '45'])
    match_time = f'{hour:02d}:{minute}'

    home_strength = random.uniform(0.25, 0.75)
    win_odds, draw_odds, lose_odds, handicap = _random_odds(home_strength)

    now_cst = datetime.now(CST)
    mock_date = now_cst.strftime('%Y-%m-%dT%H:%M:%SZ')

    # 差异化排名（1-20，实力越强排名越高）
    home_rank = random.randint(1, 20)
    away_rank = random.randint(1, 20)
    while away_rank == home_rank:
        away_rank = random.randint(1, 20)

    # 差异化状态（5场走势）
    home_form = _random_form(5)
    away_form = _random_form(5)

    # 差异化伤停（0-5人）
    home_inj_count = random.randint(0, 5)
    away_inj_count = random.randint(0, 5)
    h_injured = []
    for i in range(home_inj_count):
        h_injured.append({'name': f'球员{i+1}', 'reason_cn': random.choice(['腘绳肌损伤','踝伤','膝伤','肌肉伤']), 'status_cn': '伤停'})
    a_injured = []
    for i in range(away_inj_count):
        a_injured.append({'name': f'球员{i+1}', 'reason_cn': random.choice(['腘绳肌损伤','踝伤','膝伤','肌肉伤']), 'status_cn': '伤停'})

    return {
        'match_id': match_id,
        'raw_event_id': match_id,
        'league': league,
        'league_id': None,
        'match_time': match_time,
        'home_team': home_team,
        'away_team': away_team,
        'home_team_id': None,
        'away_team_id': None,
        'win_odds': win_odds,
        'draw_odds': draw_odds,
        'lose_odds': lose_odds,
        'handicap': handicap,
        'handicap_line': 0,
        'handicap_win_odds': 0,
        'handicap_draw_odds': 0,
        'handicap_lose_odds': 0,
        'home_strength': round(home_strength, 4),
        'injuries': {'home': h_injured, 'away': a_injured, 'home_count': home_inj_count, 'away_count': away_inj_count},
        'referee': {'name': '待定', 'strictness': '未知', 'avg_yellows': 0, 'avg_reds': 0, 'games': 0},
        'weather': {'code': None, 'desc': '未知', 'temp': None, 'wind': None, 'impact': '无明显影响'},
        'travel_distance_km': random.randint(0, 800),
        'is_derby': random.random() < 0.1,
        'venue_name': '',
        'venue_city': '',
        'venue_capacity': None,
        'home_coach': '',
        'away_coach': '',
        'ai_preview': '',
        'home_rank': home_rank,
        'away_rank': away_rank,
        'home_form': home_form,
        'away_form': away_form,
        'home_xgd': None,
        'away_xgd': None,
        'home_confidence': 0,
        'away_confidence': 0,
        'home_breakdown': {},
        'away_breakdown': {},
        'expected_total': 0,
        'top3_goals': [],
    }


def generate_matches(count=12):
    """生成批量模拟比赛数据"""
    matches = []
    used_ids = set()
    for _ in range(count):
        match = generate_single_match()
        while match['match_id'] in used_ids:
            match = generate_single_match()
        used_ids.add(match['match_id'])
        matches.append(match)
    return matches
