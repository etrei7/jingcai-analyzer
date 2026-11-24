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
    check('kelly 分数凯利封顶 0.05', abs(an._kelly(0.5, 3.0) - 0.05) < 1e-9)
    check('kelly 1/4 期望 0.025', abs(an._kelly(0.4, 3.0) - 0.025) < 1e-9)
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

    print('[4] 让球线方向一致性')
    def _hc(wo, do, lo):
        return an._compute_handicap({'win_odds': wo, 'draw_odds': do, 'lose_odds': lo}, None, 0.5, 0.5, 0, 0)
    strong_home = _hc(1.30, 5.5, 9.0)
    even = _hc(2.70, 3.2, 2.60)
    strong_away = _hc(9.0, 5.5, 1.30)
    check('强主队 line<=0', strong_home['handicap_line'] <= 0)
    check('强客队 line>=0', strong_away['handicap_line'] >= 0)
    check('势均力敌 line==0', even['handicap_line'] == 0)
    check('hcp_pick 结构完整', all(k in strong_home['hcp_pick'] for k in ('option', 'prob', 'odds', 'side')))
    check('让球赔率在合理区间[1.05,16.7]',
          all(1.05 <= _safe <= 16.7 for _safe in (strong_home['handicap_win_odds'],
                                                  strong_home['handicap_draw_odds'],
                                                  strong_home['handicap_lose_odds'])))

    print('[5] 串关组合生成')
    from data_generator import generate_matches
    anl = an.analyze_matches(generate_matches(12), {}, {})
    recs = an.generate_parlay_recommendations(anl)
    check('有推荐方案', len(recs) > 0)
    check('方案含每场明细', all('matches_detail' in r for r in recs))
    check('组合赔率>0', all((r.get('combo_odds') or 0) > 0 for r in recs))

    print('[6] 缺字段/None 不崩')
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

    print('[7] 假赔率 / EV / 前视防护')
    # 让球：只有官方 hhad 赔率才是真盘，模型推算必须标 estimated（否则串关返奖被放大）
    base = {'win_odds': 2.0, 'draw_odds': 3.3, 'lose_odds': 3.5, 'handicap_line': -1,
            'handicap': '-1', 'handicap_win_odds': 1.8, 'handicap_draw_odds': 3.6,
            'handicap_lose_odds': 4.2}

    def _hcp_est(m):
        for o in an._match_play_options(m):
            if o['play'].startswith('让球'):
                return o['estimated']
        return None

    check('模型让球标 estimated', _hcp_est(dict(base, hcp_pick={'source': '模型估算'})) is True)
    check('官方让球不标 estimated', _hcp_est(dict(base, hcp_pick={'source': '官方盘口'})) is False)
    # 价值判断必须用 EV，而不是百分点差
    va = an._value_analysis(1.5, 4.0, 7.0, 1.6, 0.9)
    check('value_analysis 含 EV 字段',
          all(k in va for k in ('ev_home', 'ev_draw', 'ev_away', 'best_ev')))
    check('value_available 与 best_ev 一致',
          va.get('value_available') == (va.get('best_ev', 0) > 0))
    check('value_source 标注市场推导', va.get('value_source') == 'market_implied')
    # 高抽水（三路同价）时即便百分点差为正，EV 也全为负 → 不得标"有价值"
    heavy_vig = an._value_analysis(1.05, 1.05, 1.05, 1.5, 1.0)
    check('高抽水不判为有价值', heavy_vig.get('value_available') is False)

    print('[8] 仓位闸门：低信心日不得出现"稳健可多投"')
    low = an._stake_plan(2.16, 0.30)
    check('低信心封顶 2%', low['pct'] == 2.0, low['pct'])
    check('低信心措辞为小注试探', '低信心' in low['note'], low['note'])
    mid = an._stake_plan(2.67, 0.50)
    check('中信心封顶 3.5%', mid['pct'] == 3.5, mid['pct'])
    high = an._stake_plan(2.16, 0.70)
    check('高信心才允许稳健可多投', '稳健可多投' in high['note'] and high['pct'] <= 6.0, high['note'])
    risky = an._stake_plan(12.0, 0.70)
    check('高赔仍是高风险小额', '高风险' in risky['note'], risky['note'])
    # 等级闸门：平均信心被高赔热门拉高时，仍按校准等级压档
    lv_mid = an._stake_plan(2.9, 0.66, '', ['中', '中'])
    check('含中档腿封顶 3.5%', lv_mid['pct'] == 3.5, lv_mid['pct'])
    check('含中档腿措辞为常规仓位', '信心中等' in lv_mid['note'], lv_mid['note'])
    lv_low = an._stake_plan(2.9, 0.66, '', ['高', '低'])
    check('含低档腿封顶 2%', lv_low['pct'] == 2.0, lv_low['pct'])
    lv_high = an._stake_plan(2.9, 0.66, '', ['高', '高'])
    check('全高档仍允许稳健可多投', '稳健可多投' in lv_high['note'], lv_high['note'])
    lv_jackpot = an._stake_plan(245.0, 0.66, '', ['高', '高', '中', '中'])
    check('超高赔必须提示高风险', '高风险' in lv_jackpot['note'], lv_jackpot['note'])

    print('[9] 选题口径：按单位风险期望收益（凯利分数）选，不按概率、也不按裸 EV')
    # 主胜概率最高（47%）但 1.6 的赔率没有价值；客胜 24% 配 5.2 的赔率凯利分数最高
    opt, edge, evs, no_bet = an._pick_by_ev(
        {'胜': 0.47, '平': 0.29, '负': 0.24}, {'胜': 1.60, '平': 3.80, '负': 5.20})
    check('不选概率最高的主胜', opt == '负', opt)
    check('EV 值可复现', abs(edge - (0.24 * 5.2 - 1)) < 1e-6, edge)
    check('有合格选项不算 no_bet', no_bet is False)
    # 裸 EV 最大的高赔冷门必须被赔率上限挡掉（赔率 10 时 12% 的概率误差就值 120% EV）
    opt_j, edge_j, _ev_j, no_bet_j = an._pick_by_ev(
        {'胜': 0.50, '平': 0.28, '负': 0.12}, {'胜': 1.70, '平': 3.30, '负': 10.0})
    check('高赔冷门不因裸 EV 入选', opt_j != '负' or no_bet_j is True, (opt_j, edge_j))
    # 全负期望：一律 no_bet，但仍给出概率最高的那个选项用于展示
    opt2, edge2, _ev2, no_bet2 = an._pick_by_ev(
        {'胜': 0.50, '平': 0.28, '负': 0.22}, {'胜': 1.70, '平': 3.20, '负': 4.60})
    check('全负期望标 no_bet', no_bet2 is True, edge2)
    check('no_bet 仍返回可展示的选项', opt2 in ('胜', '平', '负'), opt2)
    # 概率低于 18% 的选项不参选（样本太稀，无法验证）
    opt_l, _ed_l, _ev_l, _nb_l = an._pick_by_ev(
        {'胜': 0.60, '平': 0.25, '负': 0.15}, {'胜': 1.50, '平': 3.90, '负': 7.0})
    check('低概率高赔不参选', opt_l != '负', opt_l)
    # 赔率缺失的选项不得被选中
    opt3, edge3, evs3, _ = an._pick_by_ev(
        {'胜': 0.40, '平': 0.30, '负': 0.30}, {'胜': None, '平': 3.6, '负': 0.0})
    check('缺赔率的选项不参选', opt3 == '平', (opt3, edge3))
    check('缺赔率记为 -9', evs3['胜'] == -9.0 and evs3['负'] == -9.0, evs3)

    print('RESULT:', 'FAIL' if FAIL else 'ALL OK')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
