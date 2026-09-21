"""资讯抓取（Phase 1）：RSS → 关键词分类 → 队名匹配 → 结构化信号。

原则：
- 只抓 **RSS**（合规、轻量），不抓正文；不做"读新闻猜赛果"；
- 只抽取**事实类信号**（伤停/停赛/轮换/主帅/首发），匹配到竞彩场次的球队；
- 对分析只做**保守降级**或提示（预测方有伤停/停赛不利 → 降级；不直接改方向）；
- 结果存 instance/news_cache.json（gitignore），供前端「资讯」标签与后台调整。

不改变既有分析逻辑；无网络/无信号时静默失效。
"""
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
_INSTANCE = os.path.join(_BASE, 'instance')
os.makedirs(_INSTANCE, exist_ok=True)
_FILE = os.path.join(_INSTANCE, 'news_cache.json')

_FEEDS = [
    ('Sky Sports', 'https://www.skysports.com/rss/12040'),
    ('BBC Sport', 'https://feeds.bbci.co.uk/sport/football/rss.xml'),
    ('Goal', 'https://www.goal.com/feeds/en/news'),
    ('ESPN', 'https://www.espn.com/espn/rss/soccer/news'),
]
_UA = {'User-Agent': 'Mozilla/5.0 (compatible; jingcai-news/1.0)'}
_MAX_ITEMS = 500
_TTL = 6 * 3600  # 只保留近 6 小时资讯

# 关键词 → 信号类型
_TYPE_KEYWORDS = {
    'injury': ['injur', 'sidelined', 'ruled out', 'out of action', 'fitness doubt',
               'doubt for', 'hamstring', 'knock', 'setback', 'muscle problem'],
    'suspension': ['suspend', 'banned', 'red card', 'suspension', 'sent off'],
    'rotation': ['rotate', 'rotation', 'rested', 'rest him', 'fatigue'],
    'manager': ['manager', 'head coach', 'boss', 'press conference'],
    'lineup': ['line-up', 'lineup', 'starting xi', 'starting eleven', 'team news',
               'starting line', 'expected to start'],
}
_TYPE_CN = {'injury': '伤停', 'suspension': '停赛', 'rotation': '轮换',
            'manager': '主帅', 'lineup': '首发'}
_NEG_TYPES = ('injury', 'suspension')

_lock = threading.Lock()
_matcher = {'ts': 0, 'pat': None, 'idx': {}}


def _alias_index():
    idx = {}
    try:
        from team_names import TEAM_NAME_CN
        for en, cn in TEAM_NAME_CN.items():
            if not cn:
                continue
            e = (en or '').strip().lower()
            if len(e) >= 6:
                idx.setdefault(e, cn)
    except Exception:
        pass
    try:
        from team_alias import all_items
        for alias, cn in all_items().items():
            if not cn:
                continue
            a = (alias or '').strip().lower()
            if len(a) >= 6:
                idx.setdefault(a, cn)
    except Exception:
        pass
    return idx


def _get_matcher():
    with _lock:
        if _matcher['pat'] is not None and (time.time() - _matcher['ts']) < 600:
            return _matcher['pat'], _matcher['idx']
    idx = _alias_index()
    pat = None
    if idx:
        aliases = sorted(idx.keys(), key=len, reverse=True)
        try:
            pat = re.compile('|'.join(re.escape(a) for a in aliases), re.IGNORECASE)
        except Exception:
            pat = None
    with _lock:
        _matcher.update({'ts': time.time(), 'pat': pat, 'idx': idx})
    return pat, idx


def _classify(text):
    t = text.lower()
    types = []
    for typ, kws in _TYPE_KEYWORDS.items():
        if any(k in t for k in kws):
            types.append(typ)
    return types


def _match_teams(text, pat, idx):
    if not pat:
        return []
    found = set()
    try:
        for m in pat.findall(text):
            cn = idx.get(m.lower())
            if cn:
                found.add(cn)
    except Exception:
        pass
    return list(found)


def _fetch_feed(source, url):
    out = []
    try:
        r = requests.get(url, headers=_UA, timeout=10)
        if r.status_code != 200:
            return out
        root = ET.fromstring(r.content)
        for item in root.iter('item'):
            title = (item.findtext('title') or '').strip()
            desc = (item.findtext('description') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            if title:
                out.append({'source': source, 'title': title, 'desc': desc,
                            'link': link, 'time': pub})
    except Exception as e:
        logger.warning('[news] %s 抓取失败: %s', source, e)
    return out


def refresh():
    """抓取各 RSS → 分类 → 队名匹配 → 落盘。返回入库条数。"""
    pat, idx = _get_matcher()
    raw = []
    for src, url in _FEEDS:
        raw.extend(_fetch_feed(src, url))
        if len(raw) > _MAX_ITEMS * 2:
            break

    items = []
    for it in raw[:_MAX_ITEMS * 2]:
        text = (it['title'] + ' ' + it.get('desc', ''))[:1200]
        types = _classify(text)
        if not types:
            continue
        teams = _match_teams(text, pat, idx)
        if not teams:
            continue
        items.append({
            'title': it['title'][:200], 'link': it.get('link', '')[:300],
            'source': it.get('source', ''), 'time': it.get('time', ''),
            'types': types, 'neg': any(t in _NEG_TYPES for t in types),
            'teams': teams,
        })
        if len(items) >= _MAX_ITEMS:
            break

    data = {'ts': time.time(), 'items': items}
    try:
        with open(_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning('[news] 落盘失败: %s', e)
    logger.info('[news] 刷新完成：%d 条有效资讯（原始 %d）', len(items), len(raw))
    return len(items)


def _load():
    try:
        with open(_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {'ts': 0, 'items': []}


def _grouped():
    """返回 (ts, {规范队名: [item,...]})，仅保留 TTL 内且未过期的资讯。"""
    d = _load()
    ts = d.get('ts') or 0
    items = d.get('items') or []
    g = {}
    for it in items:
        for cn in it.get('teams', []):
            g.setdefault(cn, []).append(it)
    return ts, g


def _brief(it, now):
    tl = it.get('title') or ''
    return {
        'type_cn': '/'.join(_TYPE_CN.get(t, t) for t in it.get('types', [])),
        'title': tl if len(tl) <= 90 else tl[:90] + '…',
        'source': it.get('source', ''),
        'link': it.get('link', ''),
        'time': it.get('time', ''),
        'neg': bool(it.get('neg')),
    }


def _downgrade(m):
    lv = m.get('confidence_level')
    if lv == '高':
        m['confidence_level'] = '中'
    elif lv == '中':
        m['confidence_level'] = '低'


def apply_to_matches(matches):
    """把资讯挂到每场比赛，并对「预测方伤停/停赛不利」保守降级。返回降级场次数。"""
    if not matches:
        return 0
    ts, g = _grouped()
    if not g:
        for m in matches:
            m['news'] = None
        return 0
    try:
        from team_alias import canon
    except Exception:
        def canon(x):
            return x or ''
    now = time.strftime('%m-%d %H:%M', time.localtime(ts or time.time()))
    changed = 0
    for m in matches:
        h = canon(m.get('home_team'))
        a = canon(m.get('away_team'))
        hs = g.get(h, []) if h else []
        as_ = g.get(a, []) if a else []
        if not hs and not as_:
            m['news'] = None
            continue
        m['news'] = {
            'home': [_brief(i, now) for i in hs[:6]],
            'away': [_brief(i, now) for i in as_[:6]],
            'updated': now,
        }
        neg_h = sum(1 for i in hs if i.get('neg'))
        neg_a = sum(1 for i in as_ if i.get('neg'))
        po = m.get('predicted_option')
        side = 'home' if po == '胜' else 'away' if po == '负' else None
        pre = m.get('ai_preview') or ''
        if side == 'home' and neg_h > neg_a:
            _downgrade(m)
            m['ai_preview'] = pre + '（资讯：主队伤停/停赛不利 %d 条，已谨慎处理）' % neg_h
            changed += 1
        elif side == 'away' and neg_a > neg_h:
            _downgrade(m)
            m['ai_preview'] = pre + '（资讯：客队伤停/停赛不利 %d 条，已谨慎处理）' % neg_a
            changed += 1
        elif side == 'home' and neg_a > 0 and neg_h == 0:
            m['ai_preview'] = pre + '（资讯：对手有伤停/停赛，留意）'
        elif side == 'away' and neg_h > 0 and neg_a == 0:
            m['ai_preview'] = pre + '（资讯：对手有伤停/停赛，留意）'
    return changed


def summary():
    """供 /api/news 展示的概要。"""
    ts, g = _grouped()
    n_items = 0
    try:
        n_items = len(_load().get('items') or [])
    except Exception:
        pass
    return {
        'updated': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts else '',
        'items': n_items,
        'teams': len([k for k, v in g.items() if v]),
        'sources': [s for s, _ in _FEEDS],
    }
