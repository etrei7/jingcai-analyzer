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


def save_predictions(matches):
    """保存本次推荐预测到历史记录，去重已有记录"""
    hist = _load_history()
    existing_ids = {p.get('raw_event_id') for p in hist['predictions'] if p.get('raw_event_id')}
    today = datetime.now(CST).strftime('%Y-%m-%d')

    for m in matches:
        eid = m.get('raw_event_id', '')
        if not eid or eid in existing_ids or not m.get('predicted_option'):
            continue
        hist['predictions'].append({
            'match_id': m.get('match_id', ''),
            'raw_event_id': str(eid),
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
        })
        existing_ids.add(eid)

    _recalc_stats(hist)
    _save_history(hist)
    logger.info(f'[History] saved {len(hist["predictions"])} total predictions')


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
