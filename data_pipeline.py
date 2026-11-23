"""数据流水线：定时拉取赛事+赔率并落库，赛后回填结果，形成回测闭环。
基于 Bzzoiro（后端可访问）；sporttery 在服务器端 403，故竞彩官方数据走前端，此处仅回测。
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run_pipeline():
    """原 Bzzoiro 全量采集已停用：战绩只统计竞彩官方开售场次，采集改由前端
    /api/analyze（竞彩场次）经 record_jingcai_plays 写入。保留入口以兼容定时任务。"""
    logger.info('[pipeline] Bzzoiro 全量采集已停用（回测只统计竞彩官方场次）')
    return 0


def _week_stamp(match_id, when=None):
    """给竞彩编号（周一001…周日014）换算它所属那周的日期。

    竞彩编号每周从 001 重排，去重键只用 (编号, 玩法) 会把下一周同编号的场次当成
    "已记录"静默吞掉——表现为某些天战绩凭空少一批。这里按"该编号星期X 离 when 最近
    的那天"折算成日期（前后 3 天窗口：周一凌晨补跑周一场次、周二补看周一晚场都不会跳周），
    同一天内多次刷新仍得到同一 stamp，不会重复入库。
    """
    s = str(match_id or '')
    if len(s) < 2 or not s.startswith('周'):
        return ''
    idx = '一二三四五六日'.find(s[1])
    if idx < 0:
        return ''
    now = when if isinstance(when, datetime) else datetime.now()
    delta = (idx - now.weekday()) % 7
    if delta > 3:
        delta -= 7
    return (now + timedelta(days=delta)).strftime('%Y-%m-%d')


def _dedupe_key(match_id, play_type, when=None):
    """回测去重键：有 Bzzoiro 事件ID 就按 ID；竞彩编号则带周日期，避免跨周串号。"""
    mid = str(match_id or '')
    stamp = _week_stamp(mid, when)
    return (('%s|%s' % (stamp, mid)) if stamp else mid, play_type)


def record_jingcai_plays(matches):
    """把竞彩官方场次写入回测（仅匹配到 Bzzoiro 事件ID的场次，便于赛后按ID精确结算）。
    - 只记真实赔率玩法(1X2/AH)，估算玩法(比分/半全场)不记，避免噪声；
    - 按 (match_id, play_type) 去重，重复刷新不重复入库。
    返回处理的场次数。"""
    if not matches:
        return 0
    try:
        import backtest as bt
        from backtest_models import BtBet
        try:
            existing = {_dedupe_key(b.match_id, b.play_type, b.created_at)
                        for b in BtBet.query.filter_by(jingcai=True).all()}
        except Exception:
            existing = set()
        n = 0
        skipped_started = 0
        for m in matches:
            # 前视防护：已开赛/已完赛的场次不得再写"预测"。否则用户赛后刷新页面、
            # 或定时任务补跑时，会用赛后已知的首发/伤停/赔率占位，命中率接近 100%，
            # 把回测基线整体抬高（属于最致命的样本污染）。
            if m.get('started') or m.get('status') in ('live', 'finished'):
                skipped_started += 1
                continue
            # 优先 Bzzoiro 事件ID（可精确结算）；无匹配时用竞彩编号，结算走队名兜底，
            # 保证每场竞彩推荐都能进入回测闭环（提高结算覆盖率）。
            eid = str(m.get('bz_event_id') or '') or str(m.get('match_id') or '')
            if not eid:
                continue
            try:
                _record_match_plays(bt, m, eid, jingcai=True,
                                    include_estimated=True, existing_keys=existing)
                for _pt in ('1X2', 'AH', 'TG', 'HTFT', 'CS'):
                    existing.add(_dedupe_key(eid, _pt))
                n += 1
            except Exception as e:
                logger.warning('[pipeline] jingcai play record error: %s', e)
                continue
        if skipped_started:
            logger.info('[pipeline] 跳过已开赛场次 %d 场（前视防护）', skipped_started)
        logger.info('[pipeline] 竞彩回测记录 %d 场', n)
        return n
    except Exception as e:
        logger.warning('[pipeline] record_jingcai_plays error: %s', e)
        return 0


def _record_match_plays(bt, m, mid, jingcai=False, include_estimated=True, existing_keys=None):
    """为单场比赛记录赔率快照 + 多玩法预测（1X2/AH/CS/HTFT）。单场异常不影响他场。
    jingcai=True 标记为竞彩官方场次；include_estimated=False 只记真实赔率玩法(1X2/AH)。"""
    # 已记录过则跳过快照，避免每次刷新重复写入
    if not (existing_keys is not None and _dedupe_key(mid, '1X2') in existing_keys):
        bt.record_odds_snapshot(
            match_id=mid, market='1X2',
            home=m.get('win_odds'), draw=m.get('draw_odds'), away=m.get('lose_odds'),
            source='竞彩官方' if jingcai else 'Bzzoiro'
        )
    home, away = m.get('home_team'), m.get('away_team')
    _lg = m.get('league')
    _nf = bool(m.get('news'))       # 记录时是否已有资讯信号（A/B 对比）
    _lf = bool(m.get('lineup'))     # 记录时是否已有官方首发
    # 入库只用原始信心等级：calibration 的降级只影响展示，若把降级后的标签写库，
    # 下一轮校准又会拿被处理过的标签当训练集，形成反馈环。
    conf_level = m.get('confidence_level_raw') or m.get('confidence_level', '')
    conf = 0.8 if conf_level == '高' else 0.6 if conf_level == '中' else 0.4
    win, draw, loss = m.get('win_odds'), m.get('draw_odds'), m.get('lose_odds')

    # 1X2 胜平负：按「页面实际推荐的 predicted_option」记录（可能被基本面改判），
    # 使回测评估的策略与用户看到的推荐一致；无推荐时退化为最低赔方。
    _po_map = {'胜': 'H', '平': 'D', '负': 'A'}
    _odds_by = {'H': win, 'D': draw, 'A': loss}
    pick1x2 = _po_map.get(m.get('predicted_option'))
    odds1x2 = _odds_by.get(pick1x2) if pick1x2 else None
    if not (odds1x2 and odds1x2 > 0):
        _opts = [(k, v) for k, v in _odds_by.items() if v and v > 0]
        if _opts:
            pick1x2, odds1x2 = min(_opts, key=lambda x: x[1])
    if pick1x2 and odds1x2 and odds1x2 > 0:
        bt.record_prediction(mid, '1X2', pick1x2,
                             round(bt.implied_prob(odds1x2) * conf, 4), odds1x2,
                             model_name='jingcai-value', confidence=conf,
                             home_team=home, away_team=away,
                             jingcai=jingcai, existing_keys=existing_keys,
                             confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)

    # AH 让胜平负（_compute_handicap 推算，始终有值）
    try:
        line = m.get('handicap_line', 0)
        hwin, hdraw, hloss = m.get('handicap_win_odds'), m.get('handicap_draw_odds'), m.get('handicap_lose_odds')
        hcp_pick = m.get('hcp_pick')
        # 官方让球盘（hhad）为真实赔率；模型 Skellam 推算为估算，不计入 ROI
        ah_est = not (isinstance(hcp_pick, dict) and hcp_pick.get('source') == '官方盘口')
        if isinstance(hcp_pick, dict) and (hcp_pick.get('odds') or hcp_pick.get('side')):
            hside = {'让胜': 'H', '让平': 'D', '让负': 'A'}.get(hcp_pick.get('side'), 'H')
            pt_odds = hcp_pick.get('odds')
        elif hwin and (hwin > 0 or hdraw or hloss):
            # 无 hcp_pick 时用最小赔率方向
            opts = [('H', hwin), ('D', hdraw), ('A', hloss)]
            best_h = min((o for o in opts if o[1] and o[1] > 0), key=lambda x: x[1])
            hside, pt_odds = best_h[0], best_h[1]
        else:
            pt_odds = None
        if pt_odds:
            try:
                hline = float(line) if line else 0.0
            except (TypeError, ValueError):
                hline = 0.0
            pick_ah = f'{hside}|{hline}'
            bt.record_prediction(mid, 'AH', pick_ah,
                                 round(bt.implied_prob(pt_odds) * conf, 4), pt_odds,
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away,
                                 estimated=ah_est,
                                 jingcai=jingcai, existing_keys=existing_keys,
                                 confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)
    except Exception:
        pass

    # TG 总进球：竞彩官方 ttg 真实赔率为真；模型 top3 为估算
    try:
        tgp = m.get('tg_pick') or {}
        if tgp.get('label') and tgp.get('odds'):
            _lab = str(tgp['label'])
            pick_tg = '7+' if _lab == '7+' else _lab.replace('球', '')
            bt.record_prediction(mid, 'TG', pick_tg,
                                 round((tgp.get('prob') or 0) / 100.0, 4), float(tgp['odds']),
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away,
                                 estimated=(tgp.get('source') != '竞彩官方'),
                                 jingcai=jingcai, existing_keys=existing_keys,
                                 confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)
    except Exception:
        pass

    if not include_estimated:
        return

    # CS 正确比分：模型估算赔率，命中率极低，仅作参考（标记 estimated，不计入 ROI）
    try:
        rec_score = str(m.get('recommended_score', ''))
        if rec_score and '-' in rec_score:
            # 以1X2最低赔为基准估算比分赔率（绝非真实，仅供参考）
            est_odds = round(1.0 / max(0.05, 0.12 * conf), 2)
            bt.record_prediction(mid, 'CS', rec_score,
                                 round(0.12 * conf, 4), est_odds,
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away,
                                 estimated=True, jingcai=jingcai,
                                 existing_keys=existing_keys, confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)
    except Exception:
        pass

    # HTFT 半全场：优先竞彩官方 hafu 真实赔率；否则模型估算（标记 estimated，不计入 ROI）
    try:
        _htft_lab2code = {'胜胜': 'HH', '胜平': 'HD', '胜负': 'HA',
                          '平胜': 'DH', '平平': 'DD', '平负': 'DA',
                          '负胜': 'AH', '负平': 'AD', '负负': 'AA'}
        htft = m.get('htft') or {}
        if htft.get('pick') and (htft.get('odds') or 0) > 0:
            pick_htft = _htft_lab2code.get(htft['pick'], htft['pick'])
            bt.record_prediction(mid, 'HTFT', pick_htft,
                                 round((htft.get('prob') or 0) / 100.0, 4),
                                 float(htft.get('odds') or 0),
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away,
                                 estimated=(htft.get('source') != '竞彩官方'),
                                 jingcai=jingcai, existing_keys=existing_keys,
                                 confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)
        else:
            _ht = _predict_htft(m, conf)
            if _ht:
                pick_htft, prob_htft, odds_htft = _ht
                bt.record_prediction(mid, 'HTFT', pick_htft, prob_htft, odds_htft,
                                     model_name='jingcai-value', confidence=conf,
                                     home_team=home, away_team=away,
                                     estimated=True, jingcai=jingcai,
                                     existing_keys=existing_keys, confidence_level=conf_level, league=_lg, news_flag=_nf, lineup_flag=_lf)
    except Exception:
        pass


def _predict_htft(m, conf, max_goals=5):
    """用泊松模型估算半全场最可能组合（半场期望=全场期望*0.45）。
    返回 (pick, prob, odds) 或 None。pick 如 'HH'（半场胜+全场胜）。
    """
    try:
        import math
        # 全场期望进球
        he = m.get('expected_total')
        if not he:
            return None
        he = float(he)
        h_exp = he * 0.55
        a_exp = he * (1 - 0.55)
        # 半场期望
        hht_exp = h_exp * 0.45
        awt_exp = a_exp * 0.45

        def poisson(k, lam):
            if lam <= 0:
                return 1.0 if k == 0 else 0.0
            return (lam ** k) * math.exp(-lam) / math.factorial(k)

        def result(gh, ga):
            return 'H' if gh > ga else 'A' if gh < ga else 'D'

        best = None
        best_prob = 0
        for hhg in range(max_goals):
            for awg in range(max_goals):
                for fhg in range(max_goals):
                    for fwg in range(max_goals):
                        if fhg < hhg or fwg < awg:
                            continue
                        p = poisson(hhg, hht_exp) * poisson(awg, awt_exp) * \
                            poisson(fhg - hhg, h_exp - hht_exp) * poisson(fwg - awg, a_exp - awt_exp)
                        pt = result(hhg, awg) + result(fhg, fwg)
                        if p > best_prob:
                            best_prob = p
                            best = pt
        if best and best_prob > 0:
            # 用模型概率估算赔率（9种半全场概率归一）
            odds = round(1.0 / max(best_prob, 0.05), 2)
            return best, round(min(best_prob, 0.9), 4), odds
        return None
    except Exception:
        return None


def _parse_ev_dt(ev):
    """Bzzoiro 事件的开赛时间：优先 commence_utc，其次 commence_cst / kickoff / date。"""
    for k in ('commence_utc', 'commence_cst', 'kickoff', 'date'):
        v = (ev or {}).get(k)
        if not v:
            continue
        s = str(v).strip().replace('T', ' ')[:19]
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _pick_by_date(cands, created_at, max_days=3):
    """队名兜底匹配时，必须再用日期消歧：同一对球队 7 天内可能踢两场（联赛+杯赛），
    只按队名取"第一个赛果"会把今天的注单按上一场比分结算——这正是命中率异常的可疑来源。
    唯一候选且无日期信息时才允许直接采用；多候选无日期信息则放弃结算（宁缺毋滥）。"""
    cands = [c for c in (cands or []) if c]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    base = created_at if isinstance(created_at, datetime) else None
    if base is None:
        return None
    best, best_gap = None, None
    for c in cands:
        dt = _parse_ev_dt(c)
        if dt is None:
            continue
        gap = abs((dt - base).total_seconds())
        if gap > max_days * 86400:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = c, gap
    return best


def _ht_scores(ev):
    """从已完赛事件里取半场比分，兼容几种常见字段形态。

    为什么单独写：半全场（HTFT）的 269 条记录全部作废，就是因为这里只认
    home_score_ht / away_score_ht 两个字段，而数据源可能把它们放在
    score.halftime、ht_home 等位置。取不到就返回 (None, None)，结算按作废处理。
    """
    if not isinstance(ev, dict):
        return (None, None)
    for hk, ak in (('home_score_ht', 'away_score_ht'), ('ht_home', 'ht_away'),
                   ('home_ht', 'away_ht'), ('home_score_half', 'away_score_half'),
                   ('score_ht_home', 'score_ht_away')):
        try:
            if ev.get(hk) is not None and ev.get(ak) is not None:
                return (int(ev[hk]), int(ev[ak]))
        except (TypeError, ValueError):
            continue
    for key in ('score', 'scores', 'scoreboard'):
        blk = ev.get(key)
        if not isinstance(blk, dict):
            continue
        for sub in ('halftime', 'half_time', 'ht', 'period1', 'first_half'):
            v = blk.get(sub)
            if isinstance(v, dict) and v.get('home') is not None and v.get('away') is not None:
                try:
                    return (int(v['home']), int(v['away']))
                except (TypeError, ValueError):
                    pass
            if isinstance(v, (list, tuple)) and len(v) == 2:
                try:
                    return (int(v[0]), int(v[1]))
                except (TypeError, ValueError):
                    pass
    return (None, None)


_FINISHED_STATUSES = {'finished', 'ended', 'ft', 'fulltime', 'full_time', 'complete',
                      'completed', 'final', 'aet', 'after_extra_time', 'pen',
                      'after_penalties', 'awarded'}


def is_finished_event(ev):
    """这场到底踢完没有。

    为什么必须判：`GET /events/{id}/` 对未开赛事件同样返回 200，且 home_score/away_score
    是 0-0 默认值。2026-11-23 之前 settle_finished 直接拿这两个字段结算，等于把"还没踢"
    的比赛全部按 0-0 平局结算——胜平负/总进球命中率被打到 16%/3.7%，
    而让球受让方（H+1、A-1 在平局都赢）反而虚高，这正是历史战绩自相矛盾的根源。
    """
    if not isinstance(ev, dict):
        return False
    st = str(ev.get('status') or '').strip().lower()
    if st:
        return st in _FINISHED_STATUSES
    if ev.get('finished') is True or ev.get('is_finished') is True:
        return True
    return False


def settle_finished():
    """赛后结算（高覆盖）：构建「事件ID + 队名」双索引的赛果表，按ID优先、队名兜底回填。
    - 已完赛列表 fetch_finished_events（近7天）覆盖大多数场次；
    - 待结算的数字事件ID再逐个补查（列表可能不含）；
    - 竞彩编号场次（无 Bzzoiro ID）靠队名规约匹配，闭环样本。
    """
    try:
        from bizzoiro_client import API_KEY, BASE_URL, fetch_finished_events
        if not API_KEY:
            return 0
        import requests
        import json as _json
        import backtest as bt
        from backtest_models import BtBet, BtParlay, db

        def _norm(s):
            return (s or '').replace(' ', '').replace('-', '').lower()

        def _cn(s):
            try:
                from team_alias import canon
                return canon(s)
            except Exception:
                return s or ''

        results = {}
        name_idx = {}

        def _add_ev(ev):
            if not isinstance(ev, dict):
                return
            if not is_finished_event(ev):
                return
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                return
            eid = str(ev.get('id') or '')
            if eid:
                results[eid] = ev
            h, a = _norm(_cn(ev.get('home_team'))), _norm(_cn(ev.get('away_team')))
            if h and a:
                name_idx.setdefault((h, a), []).append(ev)

        # 1) 已完赛列表
        for _ev in fetch_finished_events():
            _add_ev(_ev)

        pending_q = BtBet.query.filter_by(settled_at=None).all()
        settled = 0

        # 2) 数字事件ID逐个补查（并发）
        eids, seen = [], set()
        for b in pending_q:
            e = str(b.match_id)
            if e.isdigit() and e not in seen:
                seen.add(e)
                eids.append(e)
        try:
            for _p in BtParlay.query.filter_by(settled_at=None).all():
                for _l in _json.loads(_p.legs_json or '[]'):
                    e = str(_l.get('match_id') or '')
                    if e.isdigit() and e not in seen:
                        seen.add(e)
                        eids.append(e)
        except Exception:
            pass

        headers = {'Authorization': f'Token {API_KEY}'}

        def _fetch(eid):
            try:
                resp = requests.get(f'{BASE_URL}/events/{eid}/', headers=headers, timeout=10)
                if resp.status_code == 200:
                    return eid, resp.json()
            except Exception:
                pass
            return eid, None

        todo = [e for e in eids if e not in results][:120]
        if todo:
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=6) as ex:
                    for _eid, _ev in ex.map(_fetch, todo):
                        if _ev:
                            _add_ev(_ev)
            except Exception:
                for _eid in todo:
                    _, _ev = _fetch(_eid)
                    if _ev:
                        _add_ev(_ev)

        # 3) 回填单场（ID优先，队名兜底）
        for b in pending_q:
            eid = str(b.match_id)
            ev = results.get(eid)
            if ev is None:
                cands = name_idx.get((_norm(_cn(b.home_team)), _norm(_cn(b.away_team)))) or []
                ev = _pick_by_date(cands, b.created_at)
            if ev is None:
                continue
            # 学习别名：命中同一场比赛时，记录名(竞彩中文) 与 事件名(Bzzoiro英文) 可能不同
            try:
                from team_alias import learn
                learn(ev.get('home_team'), b.home_team)
                learn(ev.get('away_team'), b.away_team)
            except Exception:
                pass
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                continue
            actual = 'H' if hs > aw else 'A' if hs < aw else 'D'
            hht, aht = _ht_scores(ev)
            try:
                outcome, pnl = bt._eval_play(b, actual, hs, aw, hht, aht, b.stake or 1.0)
            except Exception:
                continue
            b.outcome = outcome
            b.pnl = pnl
            b.settled_at = bt._now_str()
            try:
                b.home_score = hs
                b.away_score = aw
                b.home_score_ht = hht
                b.away_score_ht = aht
            except Exception:
                pass
            settled += 1
        if settled:
            db.session.commit()
        logger.info('[pipeline] settled %d bets', settled)

        # 4) 串关结算（同一批赛果）
        try:
            import parlay_tracker
            n3 = parlay_tracker.settle_parlays(results)
            logger.info('[pipeline] settled %d parlays', n3)
        except Exception as e:
            logger.warning('[pipeline] parlay settle error: %s', e)
        return settled
    except Exception as e:
        logger.warning('[pipeline] settle error: %s', e)
        return 0


_STALE_DAYS = 7


def resettle_recent(days=14, limit=300):
    """按事件的真实赛果重算最近一段时间的结算（只处理有数字事件ID的注单）。

    为什么需要：2026-11-23 之前，`/events/{id}/` 对未开赛事件也返回 0-0 默认比分，
    这些场次被当成平局结算，导致胜平负/总进球命中率虚假偏低、让球受让方虚假偏高。
    修好入口（is_finished_event）只能保证以后不再错；已经落库的错结论必须重算，
    否则首页闸门会继续依据错数据永久封杀玩法。返回一份可核对的统计。
    """
    try:
        from bizzoiro_client import API_KEY, BASE_URL
        if not API_KEY:
            return {'error': 'BZZOIRO_API_KEY 未配置'}
        import requests
        import backtest as bt
        from backtest_models import BtBet, db

        cutoff = (datetime.now() - timedelta(days=int(days))).strftime('%Y-%m-%d %H:%M:%S')
        rows = BtBet.query.filter(BtBet.settled_at >= cutoff).all()
        ids = sorted({str(b.match_id) for b in rows if str(b.match_id).isdigit()})
        events = {}
        not_played = set()

        def _fetch_one(eid):
            try:
                resp = requests.get(f'{BASE_URL}/events/{eid}/',
                                    headers={'Authorization': f'Token {API_KEY}'}, timeout=10)
                if resp.status_code == 200:
                    ev = resp.json()
                    return eid, ev
            except Exception:
                pass
            return eid, None

        todo = ids[:int(limit)]
        if todo:
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for eid, ev in ex.map(_fetch_one, todo):
                        if not ev:
                            continue
                        if is_finished_event(ev):
                            events[eid] = ev
                        else:
                            not_played.add(eid)
            except Exception:
                for eid in todo:
                    _e, _ev = _fetch_one(eid)
                    if not _ev:
                        continue
                    if is_finished_event(_ev):
                        events[_e] = _ev
                    else:
                        not_played.add(_e)
        stats = {'days': days, 'bet_rows': len(rows), 'event_ids': len(ids),
                 'finished_events': len(events), 'checked': 0, 'changed': 0, 'no_score': 0,
                 'reset_pending': 0}
        for b in rows:
            eid = str(b.match_id)
            if eid in not_played:
                # 还没踢的比赛被当成 0-0 平局结算过：退回到待结算，等真实赛果
                if b.outcome is not None:
                    stats['reset_pending'] += 1
                b.outcome = None
                b.pnl = 0.0
                b.settled_at = None
                b.home_score = None
                b.away_score = None
                b.home_score_ht = None
                b.away_score_ht = None
                continue
            ev = events.get(eid)
            if not ev:
                continue
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                stats['no_score'] += 1
                continue
            actual = 'H' if hs > aw else 'A' if hs < aw else 'D'
            hht, aht = _ht_scores(ev)
            try:
                outcome, pnl = bt._eval_play(b, actual, hs, aw, hht, aht, b.stake or 1.0)
            except Exception:
                continue
            stats['checked'] += 1
            if outcome != b.outcome or abs(float(b.pnl or 0) - float(pnl)) > 1e-6:
                stats['changed'] += 1
            b.outcome = outcome
            b.pnl = pnl
            try:
                b.home_score, b.away_score = hs, aw
                b.home_score_ht, b.away_score_ht = hht, aht
            except Exception:
                pass
        if stats['checked']:
            db.session.commit()
        logger.info('[pipeline] resettle %s', stats)
        return stats
    except Exception as e:
        logger.warning('[pipeline] resettle error: %s', e)
        return {'error': str(e)[:200]}


def expire_stale(days=_STALE_DAYS):
    """超期作废：超过 N 天仍无法结算的待结算记录标记 void（pnl=0），保持统计口径干净。
    返回作废条数。"""
    n = 0
    try:
        from datetime import datetime as _dt, timedelta as _td
        from backtest_models import BtBet, BtParlay, db
        now_str = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
        cutoff = _dt.utcnow() - _td(days=days)
        try:
            bets = BtBet.query.filter(BtBet.settled_at.is_(None)) \
                .filter(BtBet.created_at.isnot(None)) \
                .filter(BtBet.created_at < cutoff).all()
        except Exception:
            bets = []
        for b in bets:
            b.outcome = 'void'
            b.pnl = 0.0
            b.settled_at = now_str
            n += 1
        cutoff_date = (_dt.now() - _td(days=days)).strftime('%Y-%m-%d')
        try:
            parlays = BtParlay.query.filter(BtParlay.settled_at.is_(None)) \
                .filter(BtParlay.plan_date.isnot(None)) \
                .filter(BtParlay.plan_date < cutoff_date).all()
        except Exception:
            parlays = []
        for p in parlays:
            p.outcome = 'void'
            p.pnl = 0.0
            p.settled_at = now_str
            n += 1
        if n:
            db.session.commit()
            try:
                from backtest import clear_summary_cache
                clear_summary_cache()
            except Exception:
                pass
            try:
                import parlay_tracker
                parlay_tracker.clear_parlay_cache()
            except Exception:
                pass
        logger.info('[pipeline] expired %d stale records', n)
    except Exception as e:
        logger.warning('[pipeline] expire_stale error: %s', e)
    return n


def run_full():
    """完整流水线（定时任务入口）。"""
    n1 = run_pipeline()
    n2 = settle_finished()
    n3 = expire_stale()
    return {'snapshots': n1, 'settled': n2, 'expired': n3}
