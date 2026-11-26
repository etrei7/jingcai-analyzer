# -*- coding: utf-8 -*-
"""Edge 差值榜回归测试（无需数据库/网络）。

用法：
  python tools/test_edge_board.py
退出码：0=通过，1=有失败

覆盖的口径（与 analysis._pick_by_ev 同参）：已开赛剔除、模型概率缺失剔除、
赔率上限 8、概率下限 10%、凯利 >1%、可下注优先排序、同类场次命中率注入。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import calibration
import edge_board

FAIL = 0

# ---- 训练/校准数据替身：不碰数据库 ------------------------------------------
_SEGS = {
    '1X2|1.50-2.00': {'n': 120, 'wins': 58, 'hit_rate': 48.0,
                      'avg_odds': 1.85, 'required_rate': 52.0, 'edge': -4.0},
    '1X2|5.00-10.0': {'n': 12, 'wins': 1, 'hit_rate': 8.0,
                      'avg_odds': 6.5, 'required_rate': 15.0, 'edge': -7.0},
}
_real_get = calibration.get_calibration


def _fake_cal():
    return {'buckets': {}, 'segments': dict(_SEGS), 'leagues': {},
            'total': 132, 'min_n': 50, 'min_seg': 30, 'updated': 'test'}


def check(name, cond, detail=''):
    global FAIL
    if cond:
        print('  OK   %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, detail))


def _match(mid, hw, hd, ha, mp, started=False, league='测试联赛'):
    """mp: (p_win, p_draw, p_lose) 模型概率（百分数）"""
    total = 1.0 / hw + 1.0 / hd + 1.0 / ha
    m = {
        'match_id': mid, 'home_team': 'A' + mid, 'away_team': 'B' + mid,
        'league': league, 'match_time': '19:30',
        'win_odds': hw, 'draw_odds': hd, 'lose_odds': ha,
        'started': started,
        'market_prob': {'win': round(100.0 / hw / total, 1),
                        'draw': round(100.0 / hd / total, 1),
                        'lose': round(100.0 / ha / total, 1)},
    }
    if mp is not None:
        m['model_prob'] = {'win': mp[0], 'draw': mp[1], 'lose': mp[2]}
    return m


def main():
    calibration.get_calibration = _fake_cal
    try:
        # 可下注：主胜 p=58% vs 市场 51.7%，凯利约 8.6%
        ok_m = _match('001', 1.85, 3.60, 4.40, (58.0, 24.0, 18.0))
        board = edge_board.build_board([ok_m])
        rows = board['rows']
        check('可下注场次进榜', len(rows) == 1, rows)
        r = rows[0] if rows else {}
        check('选中方向为主胜', r.get('option') == '胜', r.get('option'))
        check('模型概率原样透出', r.get('model_prob') == 58.0, r.get('model_prob'))
        check('edge = 模型 - 市场（pp）',
              r.get('edge_pp') == round(58.0 - r.get('market_prob', 0), 1), r)
        check('EV = p·odds - 1', abs(r.get('ev', 0) - (0.58 * 1.85 - 1)) < 5e-3, r.get('ev'))
        check('凯利 f = (p·odds-1)/(odds-1)',
              abs(r.get('kelly', 0) - (0.58 * 1.85 - 1) / 0.85) < 5e-3, r.get('kelly'))
        check('标记为可下注', r.get('actionable') is True, r.get('blocked_reasons'))
        check('summary 计数一致',
              board['summary']['actionable'] == 1 and board['summary']['evaluated'] == 1,
              board['summary'])
        check('同类场次命中率注入（1.50-2.00）',
              r.get('segment', {}).get('bucket') == '1.50-2.00', r.get('segment'))
        check('历史不盈利 -> segment_warn', r.get('segment_warn') is True, r.get('segment'))
        check('note 写明前视风险', '前视' in (board.get('note') or ''), board.get('note'))

        # 已开赛 / 模型概率缺失：一律不进榜
        started = _match('002', 1.85, 3.60, 4.40, (58.0, 24.0, 18.0), started=True)
        nop = _match('003', 1.85, 3.60, 4.40, None)
        board2 = edge_board.build_board([started, nop])
        check('已开赛剔除', len(board2['rows']) == 0, board2['rows'])
        check('模型概率缺失剔除', board2['summary']['evaluated'] == 0, board2['summary'])

        # 赔率上限：9.0 倍即使凯利为正也不可下注
        high = _match('004', 1.40, 4.60, 9.00, (66.0, 18.0, 16.0))
        b3 = edge_board.build_board([high])
        row3 = b3['rows'][0] if b3['rows'] else {}
        check('>8 倍赔率标为不可下注', row3.get('actionable') is False, row3)
        check('>8 倍给出口径理由',
              any('赔率' in x for x in row3.get('blocked_reasons', [])), row3.get('blocked_reasons'))

        # 概率下限：8% 的冷门不可下注
        lowp = _match('005', 1.30, 5.00, 12.0, (74.0, 18.0, 8.0))
        b4 = edge_board.build_board([lowp])
        row4 = b4['rows'][0] if b4['rows'] else {}
        check('低概率选项标为不可下注', row4.get('actionable') is False, row4)

        # 排序：可下注优先，其后按凯利降序
        b5 = edge_board.build_board([high, ok_m, lowp])
        acts = [x['actionable'] for x in b5['rows']]
        check('可下注排在不可下注之前', acts == sorted(acts, reverse=True), acts)
        kellys = [x['kelly'] for x in b5['rows'] if not x['actionable']]
        check('不可下注内部按凯利降序', kellys == sorted(kellys, reverse=True), kellys)

        # limit 生效
        b6 = edge_board.build_board([high, ok_m, lowp], limit=1)
        check('limit 生效', len(b6['rows']) == 1, b6['rows'])
        # include_blocked=False 只留可下注
        b7 = edge_board.build_board([high, ok_m, lowp], include_blocked=False)
        check('include_blocked=False 只留可下注',
              [x['actionable'] for x in b7['rows']] == [True], b7['rows'])
        # 样本不足的赔率区间不注入命中率
        seg = edge_board._segment_for(6.5)
        check('同类场次样本不足则不注入', seg is None, seg)
    finally:
        calibration.get_calibration = _real_get

    print('RESULT:', 'FAIL' if FAIL else 'ALL OK')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
