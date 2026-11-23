"""玩法级专业评估（play review）。

为什么需要：面板上的"命中率 26%、ROI -44%"是把所有玩法混在一起的数字，
既不能说明某个玩法能不能用，也挡不住"样本太小"和"统计噪声"。
这里对每种玩法单独做一次可复核的评估：

1. 只统计已结算且非估算赔率的注单（估算赔率不可投注，单列参考）。
2. 命中率给 Wilson 95% 置信区间——20 注赢 5 注和 200 注赢 50 注结论完全不同。
3. 盈亏线（break-even）= 平均(1/赔率)，即该赔率组合下必须达到的命中率；
   edge = 命中率 − 盈亏线（百分点），是"准不准"唯一站得住的度量。
4. 用二项 z 检验判断 edge 是否可能只是运气（p 值），并给出样本量分级。
5. 按月份与最近 30 天看稳定性：一次性正 edge 和持续正 edge 不是一回事。
6. 结论只给三档：可用 / 观察 / 暂停 —— 并且"暂停"优先于"可用"，
   因为对普通用户来说，少推荐比错推荐代价小。
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta

logger = __import__('logging').getLogger(__name__)

PLAY_CN = {'1X2': '胜平负', 'AH': '让胜平负', 'TG': '总进球',
           'CS': '比分', 'HTFT': '半全场', 'OU': '大小球', 'VAL': '价值盘'}
PLAY_ORDER = ['1X2', 'AH', 'VAL', 'TG', 'CS', 'HTFT', 'OU']

MIN_N = 30          # 低于这个样本量不给结论
GRADE_BANDS = ((300, 'A'), (100, 'B'), (30, 'C'), (0, 'D'))


def wilson_interval(wins, n, z=1.96):
    """命中率的 Wilson 置信区间（比例，0-1）。小样本比正态近似稳。"""
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def two_sided_p(z):
    """标准正态双侧 p 值。"""
    return math.erfc(abs(z) / math.sqrt(2.0))


def _mean_se(vals):
    """均值与标准误。"""
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    m = sum(vals) / n
    if n == 1:
        return m, 0.0
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, math.sqrt(var / n)


def _weighted_roi(bets):
    """按投注额加权的 ROI 及其标准误（每注收益率 r_i = pnl_i / stake_i）。"""
    pairs = [(float(b.stake or 1.0), float(b.pnl or 0.0)) for b in bets
             if b.outcome in ('win', 'lose')]
    if not pairs:
        return 0.0, 0.0, 0.0
    stake = sum(s for s, _ in pairs)
    pnl = sum(p for _, p in pairs)
    roi = pnl / stake if stake else 0.0
    # 每注收益率的标准误 → 总 ROI 的近似标准误（按投注额加权）
    rs = [p / s for s, p in pairs]
    w = [s / stake for s, _ in pairs]
    mean_r = sum(wi * ri for wi, ri in zip(w, rs))
    var = sum(wi * wi * (ri - mean_r) ** 2 for wi, ri in zip(w, rs))
    return roi, math.sqrt(max(var, 0.0)), stake


def _month_key(v):
    s = str(v or '')
    return s[:7] if len(s) >= 7 else '未知'


def _stat_block(bets):
    """一组注单的核心统计。"""
    decided = [b for b in bets if b.outcome in ('win', 'lose')]
    n = len(decided)
    wins = sum(1 for b in decided if b.outcome == 'win')
    hit = wins / n if n else 0.0
    lo, hi = wilson_interval(wins, n)
    odds = [float(b.odds) for b in decided if b.odds and float(b.odds) > 1.0]
    req = sum(1.0 / o for o in odds) / len(odds) if odds else 0.0
    req_se = 0.0
    if len(odds) > 1:
        inv = [1.0 / o for o in odds]
        m = sum(inv) / len(inv)
        req_se = math.sqrt(sum((v - m) ** 2 for v in inv) / (len(inv) - 1) / len(inv))
    edge = (hit - req) * 100 if n and odds else 0.0
    # 二项 z：命中数是否显著偏离盈亏线要求（H0: p = req）
    z = 0.0
    p_value = 1.0
    if n and 0 < req < 1:
        se = math.sqrt(req * (1 - req) / n)
        z = (hit - req) / se if se else 0.0
        p_value = two_sided_p(z)
    roi, roi_se, stake = _weighted_roi(decided)
    # 结算可信度自检：如果注单只是"按赔率随机结算"，胜场数应围绕 sum(1/赔率) 波动。
    # 实际胜场远低于该期望（z < -3）时，多半不是模型差，而是结算/数据错配——这类战绩不能用来下结论。
    exp_wins = sum(1.0 / o for o in odds) if odds else 0.0
    var_wins = sum((1.0 / o) * (1 - 1.0 / o) for o in odds) if odds else 0.0
    integrity_z = (wins - exp_wins) / math.sqrt(var_wins) if var_wins > 0 else 0.0
    return {
        'expected_wins': round(exp_wins, 1),
        'integrity_z': round(integrity_z, 1),
        'integrity_suspect': bool(n >= 40 and integrity_z < -3),
        'n': n,
        'wins': wins,
        'losses': n - wins,
        'voids': sum(1 for b in bets if b.outcome == 'void'),
        'pending': sum(1 for b in bets if b.outcome not in ('win', 'lose', 'void')),
        'hit_rate': round(hit * 100, 1),
        'hit_ci': [round(lo * 100, 1), round(hi * 100, 1)],
        'required_rate': round(req * 100, 1),
        'required_ci': [round(max(0.0, req - 1.96 * req_se) * 100, 1),
                        round(min(1.0, req + 1.96 * req_se) * 100, 1)],
        'edge_pp': round(edge, 1),
        'edge_ci_pp': [round((lo - req - 1.96 * req_se) * 100, 1),
                       round((hi - req + 1.96 * req_se) * 100, 1)],
        'z': round(z, 2),
        'p_value': round(p_value, 4),
        'roi': round(roi * 100, 1),
        'roi_ci': [round((roi - 1.96 * roi_se) * 100, 1),
                   round((roi + 1.96 * roi_se) * 100, 1)],
        'stake': round(stake, 2),
        'avg_odds': round(sum(odds) / len(odds), 2) if odds else 0.0,
    }


def _grade(n, p_value, edge_pp, roi):
    for floor, g in GRADE_BANDS:
        if n >= floor:
            base = g
            break
    else:
        base = 'D'
    if base in ('C', 'D'):
        return base
    # 样本够大但统计上仍看不出差异 → 降一级（够大却没结论，说明效应可能很小）
    if p_value > 0.2 and abs(edge_pp) < 5:
        return 'B' if base == 'A' else base
    return base


def _verdict(st, grade, estimated):
    """三档结论。暂停优先：宁可少推，不可错推。"""
    n = st['n']
    edge = st['edge_pp']
    p = st['p_value']
    roi = st['roi']
    if estimated:
        return ('reference', '估算赔率玩法：只做命中率参考，不参与投注结论')
    if n < MIN_N:
        return ('insufficient', '样本不足（%d 注 < %d），暂不下结论' % (n, MIN_N))
    if edge <= -5 and p < 0.10:
        return ('pause', '显著低于盈亏线（edge %.1fpp，p=%.3f），建议暂停该玩法推荐' % (edge, p))
    if roi < -20 and p < 0.20:
        return ('pause', 'ROI 显著为负（%.1f%%），建议暂停该玩法推荐' % roi)
    if edge >= 3 and p < 0.10 and roi > 0:
        return ('usable', '高于盈亏线且统计显著（edge +%.1fpp，p=%.3f），可小注持续观察' % (edge, p))
    return ('watch', '与盈亏线无显著差异（edge %.1fpp，p=%.2f），维持观察、控制仓位' % (edge, p))


def _settle_audit(bets):
    """结算自检：对有比分快照的注单，用比分重算胜平负，看是否与库里 outcome 一致。
    历史行没有比分是常态（2026-11-23 才加列），所以这里同时也报告覆盖率。"""
    rows = [b for b in bets if b.outcome in ('win', 'lose')]
    checked = 0
    mismatch = 0
    samples = []
    for b in rows:
        hs, aw = getattr(b, 'home_score', None), getattr(b, 'away_score', None)
        if hs is None or aw is None:
            continue
        pt = b.play_type or '1X2'
        if pt not in ('1X2', 'VAL'):
            continue
        checked += 1
        actual = 'H' if hs > aw else 'A' if hs < aw else 'D'
        expect = 'win' if b.pick == actual else 'lose'
        if b.outcome != expect:
            mismatch += 1
            if len(samples) < 5:
                samples.append({'match_id': b.match_id, 'play': pt, 'pick': b.pick,
                                'score': '%s-%s' % (hs, aw), 'actual': actual,
                                'outcome': b.outcome})
    return {
        'decided': len(rows),
        'checked': checked,
        'coverage': round(checked / len(rows) * 100, 1) if rows else 0.0,
        'mismatch': mismatch,
        'samples': samples,
    }


def review(period='all', min_n=MIN_N):
    """按玩法给出评估结论。period 支持 'all'/'30d'/'90d'/'7d'。"""
    from backtest_models import BtBet

    cutoff = None
    if period and period != 'all':
        days = {'7d': 7, '30d': 30, '90d': 90}.get(period)
        if days:
            cutoff = datetime.now() - timedelta(days=days)

    rows = BtBet.query.filter(BtBet.jingcai.is_(True)) \
        .filter(BtBet.settled_at.isnot(None)).all()

    order = {p: i for i, p in enumerate(PLAY_ORDER)}
    by_play = defaultdict(list)
    for b in rows:
        if cutoff:
            try:
                dt = datetime.strptime(str(b.settled_at)[:19], '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if dt < cutoff:
                continue
        by_play[b.play_type or '1X2'].append(b)

    plays = []
    for pt, bets in by_play.items():
        estimated = any(getattr(b, 'estimated', False) for b in bets)
        st = _stat_block(bets)
        grade = _grade(st['n'], st['p_value'], st['edge_pp'], st['roi'])
        code, text = _verdict(st, grade, estimated)
        # 可信度优先于一切结论：战绩与赔率不相容时，先怀疑结算而不是模型
        if st['integrity_suspect'] and not estimated:
            code = 'suspect'
            text = ('战绩与赔率不相容（实际 %d 胜 / 按赔率应约 %.0f 胜，z=%.1f）：'
                    '更可能是历史结算或数据错配，而非模型判断；该玩法战绩暂不可用于决策，'
                    '已连同比分快照重建样本，待新样本复核' % (
                        st['wins'], st['expected_wins'], st['integrity_z']))
        # 月度稳定性
        monthly = defaultdict(list)
        for b in bets:
            monthly[_month_key(b.settled_at)].append(b)
        series = []
        for m in sorted(monthly):
            ms = _stat_block(monthly[m])
            series.append({'month': m, 'n': ms['n'], 'hit_rate': ms['hit_rate'],
                           'required_rate': ms['required_rate'], 'edge_pp': ms['edge_pp'],
                           'roi': ms['roi']})
        # 最近 30 天 vs 之前
        now = datetime.now()
        recent, before = [], []
        for b in bets:
            try:
                dt = datetime.strptime(str(b.settled_at)[:19], '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            (recent if dt >= now - timedelta(days=30) else before).append(b)
        rs = _stat_block(recent)
        bs = _stat_block(before)
        plays.append({
            'play_type': pt,
            'play_cn': PLAY_CN.get(pt, pt),
            'estimated': estimated,
            **st,
            'grade': grade,
            'verdict_code': code,
            'verdict': text,
            'monthly': series,
            'recent30': {'n': rs['n'], 'hit_rate': rs['hit_rate'],
                         'required_rate': rs['required_rate'], 'edge_pp': rs['edge_pp'],
                         'roi': rs['roi']},
            'before': {'n': bs['n'], 'hit_rate': bs['hit_rate'], 'edge_pp': bs['edge_pp']},
        })

    plays.sort(key=lambda x: (x['estimated'], order.get(x['play_type'], 99)))

    usable = [p['play_cn'] for p in plays if p['verdict_code'] == 'usable']
    paused = [p['play_cn'] for p in plays if p['verdict_code'] == 'pause']
    suspect = [p['play_cn'] for p in plays if p['verdict_code'] == 'suspect']
    audit = _settle_audit(rows)
    head = '可用玩法：%s；建议暂停：%s' % ('、'.join(usable) or '暂无', '、'.join(paused) or '暂无')
    if suspect:
        head += '；战绩存疑待复核：%s' % '、'.join(suspect)
    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'period': period or 'all',
        'min_n': min_n,
        'method': 'Wilson 95% 置信区间 + 盈亏线 mean(1/赔率) + 二项 z 检验；'
                  'ROI 按投注额加权并给近似置信区间；估算赔率玩法单列参考；'
                  '另做结算可信度自检：实际胜场与按赔率推算的期望胜场偏差超过 3 个标准差时，'
                  '判定为"战绩存疑"（先查结算与数据匹配，再谈模型好坏）。',
        'headline': head,
        'audit': audit,
        'plays': plays,
    }
