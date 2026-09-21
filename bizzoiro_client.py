import os, logging, requests, math, time, threading
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
        'home_team_en': home_en,
        'away_team_en': away_en,
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


_enrich_events_cache = {'ts': 0.0, 'list': []}
_enrich_events_lock = threading.Lock()
_ENRICH_TTL = 600  # 富化用赛事列表缓存 10 分钟（前端每 30s 轮询，避免重复拉取）


def _cached_bz_events(limit=40):
    """带 TTL 的 Bzzoiro 赛事列表缓存（供竞彩富化使用）。"""
    with _enrich_events_lock:
        if _enrich_events_cache['list'] and (time.time() - _enrich_events_cache['ts']) < _ENRICH_TTL:
            return _enrich_events_cache['list']
    lst = fetch_events(date_from=None, date_to=None, limit=limit)
    with _enrich_events_lock:
        if lst:
            _enrich_events_cache['list'] = lst
            _enrich_events_cache['ts'] = time.time()
        return _enrich_events_cache['list']


def enrich_jingcai_matches(matches):
    """用 Bzzoiro 数据富化竞彩场次：伤病/裁判/天气/球队状态（不改动竞彩编号与赔率）。
    优化：先按主队名建立哈希索引，将匹配从 O(N×M) 降为近 O(N)。"""
    if not API_KEY or not matches:
        return matches, 0
    try:
        bz_list = _cached_bz_events(40)
        if not bz_list:
            return matches, 0

        # 建立 (主队名->事件) 与 (客队名->事件) 的索引，供快速前缀匹配
        def _norm(s):
            return (s or '').replace(' ', '').lower()

        bz_by_home = {}
        bz_by_away = {}
        for b in bz_list:
            h = _norm(b.get('home_team'))
            a = _norm(b.get('away_team'))
            if h:
                bz_by_home.setdefault(h, []).append(b)
            if a:
                bz_by_away.setdefault(a, []).append(b)

        # 竞彩队名可能是 Bzzoiro 名的子串或超集，做一次小规模候选筛选
        def _candidates(mh, ma):
            cands = []
            seen = set()
            for store in (bz_by_home, bz_by_away):
                for nm in (mh, ma):
                    if not nm:
                        continue
                    for cand in store.get(nm, []):
                        if id(cand) not in seen:
                            seen.add(id(cand))
                            cands.append(cand)
                    for key, lst in store.items():
                        if nm != key and (nm in key or key in nm):
                            for cand in lst:
                                if id(cand) not in seen:
                                    seen.add(id(cand))
                                    cands.append(cand)
            return cands

        matched = 0
        for m in matches:
            mh = _norm(m.get('home_team'))
            ma = _norm(m.get('away_team'))
            cands = _candidates(mh, ma)
            best = None
            best_score = 0
            for b in cands:
                bh = _norm(b.get('home_team'))
                ba = _norm(b.get('away_team'))
                score = 0
                if mh and (mh in bh or bh in mh):
                    score += 2
                if ma and (ma in ba or ba in ma):
                    score += 2
                if not score:
                    continue
                # 时间接近加成
                mt = (m.get('time') or m.get('match_time') or '')[:2]
                bt = (b.get('match_time') or '')[:2]
                if mt and bt and mt == bt:
                    score += 1
                if score > best_score:
                    best_score = score
                    best = b
            # 准确性优先：要求「主客队双队名」均匹配（满分4）才认定，
            # 避免仅凭单队名子串+同小时(3分)错配到无关比赛，污染伤停/排名/国际盘。
            if best and best_score >= 4:
                m['bz_event_id'] = str(best.get('raw_event_id', '') or '')
                if best.get('ai_preview'):
                    m['ai_preview'] = best.get('ai_preview')
                m['injuries'] = best.get('injuries', {'home': [], 'away': [], 'home_count': 0, 'away_count': 0})
                m['referee'] = best.get('referee', {}) or {'name': '待定', 'strictness': '未知', 'avg_yellows': 0, 'avg_reds': 0, 'games': 0}
                m['weather'] = best.get('weather', {}) or {'code': None, 'desc': '未知', 'temp': None, 'wind': None, 'impact': '无明显影响'}
                m['home_form'] = best.get('home_form', '') or m.get('home_form', '')
                m['away_form'] = best.get('away_form', '') or m.get('away_form', '')
                m['home_rank'] = best.get('home_rank') or m.get('home_rank')
                m['away_rank'] = best.get('away_rank') or m.get('away_rank')
                m['home_xgd'] = best.get('home_xgd') or m.get('home_xgd')
                m['away_xgd'] = best.get('away_xgd') or m.get('away_xgd')
                m['home_coach'] = best.get('home_coach', '') or m.get('home_coach', '')
                m['away_coach'] = best.get('away_coach', '') or m.get('away_coach', '')
                m['venue_name'] = best.get('venue_name', '') or m.get('venue_name', '')
                matched += 1
        logger.info(f'[Bzzoiro] 富化竞彩 {matched}/{len(matches)} 场')
        return matches, matched
    except Exception as e:
        logger.warning(f'[Bzzoiro] enrich_jingcai_matches: {e}')
        return matches, 0


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
def fetch_finished_events(date_from=None, date_to=None, limit=400):
    """拉取已完赛事件（含比分），供历史预测/回测按队名匹配结算。默认近 7 天。"""
    if not API_KEY:
        return []
    if date_from is None:
        date_from = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
    if date_to is None:
        date_to = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    url = f'{BASE_URL}/events/'
    params = {'date_from': date_from, 'date_to': date_to, 'status': 'finished'}
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=20)
        resp.raise_for_status()
        results = resp.json().get('results', [])
        if not isinstance(results, list):
            results = []
        logger.info(f'[Bzzoiro] {len(results)} 场已完赛')
        return results[:limit]
    except Exception as e:
        logger.warning(f'[Bzzoiro] fetch_finished_events: {e}')
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


_pred_cache = {'ts': 0, 'data': {}}
_PRED_TTL = 300


def fetch_predictions():
    if not API_KEY:
        return {}
    if _pred_cache['data'] and (time.time() - _pred_cache['ts']) < _PRED_TTL:
        return _pred_cache['data']
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
        if pred_map:
            _pred_cache['ts'] = time.time()
            _pred_cache['data'] = pred_map
        return pred_map
    except Exception as e:
        logger.warning(f'[Bzzoiro] predictions: {e}')
        return _pred_cache['data'] or {}


def fetch_standings_for_matches(matches):
    league_ids = set(m.get('league_id') for m in matches if m.get('league_id'))
    all_standings = {}
    for lid in league_ids:
        s = fetch_standings(lid)
        if s:
            all_standings[str(lid)] = s
    logger.info(f'[Bzzoiro] {len(all_standings)} 联赛积分榜')
    return all_standings


def fetch_intl_odds_for_matches(matches, max_matches=6):
    """拉取国际博彩公司 1x2 赔率，用于与竞彩官方赔率对比（找价值）。
    Source: Bzzoiro Odds API。仅对 Bzzoiro 来源场次（raw_event_id 为事件ID）有效；
    返回 {event_id: {'bookmakers': [...], 'best': {...}, 'count': N}}。
    注意：API 单次硬上限 50 条，故每场只取一次并聚合「每公司每结果的最新报价」。"""
    if not API_KEY:
        return {}
    _SIDE = {'HOME': 'home', 'DRAW': 'draw', 'AWAY': 'away'}
    intl = {}
    done = 0
    for m in matches:
        if done >= max_matches:
            break
        eid = str(m.get('bz_event_id') or m.get('raw_event_id', '') or '')
        if not eid.isdigit():
            continue
        try:
            r = requests.get(f'{BASE_URL}/odds/', headers=_headers(),
                             params={'match': eid, 'market': '1x2', 'limit': 50}, timeout=12)
            r.raise_for_status()
            records = r.json().get('results', [])
        except Exception:
            continue
        done += 1
        if not records:
            continue

        per = {}
        for rec in records:
            side = _SIDE.get(str(rec.get('outcome', '')).upper())
            if not side:
                continue
            code = rec.get('bookmaker_code', '')
            if not code:
                continue
            ts = rec.get('updated_at', '') or ''
            d = per.setdefault(code, {'name': rec.get('bookmaker', ''),
                                      'sides': {}, 'ts': {}})
            if side not in d['ts'] or ts >= d['ts'][side]:
                d['sides'][side] = {
                    'odds': float(rec.get('decimal_odds', 0) or 0),
                    'prev': float(rec.get('previous_decimal_odds', 0) or 0),
                    'move': rec.get('movement', '') or ''
                }
                d['ts'][side] = ts

        bookmakers = []
        for code, d in per.items():
            if len(d['sides']) < 2:
                continue
            bookmakers.append({
                'code': code, 'name': d['name'],
                'home': d['sides'].get('home', {}),
                'draw': d['sides'].get('draw', {}),
                'away': d['sides'].get('away', {})
            })
        if not bookmakers:
            continue

        def _best(k):
            vals = [b[k]['odds'] for b in bookmakers if b.get(k, {}).get('odds')]
            return max(vals) if vals else 0

        # 汇总多公司赔率变动趋势（正=下降/资金流入，负=上升）
        trend = {'home': 0, 'draw': 0, 'away': 0, 'total': 0}
        for bm in bookmakers:
            for side in ('home', 'draw', 'away'):
                mv = (bm.get(side) or {}).get('move') or ''
                if mv == 'SHORTENING':
                    trend[side] += 1
                    trend['total'] += 1
                elif mv == 'DRIFTING':
                    trend[side] -= 1
                    trend['total'] += 1
        if trend['total'] > 0 and any(trend[s] for s in ('home', 'draw', 'away')):
            _mx = max(('home', 'draw', 'away'), key=lambda s: trend[s])
            pressure_side = {'home': '主队资金热', 'draw': '平局资金热', 'away': '客队资金热'}[_mx]
        else:
            pressure_side = '资金均衡'

        intl[eid] = {
            'bookmakers': bookmakers,
            'best': {'home': _best('home'), 'draw': _best('draw'), 'away': _best('away')},
            'movement': {'trend': trend, 'pressure_side': pressure_side},
            'count': len(bookmakers)
        }

    logger.info(f'[Bzzoiro] {len(intl)} 场国际赔率对比')
    return intl