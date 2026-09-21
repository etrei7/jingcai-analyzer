"""队名别名学习：从已结算数据自动学习「英文名 → 中文名」映射，提升队名兜底结算率。

- 规范名优先用 `team_names.TEAM_NAME_CN`；未覆盖时查学习到的别名。
- 学习时机：按 Bzzoiro 事件ID精确结算时，可同时拿到「记录名(常为竞彩中文)」与
  「事件名(Bzzoiro 英文)」，二者规范不同即认为同一队，登记 英文→中文。
- 存储：instance/team_aliases.json（gitignore），单 worker 加锁，容量上限防膨胀。
"""
import json
import os
import re
import threading
import logging

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
_INSTANCE = os.path.join(_BASE, 'instance')
os.makedirs(_INSTANCE, exist_ok=True)
_FILE = os.path.join(_INSTANCE, 'team_aliases.json')
_MAX = 5000
_lock = threading.Lock()
_aliases = None

_CJK = re.compile(r'[\u4e00-\u9fff]')


def _has_cjk(s):
    return bool(_CJK.search(s or ''))


def _load():
    global _aliases
    if _aliases is not None:
        return _aliases
    try:
        with open(_FILE, 'r', encoding='utf-8') as f:
            _aliases = json.load(f)
            if not isinstance(_aliases, dict):
                _aliases = {}
    except Exception:
        _aliases = {}
    return _aliases


def _save(data):
    try:
        if len(data) > _MAX:
            items = list(data.items())[-_MAX:]
            data = dict(items)
        with open(_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning('[alias] save failed: %s', e)


def canon(name):
    """返回队名的规范（中文）形式；无映射时原样返回。"""
    n = (name or '').strip()
    if not n:
        return ''
    try:
        from team_names import TEAM_NAME_CN
        if n in TEAM_NAME_CN:
            return TEAM_NAME_CN[n]
    except Exception:
        pass
    al = _load()
    if n in al:
        return al[n]
    low = n.lower()
    if low in al:
        return al[low]
    return n


def learn(name_a, name_b):
    """从同一场比赛的两个来源队名学习别名：以含中文的一方为规范名。
    返回是否新增映射。"""
    if not name_a or not name_b:
        return False
    a = str(name_a).strip()
    b = str(name_b).strip()
    a_cn, b_cn = _has_cjk(a), _has_cjk(b)
    added = False
    with _lock:
        al = _load()
        if a_cn and not b_cn:
            added = _add(al, b, a)
        elif b_cn and not a_cn:
            added = _add(al, a, b)
        if added:
            _save(al)
    return added


def _add(al, alias, canonical):
    if not alias or not canonical or alias == canonical:
        return False
    if al.get(alias) == canonical:
        return False
    al[alias] = canonical
    # 顺带登记小写键，提升大小写不敏感命中
    if alias.lower() != alias:
        al.setdefault(alias.lower(), canonical)
    return True


def stats():
    """返回 {count, sample}，供诊断。"""
    al = _load()
    sample = list(al.items())[:20]
    return {'count': len(al), 'sample': sample}
