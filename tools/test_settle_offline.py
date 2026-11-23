"""离线验证结算链路与统计口径（不联网、不碰线上库）。

覆盖：
  1. _parse_ev_dt / _pick_by_date 的日期消歧（同名球队 7 天两场，不能按"第一个赛果"结算）
  2. settle_finished 端到端：ID/队名兜底 + 比分快照 + 多候选消歧 + 无日期时放弃结算
  3. _eval_play：OU 走盘退本金、TG、CS、HTFT 基本判定
  4. compute_summary：period 真过滤、估算赔率不计入总注数、void 单列不入亏损
  5. calibration._stat：盈亏平衡率用 mean(1/odds) 而不是 1/mean(odds)

用法（必须用装了 Flask 的解释器，并禁止写 __pycache__）：
    $env:PYTHONDONTWRITEBYTECODE='1'
    venv-jingcai\\Scripts\\python.exe tools\\test_settle_offline.py
"""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

TMPDB = os.environ.get('JINGCAI_TMP_DB') or r'C:\Users\HP\Documents\deepseek-harness\default-workspace\tmp_settle_test.db'
for _p in (TMPDB, TMPDB + '-journal'):
    if os.path.exists(_p):
        os.remove(_p)
os.environ['DATABASE_URL'] = 'sqlite:///' + TMPDB.replace('\\', '/')
os.environ.setdefault('SECRET_KEY', 'test-only')
os.environ['SCHEDULER_ENABLED'] = '0'   # 测试进程不要拉起后台调度线程

from app import app                                    # noqa: E402
import backtest as bt                                  # noqa: E402
import bizzoiro_client as bz                           # noqa: E402
import calibration                                     # noqa: E402
import data_pipeline as dp                             # noqa: E402
from backtest_models import BtBet, db                  # noqa: E402

OK = True


def check(name, cond, extra=''):
    global OK
    print(('  OK   ' if cond else '  FAIL ') + name + (('  ' + str(extra)) if extra else ''))
    OK = OK and bool(cond)


def _utc(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def main():
    print('[1] 日期解析 / 消歧')
    ev = {'commence_utc': '2026-11-23T11:30:00Z'}
    check('commence_utc 可解析', dp._parse_ev_dt(ev) == datetime(2026, 11, 23, 11, 30))
    check('无日期字段返回 None', dp._parse_ev_dt({'home_team': 'x'}) is None)

    base = datetime(2026, 11, 23, 20, 0)
    near = {'commence_utc': _utc(base - timedelta(hours=2))}
    far = {'commence_utc': _utc(base - timedelta(days=9))}
    check('唯一候选直接采用', dp._pick_by_date([near], base) is near)
    check('多候选按最近日期', dp._pick_by_date([far, near], base) is near)
    check('超出窗口的候选被排除', dp._pick_by_date([far], base + timedelta(days=30)) is far)
    check('多候选且无日期信息则放弃', dp._pick_by_date([{}, {}], base) is None)

    print('[2] settle_finished 端到端')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_ev = {
        'id': '9001', 'home_team': '甲队', 'away_team': '乙队',
        'home_score': 2, 'away_score': 1, 'home_score_ht': 1, 'away_score_ht': 0,
        'commence_utc': _utc(now - timedelta(hours=1)),
    }
    old_ev = {
        'id': '9002', 'home_team': '甲队', 'away_team': '乙队',
        'home_score': 0, 'away_score': 3,
        'commence_utc': _utc(now - timedelta(days=6)),
    }
    # 同名对阵、都没有日期字段 → 无法消歧，必须放弃结算
    amb_a = {'id': '9101', 'home_team': '丙队', 'away_team': '丁队', 'home_score': 1, 'away_score': 0}
    amb_b = {'id': '9102', 'home_team': '丙队', 'away_team': '丁队', 'home_score': 0, 'away_score': 1}

    with app.app_context():
        db.create_all()
        today_bet = BtBet(match_id='周一201', home_team='甲队', away_team='乙队',
                          play_type='1X2', pick='H', odds=1.8, stake=1.0,
                          jingcai=True, created_at=now)
        old_bet = BtBet(match_id='周一202', home_team='甲队', away_team='乙队',
                        play_type='1X2', pick='H', odds=1.8, stake=1.0,
                        jingcai=True, created_at=now - timedelta(days=6))
        amb_bet = BtBet(match_id='周一203', home_team='丙队', away_team='丁队',
                        play_type='1X2', pick='H', odds=1.9, stake=1.0,
                        jingcai=True, created_at=now)
        db.session.add_all([today_bet, old_bet, amb_bet])
        db.session.commit()

        bz.API_KEY = 'offline-test'
        bz.fetch_finished_events = lambda: [today_ev, old_ev, amb_a, amb_b]
        n = dp.settle_finished()
        db.session.expire_all()

        t = BtBet.query.filter_by(match_id='周一201').first()
        o = BtBet.query.filter_by(match_id='周一202').first()
        a = BtBet.query.filter_by(match_id='周一203').first()

        check('结算了 2 条（第三条歧义放弃）', n == 2, 'n=%s' % n)
        check('今天的注单按今天赛果判胜', t.outcome == 'win' and abs((t.pnl or 0) - 0.8) < 1e-9,
              '%s/%s' % (t.outcome, t.pnl))
        check('6 天前的注单按 6 天前赛果判负（不串场）', o.outcome == 'lose' and abs((o.pnl or 0) + 1.0) < 1e-9,
              '%s/%s' % (o.outcome, o.pnl))
        check('比分快照已写入', (t.home_score, t.away_score, t.home_score_ht, t.away_score_ht) == (2, 1, 1, 0),
              (t.home_score, t.away_score, t.home_score_ht, t.away_score_ht))
        check('歧义场次保持未结算', a.outcome is None and a.settled_at is None,
              '%s/%s' % (a.outcome, a.settled_at))

        print('[3] _eval_play 边界')
        ou = BtBet(match_id='T1', play_type='OU', pick='O30', odds=1.95, stake=1.0, jingcai=True)
        check('大小球走盘退本金', bt._eval_play(ou, 'H', 2, 1, None, None, 1.0) == ('void', 0.0))
        ou2 = BtBet(match_id='T2', play_type='OU', pick='O25', odds=1.95, stake=1.0, jingcai=True)
        check('大小球大球命中', bt._eval_play(ou2, 'H', 2, 1, None, None, 1.0)[0] == 'win')
        tg = BtBet(match_id='T3', play_type='TG', pick='3', odds=4.0, stake=1.0, jingcai=True)
        check('总进球精确命中', bt._eval_play(tg, 'H', 2, 1, None, None, 1.0)[0] == 'win')
        cs = BtBet(match_id='T4', play_type='CS', pick='2-1', odds=7.0, stake=1.0, jingcai=True)
        check('比分命中', bt._eval_play(cs, 'H', 2, 1, None, None, 1.0)[0] == 'win')
        htft = BtBet(match_id='T5', play_type='HTFT', pick='HH', odds=9.0, stake=1.0, jingcai=True)
        check('半全场命中', bt._eval_play(htft, 'H', 2, 1, 1, 0, 1.0)[0] == 'win')
        check('半全场缺半场比分判作废', bt._eval_play(htft, 'H', 2, 1, None, None, 1.0) == ('void', 0.0))

        print('[4] compute_summary 口径')
        for b in BtBet.query.filter(BtBet.match_id.like('周一2%')).all():
            db.session.delete(b)
        db.session.commit()
        d7 = _utc(datetime.now() - timedelta(days=1))
        d30 = _utc(datetime.now() - timedelta(days=30))
        rows = [
            BtBet(match_id='S1', play_type='1X2', pick='H', odds=2.0, stake=1.0, pnl=1.0,
                  outcome='win', settled_at=d7, jingcai=True, estimated=False),
            BtBet(match_id='S2', play_type='1X2', pick='H', odds=2.0, stake=1.0, pnl=-1.0,
                  outcome='lose', settled_at=d7, jingcai=True, estimated=False),
            BtBet(match_id='S3', play_type='1X2', pick='H', odds=2.0, stake=1.0, pnl=0.0,
                  outcome='void', settled_at=d7, jingcai=True, estimated=False),
            BtBet(match_id='S4', play_type='CS', pick='1-0', odds=8.0, stake=1.0, pnl=7.0,
                  outcome='win', settled_at=d7, jingcai=True, estimated=True),
            BtBet(match_id='S5', play_type='1X2', pick='H', odds=2.0, stake=1.0, pnl=1.0,
                  outcome='win', settled_at=d30, jingcai=True, estimated=False),
            BtBet(match_id='S6', play_type='1X2', pick='H', odds=2.0, stake=1.0, jingcai=True),
        ]
        db.session.add_all(rows)
        db.session.commit()

        s_all = bt.compute_summary('all')
        check('估算赔率不计入总注数', s_all['total_bets'] == 4, s_all['total_bets'])
        check('void 单列且不计入亏损', s_all['voids'] == 1 and s_all['losses'] == 1, s_all['losses'])
        check('命中率只按胜负计算', s_all['hit_rate'] == 66.7, s_all['hit_rate'])
        check('ROI 只按已决注单本金', abs(s_all['roi'] - 33.3) < 1e-6, s_all['roi'])
        check('估算玩法单独计数', s_all['estimated_total'] == 1, s_all['estimated_total'])
        check('待结算数量', s_all['pending'] == 1, s_all['pending'])

        s7 = bt.compute_summary('7d')
        check('period=7d 真正过滤掉 30 天前的注单', s7['total_bets'] == 3, s7['total_bets'])
        check('period=7d 命中率', s7['hit_rate'] == 50.0, s7['hit_rate'])

        print('[5] calibration 盈亏平衡率')
        d = {}
        calibration._add(d, 'k', types.SimpleNamespace(odds=1.5, outcome='win'))
        calibration._add(d, 'k', types.SimpleNamespace(odds=3.0, outcome='lose'))
        st = calibration._stat(d['k'])
        check('用 mean(1/odds) 得 50.0%', st['required_rate'] == 50.0, st['required_rate'])
        check('1/mean 的旧算法会是 44.4%', round((1 / st['avg_odds']) * 100, 1) == 44.4, st['avg_odds'])
        check('赔率缺失不进 break-even', calibration._stat(
            {'n': 1, 'wins': 0, 'odds_sum': 0.0, 'inv_sum': 0.0, 'inv_n': 0})['required_rate'] == 0.0)

    print('RESULT: ' + ('ALL OK' if OK else 'FAIL'))
    try:
        os.remove(TMPDB)
    except OSError:
        pass
    return 0 if OK else 1


if __name__ == '__main__':
    sys.exit(main())
