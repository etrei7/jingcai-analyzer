"""比赛时间工具：从 (businessDate, match_time) 推算开赛时刻并判断是否已开赛。

竞彩按"销售日"归类：同一 businessDate 下，00:00–11:59 的场次实际在**次日凌晨**开赛
（如「周三004 01:00」= 周四 01:00）。故 hour<12 时顺延一天。
"""
from datetime import datetime, timedelta


def kickoff_dt(m):
    d = m.get('date')
    t = (m.get('match_time') or m.get('time') or '')[:5]
    if not d or len(t) < 5 or ':' not in t:
        return None
    try:
        dt = datetime.strptime('%s %s' % (d, t), '%Y-%m-%d %H:%M')
    except Exception:
        return None
    if dt.hour < 12:
        dt = dt + timedelta(days=1)
    return dt


def started(m, now=None):
    """已开赛返回 True；无法判断时间时返回 False（不误杀）。"""
    dt = kickoff_dt(m)
    if dt is None:
        return False
    return (now or datetime.now()) >= dt
