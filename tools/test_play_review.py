"""离线验证「玩法级专业评估」（play_review）：不联网、不碰线上库。

覆盖：
  1. Wilson 置信区间：小样本比正态近似更保守，全胜/全负不会给出 [100%,100%]
  2. 盈亏线：用 mean(1/赔率)，不是 1/平均赔率
  3. 显著性：同赔率下 60/100 命中 vs 盈亏线 50%，z 与 p 值方向正确
  4. 结论三档：样本不足 → insufficient；显著低于盈亏线 → pause；
     显著高于盈亏线且 ROI>0 → usable；介于两者 → watch
  5. 估算赔率玩法（比分/半全场）标 reference，不参与投注结论
  6. 作废（void）不计入样本，但单独计数

用法（必须用装了 Flask 的解释器，并禁止写 __pycache__）：
    $env:PYTHONDONTWRITEBYTECODE='1'
    venv-jingcai\\Scripts\\python.exe tools\\test_play_review.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

TMPDB = os.environ.get('JINGCAI_TMP_DB') or r'C:\Users\HP\Documents\deepseek-harness\default-workspace\tmp_review_test.db'
for _p in (TMPDB, TMPDB + '-journal'):
    if os.path.exists(_p):
        os.remove(_p)
os.environ['DATABASE_URL'] = 'sqlite:///' + TMPDB.replace('\\', '/')
os.environ.setdefault('SECRET_KEY', 'test-only')
os.environ['SCHEDULER_ENABLED'] = '0'

from app import app                 # noqa: E402
import play_review as pr            # noqa: E402
from backtest_models import BtBet, db   # noqa: E402

OK = True


def check(name, cond, extra=''):
    global OK
    print(('  OK   ' if cond else '  FAIL ') + name + (('  ' + str(extra)) if extra else ''))
    OK = OK and bool(cond)


def add(mid, play, pick, odds, outcome, estimated=False, day='2026-11-10', stake=1.0):
    from datetime import datetime as _dt
    b = BtBet(match_id=mid, home_team='H', away_team='A', play_type=play, pick=pick,
              odds=odds, stake=stake, outcome=outcome,
              pnl=(stake * (odds - 1) if outcome == 'win' else -stake if outcome == 'lose' else 0.0),
              settled_at=day + ' 12:00:00', created_at=_dt.strptime(day, '%Y-%m-%d'),
              estimated=estimated, jingcai=True)
    db.session.add(b)


def main():
    with app.app_context():
        db.create_all()
        # 玩法 A（1X2）：100 注、赔率 2.0、赢 60 → 命中 60%、盈亏线 50%、ROI +20%
        for i in range(60):
            add('A%d' % i, '1X2', 'H', 2.0, 'win')
        for i in range(40):
            add('B%d' % i, '1X2', 'A', 2.0, 'lose')
        # 玩法 B（TG）：40 注、赔率 4.0、赢 4 → 命中 10%、盈亏线 25% → 显著偏差
        for i in range(4):
            add('C%d' % i, 'TG', '3', 4.0, 'win')
        for i in range(36):
            add('D%d' % i, 'TG', '2', 4.0, 'lose')
        # 玩法 C（AH）：12 注，样本不足
        for i in range(6):
            add('E%d' % i, 'AH', 'H|-1.0', 1.9, 'win')
        for i in range(6):
            add('F%d' % i, 'AH', 'H|-1.0', 1.9, 'lose')
        # 玩法 D（HTFT）：估算赔率，且全部作废（历史数据里确实如此）
        for i in range(30):
            add('G%d' % i, 'HTFT', 'HH', 3.2, 'void', estimated=True)
        # 校验 void 不计入 1X2 的样本
        for i in range(5):
            add('V%d' % i, '1X2', 'D', 3.3, 'void')
        db.session.commit()

        data = pr.review('all')
        plays = {p['play_type']: p for p in data['plays']}

        print('[1] 基础统计 / 盈亏线')
        x = plays['1X2']
        check('样本 100（void 不计）', x['n'] == 100, x['n'])
        check('void 单独计数为 5', x['voids'] == 5, x['voids'])
        check('命中率 60.0', x['hit_rate'] == 60.0, x['hit_rate'])
        check('盈亏线 50.0（mean(1/2.0)）', x['required_rate'] == 50.0, x['required_rate'])
        check('edge +10.0pp', x['edge_pp'] == 10.0, x['edge_pp'])
        check('ROI +20.0', x['roi'] == 20.0, x['roi'])
        check('置信区间包含 60 且不越界', x['hit_ci'][0] < 60 < x['hit_ci'][1], x['hit_ci'])

        print('[2] 显著性')
        check('z 为正', x['z'] > 0, x['z'])
        check('p 值显著（<0.05）', x['p_value'] < 0.05, x['p_value'])
        check('结论 usable', x['verdict_code'] == 'usable', x['verdict_code'])
        check('样本 100 → 等级 B', x['grade'] == 'B', x['grade'])

        print('[3] 负 edge → pause')
        t = plays['TG']
        check('TG 命中率 10.0', t['hit_rate'] == 10.0, t['hit_rate'])
        check('TG 盈亏线 25.0', t['required_rate'] == 25.0, t['required_rate'])
        check('TG edge 为负且显著', t['edge_pp'] < 0 and t['p_value'] < 0.10, (t['edge_pp'], t['p_value']))
        check('TG 结论 pause', t['verdict_code'] == 'pause', t['verdict_code'])

        print('[4] 样本不足 / 估算赔率')
        check('AH 样本 12 → insufficient', plays['AH']['verdict_code'] == 'insufficient', plays['AH']['verdict_code'])
        check('AH 等级 C/D', plays['AH']['grade'] in ('C', 'D'), plays['AH']['grade'])
        check('HTFT 标 estimated', plays['HTFT']['estimated'] is True)
        check('HTFT 全作废 → n=0', plays['HTFT']['n'] == 0, plays['HTFT']['n'])
        check('HTFT 结论 reference', plays['HTFT']['verdict_code'] == 'reference', plays['HTFT']['verdict_code'])

        print('[5] 汇总文案')
        check('headline 含可用玩法', '胜平负' in data['headline'], data['headline'])
        check('headline 含暂停玩法', '总进球' in data['headline'], data['headline'])
        check('method 说明 Wilson', 'Wilson' in data['method'])

        print('[6] Wilson 区间边界')
        lo, hi = pr.wilson_interval(10, 10)
        check('全胜下限 <100%', lo < 1.0, round(lo, 3))
        check('全胜上限 =100%', abs(hi - 1.0) < 1e-9, round(hi, 3))
        lo0, hi0 = pr.wilson_interval(0, 0)
        check('零样本返回 (0,0)', (lo0, hi0) == (0.0, 0.0))

    print()
    print('RESULT: ' + ('ALL OK' if OK else 'HAS FAILURES'))
    return 0 if OK else 1


if __name__ == '__main__':
    code = main()
    try:
        if os.path.exists(TMPDB):
            os.remove(TMPDB)
    except Exception:
        pass
    sys.exit(code)
