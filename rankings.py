"""联赛排名模块：从 thesportsdb.com 拉取积分榜，按中英文队名模糊匹配填充排名。
参考：参考网站使用 thesportsdb 免费 API + 中文→英文映射 + Jaccard 模糊匹配。
本模块实现：联赛ID映射、赛季推算、拉榜、队名匹配、缓存、Bzzoiro standings 备选。
"""
import logging
import threading
import time
import re
from datetime import datetime, timedelta, timezone

import requests
from team_names import TEAM_NAME_CN

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# ===== thesportsdb =====
TSD_BASE = 'https://www.thesportsdb.com/api/v1/json/3'
TSD_TIMEOUT = 12
CACHE_TTL = 6 * 3600  # 6 小时

# 中文联赛名 -> thesportsdb leagueId（竞彩常开主流联赛）
CHINESE_TO_LEAGUE_ID = {
    '英超': '4328', '英冠': '4329', '英甲': '4330',
    '西甲': '4335', '德甲': '4331', '意甲': '4332', '法甲': '4334',
    '日职': '4356', '日乙': '4360', '韩K联': '4357', '韩国K联赛': '4357',
    '中超': '4333', '中甲': '4344', '荷甲': '4339',
    '葡超': '4338', '巴甲': '4341', '阿甲': '4342', '美职': '4349', 'MLS': '4349',
    '苏超': '4400', '土超': '4340', '比甲': '4386',
    '丹麦超': '4353', '瑞典超': '4354', '挪超': '4355', '瑞士超': '4434',
    '墨西超': '4347', '波兰超': '4415', '奥超': '4414',
    # 杯赛无积分榜
    '欧冠': None, '欧联': None, '欧协联': None, '亚冠': None, '亚冠精英': None,
    '解放者杯': None, '欧超杯': None, '世俱杯': None, '足总杯': None,
    '英联杯': None, '国王杯': None, '意杯': None, '德国杯': None,
    '巴西杯': None,
}

# ===== 缓存 =====
_cache = {}          # {league_cn: {'ts':..., 'data': {cn_name: rank}, 'source': 'thesportsdb'|'bzzoiro'}}
_lock = threading.Lock()


def _now():
    return time.time()


def _norm(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', (s or '').lower())


def _jaccard(a, b):
    """字符串相似度：字符集合 + 二元组 Jaccard。"""
    a, b = (a or '').lower(), (b or '').lower()
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    for s, out in ((a, sa), (b, sb)):
        for i in range(len(s)):
            out.add(s[i:i + 2])
    uni = sa | sb
    inter = sa & sb
    return len(inter) / len(uni) if uni else 0.0


def _reverse_cn_to_en():
    m = {}
    for en, cn in TEAM_NAME_CN.items():
        m.setdefault(cn, []).append(en)
    return m


_CN_TO_EN = _reverse_cn_to_en()

# 常见中名别名补充
_ALIASES = {
    '曼联': ['Manchester United'], '曼城': ['Manchester City'],
    '热刺': ['Tottenham Hotspur', 'Tottenham'], '纽卡斯尔': ['Newcastle United', 'Newcastle'],
    '阿森纳': ['Arsenal'], '利物浦': ['Liverpool'], '切尔西': ['Chelsea'],
    '巴黎': ['Paris Saint-Germain', 'Paris SG'], '皇马': ['Real Madrid'],
    '巴萨': ['Barcelona', 'FC Barcelona'], '马竞': ['Atletico Madrid', 'Atlético Madrid'],
    '拜仁': ['Bayern Munich'], '多特': ['Borussia Dortmund', 'Borussia M\'gladbach'],
    '国米': ['Inter Milan', 'Internazionale'], '米兰': ['AC Milan'],
    '尤文': ['Juventus'], '那不勒斯': ['Napoli', 'SSC Napoli'],
    '本菲卡': ['Benfica', 'SL Benfica'], '波尔图': ['Porto', 'FC Porto'],
    '里斯本竞技': ['Sporting CP', 'Sporting Lisbon'], '阿贾克斯': ['Ajax', 'AFC Ajax'],
    '埃因霍温': ['PSV', 'PSV Eindhoven'], '凯尔特人': ['Celtic', 'Celtic FC'],
    '流浪者': ['Rangers', 'Rangers FC'], '加拉塔萨雷': ['Galatasaray'],
    '费内巴切': ['Fenerbahce'], '国际米兰': ['Inter Milan', 'Internazionale'],
}


def _cn_to_en_candidates(cn_name):
    cands = set(_CN_TO_EN.get(cn_name, []))
    cands.update(_ALIASES.get(cn_name, []))
    return list(cands)


def compute_season(league_cn, today=None):
    """推算当前赛季候选列表（按可能优先）。亚洲/美洲同年，欧洲跨年。"""
    today = today or datetime.now(CST).date()
    year = today.year
    month = today.month
    is_north = any(k in (league_cn or '') for k in
                   ['日职', '日乙', '韩K', '中超', '中甲', '巴甲', '阿甲', '美职', 'MLS', 'J联赛', 'K联赛'])
    if is_north:
        # 北半球同年：赛季通常从年初开始（含跨年跨场），优先当年
        return [str(year), str(year - 1)]
    # 欧洲跨年联赛：7月后进入新赛季
    start_year = year if month >= 7 else year - 1
    return [f'{start_year}-{start_year + 1}', f'{start_year - 1}-{start_year}']


def fetch_standings_tsd(league_cn):
    """拉取 thesportsdb 积分榜。返回 [{rank,team_en,points,form,...}] 或 None。"""
    lid = CHINESE_TO_LEAGUE_ID.get(league_cn)
    if not lid or not str(lid).isdigit():
        return None
    for season in compute_season(league_cn):
        url = f'{TSD_BASE}/lookuptable.php?l={lid}&s={season}'
        try:
            r = requests.get(url, timeout=TSD_TIMEOUT)
            if r.status_code == 429:
                logger.warning('[rankings] thesportsdb 限流(429)，稍候重试')
                time.sleep(2)
                r = requests.get(url, timeout=TSD_TIMEOUT)
            if r.status_code != 200:
                logger.warning('[rankings] thesportsdb %s: HTTP %s', league_cn, r.status_code)
                continue
            data = r.json()
            table = data.get('table') or []
            if not table:
                continue
            rows = [{
                'rank': row.get('intRank'),
                'team_en': row.get('strTeam'),
                'points': row.get('intPoints'),
                'form': row.get('strForm') or '',
                'played': row.get('intPlayed'),
                'win': row.get('intWin'), 'draw': row.get('intDraw'), 'loss': row.get('intLoss'),
                'gf': row.get('intGoalsFor'), 'ga': row.get('intGoalsAgainst'),
                'gd': row.get('intGoalDifference'),
            } for row in table if row.get('strTeam')]
            if rows:
                return rows
        except requests.exceptions.RequestException as e:
            logger.warning('[rankings] thesportsdb 请求异常: %s', e)
            continue
    return None


def _en_to_cn_candidate(en):
    for name, cn in TEAM_NAME_CN.items():
        if name.lower() == (en or '').lower():
            return cn
    return (en or '')


def _build_cn_rankmap(rows):
    """将积分榜(英文名)转成 中文名->排名 映射。"""
    m = {}
    for row in rows:
        r = row.get('rank')
        if r is None:
            continue
        en = row.get('team_en', '')
        cn = _en_to_cn_candidate(en)
        if cn:
            m.setdefault(cn, r)
        # 存英文名兜底，便于反查
        if en:
            m.setdefault(en, r)
    return m


def _match_rank(cn_name, rank_map):
    """在 rank_map 中匹配中文队名排名。返回 rank 或 None。"""
    if not cn_name:
        return None
    if cn_name in rank_map:
        return rank_map[cn_name]
    # 归一化精确匹配
    cn_norm = _norm(cn_name)
    for k, v in rank_map.items():
        if _norm(k) == cn_norm:
            return v
    # 中->英候选，逐个匹配
    en_cands = _cn_to_en_candidates(cn_name)
    best_rank, best_score = None, 0.0
    for cand in en_cands:
        cand_norm = _norm(cand)
        for k, v in rank_map.items():
            k_norm = _norm(k)
            if cand_norm and cand_norm == k_norm:
                return v
            score = _jaccard(cand_norm, k_norm)
            if score > best_score:
                best_score, best_rank = score, v
    if best_score > 0.5:
        return best_rank
    return None


def _fetch_bzzoiro_rank_map(league_cn):
    """备选：用 Bzzoiro standings 构建 排名映射。"""
    try:
        from bizzoiro_client import fetch_events, fetch_standings_for_matches
        events = fetch_events(limit=30)
        if not events:
            return {}
        standings = fetch_standings_for_matches(events)
        rank_map = {}
        for _lid, data in (standings or {}).items():
            if not isinstance(data, dict):
                continue
            for team_key, entry in data.items():
                if isinstance(entry, dict) and entry.get('position') is not None:
                    rank_map.setdefault(team_key, entry['position'])
        return rank_map
    except Exception as e:
        logger.warning('[rankings] Bzzoiro standings 备选失败: %s', e)
        return {}


def get_league_rank_map(league_cn):
    """获取 联赛->(中文队名->排名, source) 带缓存。"""
    if not league_cn:
        return {}, ''
    key = league_cn
    with _lock:
        cached = _cache.get(key)
        if cached and (_now() - cached['ts']) < CACHE_TTL:
            return cached['data'], cached['source']

    rank_map, source = {}, ''
    tsd_rows = fetch_standings_tsd(league_cn)
    if tsd_rows:
        rank_map = _build_cn_rankmap(tsd_rows)
        source = 'thesportsdb'
    else:
        bz = _fetch_bzzoiro_rank_map(league_cn)
        if bz:
            rank_map = bz
            source = 'bzzoiro'

    with _lock:
        _cache[key] = {'ts': _now(), 'data': rank_map, 'source': source}
    return rank_map, source


def get_team_rank(league_cn, cn_name):
    """对外接口：返回 (rank, source)。rank 可能为 None。"""
    if not league_cn or not cn_name:
        return None, ''
    rank_map, source = get_league_rank_map(league_cn)
    if not rank_map:
        return None, ''
    rank = _match_rank(cn_name, rank_map)
    return rank, source


def enhance_matches(matches):
    """给分析后的比赛列表填充 home_rank/away_rank（仅当已为空时）。返回增强数量。"""
    n = 0
    for m in matches or []:
        lg = m.get('league', '')
        if not lg:
            continue
        h = m.get('home_team', '')
        a = m.get('away_team', '')
        hr, _ = get_team_rank(lg, h) if h else (None, '')
        ar, _ = get_team_rank(lg, a) if a else (None, '')
        if hr is not None and not m.get('home_rank'):
            m['home_rank'] = hr
            n += 1
        if ar is not None and not m.get('away_rank'):
            m['away_rank'] = ar
            n += 1
    return n
