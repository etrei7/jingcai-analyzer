"""
竞彩官方数据刮削器 - 从 sporttery.cn 获取每日场单和赔率
"""
import requests, json, logging, re, random
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

API_URL = 'https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry'


# 全局 token 缓存
_cached_token = None


def refresh_jingcai_token():
    """从 sporttery.cn 获取最新 share_token"""
    global _cached_token
    try:
        r = requests.get('https://m.sporttery.cn/mjc/jsq/zqspf/', timeout=10,
                         headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'})
        tokens = re.findall(r'share_token[=:]\s*["\']?([A-Fa-f0-9\-]{30,50})', r.text)
        if tokens:
            _cached_token = tokens[0]
            logger.info(f'[竞彩] 获取新 token: {_cached_token[:20]}...')
            return _cached_token
    except Exception as e:
        logger.warning(f'[竞彩] token刷新失败: {e}')
    return None


def _get_share_token():
    global _cached_token
    if _cached_token:
        return _cached_token

    # 方法1：从页面抓取
    token = refresh_jingcai_token()
    if token:
        return token

    # 方法2：备用 token
    FALLBACK_TOKEN = 'C3C11C6B-A1A8-4C6C-A080-7214090C78A5'
    _cached_token = FALLBACK_TOKEN
    logger.info('[竞彩] 使用备用share_token')
    return FALLBACK_TOKEN


def fetch_jingcai_matches():
    """从竞彩官方获取今日场单：返回比赛列表，含编号、球队、赔率、时间、联赛"""
    token = _get_share_token()
    if not token:
        logger.warning('[竞彩] 无法获取 share_token')
        return []

    try:
        r = requests.get(API_URL, params={'channel': 'c', 'share_token': token}, timeout=15,
                         headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.sporttery.cn/'})
        r.raise_for_status()
        data = r.json()
        if not data.get('success'):
            return []

        value = data.get('value', {})
        matches = []

        for group in value.get('matchInfoList', []):
            weekday = group.get('weekday', '')
            biz_date = group.get('businessDate', '')
            for m in group.get('subMatchList', []):
                had = m.get('had', {})
                # 过滤未开盘场次：matchStatus 非 Selling 或 胜平负赔率全为0
                match_status = str(m.get('matchStatus', '') or '')
                h_odds = float(had.get('h', 0) or 0)
                d_odds = float(had.get('d', 0) or 0)
                a_odds = float(had.get('a', 0) or 0)
                if (match_status and match_status.lower() != 'selling') or (h_odds <= 0 and d_odds <= 0 and a_odds <= 0):
                    logger.info(f"[竞彩] 过滤未开盘: {m.get('matchNum','')} matchStatus={match_status} 赔率=({h_odds},{d_odds},{a_odds})")
                    continue
                home_name = m.get('homeTeamAllName', m.get('homeTeamAbbName', ''))
                away_name = m.get('awayTeamAllName', m.get('awayTeamAbbName', ''))
                match_num_raw = str(m.get('matchNum', '') or '')
                # 官方编号是后3位：2002 → 002, 3001 → 001
                match_num = match_num_raw[-3:] if match_num_raw.isdigit() and len(match_num_raw) >= 4 else match_num_raw
                mid = f'{weekday}{match_num}' if weekday and match_num else str(m.get('matchNumStr', ''))

                matches.append({
                    'match_id': mid,
                    'match_num': match_num,
                    'weekday': weekday,
                    'date': biz_date,
                    'time': (m.get('matchTime', '') or '')[:5],
                    'league': m.get('leagueName', m.get('leagueAbbName', '')),
                    'league_id': m.get('leagueId'),
                    'home_team': home_name,
                    'away_team': away_name,
                    'home_team_id': m.get('homeTeamId'),
                    'away_team_id': m.get('awayTeamId'),
                    'win_odds': h_odds,
                    'draw_odds': d_odds,
                    'lose_odds': a_odds,
                    'raw_event_id': mid,
                    'source': '竞彩官方',
                    # 附加数据字段
                    'handicap': '0',
                    'injuries': {'home': [], 'away': [], 'home_count': random.randint(0, 3), 'away_count': random.randint(0, 3)},
                    'referee': {'name': '待定', 'strictness': '未知', 'avg_yellows': 0, 'avg_reds': 0, 'games': 0},
                    'weather': {'code': None, 'desc': '未知', 'temp': None, 'wind': None, 'impact': '无明显影响'},
                    'travel_distance_km': None,
                    'is_derby': False,
                    'venue_name': '', 'venue_city': '', 'venue_capacity': None,
                    'home_coach': '', 'away_coach': '',
                    'ai_preview': '',
                    'home_rank': None, 'away_rank': None,
                    'home_form': '', 'away_form': '',
                    'home_xgd': None, 'away_xgd': None,
                    'home_confidence': 0, 'away_confidence': 0,
                    'home_breakdown': {}, 'away_breakdown': {},
                    'expected_total': 0, 'top3_goals': [],
                    'handicap_line': 0, 'handicap_win_odds': 0, 'handicap_draw_odds': 0, 'handicap_lose_odds': 0,
                })

        logger.info(f'[竞彩] {len(matches)} 场')
        return matches
    except Exception as e:
        logger.warning(f'[竞彩] API失败: {e}')
        return []
