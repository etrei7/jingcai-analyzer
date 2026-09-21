"""官方首发（Phase 2，只读展示 + 确认标记）。

用 API-Football `/fixtures/lineups` 获取**临场官方首发**：
- 只在开赛前 ~120 分钟窗口内尝试；
- **严格匹配**（联赛 + 日期 + 主客双队名），匹配不到一律跳过，绝不展示错误首发；
- 后台线程拉取 + 内存缓存，避免阻塞请求；受 API-Football 每日额度与限流保护；
- **不据此改方向**：仅展示阵型/首发，并在 AI 综合里标注"官方首发已确认"。

局限：无球员评分基线，首发主要用于"确认伤停/阵容"，量化增量有限。
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
_cache = {}
_lock = threading.Lock()
_TTL = 600          # 首发缓存 10 分钟
_WINDOW_MIN = 120   # 只处理开赛前 120 分钟内
_MAX_FETCH = 3      # 单次最多后台拉取场次（省额度）
_pending = False


def _kickoff(m):
    d = m.get('date')
    t = (m.get('match_time') or m.get('time') or '')
    if not d:
        return None
    try:
        if len(t) >= 5:
            return datetime.strptime('%s %s' % (d, t[:5]), '%Y-%m-%d %H:%M').replace(tzinfo=CST)
        return datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=CST)
    except Exception:
        return None


def _cn_to_en(cn):
    out = []
    try:
        from team_names import TEAM_NAME_CN
        for en, c in TEAM_NAME_CN.items():
            if c == cn:
                out.append(en)
    except Exception:
        pass
    return out


def _name_match(cn, en):
    if not cn or not en:
        return False
    try:
        from team_alias import canon
        if canon(en) == canon(cn):
            return True
    except Exception:
        pass
    en_l = en.lower()
    for cand in _cn_to_en(cn):
        cl = cand.lower()
        if cl == en_l or cl in en_l or en_l in cl:
            return True
    return False


def _fetch_lineups(m):
    """严格匹配并返回 {'home': {...}, 'away': {...}, 'fixture_id': id} 或 None。"""
    try:
        from api_football_client import _fetch, _rate_limited, LEAGUE_ID_MAP, _season_candidates
    except Exception:
        return None
    if _rate_limited():
        return None
    lid = LEAGUE_ID_MAP.get(m.get('league'))
    d = m.get('date')
    if not lid or not d:
        return None
    try:
        season = _season_candidates(m.get('league'))[0]
    except Exception:
        return None
    fixtures = _fetch('/fixtures', {'league': lid, 'season': season, 'date': d})
    if not fixtures:
        return None
    fxid = None
    for fx in fixtures:
        t = fx.get('teams') or {}
        hn = (t.get('home') or {}).get('name')
        an = (t.get('away') or {}).get('name')
        if _name_match(m.get('home_team'), hn) and _name_match(m.get('away_team'), an):
            fxid = (fx.get('fixture') or {}).get('id')
            break
    if not fxid:
        return None
    rows = _fetch('/fixtures/lineups', {'fixture': fxid})
    if not rows:
        return None

    def _side(row):
        return {
            'formation': row.get('formation') or '',
            'coach': (row.get('coach') or {}).get('name') or '',
            'startXI': [((p.get('player') or {}).get('name') or '')
                        for p in (row.get('startXI') or [])][:11],
        }

    out = {'fixture_id': fxid, 'home': None, 'away': None}
    for row in rows:
        tname = (row.get('team') or {}).get('name')
        if _name_match(m.get('home_team'), tname):
            out['home'] = _side(row)
        elif _name_match(m.get('away_team'), tname):
            out['away'] = _side(row)
    if not out['home'] and not out['away']:
        return None
    return out


def _refresh_async(matches):
    global _pending
    with _lock:
        if _pending:
            return
        _pending = True

    def work():
        global _pending
        try:
            for m in matches:
                key = str(m.get('match_id'))
                try:
                    data = _fetch_lineups(m)
                except Exception:
                    data = None
                if data:
                    with _lock:
                        _cache[key] = {'ts': time.time(), 'data': data}
                    logger.info('[lineup] %s 官方首发已获取', key)
        except Exception as e:
            logger.warning('[lineup] 后台拉取失败: %s', e)
        finally:
            with _lock:
                _pending = False

    threading.Thread(target=work, daemon=True).start()


def attach(matches):
    """把缓存的官方首发挂到临近开赛的场次，并触发后台拉取。返回已挂载场次数。"""
    if not matches:
        return 0
    now = datetime.now(CST)
    todo = []
    n = 0
    for m in matches:
        ko = _kickoff(m)
        if not ko:
            continue
        mins = (ko - now).total_seconds() / 60.0
        if mins < -30 or mins > _WINDOW_MIN:
            continue
        key = str(m.get('match_id'))
        with _lock:
            c = _cache.get(key)
        if c and (time.time() - c['ts']) < _TTL:
            m['lineup'] = c['data']
            n += 1
        else:
            todo.append(m)
    if todo:
        _refresh_async(todo[:_MAX_FETCH])
    return n


def summary():
    with _lock:
        return {'cached': len(_cache), 'ttl_sec': _TTL, 'window_min': _WINDOW_MIN}
