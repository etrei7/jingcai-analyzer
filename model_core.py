# -*- coding: utf-8 -*-
"""独立球队实力模型（Dixon-Coles 泊松，带时间衰减与收缩）。

为什么单独做一个模块：
    原实现的"信心"是 1/最低赔率、"λ"也由同一组竞彩赔率反推，等于把市场概率换个
    说法再用它去判断市场错没错——自我循环，回测里胜平负永远没有区分度。

    这里改成：λ 只来自**历史比分**（球队攻防参数 + 主场优势 + 联赛均值），
    赔率只在最后一步作为**先验**参与融合（权重可调、可验证），
    所以模型概率与市场概率是两个独立来源，算出来的 EV 才有意义。

参数落盘：instance/model_params.json（含版本、训练截止日、样本量、每队 att/def）。
没有参数或球队未知时 predict_match 返回 None，调用方退回旧口径——不静默给假概率。
"""
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy 缺省时模型不可用，退回旧口径
    np = None

PARAM_FILE = 'model_params.json'
_VERSION = 'dc-1.0'
_RHO = -0.08
_MAX_GOALS = 9
_DEFAULT_WEIGHT = 0.35      # 模型权重：先用保守值，walk-forward 有证据后再调
_MIN_MATCHES = 60           # 少于这个样本量不启用（拟合出来的参数不可信）
_TIME_DECAY_DAYS = 180.0    # 半衰期：180 天前的比赛权重减半
_REG = 1.2                  # 攻防参数的 L2 收缩强度（队均样本少时靠向均值）

_CACHE = {'path': None, 'mtime': None, 'params': None}


def _poisson(k, lam):
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _score_matrix(lam_h, lam_a, rho=_RHO, max_goals=_MAX_GOALS):
    """Dixon-Coles 比分矩阵（低比分相关性修正），返回 list[list[float]]。"""
    lam_h = max(0.05, float(lam_h or 0))
    lam_a = max(0.05, float(lam_a or 0))
    m = [[_poisson(i, lam_h) * _poisson(j, lam_a) for j in range(max_goals)] for i in range(max_goals)]
    m[0][0] *= max(0.0, 1 - lam_h * lam_a * rho)
    m[0][1] *= max(0.0, 1 + lam_h * rho)
    m[1][0] *= max(0.0, 1 + lam_a * rho)
    m[1][1] *= max(0.0, 1 - rho)
    total = sum(sum(row) for row in m)
    if total > 0:
        m = [[v / total for v in row] for row in m]
    return m


def outcome_probs(lam_h, lam_a):
    """由 λ 得到 1X2 概率（H/D/A）与总进球分布。"""
    m = _score_matrix(lam_h, lam_a)
    ph = pd = pa = 0.0
    totals = {}
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
            totals[i + j] = totals.get(i + j, 0.0) + p
    return {'home': ph, 'draw': pd, 'away': pa, 'totals': totals}


# ---------------------------------------------------------------- 参数读写
def params_path(instance_path=None):
    base = instance_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    return os.path.join(base, PARAM_FILE)


def load_params(instance_path=None):
    path = params_path(instance_path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _CACHE['path'] == path and _CACHE['mtime'] == mtime and _CACHE['params']:
        return _CACHE['params']
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not data.get('teams'):
        return None
    _CACHE.update({'path': path, 'mtime': mtime, 'params': data})
    return data


def save_params(params, instance_path=None):
    path = params_path(instance_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(params, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        return None
    _CACHE.update({'path': path, 'mtime': None, 'params': params})
    return path


def _canon(name):
    try:
        from team_alias import canon
        return canon(name or '')
    except Exception:
        return name or ''


# ---------------------------------------------------------------- 拟合
def _design(records):
    """把比赛记录整理成数组，便于向量化梯度。"""
    teams = sorted({r['home'] for r in records} | {r['away'] for r in records})
    idx = {t: i for i, t in enumerate(teams)}
    hi = np.array([idx[r['home']] for r in records], dtype=np.int64)
    ai = np.array([idx[r['away']] for r in records], dtype=np.int64)
    hg = np.array([r['hg'] for r in records], dtype=np.float64)
    ag = np.array([r['ag'] for r in records], dtype=np.float64)
    w = np.array([r.get('weight', 1.0) for r in records], dtype=np.float64)
    return teams, idx, hi, ai, hg, ag, w


def fit(records, max_iter=260, lr=0.06):
    """最大似然拟合 att/def/home_adv/mu（带 L2 收缩与时间权重）。"""
    if np is None:
        raise RuntimeError('numpy 不可用')
    records = [r for r in records if r.get('hg') is not None and r.get('ag') is not None]
    if len(records) < _MIN_MATCHES:
        raise ValueError('样本不足：%d 场（至少 %d）' % (len(records), _MIN_MATCHES))
    teams, idx, hi, ai, hg, ag, w = _design(records)
    n = len(teams)
    total = float(np.sum(w * (hg + ag)))
    total_matches = float(np.sum(w))
    mu = math.log(max(0.2, total / max(1e-6, 2.0 * total_matches)))
    att = np.zeros(n)
    dfn = np.zeros(n)
    home_adv = 0.15
    # 简单 Adam，避免手调步长
    m_att = np.zeros(n); v_att = np.zeros(n)
    m_def = np.zeros(n); v_def = np.zeros(n)
    m_ha = 0.0; v_ha = 0.0
    b1, b2, eps = 0.9, 0.999, 1e-8
    for it in range(1, max_iter + 1):
        lam_h = np.exp(mu + home_adv + att[hi] - dfn[ai])
        lam_a = np.exp(mu + att[ai] - dfn[hi])
        # dNLL/dλ = w * (1 - y/λ)
        g_h = w * (1.0 - hg / np.maximum(lam_h, 1e-6))
        g_a = w * (1.0 - ag / np.maximum(lam_a, 1e-6))
        gh_lam = g_h * lam_h
        ga_lam = g_a * lam_a
        g_att = np.zeros(n); g_def = np.zeros(n)
        np.add.at(g_att, hi, gh_lam)
        np.add.at(g_def, ai, -gh_lam)
        np.add.at(g_att, ai, ga_lam)
        np.add.at(g_def, hi, -ga_lam)
        g_att += 2 * _REG * att
        g_def += 2 * _REG * dfn
        g_ha = float(np.sum(gh_lam))
        # Adam 更新
        m_att = b1 * m_att + (1 - b1) * g_att; v_att = b2 * v_att + (1 - b2) * g_att ** 2
        m_def = b1 * m_def + (1 - b1) * g_def; v_def = b2 * v_def + (1 - b2) * g_def ** 2
        m_ha = b1 * m_ha + (1 - b1) * g_ha; v_ha = b2 * v_ha + (1 - b2) * g_ha ** 2
        corr = 1 - b2 ** it
        att -= lr * (m_att / (1 - b1 ** it)) / (np.sqrt(v_att / corr) + eps)
        dfn -= lr * (m_def / (1 - b1 ** it)) / (np.sqrt(v_def / corr) + eps)
        home_adv -= lr * (m_ha / (1 - b1 ** it)) / (math.sqrt(v_ha / corr) + eps)
        # 可辨识性：让 att 均值为 0（否则与 mu 完全共线），同时把均值并入 mu 保持预测不变
        ma = att.mean()
        if abs(ma) > 1e-12:
            att -= ma
            mu += float(ma)
    lam_h = np.exp(mu + home_adv + att[hi] - dfn[ai])
    lam_a = np.exp(mu + att[ai] - dfn[hi])
    ll = float(np.sum(w * (hg * np.log(np.maximum(lam_h, 1e-9)) - lam_h
                           + ag * np.log(np.maximum(lam_a, 1e-9)) - lam_a)))
    teams_map = {t: {'att': round(float(att[i]), 4), 'def': round(float(dfn[i]), 4)} for t, i in idx.items()}
    return {
        'version': _VERSION,
        'trained_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'n_matches': len(records),
        'effective_matches': round(total_matches, 1),
        'mu': round(float(mu), 4),
        'home_adv': round(float(home_adv), 4),
        'rho': _RHO,
        'loglik': round(ll, 2),
        'teams': teams_map,
        'weight': _DEFAULT_WEIGHT,
    }


# ---------------------------------------------------------------- 预测
def _lambdas(params, home, away):
    t = params.get('teams') or {}
    h = t.get(home); a = t.get(away)
    att_h = h['att'] if h else 0.0
    def_h = h['def'] if h else 0.0
    att_a = a['att'] if a else 0.0
    def_a = a['def'] if a else 0.0
    mu = float(params.get('mu') or 0.0)
    ha = float(params.get('home_adv') or 0.0)
    lam_h = math.exp(mu + ha + att_h - def_a)
    lam_a = math.exp(mu + att_a - def_h)
    lam_h = min(4.5, max(0.25, lam_h))
    lam_a = min(4.5, max(0.2, lam_a))
    covered = bool(h) and bool(a)
    return lam_h, lam_a, covered


def predict_match(home, away, league='', win_odds=0, draw_odds=0, lose_odds=0, instance_path=None):
    """返回模型/融合概率；参数缺失或 numpy 不可用时返回 None。"""
    params = load_params(instance_path)
    if not params:
        return None
    h = _canon(home)
    a = _canon(away)
    if not h or not a:
        return None
    lam_h, lam_a, covered = _lambdas(params, h, a)
    mp = outcome_probs(lam_h, lam_a)
    p_model = np.array([mp['home'], mp['draw'], mp['away']]) if np is not None else \
        [mp['home'], mp['draw'], mp['away']]
    p_market = None
    o = [win_odds or 0, draw_odds or 0, lose_odds or 0]
    if all(x > 1.0 for x in o):
        inv = [1.0 / x for x in o]
        s = sum(inv)
        p_market = [v / s for v in inv]
    w = float(params.get('weight') or _DEFAULT_WEIGHT)
    if p_market:
        p = [w * float(p_model[i]) + (1 - w) * p_market[i] for i in range(3)]
    else:
        p = [float(x) for x in p_model]
    s = sum(p) or 1.0
    p = [x / s for x in p]
    return {
        'version': params.get('version'),
        'trained_at': params.get('trained_at'),
        'n_matches': params.get('n_matches'),
        'weight': w,
        'covered': covered,
        'lam_h': round(lam_h, 3),
        'lam_a': round(lam_a, 3),
        'p_home': round(p[0], 4),
        'p_draw': round(p[1], 4),
        'p_away': round(p[2], 4),
        'p_model': [round(float(x), 4) for x in p_model],
        'p_market': [round(float(x), 4) for x in p_market] if p_market else None,
    }


# ---------------------------------------------------------------- 数据与刷新
def _ev_team(ev, side):
    obj = ev.get(side + '_team_obj') or {}
    if isinstance(obj, dict) and obj.get('name'):
        return obj.get('name') or ''
    return ev.get(side + '_team') or ''


def build_dataset(days=120, max_days=90):
    """从 Bzzoiro 拉已完赛事件，构造训练集（按天拉，整段窗口会被接口截断）。"""
    from bizzoiro_client import fetch_finished_events
    now = datetime.now(timezone.utc)
    records = []
    seen = set()
    fetched_days = 0
    d = 0
    while d < int(days) and fetched_days < int(max_days):
        day = now - timedelta(days=d)
        d += 1
        if day.weekday() not in (1, 2, 3, 4, 5, 6, 0):
            continue
        ds = day.strftime('%Y-%m-%d')
        try:
            evs = fetch_finished_events(date_from=ds, date_to=ds, limit=400) or []
        except Exception:
            evs = []
        fetched_days += 1
        for ev in evs:
            if str(ev.get('status') or '').lower() not in ('finished', 'ended', 'ft', 'complete', 'completed', 'aet', 'pen'):
                continue
            eid = ev.get('id')
            if eid in seen:
                continue
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                continue
            home = _canon(_ev_team(ev, 'home'))
            away = _canon(_ev_team(ev, 'away'))
            if not home or not away:
                continue
            try:
                hs = int(hs); aw = int(aw)
            except (TypeError, ValueError):
                continue
            age = max(0.0, (now - day).days)
            records.append({
                'id': str(eid), 'date': ds, 'home': home, 'away': away,
                'hg': hs, 'ag': aw,
                'league': ev.get('league') or ev.get('league_name') or '',
                'weight': round(0.5 ** (age / _TIME_DECAY_DAYS), 4),
            })
            seen.add(eid)
        time.sleep(0.15)
    return records


def refresh(days=150, instance_path=None):
    """重建训练集并拟合参数，落盘。返回统计字典（供维护任务记录日志）。"""
    t0 = time.time()
    records = build_dataset(days=days)
    if len(records) < _MIN_MATCHES:
        return {'ok': False, 'reason': '样本不足', 'records': len(records)}
    params = fit(records)
    path = save_params(params, instance_path)
    covered = len(params.get('teams') or {})
    return {
        'ok': True,
        'records': len(records),
        'teams': covered,
        'n_matches': params['n_matches'],
        'home_adv': params['home_adv'],
        'mu': params['mu'],
        'loglik': params['loglik'],
        'path': path,
        'elapsed_sec': round(time.time() - t0, 1),
    }
