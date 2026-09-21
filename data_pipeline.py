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
            existing = {(b.match_id, b.play_type)
                        for b in BtBet.query.filter_by(jingcai=True).all()}
        except Exception:
            existing = set()
        n = 0
        for m in matches:
            # 优先 Bzzoiro 事件ID（可精确结算）；无匹配时用竞彩编号，结算走队名兜底，
            # 保证每场竞彩推荐都能进入回测闭环（提高结算覆盖率）。
            eid = str(m.get('bz_event_id') or '') or str(m.get('match_id') or '')
            if not eid:
                continue
            try:
                _record_match_plays(bt, m, eid, jingcai=True,
                                    include_estimated=True, existing_keys=existing)
                for _pt in ('1X2', 'AH', 'TG', 'HTFT', 'CS'):
                    existing.add((eid, _pt))
                n += 1
            except Exception as e:
                logger.warning('[pipeline] jingcai play record error: %s', e)
                continue
        logger.info('[pipeline] 竞彩回测记录 %d 场', n)
        return n
    except Exception as e:
        logger.warning('[pipeline] record_jingcai_plays error: %s', e)
        return 0


def _record_match_plays(bt, m, mid, jingcai=False, include_estimated=True, existing_keys=None):
    """为单场比赛记录赔率快照 + 多玩法预测（1X2/AH/CS/HTFT）。单场异常不影响他场。
    jingcai=True 标记为竞彩官方场次；include_estimated=False 只记真实赔率玩法(1X2/AH)。"""
    # 已记录过则跳过快照，避免每次刷新重复写入
    if not (existing_keys is not None and (mid, '1X2') in existing_keys):
        bt.record_odds_snapshot(
            match_id=mid, market='1X2',
            home=m.get('win_odds'), draw=m.get('draw_odds'), away=m.get('lose_odds'),
            source='竞彩官方' if jingcai else 'Bzzoiro'
        )
    home, away = m.get('home_team'), m.get('away_team')
    _lg = m.get('league')
    conf_level = m.get('confidence_level', '')
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
                             confidence_level=conf_level, league=_lg)

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
                                 confidence_level=conf_level, league=_lg)
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
                                 confidence_level=conf_level, league=_lg)
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
                                 existing_keys=existing_keys, confidence_level=conf_level, league=_lg)
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
                                 confidence_level=conf_level, league=_lg)
        else:
            _ht = _predict_htft(m, conf)
            if _ht:
                pick_htft, prob_htft, odds_htft = _ht
                bt.record_prediction(mid, 'HTFT', pick_htft, prob_htft, odds_htft,
                                     model_name='jingcai-value', confidence=conf,
                                     home_team=home, away_team=away,
                                     estimated=True, jingcai=jingcai,
                                     existing_keys=existing_keys, confidence_level=conf_level, league=_lg)
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
            hs, aw = ev.get('home_score'), ev.get('away_score')
            if hs is None or aw is None:
                return
            eid = str(ev.get('id') or '')
            if eid:
                results[eid] = ev
            h, a = _norm(_cn(ev.get('home_team'))), _norm(_cn(ev.get('away_team')))
            if h and a:
                name_idx.setdefault((h, a), ev)

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
                ev = name_idx.get((_norm(_cn(b.home_team)), _norm(_cn(b.away_team))))
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
            try:
                outcome, pnl = bt._eval_play(b, actual, hs, aw,
                                             ev.get('home_score_ht'), ev.get('away_score_ht'),
                                             b.stake or 1.0)
            except Exception:
                continue
            b.outcome = outcome
            b.pnl = pnl
            b.settled_at = bt._now_str()
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


_STALE_DAYS = 3


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
