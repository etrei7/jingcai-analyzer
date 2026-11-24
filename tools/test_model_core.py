# -*- coding: utf-8 -*-
"""model_core 回归测试：拟合能否还原已知强度、概率是否自洽、融合是否按权重。

跑法（Flask 不参与，但统一用 venv 里的 python）：
    $env:PYTHONDONTWRITEBYTECODE='1'
    venv-jingcai\\Scripts\\python.exe tools\\test_model_core.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import model_core as mc

OK = 0
FAIL = 0


def check(name, cond, detail=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('  OK   %s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, detail))


def _pois(lam, rnd):
    # 逆变换采样足够（λ 很小）
    L = pow(2.718281828459045, -lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= rnd.random()
        if p <= L:
            return k - 1


def synthetic(n_teams=12, n_rounds=26, seed=7):
    rnd = random.Random(seed)
    teams = ['T%02d' % i for i in range(n_teams)]
    strength = {t: (rnd.uniform(-0.45, 0.45), rnd.uniform(-0.45, 0.45)) for t in teams}
    mu, ha = 0.30, 0.22
    recs = []
    for r in range(n_rounds):
        for i in range(0, n_teams, 2):
            h = teams[i]
            a = teams[i + 1] if i + 1 < n_teams else teams[0]
            if h == a:
                continue
            if r % 2 == 1:          # 交替主客，否则主场优势与固定配对混淆
                h, a = a, h
            att_h, def_h = strength[h]
            att_a, def_a = strength[a]
            lam_h = pow(2.718281828459045, mu + ha + att_h - def_a)
            lam_a = pow(2.718281828459045, mu + att_a - def_h)
            recs.append({'home': h, 'away': a, 'hg': _pois(lam_h, rnd), 'ag': _pois(lam_a, rnd),
                         'weight': 1.0})
    return teams, strength, recs


print('[1] 由已知强度生成的比赛能否还原强弱顺序')
teams, strength, recs = synthetic()
params = mc.fit(recs, max_iter=300)
check('样本量', params['n_matches'] == len(recs), params['n_matches'])
check('主场优势为正', params['home_adv'] > 0, params['home_adv'])
# 真实净实力 = att - def；拟合的 att-def 应与真实值正相关
pairs = []
for t in teams:
    true_net = strength[t][0] - strength[t][1]
    fit_net = params['teams'][t]['att'] - params['teams'][t]['def']
    pairs.append((true_net, fit_net))
pairs.sort()
n = len(pairs)


def _rank_corr(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(rx)))
    vx = sum((rx[i] - mx) ** 2 for i in range(len(rx))) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(len(ry))) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


corr = _rank_corr([p[0] for p in pairs], [p[1] for p in pairs])
check('强弱排序正相关（Spearman>0.6）', corr > 0.6, round(corr, 3))

print('[2] 概率自洽')
mp = mc.outcome_probs(1.6, 1.1)
check('1X2 归一', abs(mp['home'] + mp['draw'] + mp['away'] - 1.0) < 1e-9)
check('主强于客', mp['home'] > mp['away'], round(mp['home'], 3))
check('总进球分布归一', abs(sum(mp['totals'].values()) - 1.0) < 1e-9)
check('平局概率合理区间', 0.15 < mp['draw'] < 0.40, round(mp['draw'], 3))

print('[3] 融合按权重')
mc.save_params(dict(params, weight=0.35), instance_path=os.path.dirname(os.path.abspath(__file__)))
# 参数文件写到上面的目录会污染仓库，这里改写到临时目录
import tempfile
tmpdir = tempfile.mkdtemp()
mc.save_params(dict(params, weight=0.35), instance_path=tmpdir)
p = mc.predict_match(teams[0], teams[1], win_odds=2.0, draw_odds=3.4, lose_odds=3.6, instance_path=tmpdir)
check('返回结构', p and p['p_home'] + p['p_draw'] + p['p_away'] == 1.0)
check('融合权重生效', abs(p['weight'] - 0.35) < 1e-9, p['weight'])
check('模型与市场都给了', bool(p['p_model']) and bool(p['p_market']))
p2 = mc.predict_match(teams[0], teams[1], win_odds=2.0, draw_odds=3.4, lose_odds=3.6, instance_path=tmpdir)
check('两次调用一致', p['p_home'] == p2['p_home'])
check('未知球队仍能出概率（att/def=0）',
      bool(mc.predict_match('NOT_A_TEAM', 'T00', instance_path=tmpdir)))

print('[4] 边界')
try:
    mc.fit(recs[:20])
    check('样本不足应报错', False)
except ValueError:
    check('样本不足应报错', True)
check('无参数文件返回 None', mc.predict_match('A', 'B', instance_path=os.path.join(tmpdir, 'nope')) is None)

print()
if FAIL:
    print('RESULT: %d FAILED / %d passed' % (FAIL, OK))
    sys.exit(1)
print('RESULT: ALL OK (%d checks)' % OK)
