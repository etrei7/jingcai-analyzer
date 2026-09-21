"""价值盘引擎（锐盘基准 + Dixon-Coles 模型校准）。

思路（成熟做法，非"维度投票"）：
- 不预测胜负，只找"错价"：以国际锐盘（Bzzoiro 多家博彩公司最佳赔率）去水概率为真实概率基准；
- 用 Dixon-Coles 模型概率在对数几率(logit)空间小幅校准，得到最终概率 p；
- 当 竞彩赔率 × p − 1 ≥ 阈值 时视为有价值（正期望），按分数凯利给建议仓位；
- 记录到回测（play_type='VAL'），用 ROI / Brier / log-loss 验证，而非只看命中率。

权重为保守默认值（市场占主导）。有足够历史样本后可用最大似然拟合（Benter/堆叠思路）。
本模块为纯函数 + 轻量写库，不改变既有推荐逻辑。
"""
import math
import logging

logger = logging.getLogger(__name__)

# ===== 可调参数 =====
W_SHARP = 0.85        # 锐盘权重（市场高效，占主导）
W_MODEL = 0.15        # 模型权重（样本少时保守）
EDGE_MIN = 0.02       # 最小正期望阈值（2%）
KELLY_FRACTION = 0.25  # 分数凯利
KELLY_CAP = 0.05      # 单注本金上限 5%

_SIDES = ('home', 'draw', 'away')
_LAB = {'home': '主胜', 'draw': '平局', 'away': '客胜'}
_PICK = {'home': 'H', 'draw': 'D', 'away': 'A'}


def _logit(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _devig3(h, d, a):
    """三家赔率去水归一化。"""
    try:
        h, d, a = float(h), float(d), float(a)
    except (TypeError, ValueError):
        return None
    if h <= 1 or d <= 1 or a <= 1:
        return None
    ih, id_, ia = 1.0 / h, 1.0 / d, 1.0 / a
    t = ih + id_ + ia
    if t <= 0:
        return None
    return ih / t, id_ / t, ia / t


def _kelly(prob, odds, fraction=KELLY_FRACTION, cap=KELLY_CAP):
    """分数凯利建议仓位（返回占本金比例 0~cap）。"""
    if not odds or odds <= 1 or not prob or prob <= 0 or prob >= 1:
        return 0.0
    b = odds - 1.0
    f = (b * prob - (1.0 - prob)) / b
    return round(max(0.0, min(f * fraction, cap)), 4)


def sharp_value(win_odds, draw_odds, lose_odds, intl_odds, lam_h, lam_a):
    """计算单场价值盘。
    intl_odds: {'best': {'home','draw','away'}}（国际锐盘最佳赔率）。
    返回 dict；value_available=False 表示无锐盘基准，无法判断价值。
    """
    jc = {'home': win_odds, 'draw': draw_odds, 'away': lose_odds}
    if not all(jc[k] and jc[k] > 0 for k in _SIDES):
        return {'value_available': False, 'reason': '缺少竞彩胜平负赔率'}
    best = (intl_odds or {}).get('best') or {}
    sharp = _devig3(best.get('home'), best.get('draw'), best.get('away'))
    if not sharp:
        return {'value_available': False, 'reason': '无国际锐盘赔率基准'}

    # 模型概率（Dixon-Coles）；失败则退化为纯锐盘基准
    model = None
    try:
        from analysis import _dixon_coles_probs
        model = _dixon_coles_probs(lam_h, lam_a)
    except Exception:
        model = None

    # 对数几率融合
    pc = {}
    for i, k in enumerate(_SIDES):
        if model:
            pc[k] = _sigmoid(W_SHARP * _logit(sharp[i]) + W_MODEL * _logit(model[i]))
        else:
            pc[k] = sharp[i]
    tot = sum(pc.values())
    if tot <= 0:
        return {'value_available': False, 'reason': '概率归一失败'}
    pc = {k: v / tot for k, v in pc.items()}

    edges = {}
    picks = []
    for k in _SIDES:
        p = pc[k]
        edge_pct = round((p * jc[k] - 1.0) * 100, 2)
        edges[k] = edge_pct
        if edge_pct >= EDGE_MIN * 100:
            kelly = _kelly(p, jc[k])
            picks.append({
                'side': k, 'option': _LAB[k], 'jc_odds': round(jc[k], 2),
                'prob': round(p * 100, 1),
                'fair_odds': round(1.0 / p, 2) if p > 0 else 0,
                'edge_pct': edge_pct,
                'kelly_pct': round(kelly * 100, 2),
                'amount': max(1, round(kelly * 1000)),
            })
    picks.sort(key=lambda x: x['edge_pct'], reverse=True)

    return {
        'value_available': True,
        'sharp_prob': {k: round(sharp[i] * 100, 1) for i, k in enumerate(_SIDES)},
        'model_prob': ({k: round(model[i] * 100, 1) for i, k in enumerate(_SIDES)}
                       if model else None),
        'final_prob': {k: round(pc[k] * 100, 1) for k in _SIDES},
        'edges': edges,
        'picks': picks,
        'has_value': bool(picks),
        'weights': {'sharp': W_SHARP, 'model': W_MODEL},
        'threshold_pct': EDGE_MIN * 100,
        'method': '锐盘去水基准 %d%% + Dixon-Coles 校准 %d%%，阈值 %s%%，分数凯利'
                  % (round(W_SHARP * 100), round(W_MODEL * 100), EDGE_MIN * 100),
        'disclaimer': '价值盘=正期望估算，非必胜；竞彩含抽水，长期才体现',
    }


def generate_value_recommendations(matches, top_n=12):
    """从已分析比赛里抽取价值盘推荐（按 edge 降序）。"""
    recs = []
    for m in matches or []:
        sv = m.get('sharp_value') or {}
        if not sv.get('value_available') or not sv.get('has_value'):
            continue
        for pk in sv['picks']:
            recs.append({
                'match_id': m.get('match_id'), 'league': m.get('league'),
                'home_team': m.get('home_team'), 'away_team': m.get('away_team'),
                'match_time': m.get('match_time', ''),
                'side': pk['side'], 'option': pk['option'],
                'jc_odds': pk['jc_odds'], 'prob': pk['prob'],
                'fair_odds': pk['fair_odds'], 'edge_pct': pk['edge_pct'],
                'kelly_pct': pk['kelly_pct'], 'amount': pk['amount'],
                'sharp_prob': sv['sharp_prob'], 'final_prob': sv['final_prob'],
                'confidence_level': m.get('confidence_level', ''),
            })
    recs.sort(key=lambda r: r['edge_pct'], reverse=True)
    return recs[:top_n]


def record_value_picks(matches):
    """把价值盘推荐写入回测（play_type='VAL'），供 ROI / Brier / log-loss 验证。
    按 (match_id, 'VAL') 去重，同场只记最高 edge 的一注。返回新增条数。"""
    if not matches:
        return 0
    try:
        import backtest as bt
        from backtest_models import BtBet
        try:
            existing = {(b.match_id, b.play_type)
                        for b in BtBet.query.filter_by(play_type='VAL').all()}
        except Exception:
            existing = set()
        n = 0
        for m in matches:
            eid = str(m.get('bz_event_id') or '')
            sv = m.get('sharp_value') or {}
            if not eid or not sv.get('has_value'):
                continue
            if (eid, 'VAL') in existing:
                continue
            pk = sv['picks'][0]
            try:
                bt.record_prediction(
                    eid, 'VAL', _PICK[pk['side']],
                    round(pk['prob'] / 100.0, 4), pk['jc_odds'],
                    model_name='value-sharp', confidence=pk['prob'] / 100.0,
                    home_team=m.get('home_team'), away_team=m.get('away_team'),
                    jingcai=True, confidence_level=m.get('confidence_level'))
                existing.add((eid, 'VAL'))
                n += 1
            except Exception as e:
                logger.warning('[value] record failed: %s', e)
                continue
        return n
    except Exception as e:
        logger.warning('[value] record_value_picks: %s', e)
        return 0


def value_summary():
    """价值盘战绩：ROI/命中率（复用回测）+ Brier / log-loss（概率校准质量）。"""
    try:
        from backtest import compute_summary
        s = compute_summary(play_type='VAL', jingcai_only=True)
        from backtest_models import BtBet
        bets = [b for b in BtBet.query.filter_by(play_type='VAL').all() if b.settled_at]
        n = 0
        brier = 0.0
        log_loss = 0.0
        for b in bets:
            if b.predicted_prob is None or b.outcome not in ('win', 'lose'):
                continue
            p = min(max(float(b.predicted_prob), 1e-6), 1 - 1e-6)
            y = 1.0 if b.outcome == 'win' else 0.0
            brier += (p - y) ** 2
            log_loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
            n += 1
        s['scored'] = n
        s['brier'] = round(brier / n, 4) if n else None
        s['log_loss'] = round(log_loss / n, 4) if n else None
        s['method'] = '锐盘基准 + Dixon-Coles 校准，阈值 %s%%，分数凯利' % (EDGE_MIN * 100)
        return s
    except Exception as e:
        logger.warning('[value] value_summary: %s', e)
        return {'total_bets': 0, 'wins': 0, 'losses': 0, 'pending': 0,
                'hit_rate': 0, 'roi': 0, 'total_pnl': 0, 'brier': None,
                'log_loss': None, 'scored': 0, 'records': []}
