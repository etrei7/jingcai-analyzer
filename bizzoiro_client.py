import os
import logging
import requests
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api')
API_KEY = os.environ.get('BZZOIRO_API_KEY', '')

LEAGUE_NAME_MAP = {
    'Premier League': '英超',
    'La Liga': '西甲',
    'Bundesliga': '德甲',
    'Serie A': '意甲',
    'Ligue 1': '法甲',
    'Chinese Super League': '中超',
    'J1 League': '日职',
    'K League 1': '韩K联',
    'A-League': '澳超',
    'Eliteserien': '挪超',
    'Eredivisie': '荷甲',
    'Primeira Liga': '葡超',
    'Super Lig': '土超',
    'Brasileirão Série A': '巴甲',
    'Brasileirão Serie A': '巴甲',
    'Brasileirão': '巴甲',
    'Major League Soccer': 'MLS',
    'MLS': 'MLS',
    'Liga Profesional de Fútbol': '阿甲',
    'Categoría Primera A': '哥伦甲',
    'Allsvenskan': '瑞典超',
    'Danish Superliga': '丹超',
    'Ekstraklasa': '波甲',
    'Veikkausliiga': '芬超',
    'Liga MX Apertura': '墨西超',
    'Liga MX': '墨西超',
    'Superliga': '罗甲',
    'Scottish Premiership': '苏超',
    'Pro League': '比甲',
    'Austrian Bundesliga': '奥甲',
    'Swiss Super League': '瑞士超',
    'Greek Super League': '希超',
    'Czech Liga': '捷甲',
    'Croatian HNL': '克甲',
    'Slovenian PrvaLiga': '斯甲',
    'Ukrainian Premier League': '乌超',
    'Russian Premier League': '俄超',
    'Saudi Pro League': '沙超',
    'Qatar Stars League': '卡联',
    'UAE Pro League': '阿联超',
    'J2 League': '日乙',
    'K League 2': '韩K2',
    'Championship': '英冠',
    'Serie B': '意乙',
    'La Liga 2': '西乙',
    '2. Bundesliga': '德乙',
    'Ligue 2': '法乙',
    'Carabao Cup': '英联杯',
    'FA Cup': '足总杯',
    'Copa del Rey': '国王杯',
    'Coppa Italia': '意杯',
    'DFB Pokal': '德国杯',
    'Copa do Brasil': '巴西杯',
    'Champions League': '欧冠',
    'Europa League': '欧联',
    'Conference League': '欧协联',
    'Club Friendlies': '友谊赛',
    'NPL Queensland': '澳NPL',
    'USL Championship': '美冠',
    'Parva Liga': '保甲',
    'Super League': '瑞士超',
    'Copa Colombia': '哥伦杯',
    'Puchar Polski': '波兰杯',
    'Liga 3': '葡甲',
    'Liga Portugal Betclic': '葡超',
    'NWSL': None,  # 女子联赛，跳过
}


def _headers():
    return {'Authorization': f'Token {API_KEY}'} if API_KEY else {}


def _map_league(name_en):
    return LEAGUE_NAME_MAP.get(name_en, name_en)


def _format_time(event_date_str):
    """将 Bzzoiro 时间(+04:00) 转为北京时间(HH:MM)"""
    try:
        dt = datetime.fromisoformat(event_date_str)
        dt_beijing = dt + timedelta(hours=4)
        return dt_beijing.strftime('%H:%M')
    except Exception:
        return event_date_str


def _parse_event_to_match(event):
    home_team = event.get('home_team', '')
    away_team = event.get('away_team', '')
    league = event.get('league', {})
    league_name = league.get('name', '') if isinstance(league, dict) else str(league)

    return {
        'match_id': str(event.get('id', '')),
        'league': _map_league(league_name),
        'match_time': _format_time(event.get('event_date', '')),
        'home_team': home_team,
        'away_team': away_team,
        'win_odds': float(event.get('odds_home', 0) or 0),
        'draw_odds': float(event.get('odds_draw', 0) or 0),
        'lose_odds': float(event.get('odds_away', 0) or 0),
        'handicap': '0',
    }


def fetch_events(date_from=None, date_to=None, limit=15):
    """从 Bzzoiro API 获取赛事列表"""
    if not API_KEY:
        logger.info('[Bzzoiro] 未设置 API Key，跳过真实数据请求')
        return []

    if date_from is None:
        date_from = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if date_to is None:
        date_to = (datetime.now(timezone.utc) + timedelta(days=2)).strftime('%Y-%m-%d')

    url = f'{BASE_URL}/events/'
    params = {
        'date_from': date_from,
        'date_to': date_to,
        'status': 'notstarted',
    }

    try:
        logger.info(f'[Bzzoiro] 请求赛事: {url} params={params}')
        resp = requests.get(url, headers=_headers(), params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = data.get('results', [])
        if not isinstance(results, list):
            results = []

        matches = []
        for e in results:
            league_obj = e.get('league', {})
            if isinstance(league_obj, dict):
                if league_obj.get('is_women'):
                    continue
                league_name_en = league_obj.get('name', '')
            else:
                league_name_en = str(league_obj)

            # 跳过未映射的女子联赛
            translated = LEAGUE_NAME_MAP.get(league_name_en)
            if translated is None:
                continue

            m = _parse_event_to_match(e)
            if m['win_odds'] <= 0 and m['draw_odds'] <= 0:
                continue
            matches.append(m)

        matches = matches[:limit]
        logger.info(f'[Bzzoiro] 获取到 {len(matches)} 场有效比赛')
        return matches

    except requests.RequestException as e:
        logger.warning(f'[Bzzoiro] API 请求失败: {e}')
        return []
    except Exception as e:
        logger.error(f'[Bzzoiro] 数据异常: {e}')
        return []
