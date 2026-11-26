# -*- coding: utf-8 -*-
"""Bzzoiro 时间字段回归测试（无需网络/数据库）。

用法：
  python tools/test_bzzoiro_time.py
退出码：0=通过，1=有失败

覆盖：event_date(UTC) -> 北京时间 match_time；竞彩销售日 date（凌晨场次归前一天）；
编号按销售日分组且星期偏移正确（周四001 而不是周三001）；match_time_util 与本模块口径一致；
已开赛事件（commence 早于 now-2h）被过滤。
"""
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bizzoiro_client as bz
import match_time_util as mtu

FAIL = 0


def check(name, cond, detail=''):
    global FAIL
    if cond:
        print('  OK   %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s %s' % (name, detail))


def _event(eid, iso, home='Ajax', away='Thun', odds=(1.15, 8.15, 13.21)):
    return {'id': eid, 'event_date': iso, 'home_team': home, 'away_team': away,
            'league': {'name': 'Conference League', 'id': 17},
            'odds_home': odds[0], 'odds_draw': odds[1], 'odds_away': odds[2]}


def main():
    # 1) 深夜场：2026-11-26 23:30 北京时间（周四晚）
    m_late = bz._parse_event_to_match(_event(601340, '2026-11-26T15:30:00Z',
                                             'FC Kairat Almaty', 'Mjallby'))
    check('深夜场 match_time=23:30', m_late['match_time'] == '23:30', m_late['match_time'])
    check('深夜场 销售日=2026-11-26', m_late['date'] == '2026-11-26', m_late['date'])
    check('深夜场 commence 带 +08:00',
          m_late['commence'].startswith('2026-11-26T23:30:00+08:00'), m_late['commence'])

    # 2) 凌晨场：2026-11-27 01:45 北京时间，销售日应归 11-26
    m_early = bz._parse_event_to_match(_event(601319, '2026-11-26T17:45:00Z'))
    check('凌晨场 match_time=01:45', m_early['match_time'] == '01:45', m_early['match_time'])
    check('凌晨场 销售日归前一天', m_early['date'] == '2026-11-26', m_early['date'])
    check('凌晨场 commence 次日 01:45',
          m_early['commence'].startswith('2026-11-27T01:45:00+08:00'), m_early['commence'])

    # 3) 编号：同销售日两场，按开赛时间排序，星期名取销售日（周四）
    m_early2 = bz._parse_event_to_match(_event(601387, '2026-11-26T17:45:00Z',
                                              'Egnatia', 'Lincoln'))
    group = [m_early, m_late, m_early2]
    bz._assign_match_ids(group)
    ids = [m['match_id'] for m in group]
    check('编号全部为周四', all(i.startswith('周四') for i in ids), ids)
    check('编号唯一', len(set(ids)) == 3, ids)
    check('23:30 场排在同销售日最早（周四001）', m_late['match_id'] == '周四001', m_late['match_id'])

    # 4) match_time_util 口径一致：深夜场当晚开赛、凌晨场次日开赛
    check('深夜场 kickoff=2026-11-26 23:30',
          mtu.kickoff_dt(m_late) == datetime(2026, 11, 26, 23, 30), str(mtu.kickoff_dt(m_late)))
    check('凌晨场 kickoff=2026-11-27 01:45',
          mtu.kickoff_dt(m_early) == datetime(2026, 11, 27, 1, 45), str(mtu.kickoff_dt(m_early)))
    check('深夜场 22:00 未开赛', mtu.started(m_late, datetime(2026, 11, 26, 22, 0)) is False)
    check('深夜场 23:31 已开赛', mtu.started(m_late, datetime(2026, 11, 26, 23, 31)) is True)
    check('凌晨场 01:00 未开赛', mtu.started(m_early, datetime(2026, 11, 27, 1, 0)) is False)

    # 5) 已开赛过滤
    past = dict(m_late)
    past['commence'] = '2026-11-25T23:30:00+08:00'
    check('commence 在 24 小时前 -> 视为已开赛', bz._already_started(past) is True)
    future = dict(m_late)
    future['commence'] = '2099-11-26T23:30:00+08:00'
    check('commence 在未来 -> 未开赛', bz._already_started(future) is False)
    check('commence 缺失 -> 不误杀', bz._already_started({'match_time': '01:45'}) is False)

    # 6) 解析失败兜底
    bad = bz._parse_event_to_match(_event(1, 'not-a-date'))
    check('脏 event_date -> date 空', bad['date'] == '', bad['date'])
    check('脏 event_date -> commence 空', bad['commence'] == '', bad['commence'])

    print('')
    if FAIL:
        print('RESULT: %d FAILED' % FAIL)
        return 1
    print('RESULT: ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
