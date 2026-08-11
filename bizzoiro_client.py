import os, logging, requests, math
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
from team_names import TEAM_NAME_CN

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api')
API_KEY = os.environ.get('BZZOIRO_API_KEY', '')

LEAGUE_NAME_MAP = {
    # 竞彩常开联赛（含中文名）
    'Premier League': '英超', 'La Liga': '西甲', 'Bundesliga': '德甲', 'Serie A': '意甲',
    'Ligue 1': '法甲', 'Chinese Super League': '中超', 'J1 League': '日职',
    'K League 1': '韩K联', 'A-League': '澳超',
    'Eredivisie': '荷甲', 'Primeira Liga': '葡超', 'Liga Portugal Betclic': '葡超',
    'Brasileirão Série A': '巴甲', 'Brasileirão Serie A': '巴甲', 'Brasileirão': '巴甲',
    'Major League Soccer': 'MLS', 'MLS': 'MLS',
    'Liga Profesional de Fútbol': '阿甲',
    'Allsvenskan': '瑞典超', 'Danish Superliga': '丹超', 'Eliteserien': '挪超',
    'Liga MX Apertura': '墨西超', 'Liga MX': '墨西超',
    'Scottish Premiership': '苏超', 'Pro League': '比甲',
    'Super Lig': '土超',
    'Championship': '英冠', 'Serie B': '意乙', 'La Liga 2': '西乙',
    '2. Bundesliga': '德乙', 'Ligue 2': '法乙', 'J2 League': '日乙', 'K League 2': '韩K2',
    'Carabao Cup': '英联杯', 'FA Cup': '足总杯', 'Copa del Rey': '国王杯',
    'Coppa Italia': '意杯', 'DFB Pokal': '德国杯', 'Copa do Brasil': '巴西杯',
    'Champions League': '欧冠', 'Europa League': '欧联', 'Conference League': '欧协联',
    'Europa Conference League': '欧协联',
    # 竞彩常开杯赛
    'AFC Champions League': '亚冠', 'AFC Champions League Elite': '亚冠',
    'Copa Libertadores': '解放者杯', 'UEFA Super Cup': '欧超杯',
    # 以下联赛竞彩不开，全部排除
    'Club Friendlies': None,
    'NWSL': None,
    'NPL Queensland': None,
    'USL Championship': None,
    'Parva Liga': None,
    'Superliga': None,
    'Copa Colombia': None,
    'Puchar Polski': None,
    'Liga 3': None,
    'Categoría Primera A': None,
    'Ekstraklasa': None,
    'Veikkausliiga': None,
    'Austrian Bundesliga': None,
    'Swiss Super League': None,
    'Super League': None,
    'Greek Super League': None,
    'Czech Liga': None,
    'Croatian HNL': None,
    'Ukrainian Premier League': None,
    'Saudi Pro League': None,
    'Qatar Stars League': None,
    'UAE Pro League': None,
}

WEATHER_MAP = {
    0: '晴', 1: '晴', 2: '多云', 3: '阴', 45: '雾', 48: '霜雾',
    51: '小雨', 53: '中雨', 55: '大雨', 61: '阵雨', 63: '中阵雨', 65: '大阵雨',
    71: '小雪', 73: '中雪', 75: '大雪', 80: '雷阵雨', 95: '雷暴', 96: '冰雹雷暴',
}

INJURY_TYPE_CN = {
    'Hamstring Injury': '腘绳肌损伤', 'Foot Injury': '脚伤', 'Muscle Injury': '肌肉伤',
    'Knee Injury': '膝伤', 'Ankle Injury': '踝伤', 'Groin Injury': '腹股沟伤',
    'Calf Injury': '小腿伤', 'Thigh Injury': '大腿伤', 'Shoulder Injury': '肩伤',
    'Back Injury': '背伤', 'Hip Injury': '髋伤', 'Concussion': '脑震荡',
    'Illness': '疾病', 'Suspended': '停赛', 'Unknown': '原因不明',
    'ACL Injury': '十字韧带', 'MCL Injury': '内侧副韧带',
    'Broken Leg': '腿骨折', 'Fractured Rib': '肋骨骨折',
    'Metatarsal Fracture': '跖骨骨折', 'Achilles Tendon': '跟腱伤',
    'Cruciate Ligament': '十字韧带', 'Virus': '病毒感染',
    'Quarantine': '隔离', 'Personal Reasons': '个人原因',
    'Yellow card suspension': '累计黄牌停赛', 'Red card suspension': '红牌停赛',
    'Called up to national team': '国家队征召',
}


def _headers():
    return {'Authorization': f'Token {API_KEY}'} if API_KEY else {}


def _map_league(name_en):
    return LEAGUE_NAME_MAP.get(name_en, name_en)


def _format_time(event_date_str):
    try:
        dt = datetime.fromisoformat(event_date_str)
        return (dt + timedelta(hours=4)).strftime('%H:%M')
    except Exception:
        return event_date_str


def _parse_injuries(event):
    """解析伤停/缺席球员"""
    unavailable = event.get('unavailable_players') or {}
    home_list = unavailable.get('home', [])
    away_list = unavailable.get('away', [])

    def translate(p):
        p['reason_cn'] = INJURY_TYPE_CN.get(p.get('reason', ''), p.get('reason', '未知'))
        p['status_cn'] = '伤停' if p.get('status') == 'injured' else '停赛' if p.get('status') == 'suspended' else p.get('status', '缺席')
        return p

    return {
        'home': [translate(p) for p in home_list],
        'away': [translate(p) for p in away_list],
        'home_count': len(home_list),
        'away_count': len(away_list)
    }


def _parse_referee(event):
    """解析裁判数据"""
    ref = event.get('referee') or {}
    if not ref:
        return {'name': '待定', 'strictness': '未知', 'avg_yellows': 0, 'avg_reds': 0, 'games': 0}
    games = ref.get('career_games', 0) or 1
    yellows = ref.get('career_yellow_cards', 0) or 0
    reds = ref.get('career_red_cards', 0) or 0
    avg_y = round(yellows / games, 1)
    avg_r = round(reds / games, 2)
    if avg_y >= 5.0:
        strictness = '严格 (出牌多)'
    elif avg_y >= 3.5:
        strictness = '适中'
    else:
        strictness = '宽松 (少出牌)'
    return {
        'name': ref.get('name', '待定'),
        'country': ref.get('country', ''),
        'avg_yellows': avg_y,
        'avg_reds': avg_r,
        'games': games,
        'strictness': strictness
    }


def _parse_weather(event):
    """解析天气数据"""
    code = event.get('weather_code')
    temp = event.get('temperature_c')
    wind = event.get('wind_speed')
    weather_cn = WEATHER_MAP.get(code, '未知') if code is not None else '未知'

    return {
        'code': code,
        'desc': weather_cn,
        'temp': temp,
        'wind': wind,
        'impact': _weather_impact(code, wind, temp)
    }


def _weather_impact(code, wind, temp):
    parts = []
    if code is not None:
        if code in (51, 53, 55, 61, 63, 65):
            parts.append('雨天影响地面传球')
        elif code in (80, 95, 96):
            parts.append('雷雨可能中断比赛')
        elif code in (71, 73, 75):
            parts.append('雪战影响速度')
        elif code in (45, 48):
            parts.append('能见度低')
    if wind and wind > 15:
        parts.append('大风影响长传')
    if temp is not None:
        if temp > 30:
            parts.append('高温消耗体力')
        elif temp < 5:
            parts.append('低温需适应')
    return '; '.join(parts) if parts else '无明显影响'


CST = timezone(timedelta(hours=8))
DAY_NAMES = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']


def _format_time_cst(event_date_str):
    try:
        dt = datetime.fromisoformat(event_date_str.replace('Z', '+00:00'))
        cst_dt = dt.astimezone(CST)
        return cst_dt.strftime('%H:%M')
    except Exception:
        return event_date_str


def _assign_match_ids(matches):
    grouped = OrderedDict()
    for m in matches:
        try:
            dt = datetime.fromisoformat(m['_raw_date'].replace('Z', '+00:00'))
            cst_dt = dt.astimezone(CST)
            day_key = cst_dt.weekday()
        except Exception:
            day_key = -1
        grouped.setdefault(day_key, []).append(m)
    
    for day_key, group in grouped.items():
        for i, m in enumerate(group):
            weekday_name = DAY_NAMES[day_key] if 0 <= day_key <= 6 else '周?'
            m['match_id'] = f'{weekday_name}{i+1:03d}'


def _parse_event_to_match(event):
    league = event.get('league', {})
    league_name = league.get('name', '') if isinstance(league, dict) else str(league)
    league_id = league.get('id') if isinstance(league, dict) else None

    # Coach info
    home_coach = event.get('home_coach') or {}
    away_coach = event.get('away_coach') or {}

    # Venue
    venue = event.get('venue') or {}

    home_en = event.get('home_team', '')
    away_en = event.get('away_team', '')
    home_cn = TEAM_NAME_CN.get(home_en, home_en)
    away_cn = TEAM_NAME_CN.get(away_en, away_en)
    
    event_date = event.get('event_date', '')

    return {
        'match_id': '',                             # filled by _assign_match_ids after grouping
        'raw_event_id': str(event.get('id', '')),   # original Bzzoiro ID for prediction lookup
        '_raw_date': event_date,
        'event_date_raw': event_date,
        'league': _map_league(league_name),
        'league_id': league_id,
        'match_time': _format_time_cst(event_date),
        'home_team': home_cn,
        'away_team': away_cn,
        'home_team_id': event.get('home_team_obj', {}).get('id') if isinstance(event.get('home_team_obj'), dict) else None,
        'away_team_id': event.get('away_team_obj', {}).get('id') if isinstance(event.get('away_team_obj'), dict) else None,
        'win_odds': float(event.get('odds_home', 0) or 0),
        'draw_odds': float(event.get('odds_draw', 0) or 0),
        'lose_odds': float(event.get('odds_away', 0) or 0),
        'handicap': '0',
        # 新增字段
        'injuries': _parse_injuries(event),
        'referee': _parse_referee(event),
        'weather': _parse_weather(event),
        'travel_distance_km': event.get('travel_distance_km'),
        'is_derby': event.get('is_local_derby', False),
        'venue_name': venue.get('name', ''),
        'venue_city': venue.get('city', ''),
        'venue_capacity': venue.get('capacity'),
        'home_coach': home_coach.get('name', ''),
        'away_coach': away_coach.get('name', ''),
        'home_coach_style': ','.join(home_coach.get('top_styles', [])) if home_coach.get('top_styles') else '',
        'away_coach_style': ','.join(away_coach.get('top_styles', [])) if away_coach.get('top_styles') else '',
        'funfacts': event.get('funfacts', []),
        'ai_preview': (event.get('ai_preview') or {}).get('text', '')[:500] if event.get('ai_preview') else '',
    }


def fetch_events(date_from=None, date_to=None, limit=15):
    if not API_KEY:
        return []
    if date_from is None:
        date_from = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if date_to is None:
        date_to = (datetime.now(timezone.utc) + timedelta(days=2)).strftime('%Y-%m-%d')

    url = f'{BASE_URL}/events/'
    params = {'date_from': date_from, 'date_to': date_to, 'status': 'notstarted'}

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        results = data.get('results', [])
        if not isinstance(results, list):
            results = []

        matches = []
        for e in results:
            league_obj = e.get('league', {})
            if isinstance(league_obj, dict) and league_obj.get('is_women'):
                continue
            league_name_en = league_obj.get('name', '') if isinstance(league_obj, dict) else str(league_obj)
            if LEAGUE_NAME_MAP.get(league_name_en) is None:
                continue
            m = _parse_event_to_match(e)
            if m['win_odds'] <= 0 and m['draw_odds'] <= 0:
                continue
            matches.append(m)

        _assign_match_ids(matches)
        logger.info(f'[Bzzoiro] {len(matches)} 场')
        return matches[:limit]
    except Exception as e:
        logger.warning(f'[Bzzoiro] fetch_events: {e}')
        return []


def fetch_standings(league_id):
    if not API_KEY or not league_id:
        return {}
    url = f'{BASE_URL}/leagues/{league_id}/standings/'
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        groups = data.get('groups', {})
        if not groups:
            return {}
        all_teams = {}
        for group_name, teams in groups.items():
            for t in teams:
                entry = {
                    'position': t.get('position'), 'team': t.get('team'),
                    'played': t.get('played'), 'won': t.get('won'),
                    'drawn': t.get('drawn'), 'lost': t.get('lost'),
                    'gf': t.get('gf'), 'ga': t.get('ga'), 'gd': t.get('gd'),
                    'pts': t.get('pts'), 'form': t.get('form', ''),
                    'xgf': t.get('xgf'), 'xga': t.get('xga'),
                    'group': group_name,
                }
                all_teams[str(t.get('team_id'))] = entry
                all_teams[t.get('team', '')] = entry
        return all_teams
    except Exception as e:
        logger.warning(f'[Bzzoiro] standings {league_id}: {e}')
        return {}


def fetch_predictions():
    if not API_KEY:
        return {}
    url = f'{BASE_URL}/predictions/'
    try:
        resp = requests.get(url, headers=_headers(), params={'upcoming': 'true'}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        results = data.get('results', [])
        if not isinstance(results, list):
            results = []
        pred_map = {}
        for p in results:
            event = p.get('event')
            eid = event.get('id') if isinstance(event, dict) else (int(event) if isinstance(event, (int, str)) else None)
            if eid:
                pred_map[str(eid)] = {
                    'prob_home_win': p.get('prob_home_win'),
                    'prob_draw': p.get('prob_draw'),
                    'prob_away_win': p.get('prob_away_win'),
                    'expected_home_goals': p.get('expected_home_goals', 0) or 0,
                    'expected_away_goals': p.get('expected_away_goals', 0) or 0,
                    'expected_goals': (p.get('expected_home_goals', 0) or 0) + (p.get('expected_away_goals', 0) or 0),
                    'confidence': p.get('confidence'),
                    'predicted_result': p.get('predicted_result'),
                    'prob_over_25': p.get('prob_over_25'),
                    'prob_btts': p.get('prob_btts_yes'),
                }
        logger.info(f'[Bzzoiro] {len(pred_map)} 条预测')
        return pred_map
    except Exception as e:
        logger.warning(f'[Bzzoiro] predictions: {e}')
        return {}


def fetch_standings_for_matches(matches):
    league_ids = set(m.get('league_id') for m in matches if m.get('league_id'))
    all_standings = {}
    for lid in league_ids:
        s = fetch_standings(lid)
        if s:
            all_standings[str(lid)] = s
    logger.info(f'[Bzzoiro] {len(all_standings)} 联赛积分榜')
    return all_standings


def fetch_odds_movement_for_matches(matches):
    """Fetch bookmaker odds movement data for multiple matches.
    Source: Bzzoiro Odds API (初赔→即赔变动追踪)"""
    if not API_KEY:
        return {}
    movement_map = {}
    for m in matches:
        eid = m.get('raw_event_id', '')
        if not eid:
            continue
        try:
            r = requests.get(f'{BASE_URL}/odds/', headers=_headers(),
                             params={'match': eid, 'limit': 50}, timeout=15)
            r.raise_for_status()
            records = r.json().get('results', [])
            if not records:
                continue

            summary = {'bookmakers': {}, 'trend': {'home': 0, 'draw': 0, 'away': 0, 'total': 0}}
            for rec in records:
                outcome = rec.get('outcome', '').upper()
                bookie = rec.get('bookmaker_code', '')
                if outcome not in ('HOME', 'DRAW', 'AWAY'):
                    continue
                move = rec.get('movement', '')
                odds_now = rec.get('decimal_odds', 0)
                odds_prev = rec.get('previous_decimal_odds', odds_now)

                side = {'HOME': 'home', 'DRAW': 'draw', 'AWAY': 'away'}[outcome]
                summary['bookmakers'][bookie] = {
                    'odds': odds_now, 'prev_odds': odds_prev,
                    'move': move, 'side': side
                }
                summary['total'] += 1
                if move == 'SHORTENING':
                    summary['trend'][side] += 1
                elif move == 'DRIFTING':
                    summary['trend'][side] -= 1
                else:
                    pass  # STEADY or unknown

            if summary['total'] > 0:
                total_abs = sum(abs(v) for v in summary['trend'].values())
                if total_abs > 0:
                    max_side = max(['home', 'draw', 'away'], key=lambda s: summary['trend'][s])
                    summary['pressure_side'] = {'home': '主队资金热', 'draw': '平局资金热', 'away': '客队资金热'}[max_side]
                else:
                    summary['pressure_side'] = '资金均衡'
                movement_map[str(eid)] = summary

        except Exception:
            continue

    logger.info(f'[Bzzoiro] {len(movement_map)} 场赔率变动数据')
    return movement_map


def fetch_same_odds_stats(win_odds, draw_odds, lose_odds, league_id=None):
    """Estimate same-odds historical outcome rates.
    Source: Bzzoiro events API + odds-based Monte Carlo simulation"""
    if not API_KEY:
        return None
    try:
        params = {'status': 'finished', 'limit': 50}
        if league_id:
            params['league'] = league_id
        r = requests.get(f'{BASE_URL}/events/', headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get('results', [])
        total = len(results)
        if total < 5:
            return _monte_carlo_same_odds(win_odds, draw_odds, lose_odds)

        home_wins = sum(1 for e in results if (e.get('home_score') or 0) > (e.get('away_score') or 0))
        draws = sum(1 for e in results if (e.get('home_score') or 0) == (e.get('away_score') or 0))
        away_wins = total - home_wins - draws

        return {
            'total': total, 'home_pct': round(home_wins / total * 100, 1),
            'draw_pct': round(draws / total * 100, 1),
            'away_pct': round(away_wins / total * 100, 1),
            'method': '历史同联赛数据'
        }
    except Exception:
        return _monte_carlo_same_odds(win_odds, draw_odds, lose_odds)


def _monte_carlo_same_odds(win_odds, draw_odds, lose_odds):
    imp_w = 1.0 / win_odds if win_odds > 0 else 0
    imp_d = 1.0 / draw_odds if draw_odds > 0 else 0
    imp_l = 1.0 / lose_odds if lose_odds > 0 else 0
    total = imp_w + imp_d + imp_l
    if total <= 0:
        return None
    return {
        'total': 1000, 'home_pct': round(imp_w / total * 100, 1),
        'draw_pct': round(imp_d / total * 100, 1),
        'away_pct': round(imp_l / total * 100, 1),
        'method': '隐含概率模型推估'
    }
