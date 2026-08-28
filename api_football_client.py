"""API-Football 鏁版嵁瀹㈡埛绔細涓哄垎鏋愭ā鍨嬭ˉ鍏呭熀鏈潰鏁版嵁锛堢Н鍒嗘鎺掑悕/鐘舵€?涓诲鍦?鍑€鑳滅悆锛夈€?鍏嶈垂鐗?100 娆?澶┿€傝璁★細
- 闃熷悕/鑱旇禌 ID 鐢ㄥ唴瀛樼紦瀛橈紝閬垮厤閲嶅璇锋眰
- standings 鎸夎仈璧涙暣姒滄媺鍙栦竴娆★紝渚涜鑱旇禌鎵€鏈夋瘮璧涘鐢紙鐪侀搴︼級
- 鎸?Bzzoiro 鍘熷鑻辨枃闃熷悕鍖归厤 API-Football 鐨?team
"""
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

API_KEY = os.environ.get('API_FOOTBALL_KEY', '')
BASE = 'https://v3.football.api-sports.io'
TIMEOUT = 6

# 鑱旇禌鍚?涓枃) 鈫?API-Football league id锛堜富娴佺珵褰╄仈璧涳紝瀹樻柟绋冲畾ID锛?LEAGUE_ID_MAP = {
    # 涓绘祦浜斿ぇ
    '鑻辫秴': 39, '瑗跨敳': 140, '寰风敳': 78, '鎰忕敳': 135, '娉曠敳': 61,
    # 娆＄骇
    '鑻卞啝': 40, '瑗夸箼': 141, '寰蜂箼': 79, '鎰忎箼': 136, '娉曚箼': 60,
    # 娆ф床涓绘祦
    '鑽风敳': 88, '钁¤秴': 94, '鍦熻秴': 203, '姣旂敳': 144, '鑻忚秴': 179,
    '鐟炶秴': 113, '鎸秴': 103, '涓硅秴': 119, '娉㈢敳': 106,
    '濂ョ敳': 204, '鐟炲＋瓒?: 96, '甯岃秴': 197,
    # 缇庢床
    '宸寸敳': 71, '闃跨敳': 128, '澧ㄨタ瓒?: 262, '缇庤亴鑱?: 253,
    # 浜氭床/澶ф磱娲?    '鏃ヨ亴': 233, '闊㎏鑱?: 292, '涓秴': 169, '婢宠秴': 188,
    # 鏉禌锛堟鎴橈級
    '娆у啝': 2, '娆ц仈': 3, '娆у崗鑱?: 3, '瑙ｆ斁鑰呮澂': 12,
}

# 闃熷悕(涓枃) 鈫?闃熷悕(鑻辨枃)锛岀敤浜庡弽鏌ワ紱浼樺厛鐢ㄦ瘮璧涜嚜甯︾殑 home_team_en
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
    """鎷夊彇 API-Football銆傞檺娴?rateLimit)鏃跺揩閫熼檷绾ц繑鍥炵┖锛屼笉鎷栨參涓绘祦绋嬨€?""
    try:
        resp = requests.get(BASE + path, headers=_headers(), params=params, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        errors = data.get('errors') or {}
        if errors:
            # 闄愭祦鏍囪锛氳缃喎鍗达紝鍚庣画璇锋眰鐩存帴璺宠繃
            if any('rateLimit' in str(e).lower() or 'limit' in str(e).lower() for e in errors.values() if isinstance(e, str)):
                _set_rate_limited(60)
                logger.warning('[apifb] rate-limited, cooldown 60s')
            else:
                logger.warning('[apifb] errors: %s', errors)
            return None
        return data.get('response') or []
    except Exception as e:
        logger.warning('[apifb] %s failed: %s', path, e)
        return []


_RATE_LIMIT_UNTIL = 0.0
import time as _time


def _set_rate_limited(seconds):
    global _RATE_LIMIT_UNTIL
    _RATE_LIMIT_UNTIL = _time.time() + seconds


def _rate_limited():
    return _time.time() < _RATE_LIMIT_UNTIL


def _season_for(league_id):
    """纭畾褰撳墠鍙敤璧涘锛氭湰绔欑敤2024璧涘锛圓PI-Football 鍏嶈垂鐗堢ǔ瀹氭彁渚涳級銆?""
    return 2024


def fetch_league_standings(league_cn):
    """鎸夎仈璧涗腑鏂囧悕鎷夊彇鏁存 teams: {name_en: row}銆傛暣姒滅紦瀛橈紝渚涘悓鑱旇禌澶嶇敤銆?""
    if not _enabled():
        return {}
    if _rate_limited():
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
    logger.info('[apifb] standings league=%s(%s) 鈫?%d闃?, league_cn, lid, len(by_name))
    return by_name


def match_row(match):
    """浠庢瘮璧?鍚?home_team/away_team/home_team_en) 鎻愬彇涓ら槦 standings 琛屻€?    浼樺厛鐢ㄨ嫳鏂囬槦鍚嶅尮閰?API-Football锛涢€€鍖栫敤涓枃鍚嶅弽鏌ャ€?    """
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
            # 閮ㄥ垎鍖归厤锛堥槦鍚嶅寘鍚叧绯伙級
            for k, v in by_name.items():
                if cand and (cand in k or k in cand):
                    return v
        return {}

    hrow = find(match.get('home_team_en'), match.get('home_team'))
    arow = find(match.get('away_team_en'), match.get('away_team'))
    return hrow, arow


def enrich_form_data(hrow, arow):
    """浠?standings 琛屾彁鍙栧熀鏈潰瀛楁锛屼緵 _fundamental_pick 澧炲己銆傝繑鍥?dict 鎴?None銆?""
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
