"""API-Football 数据客户端：为分析模型补充基本面数据（积分榜排名/状态/主客场/净胜球）。
免费版 100 次/天。设计：
- 队名/联赛 ID 用内存缓存，避免重复请求
- standings 按联赛整榜拉取一次，供该联赛所有比赛复用（省额度）
- 按 Bzzoiro 原始英文队名匹配 API-Football 的 team
"""
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

API_KEY = os.environ.get('API_FOOTBALL_KEY', '')
BASE = 'https://v3.football.api-sports.io'
TIMEOUT = 15

# 联赛名(中文) → API-Football league id（主流竞彩联赛）
LEAGUE_ID_MAP = {
    '英超': 39, '西甲': 140, '德甲': 78, '意甲': 135, '法甲': 61,
    '英冠': 40, '西乙': 141, '德乙': 79, '意乙': 136, '法乙': 60,
    '荷甲': 88, '葡超': 94, '土超': 203, '比甲': 144, '苏超': 179,
    '巴甲': 71, '阿甲': 128, '墨西超': 262, '美职联': 253,
    '日职': 233, '韩K联': 292, '中超': 169, '澳超': 188,
    '瑞超': 113, '挪超': 103, '丹超': 119, '波兰超': 106,
    '欧冠': 2, '欧联': 3, '欧协联': 3, '解放者杯': 12,
}

# 队名(中文) → 队名(英文)，用于反查；优先用比赛自带的 home_team_en
TEAM_CN_TO_EN = {}


def _enabled():
    return bool(API_KEY)


def _headers():
    return {'x-apisports-key': API_KEY}


class _Cache:
    def __init__(self):
        self.standings = {}   # league_id -> {team_name_en: row}
        self.team_id = {}     # team_name_en -> id
        self.ttl_map = {}     # key -> timestamp

    def get(self, k):
        v = self.ttl_map.get(k)
        if v and (datetime.now().timestamp() - v) < 3600:
            return self._data.get(k)
        return None

    def set(self, k, val):
        if not hasattr(self, '_data'):
            self._data = {}
        self._data[k] = val
        self.ttl_map[k] = datetime.now().timestamp()


_CACHE = _Cache()


def _fetch(path, params):
    try:
        resp = requests.get(BASE + path, headers=_headers(), params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get('errors'):
            logger.warning('[apifb] errors: %s', data['errors'])
            return None
        return data.get('response') or []
    except Exception as e:
        logger.warning('[apifb] %s failed: %s', path, e)
        return []


def _season_for(league_id):
    """确定当前可用赛季：本站用2024赛季（API-Football 免费版稳定提供）。"""
    return 2024


def fetch_league_standings(league_cn):
    """按联赛中文名拉取整榜 teams: {name_en: row}。整榜缓存，供同联赛复用。"""
    if not _enabled():
        return {}
    lid = LEAGUE_ID_MAP.get(league_cn)
    if not lid:
        return {}
    season = _season_for(lid)
    key = f'std_{lid}_{season}'
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    rows = _fetch('/standings', {'league': lid, 'season': season})
    by_name = {}
    if rows:
        standings = rows[0].get('league', {}).get('standings', [])
        for group in standings:
            for row in group:
                team = row.get('team', {})
                tname = team.get('name') or team.get('short_name')
                if tname:
                    by_name[tname] = row
                    by_name[tname.lower()] = row
    _CACHE.set(key, by_name)
    logger.info('[apifb] standings league=%s(%s) → %d队', league_cn, lid, len(by_name))
    return by_name


def match_row(match):
    """从比赛(含 home_team/away_team/home_team_en) 提取两队 standings 行。
    优先用英文队名匹配 API-Football；退化用中文名反查。
    """
    league = match.get('league', '')
    by_name = fetch_league_standings(league)
    if not by_name:
        return {}, {}

    def find(en, cn):
        candidates = [en, (en or '').replace('FC ', '').strip(), cn]
        for cand in candidates:
            if not cand:
                continue
            if cand in by_name:
                return by_name[cand]
            if cand.lower() in by_name:
                return by_name[cand.lower()]
            # 部分匹配（队名包含关系）
            for k, v in by_name.items():
                if cand and (cand in k or k in cand):
                    return v
        return {}

    hrow = find(match.get('home_team_en'), match.get('home_team'))
    arow = find(match.get('away_team_en'), match.get('away_team'))
    return hrow, arow


def enrich_form_data(hrow, arow):
    """从 standings 行提取基本面字段，供 _fundamental_pick 增强。返回 dict 或 None。"""
    def extract(row):
        if not row:
            return None
        return {
            'rank': row.get('rank'),
            'points': row.get('points'),
            'goals_diff': row.get('goalsDiff'),
            'form': row.get('form', ''),
            'played': row.get('all', {}).get('played'),
            'home_played': row.get('home', {}).get('played'),
            'home_win': row.get('home', {}).get('win'),
            'home_goals_for': row.get('home', {}).get('goals', {}).get('for'),
            'home_goals_against': row.get('home', {}).get('goals', {}).get('against'),
            'away_played': row.get('away', {}).get('played'),
            'away_win': row.get('away', {}).get('win'),
            'away_goals_for': row.get('away', {}).get('goals', {}).get('for'),
            'away_goals_against': row.get('away', {}).get('goals', {}).get('against'),
        }
    return extract(hrow), extract(arow)
