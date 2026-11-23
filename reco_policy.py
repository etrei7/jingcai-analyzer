"""推荐闸门：用玩法级历史评估（play_review）决定"今天还能不能推这个玩法"。

为什么要有它：模型输出的"高信心"只是 1/最低赔率，并没有经过历史验证。
如果某个玩法在真实结算数据里长期低于盈亏线（比如胜平负 edge −17pp），
继续把它放进首页推荐就是把统计噪声当成信号。这里做三件事：

1. 读 play_review 的结论（缓存 30 分钟，避免每次请求都扫全表）。
2. 暂停（pause）/ 不可投注（reference）的玩法，不进推荐列表，
   但会把"为什么被拿掉"记进 suppressed，前端可展示，不做黑箱。
3. 样本不足（insufficient）的玩法保留但降级提示，只能小注观察。

原则：宁可今天不推，也不推一个历史数据已经否定的玩法。
"""
import logging
import time

logger = logging.getLogger(__name__)

_CACHE = {'ts': 0.0, 'verdicts': None}
_TTL = 1800

# plan_type 关键字 → 玩法代码
_PLAN_PLAY = (('让球', 'AH'), ('总进球', 'TG'), ('价值', 'VAL'), ('胜平负', '1X2'),
              ('稳胆', '1X2'), ('比分', 'CS'), ('半全场', 'HTFT'))


def _play_of_plan(plan_type):
    s = str(plan_type or '')
    for kw, play in _PLAN_PLAY:
        if kw in s:
            return play
    return '1X2'


def verdicts(max_age=_TTL):
    if _CACHE['verdicts'] is not None and time.time() - _CACHE['ts'] < max_age:
        return _CACHE['verdicts']
    data = {}
    try:
        import play_review
        rep = play_review.review('all')
        data = {p['play_type']: p for p in rep.get('plays', [])}
    except Exception as e:
        logger.warning('[policy] 读取玩法评估失败（放行全部推荐）: %s', e)
        data = {}
    _CACHE.update(ts=time.time(), verdicts=data)
    return data


def clear_cache():
    _CACHE.update(ts=0.0, verdicts=None)


def _check(vmap, play):
    """返回 (是否放行, 结论码, 说明)。没有该玩法历史 → 放行（新玩法不预判）。"""
    p = vmap.get(play)
    if not p:
        return (True, 'unknown', '暂无该玩法的历史样本，按常规仓位小注观察')
    code = p.get('verdict_code') or 'watch'
    note = p.get('verdict') or ''
    if code in ('pause', 'reference'):
        return (False, code, note)
    if code == 'insufficient':
        return (True, code, '样本不足（%d 注），仅小注观察：%s' % (p.get('n', 0), note))
    return (True, code, note)


def apply_policy(parlays=None, tg_recs=None, val_recs=None):
    """过滤三组推荐，返回 (parlays, tg_recs, val_recs, info)。"""
    vmap = verdicts()
    info = {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'plays': {}, 'suppressed': [], 'cache_ttl_min': _TTL // 60}
    for play, p in vmap.items():
        info['plays'][play] = {'code': p.get('verdict_code'), 'grade': p.get('grade'),
                               'n': p.get('n'), 'edge_pp': p.get('edge_pp'),
                               'note': p.get('verdict')}

    def _filter(items, play_of, kind):
        kept = []
        for rec in items or []:
            play = play_of(rec)
            ok, code, note = _check(vmap, play)
            if ok:
                rec = dict(rec)
                rec['policy'] = {'play': play, 'code': code, 'note': note}
                kept.append(rec)
            else:
                info['suppressed'].append({
                    'kind': kind, 'play': play, 'code': code, 'note': note,
                    'name': rec.get('name') or rec.get('title') or rec.get('match_id') or '',
                })
        return kept

    out_p = _filter(parlays, lambda r: _play_of_plan(r.get('plan_type') or r.get('name')), '串关')
    out_t = _filter(tg_recs, lambda r: _play_of_plan(r.get('play_type') or r.get('plan_type') or 'TG'), '总进球')
    out_v = _filter(val_recs, lambda r: _play_of_plan(r.get('play_type') or r.get('plan_type') or 'VAL'), '价值盘')
    return out_p, out_t, out_v, info
