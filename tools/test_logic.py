"""核心逻辑回归测试（无需数据库/网络）。改动分析或结算逻辑后建议运行。

用法：
  python tools/test_logic.py
退出码：0=通过，1=有失败
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analysis as an
import backtest as bt

FAIL = 0


def check(name, cond, detail=''):
    global FAIL
    if cond:
        print('  OK   %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, detail))


class _B:
    def __init__(self, pt, pick, odds=1.9):
        self.play_type = pt
        self.pick = pick
        self.odds = odds


def ev(b, hs, aw, hht=None, awt=None):
    actual = 'H' if hs > aw else 'A' if hs < aw else 'D'
    return bt._eval_play(b, actual, hs, aw, hht, awt, 1.0)


def main():
    print('[1] 数学模型归一化 / 边界')
    check('poisson 归一', abs(sum(an._poisson_prob(k, 2.5) for k in range(30)) - 1) < 1e-6)
    for lh, la in [(1.5, 1.1), (2.3, 0.7), (0.9, 0.9), (3.5, 0.3)]:
        p = an._dixon_coles_probs(lh, la)
        check('DixonColes 归一 %s' % ((lh, la),), p is not None and abs(sum(p) - 1) < 1e-6)
    check('devig 归一', abs(sum(an._devig(2.1, 3.4, 3.2)) - 1) < 1e-6)
    check('kelly 封顶 0.25', abs(an._kelly(0.5, 3.0) - 0.25) < 1e-9)
    check('kelly 期望 0.10', abs(an._kelly(0.4, 3.0) - 0.10) < 1e-9)
    check('kelly 负值→0', an._kelly(0.3, 3.0) == 0.0)
    check('kelly 边界(odds<=1/None)', an._kelly(0.5, 1) == 0.0 and an._kelly(None, 2) == 0.0)

    print('[2] 结算（各玩法）')
    check('1X2 命中', ev(_B('1X2', 'H'), 2, 1)[0] == 'win')
    check('1X2 未中', ev(_B('1X2', 'H'), 1, 1)[0] == 'lose')
    check('AH 主让1 2-0 让胜中', ev(_B('AH', 'H|-1'), 2, 0)[0] == 'win')
    check('AH 主让1 2-1 让平挂', ev(_B('AH', 'H|-1'), 2, 1)[0] == 'lose')
    check('AH 主让1 1-1 挂', ev(_B('AH', 'H|-1'), 1, 1)[0] == 'lose')
    check('AH 主让1 2-1 让平中', ev(_B('AH', 'D|-1'), 2, 1)[0] == 'win')
    check('AH 主受让1 1-1 中', ev(_B('AH', 'H|1'), 1, 1)[0] == 'win')
    check('AH 不让球 0盘等同1X2', ev(_B('AH', 'H|0'), 2, 1)[0] == 'win')
    check('CS 命中', ev(_B('CS', '1-0'), 1, 0)[0] == 'win')
    check('CS 未中', ev(_B('CS', '1-0'), 2, 0)[0] == 'lose')
    check('HTFT 命中', ev(_B('HTFT', 'HH'), 2, 0, 1, 0)[0] == 'win')
    check('HTFT 未中', ev(_B('HTFT', 'HH'), 2, 0, 0, 0)[0] == 'lose')
    check('HTFT 缺半场→void', ev(_B('HTFT', 'HH'), 2, 0, None, None)[0] == 'void')

    print('[3] 半全场赔率反推')
    r = an._htft_from_odds({'hafu_hh': 3.2, 'hafu_hd': 15, 'hafu_ha': 26, 'hafu_dh': 7.5,
                            'hafu_dd': 6.0, 'hafu_da': 9.5, 'hafu_ah': 15, 'hafu_ad': 6.5, 'hafu_aa': 4.5})
    check('pick=胜胜 & 来源竞彩', bool(r) and r['pick'] == '胜胜' and r['source'] == '竞彩官方')
    check('不足3项→None', an._htft_from_odds({'hafu_hh': 3.2, 'hafu_dd': 6.0}) is None)

    print('[4] 串关组合生成')
    from data_generator import generate_matches
    anl = an.analyze_matches(generate_matches(12), {}, {})
    recs = an.generate_parlay_recommendations(anl)
    check('有推荐方案', len(recs) > 0)
    check('方案含每场明细', all('matches_detail' in r for r in recs))
    check('组合赔率>0', all((r.get('combo_odds') or 0) > 0 for r in recs))

    print('[5] 缺字段/None 不崩')
    bad_cases = [
        {'match_id': 'x1'},
        {'match_id': 'x2', 'win_odds': None, 'draw_odds': None, 'lose_odds': None},
        {'match_id': 'x3', 'home_team': None, 'away_team': None, 'league': None,
         'win_odds': 2.0, 'draw_odds': 3.3, 'lose_odds': 3.5},
        {'match_id': 'x4', 'home_team': 'A', 'away_team': 'B',
         'win_odds': 1.01, 'draw_odds': 100, 'lose_odds': 100},
    ]
    for bad in bad_cases:
        try:
            an.analyze_single_match(dict(bad))
            check('归一化 %s' % bad['match_id'], True)
        except Exception as e:
            check('归一化 %s' % bad['match_id'], False, '%s: %s' % (type(e).__name__, e))

    print('RESULT:', 'FAIL' if FAIL else 'ALL OK')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
