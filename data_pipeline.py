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
            eid = str(m.get('bz_event_id') or '')
            if not eid:
                continue
            try:
                _record_match_plays(bt, m, eid, jingcai=True,
                                    include_estimated=False, existing_keys=existing)
                existing.add((eid, '1X2'))
                existing.add((eid, 'AH'))
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
                             jingcai=jingcai, existing_keys=existing_keys)

    # AH 让胜平负（_compute_handicap 推算，始终有值）
    try:
        line = m.get('handicap_line', 0)
        hwin, hdraw, hloss = m.get('handicap_win_odds'), m.get('handicap_draw_odds'), m.get('handicap_lose_odds')
        hcp_pick = m.get('hcp_pick')
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
                                 jingcai=jingcai, existing_keys=existing_keys)
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
                                 existing_keys=existing_keys)
    except Exception:
        pass

    # HTFT 半全场：模型估算赔率（非真实市场赔率），标记 estimated，不计入 ROI
    try:
        htft = _predict_htft(m, conf)
        if htft:
            pick_htft, prob_htft, odds_htft = htft
            bt.record_prediction(mid, 'HTFT', pick_htft, prob_htft, odds_htft,
                                 model_name='jingcai-value', confidence=conf,
                                 home_team=home, away_team=away,
                                 estimated=True, jingcai=jingcai,
                                 existing_keys=existing_keys)
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
    """赛后结算：按待结算 bt_bets 的 match_id（Bzzoiro 单场ID）逐个精确查询赛果回填。
    这是最精准的方式——直接拿到预测那场比赛的比分，不受队名中英文/联赛淹没影响。
    """
    try:
        from bizzoiro_client import API_KEY, BASE_URL
        if not API_KEY:
            return 0
        import requests
        import backtest as bt
        from backtest_models import BtBet, db
        # 取所有未结算的 bet（按 match_id 精确查，去重）
        pending_q = BtBet.query.filter_by(settled_at=None).all()
        settled = 0
        headers = {'Authorization': f'Token {API_KEY}'}

        # 先对未结算场次去重，再并发抓取赛果
        eids = []
        seen_mid = set()
        for b in pending_q:
            eid = str(b.match_id)
            if eid and eid not in seen_mid:
                seen_mid.add(eid)
                eids.append(eid)
        # 串关待结算场次也纳入抓取（否则无单场待结算时，串关永不结算）
        try:
            import json as _json
            from backtest_models import BtParlay
            for _p in BtParlay.query.filter_by(settled_at=None).all():
                try:
                    for _l in _json.loads(_p.legs_json or '[]'):
                        _eid = str(_l.get('match_id') or '')
                        # 仅 Bzzoiro 数字事件ID可查；竞彩编号（如 周日001）跳过
                        if _eid.isdigit() and _eid not in seen_mid:
                            seen_mid.add(_eid)
                            eids.append(_eid)
                except Exception:
                    continue
        except Exception:
            pass

        def _fetch(eid):
            try:
                resp = requests.get(f'{BASE_URL}/events/{eid}/', headers=headers, timeout=10)
                if resp.status_code == 200:
                    return eid, resp.json()
            except Exception:
                pass
            return eid, None

        data = {}
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=5) as ex:
                for eid, ev in ex.map(_fetch, eids):
                    if ev:
                        data[eid] = ev
        except Exception:
            for eid in eids:
                _, ev = _fetch(eid)
                if ev:
                    data[eid] = ev

        for eid, ev in data.items():
            hs = ev.get('home_score')
            aw = ev.get('away_score')
            if hs is None or aw is None:
                continue
            try:
                settled += bt.settle_bet(
                    match_id=eid, home_score=hs, away_score=aw,
                    home_team=ev.get('home_team'), away_team=ev.get('away_team'),
                    home_score_ht=ev.get('home_score_ht'), away_score_ht=ev.get('away_score_ht')
                ) or 0
            except Exception:
                continue
        logger.info('[pipeline] settled %d (by match_id)', settled)
        # 串关级结算：用同一批赛果结算串关方案（任一腿未中即整套未中）
        try:
            import parlay_tracker
            n3 = parlay_tracker.settle_parlays(data)
            logger.info('[pipeline] settled %d parlays', n3)
        except Exception as e:
            logger.warning('[pipeline] parlay settle error: %s', e)
        return settled
    except Exception as e:
        logger.warning('[pipeline] settle error: %s', e)
        return 0


def run_full():
    """完整流水线（定时任务入口）。"""
    n1 = run_pipeline()
    n2 = settle_finished()
    return {'snapshots': n1, 'settled': n2}
