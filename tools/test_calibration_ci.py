# -*- coding: utf-8 -*-
"""离线回归：命中率 Wilson 95% 置信区间 + ROI 置信带（不连数据库、不发请求）。

校准口径的置信区间是"这个分段的历史命中率到底可能是多少"的容错范围：
小样本（n=30~60）下命中率的抽样误差常有 ±10pp，跨过盈亏线时优势就不能当结论。
本机没有 numpy，因此用 Wilson 解析区间，而不是 bootstrap 抽样。

用法：python tools/test_calibration_ci.py   （必须输出 RESULT: ALL OK）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import calibration  # noqa: E402

fail = 0


def check(name, cond, detail=''):
    global fail
    if cond:
        print('  OK   ' + name)
    else:
        fail += 1
        print('  FAIL ' + name + ' ' + str(detail)[:200])


def near(a, b, tol=1e-3):
    return a is not None and abs(float(a) - float(b)) <= tol


print('[1/4] Wilson 区间边界')
lo, hi = calibration._wilson(40, 120)
check('40/120 命中率区间 25.5%-42.2%', near(lo, 0.2553) and near(hi, 0.4217), (lo, hi))
lo, hi = calibration._wilson(0, 30)
check('0/30 下界为 0、上界 11.4%', lo == 0.0 and near(hi, 0.1135), (lo, hi))
lo, hi = calibration._wilson(30, 30)
check('30/30 上界为 1、下界 88.6%', near(lo, 0.8865) and hi == 1.0, (lo, hi))
check('n=0 返回 (0,0) 不抛异常', calibration._wilson(0, 0) == (0.0, 0.0), calibration._wilson(0, 0))

print('[2/4] 区间性质')
for wins, n in [(3, 30), (18, 30), (60, 120), (400, 800)]:
    lo, hi = calibration._wilson(wins, n)
    p = wins / float(n)
    check('区间包含点估计 %d/%d' % (wins, n), lo <= p <= hi, (lo, p, hi))
    check('区间夹在 [0,1] 内 %d/%d' % (wins, n), 0.0 <= lo <= hi <= 1.0, (lo, hi))
w30 = calibration._wilson(15, 30)
w120 = calibration._wilson(60, 120)
w800 = calibration._wilson(400, 800)
check('样本越大区间越窄', (w30[1] - w30[0]) > (w120[1] - w120[0]) > (w800[1] - w800[0]),
      (w30[1] - w30[0], w120[1] - w120[0], w800[1] - w800[0]))

print('[3/4] _stat 口径与 ROI 带')
s = calibration._stat({'n': 120, 'wins': 58, 'odds_sum': 222.0, 'inv_sum': 65.0, 'inv_n': 120})
check('命中率 48.3%', near(s['hit_rate'], 48.3, 0.05), s['hit_rate'])
check('区间 39.6%-57.2%', near(s['ci_low'], 39.6, 0.05) and near(s['ci_high'], 57.2, 0.05),
      (s['ci_low'], s['ci_high']))
check('平均赔率 1.85', near(s['avg_odds'], 1.85, 0.005), s['avg_odds'])
check('盈亏线 54.2%', near(s['required_rate'], 54.2, 0.05), s['required_rate'])
check('优势 -5.8pp', near(s['edge'], -5.8, 0.05), s['edge'])
check('ROI -10.6%', near(s['roi'], -10.6, 0.05), s['roi'])
check('ROI 带包住点估计且下界为负', s['roi_low'] < s['roi'] < s['roi_high'] and s['roi_low'] < 0,
      (s['roi_low'], s['roi'], s['roi_high']))
check('ROI 上界与区间上界自洽',
      near(s['roi_high'], (s['ci_high'] / 100.0 * s['avg_odds'] - 1.0) * 100.0, 0.15),
      s['roi_high'])
z = calibration._stat({'n': 0, 'wins': 0, 'odds_sum': 0.0, 'inv_sum': 0.0, 'inv_n': 0})
check('空样本不抛异常且各字段为 0', z['n'] == 0 and z['hit_rate'] == 0.0 and z['roi'] == 0.0
      and z['ci_low'] == 0.0 and z['ci_high'] == 0.0, z)
hi_odds = calibration._stat({'n': 100, 'wins': 40, 'odds_sum': 400.0, 'inv_sum': 100.0, 'inv_n': 100})
lo_odds = calibration._stat({'n': 100, 'wins': 40, 'odds_sum': 160.0, 'inv_sum': 100.0, 'inv_n': 100})
check('同命中率下赔率越高 ROI 越高', hi_odds['roi'] > lo_odds['roi'], (hi_odds['roi'], lo_odds['roi']))
check('赔率 4.0、命中 40% 时 ROI 约 +60%', near(hi_odds['roi'], 60.0, 0.05), hi_odds['roi'])

print('[4/4] apply_calibration 透传 CI 字段')
fake = {
    'min_n': 50, 'min_seg': 30, 'total': 300,
    'buckets': {'中': {'n': 120, 'wins': 61, 'hit_rate': 51.1, 'ci_low': 42.4, 'ci_high': 59.8,
                       'avg_odds': 1.85, 'required_rate': 54.2, 'edge': -3.1,
                       'roi': -5.5, 'roi_low': -21.6, 'roi_high': 10.6}},
    'segments': {'1X2|1.50-2.00': {'n': 60, 'wins': 30, 'hit_rate': 50.0, 'ci_low': 37.7,
                                   'ci_high': 62.3, 'avg_odds': 1.9, 'required_rate': 52.6,
                                   'edge': -2.6, 'roi': -5.0, 'roi_low': -28.4, 'roi_high': 18.4}},
    'leagues': {},
}
_orig = calibration.get_calibration
calibration.get_calibration = lambda force=False: fake
try:
    m = {'confidence_level': '中', 'predicted_option': '胜', 'win_odds': 1.85, 'league': '英超'}
    changed = calibration.apply_calibration([m])
    check('calib 带置信区间', m['calib'] and near(m['calib']['ci_low'], 42.4, 0.05)
          and near(m['calib']['ci_high'], 59.8, 0.05), m['calib'])
    check('calib 带 ROI 带', m['calib'] and near(m['calib']['roi'], -5.5, 0.05)
          and near(m['calib']['roi_low'], -21.6, 0.05), m['calib'])
    check('segment 带置信区间', m['segment'] and near(m['segment']['ci_low'], 37.7, 0.05)
          and near(m['segment']['ci_high'], 62.3, 0.05), m['segment'])
    check('segment 带 ROI 带', m['segment'] and near(m['segment']['roi'], -5.0, 0.05)
          and near(m['segment']['roi_low'], -28.4, 0.05), m['segment'])
    check('弱负优势不降级（未超过 1 个标准误）', changed == 0 and 'calib_downgraded' not in m,
          (changed, m.get('confidence_level')))
    fake_bad = dict(fake)
    fake_bad['segments'] = {'1X2|1.50-2.00': {'n': 200, 'wins': 60, 'hit_rate': 30.0,
                                              'ci_low': 24.2, 'ci_high': 36.5, 'avg_odds': 1.9,
                                              'required_rate': 52.6, 'edge': -22.6,
                                              'roi': -43.0, 'roi_low': -54.0, 'roi_high': -30.6}}
    calibration.get_calibration = lambda force=False: fake_bad
    m2 = {'confidence_level': '中', 'predicted_option': '胜', 'win_odds': 1.85, 'league': '英超'}
    changed2 = calibration.apply_calibration([m2])
    check('硬负优势仍会降级（保留原等级）', changed2 == 1
          and m2.get('confidence_level_raw') == '中' and m2.get('confidence_level') == '低',
          (changed2, m2.get('confidence_level'), m2.get('confidence_level_raw')))
    check('降级场景 segment 仍带 CI', m2['segment'] and near(m2['segment']['ci_low'], 24.2, 0.05),
          m2['segment'])
finally:
    calibration.get_calibration = _orig

print('RESULT:', 'FAIL' if fail else 'ALL OK')
sys.exit(1 if fail else 0)
