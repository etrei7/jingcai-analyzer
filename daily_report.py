"""每日分析报告：把当前分析结果聚合成结构化的「今日综述」，供前端实时展示。

每次刷新基于最新赔率/资讯/首发/价值盘/校准重新生成（不落库、不缓存），
所以天然是"实时"的：数据一变，报告跟着变。
"""
import logging
import time

logger = logging.getLogger(__name__)


def _odds_ok(v):
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def build_report(matches, recommendations=None, value_recs=None, stats=None):
    stats = stats or {}
    matches = matches or []
    recs = recommendations or []
    vrecs = value_recs or []
    rep = {
        'date': time.strftime('%Y-%m-%d'),
        'updated': time.strftime('%H:%M:%S'),
        'total': len(matches),
        'source': stats.get('source', ''),
        'counts': {}, 'steady': [], 'value': [], 'avoid': [],
        'off_field': [], 'risk_notes': [], 'headline': [],
    }

    # ===== 计数 =====
    lvl = {'高': 0, '中': 0, '低': 0}
    extreme = 0
    for m in matches:
        lv = m.get('confidence_level') or '低'
        lvl[lv] = lvl.get(lv, 0) + 1
        odds = [o for o in (m.get('win_odds'), m.get('draw_odds'), m.get('lose_odds')) if _odds_ok(o)]
        if odds and min(float(o) for o in odds) < 1.4:
            extreme += 1
    rep['counts'] = {'high': lvl.get('高', 0), 'mid': lvl.get('中', 0),
                     'low': lvl.get('低', 0), 'extreme_fav': extreme}

    # ===== 稳健：非估算、组合赔率较低的方案 =====
    for r in recs:
        try:
            co = float(r.get('combo_odds') or 0)
        except (TypeError, ValueError):
            co = 0
        if r.get('has_estimated_legs') or co <= 0 or co > 3.2:
            continue
        legs = []
        for md in (r.get('matches_detail') or []):
            legs.append('%s vs %s：%s @%s' % (md.get('home_team', ''), md.get('away_team', ''),
                                               md.get('option', ''), md.get('odds', '')))
        rep['steady'].append({
            'name': r.get('name', ''), 'plan_type': r.get('plan_type', ''),
            'combo_odds': co, 'risk_level': r.get('risk_level', ''),
            'legs': legs, 'stake_note': r.get('stake_note', ''),
            'cutoff_note': r.get('cutoff_note', ''),
            'correlation_warning': r.get('correlation_warning', ''),
        })

    # ===== 价值：价值盘（>30% 标为异常，提示疑数据错配）=====
    for v in vrecs:
        try:
            edge = float(v.get('edge_pct') or 0)
        except (TypeError, ValueError):
            edge = 0
        rep['value'].append({
            'teams': '%s vs %s' % (v.get('home_team', ''), v.get('away_team', '')),
            'match_time': v.get('match_time', ''), 'league': v.get('league', ''),
            'option': v.get('option', ''), 'jc_odds': v.get('jc_odds'),
            'fair_odds': v.get('fair_odds'), 'edge_pct': edge,
            'kelly_pct': v.get('kelly_pct'), 'prob': v.get('prob'),
            'anomaly': edge > 30,
        })

    # ===== 回避：偏贵 / 同类不盈利 / 价值异常 =====
    seen = set()
    for m in matches:
        reasons = []
        if m.get('overpriced'):
            reasons.append('竞彩赔率相对国际锐盘偏贵')
        if m.get('seg_bad'):
            reasons.append('同类场次（赔率区间/联赛）历史不盈利')
        va = m.get('value_analysis') or {}
        if (va.get('best_edge') or 0) >= 30:
            reasons.append('模型价值异常(>30%)，疑数据错配')
        sv = m.get('sharp_value') or {}
        if sv.get('has_value') and sv.get('picks') and (sv['picks'][0].get('edge_pct') or 0) > 30:
            reasons.append('锐盘价值异常(>30%)，疑数据错配')
        if not reasons:
            continue
        key = m.get('match_id')
        if key in seen:
            continue
        seen.add(key)
        rep['avoid'].append({
            'match_id': key,
            'teams': '%s vs %s' % (m.get('home_team', ''), m.get('away_team', '')),
            'time': m.get('match_time', ''), 'reasons': reasons,
        })

    # ===== 场外因素（仅有真实富化的场次）=====
    for m in matches:
        inj = m.get('injuries') or {}
        wx = m.get('weather') or {}
        rf = m.get('referee') or {}
        news = m.get('news') or {}
        line = m.get('lineup') or {}
        nh = len(news.get('home') or []) if isinstance(news, dict) else 0
        na = len(news.get('away') or []) if isinstance(news, dict) else 0
        has_inj = (inj.get('home_count') or 0) + (inj.get('away_count') or 0) > 0
        has_wx = wx.get('desc') and wx.get('desc') != '未知'
        has_ref = rf.get('name') and rf.get('name') != '待定'
        has_lu = bool(line.get('home') or line.get('away'))
        if not (has_inj or has_wx or has_ref or nh or na or has_lu or m.get('home_rank')):
            continue
        items = []
        if has_inj:
            items.append('伤停 主%s/客%s' % (inj.get('home_count', 0), inj.get('away_count', 0)))
        if has_wx:
            items.append('天气:%s(%s)' % (wx.get('desc'), wx.get('impact') or '无明显影响'))
        if has_ref:
            items.append('裁判:%s(%s)' % (rf.get('name'), rf.get('strictness') or '未知'))
        if nh or na:
            items.append('资讯 主%d/客%d' % (nh, na))
        if has_lu:
            fm = []
            if line.get('home'):
                fm.append('主 ' + (line['home'].get('formation') or '?'))
            if line.get('away'):
                fm.append('客 ' + (line['away'].get('formation') or '?'))
            items.append('首发已确认(' + '/'.join(fm) + ')')
        rep['off_field'].append({
            'teams': '%s vs %s' % (m.get('home_team', ''), m.get('away_team', '')),
            'time': m.get('match_time', ''), 'items': items,
        })

    # ===== 风险提示 =====
    notes = rep['risk_notes']
    if extreme >= 5:
        notes.append('今日 %d 场为极端热门（最低赔<1.4），"诱盘/爆冷"风险高，长串与全热门组合慎用。' % extreme)
    if rep['counts']['high'] == 0:
        notes.append('今日无"高信心"场次，说明模型普遍把握不足，宜小注或观望。')
    if rep['avoid']:
        notes.append('有 %d 场被标记需回避（赔率偏贵/同类不盈利/价值异常），见下。' % len(rep['avoid']))
    enriched = len(rep['off_field'])
    if matches and enriched < len(matches):
        notes.append('仅 %d/%d 场有场外数据（伤停/排名/天气/首发），其余以赔率为主，勿臆测。' % (enriched, len(matches)))
    corr = [s.get('correlation_warning') for s in rep['steady'] if s.get('correlation_warning')]
    if corr:
        notes.append('部分串关存在同联赛/同时段关联风险，建议分散或拆开。')
    try:
        from calibration import get_calibration
        cal = get_calibration()
        b = (cal or {}).get('buckets') or {}
        seg = []
        for k in ('高', '中', '低'):
            if b.get(k) and b[k].get('n', 0) >= 20:
                seg.append('%s档命中%s%%/盈亏线%s%%(n=%s)' % (
                    k, b[k].get('hit_rate'), b[k].get('required_rate'), b[k].get('n')))
        if seg:
            notes.append('信心校准：' + '；'.join(seg) + '。命中率低于盈亏线即长期亏损。')
    except Exception:
        pass
    notes.append('以上为数据整理与模型输出，非投注建议；彩票含抽水，请理性、小额。')

    # ===== 一句话结论 =====
    rep['headline'] = [
        '共 %d 场：高%d/中%d/低%d，极端热门 %d 场。' % (
            len(matches), rep['counts']['high'], rep['counts']['mid'],
            rep['counts']['low'], extreme),
        '稳健方案 %d 个，价值机会 %d 个，需回避 %d 场。' % (
            len(rep['steady']), len(rep['value']), len(rep['avoid'])),
    ]

    try:
        rep['text'] = _build_text(rep)
    except Exception as e:
        logger.warning('[report] text failed: %s', e)
        rep['text'] = ''
    return rep


def _build_text(rep):
    """把结构化综述转成可直接阅读/复制的文本。"""
    L = []
    date = rep.get('date', '')
    total = rep.get('total', 0)
    src = rep.get('source') or '竞彩官方'
    cnt = rep.get('counts') or {}
    L.append('今日竞彩分析总结（%s，%d 场）' % (date, total))
    L.append('')

    # 一、口径与限制
    L.append('一、数据口径与限制')
    L.append('- 场次：%d 场，来源：%s（真实赔率）。' % (total, src))
    enriched = len(rep.get('off_field') or [])
    L.append('- Bzzoiro 场外富化成功 %d 场；其余 %d 场无伤停/排名/天气/裁判数据，'
             '方向基本为"市场最低赔方"，不要当成完整多维分析。'
             % (enriched, max(0, total - enriched)))
    try:
        from calibration import get_calibration
        b = ((get_calibration() or {}).get('buckets') or {})
        segs = []
        for k in ('高', '中', '低'):
            if b.get(k) and b[k].get('n', 0) >= 20:
                segs.append('%s档命中%s%%/盈亏线%s%%(n=%s)' % (
                    k, b[k].get('hit_rate'), b[k].get('required_rate'), b[k].get('n')))
        if segs:
            L.append('- 信心校准（历史）：' + '；'.join(segs) + '。命中率低于盈亏线即长期亏损。')
    except Exception:
        pass
    L.append('')

    # 二、整体判断
    L.append('二、整体判断')
    L.append('- 高信心 %d 场、中 %d、低 %d；极端热门(<1.4) %d 场。' % (
        cnt.get('high', 0), cnt.get('mid', 0), cnt.get('low', 0), cnt.get('extreme_fav', 0)))
    concl = []
    if cnt.get('extreme_fav', 0) >= 5:
        concl.append('极端热门偏多，"诱盘/爆冷"风险高，不适合重仓长串')
    if cnt.get('high', 0) == 0:
        concl.append('今日无高信心场次，模型把握不足，宜小注观望')
    if not concl:
        concl.append('整体把握一般，建议小注、分散')
    L.append('- 结论：' + '；'.join(concl) + '。')
    L.append('')

    # 三、分级建议
    L.append('三、分级建议（若参与，小注参考）')
    steady = rep.get('steady') or []
    L.append('A. 相对稳健（低赔、非估算）')
    if steady:
        for s in steady[:4]:
            legs = ' / '.join(s.get('legs') or [])
            extra = ('；' + s['stake_note']) if s.get('stake_note') else ''
            L.append('- %s [%s] 组合 %s ｜ %s%s' % (
                s.get('name', ''), s.get('plan_type', ''), s.get('combo_odds'), legs, extra))
    else:
        L.append('- 今日无满足条件的稳健方案。')

    vals = [v for v in (rep.get('value') or []) if not v.get('anomaly')]
    L.append('B. 价值小注（正期望、命中率低，只当彩票）')
    if vals:
        for v in vals[:6]:
            L.append('- %s %s：%s @%s ｜ 公平 %s ｜ 正期望 +%s%% ｜ 建议仓位 %s%%' % (
                v.get('teams', ''), v.get('match_time') or '', v.get('option'),
                v.get('jc_odds'), v.get('fair_odds'), v.get('edge_pct'), v.get('kelly_pct')))
    else:
        L.append('- 今日无明显正期望标的。')

    L.append('C. 明确回避')
    avoid = rep.get('avoid') or []
    if avoid:
        for a in avoid[:8]:
            L.append('- %s %s — %s' % (a.get('teams', ''), a.get('time') or '',
                                       '；'.join(a.get('reasons') or [])))
    for v in (rep.get('value') or []):
        if v.get('anomaly'):
            L.append('- %s：%s @%s 正期望 +%s%% 属异常量级（疑数据错配），不要追' % (
                v.get('teams', ''), v.get('option'), v.get('jc_odds'), v.get('edge_pct')))
    if not avoid and not any(v.get('anomaly') for v in (rep.get('value') or [])):
        L.append('- 暂无。')
    L.append('')

    # 四、场外因素
    L.append('四、场外因素（仅真实数据场次）')
    off = rep.get('off_field') or []
    if off:
        for o in off[:8]:
            L.append('- %s %s — %s' % (o.get('teams', ''), o.get('time') or '',
                                       '；'.join(o.get('items') or [])))
    else:
        L.append('- 今日暂无场外数据，不要假设其余比赛"没问题"。')
    L.append('')

    # 五、纪律
    L.append('五、纪律（比选哪场更重要）')
    L.append('- 今日总投入建议 ≤ 本金的 3%~5%，单注小、别追高、别加注。')
    L.append('- 优先 A 类；B 类当彩票，输光不心疼；坚决不碰 C 类（尤其异常量级）。')
    L.append('- 只买能在开赛前看到首发的场次；首发与推荐冲突就放弃。')
    L.append('')
    L.append('免责声明：以上为数据整理与模型输出，不构成投注建议；彩票含抽水、长期负期望，请理性、小额。')
    return '\n'.join(L)
