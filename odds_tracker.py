"""赔率快照与「初盘→即时」变动追踪。
首次记录某场比赛时保存为初盘(opening)，之后每次刷新更新最新值(last)，
并计算相对初盘的变动幅度/方向（赔率下降=资金流入该选项）。
存储于 instance/odds_snapshots.json（gitignore，单 worker 加锁安全）。
"""
import json
import os
import threading
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

_BASE = os.path.dirname(os.path.abspath(__file__))
_INSTANCE = os.path.join(_BASE, 'instance')
os.makedirs(_INSTANCE, exist_ok=True)
_FILE = os.path.join(_INSTANCE, 'odds_snapshots.json')
_lock = threading.Lock()


def _load():
    try:
        with open(_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    try:
        with open(_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning('[odds] save failed: %s', e)


def _pct(old, new):
    if old and old > 0:
        return round((new - old) / old * 100, 1)
    return 0.0


def track(match_id, win_odds, draw_odds, lose_odds):
    """记录快照并返回初盘→即时的变动分析。数据全为0时返回 None。"""
    mid = str(match_id or '')
    if not mid:
        return None
    try:
        w = float(win_odds or 0)
        d = float(draw_odds or 0)
        l = float(lose_odds or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 and d <= 0 and l <= 0:
        return None

    with _lock:
        data = _load()
        now = datetime.now(CST).strftime('%m-%d %H:%M')
        entry = data.get(mid)
        if not entry:
            entry = {'opening': {'w': w, 'd': d, 'l': l, 'time': now},
                     'last': {'w': w, 'd': d, 'l': l, 'time': now}}
            data[mid] = entry
        else:
            entry['last'] = {'w': w, 'd': d, 'l': l, 'time': now}
        _save(data)

    op = entry['opening']
    mw = _pct(op['w'], w)
    md = _pct(op['d'], d)
    ml = _pct(op['l'], l)

    # 赔率下降=资金流入（市场更看好）。找出降幅最大的选项作为「资金压力」方向
    moves = [('主胜', mw), ('平局', md), ('客胜', ml)]
    top = min(moves, key=lambda x: x[1])
    if top[1] <= -2:
        pressure = top[0] + '资金热'
    elif top[1] >= 2:
        pressure = '资金流出'
    else:
        pressure = '资金平稳'

    return {
        'opening_w': op['w'], 'opening_d': op['d'], 'opening_l': op['l'],
        'current_w': w, 'current_d': d, 'current_l': l,
        'move_w': mw, 'move_d': md, 'move_l': ml,
        'opening_time': op.get('time', ''), 'last_time': now,
        'pressure': pressure,
        'has_changed': (abs(mw) >= 1 or abs(md) >= 1 or abs(ml) >= 1),
    }


def get(match_id):
    """只读获取已有变动（不更新快照）。"""
    mid = str(match_id or '')
    data = _load()
    entry = data.get(mid)
    if not entry:
        return None
    op = entry['opening']
    last = entry['last']
    mw = _pct(op['w'], last['w'])
    md = _pct(op['d'], last['d'])
    ml = _pct(op['l'], last['l'])
    return {
        'opening_w': op['w'], 'opening_d': op['d'], 'opening_l': op['l'],
        'current_w': last['w'], 'current_d': last['d'], 'current_l': last['l'],
        'move_w': mw, 'move_d': md, 'move_l': ml,
        'opening_time': op.get('time', ''), 'last_time': last.get('time', ''),
        'has_changed': (abs(mw) >= 1 or abs(md) >= 1 or abs(ml) >= 1),
    }
