"""预测历史追踪系统：记录每次推荐并追踪命中率"""
import json, os, logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'predictions_history.json')


def _load_history():
    if not os.path.exists(HISTORY_FILE):
        return {'predictions': [], 'stats': {'total': 0, 'hits': 0, 'misses': 0, 'total_rate': 0, 'recent': []}}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'predictions': [], 'stats': {'total': 0, 'hits': 0, 'misses': 0, 'total_rate': 0, 'recent': []}}


def _save_history(hist):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f'Failed to save history: {e}')


def _norm_key(s):
    """规范化的去重字符串：小写、去空格/连字符"""
    return (s or '').replace(' ', '').replace('-', '').lower()


def _match_dedup_key(m):
    """构造跨数据源的统一去重键：球队+联赛+时间。
    竞彩源与 Bzzoiro 源的 raw_event_id 不同，但同一场比赛可通过该键去重。"""
    home = _norm_key(m.get('home_team', ''))
    away = _norm_key(m.get('away_team', ''))
    league = _norm_key(m.get('league', ''))
    time = _norm_key(m.get('match_time') or m.get('time') or '')
    return f'{home}|{away}|{league}|{time}'


def save_predictions(matches):
    """保存本次推荐预测到历史记录，去重已有记录（按跨数据源统一键 + raw_event_id）"""
    hist = _load_history()
    existing_ids = {p.get('raw_event_id') for p in hist['predictions'] if p.get('raw_event_id')}
    existing_keys = {p.get('_dedup_key') for p in hist['predictions'] if p.get('_dedup_key')}
    today = datetime.now(CST).strftime('%Y-%m-%d')

    added = 0
    for m in matches:
        eid = m.get('raw_event_id', '')
        dkey = _match_dedup_key(m)
        if not m.get('predicted_option'):
            continue
        # 优先用统一键去重，兜底用 raw_event_id
        if dkey and dkey in existing_keys:
            continue
        if eid and eid in existing_ids:
            continue
        pred = {
            'match_id': m.get('match_id', ''),
            'raw_event_id': str(eid) if eid else '',
            '_dedup_key': dkey,
            'home_team': m.get('home_team', ''),
            'away_team': m.get('away_team', ''),
            'league': m.get('league', ''),
            'predicted': m.get('predicted_option', ''),
            'confidence': m.get('confidence_level', ''),
            'odds': m.get('win_odds', 0) if m.get('predicted_option') == '胜'
                    else m.get('draw_odds', 0) if m.get('predicted_option') == '平'
                    else m.get('lose_odds', 0),
            'date': today,
            'verified': False,
            'actual': None,
            'score': None,
            'hit': None,
        }
        hist['predictions'].append(pred)
        existing_ids.add(eid)
        if dkey:
            existing_keys.add(dkey)
        added += 1

    _recalc_stats(hist)
    _save_history(hist)
    logger.info(f'[History] saved {added} new, {len(hist["predictions"])} total predictions')


def verify_prediction(raw_event_id, actual_result, score=''):
    """验证单条预测结果: actual_result = '胜'/'平'/'负'"""
    hist = _load_history()
    for p in hist['predictions']:
        if str(p.get('raw_event_id', '')) == str(raw_event_id) and not p.get('verified'):
            p['verified'] = True
            p['actual'] = actual_result
            p['score'] = score
            p['hit'] = (p['predicted'] == actual_result)
            logger.info(f'[History] {p["match_id"]} {p["home_team"]}vs{p["away_team"]}: predicted={p["predicted"]} actual={actual_result} hit={p["hit"]}')
            break
    _recalc_stats(hist)
    _save_history(hist)


def get_stats():
    """返回当前战绩统计数据"""
    hist = _load_history()
    return hist.get('stats', {'total': 0, 'hits': 0, 'misses': 0, 'total_rate': 0, 'recent': []})


def get_history_records():
    """返回所有历史记录（含用户投注记录），按时间倒序"""
    hist = _load_history()
    records = hist.get('predictions', [])
    bets = hist.get('bets', [])
    combined = []
    for p in records:
        combined.append({
            'type': 'prediction',
            'match_id': p.get('match_id', ''),
            'matchNum': p.get('raw_event_id', ''),
            'homeTeam': p.get('home_team', ''),
            'awayTeam': p.get('away_team', ''),
            'league': p.get('league', ''),
            'direction': p.get('predicted', ''),
            'odds': p.get('odds', 0),
            'confidence': p.get('confidence', ''),
            'verified': p.get('verified', False),
            'actual': p.get('actual', None),
            'score': p.get('score', None),
            'hit': p.get('hit', None),
            'date': p.get('date', ''),
        })
    for b in bets:
        combined.append({
            'type': 'bet',
            'matchNum': b.get('matchNum', ''),
            'playType': b.get('playType', ''),
            'playTypeLabel': b.get('playTypeLabel', ''),
            'direction': b.get('direction', ''),
            'odds': b.get('odds', 0),
            'teams': b.get('teams', ''),
            'actualScore': b.get('actualScore', None),
            'result': b.get('result', 'pending'),
            'createdAt': b.get('createdAt', ''),
            'updatedAt': b.get('updatedAt', ''),
        })
    combined.sort(key=lambda x: x.get('date') or x.get('createdAt') or '', reverse=True)
    return combined


def add_bet_record(record):
    """保存一条用户投注记录，去重 (matchNum+playType)"""
    hist = _load_history()
    bets = hist.setdefault('bets', [])
    match_num = record.get('matchNum', '')
    play_type = record.get('playType', '')
    if not match_num or not play_type:
        return False
    # 去重：同场同玩法更新
    updated = False
    for b in bets:
        if b.get('matchNum') == match_num and b.get('playType') == play_type:
            b.update(record)
            b['updatedAt'] = datetime.now().isoformat()
            updated = True
            break
    if not updated:
        rec = dict(record)
        rec.setdefault('createdAt', datetime.now().isoformat())
        rec.setdefault('updatedAt', datetime.now().isoformat())
        rec.setdefault('result', 'pending')
        bets.append(rec)
    _save_history(hist)
    logger.info(f'[History] bet saved: {match_num} {play_type}')
    return True


def _recalc_stats(hist):
    """重新计算命中率统计"""
    preds = hist['predictions']
    verified = [p for p in preds if p.get('verified')]
    total_verified = len(verified)
    hits = sum(1 for p in verified if p.get('hit'))
    misses = total_verified - hits
    rate = round(hits / total_verified * 100, 1) if total_verified > 0 else 0

    recent_10 = sorted(verified, key=lambda p: p.get('date', ''), reverse=True)[:10]
    recent = []
    for p in recent_10:
        recent.append({
            'match_id': p.get('match_id', ''),
            'teams': f'{p.get("home_team", "")}vs{p.get("away_team", "")}',
            'predicted': p.get('predicted', ''),
            'actual': p.get('actual', ''),
            'hit': p.get('hit', False),
            'date': p.get('date', ''),
        })

    hist['stats'] = {
        'total': total_verified,
        'hits': hits,
        'misses': misses,
        'total_rate': rate,
        'recent': recent,
        'total_predictions': len(preds),
    }
