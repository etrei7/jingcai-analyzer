import logging
import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import Config
from models import db
from data_generator import generate_matches as generate_mock_matches
from analysis import analyze_matches, generate_parlay_recommendations, generate_total_goals_recommendations
from scheduler import init_scheduler
from history import save_predictions, get_stats
from bizzoiro_client import _parse_event_to_match, _assign_match_ids, LEAGUE_NAME_MAP

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()

init_scheduler(app)


@app.route('/')
def index():
    api_key = os.environ.get('BZZOIRO_API_KEY', '')
    return render_template('index.html', api_key=api_key)


@app.route('/api/data')
def get_data():
    matches = []
    source = ''
    odds_movement = {}

    try:
        from bizzoiro_client import (
            fetch_events, fetch_standings_for_matches, fetch_predictions,
            fetch_odds_movement_for_matches
        )
        matches = fetch_events(limit=15)
    except Exception:
        matches = []

    if matches and len(matches) >= 3:
        logging.info('[API] 服务端直连 Bzzoiro 成功')
        try:
            from bizzoiro_client import fetch_standings_for_matches, fetch_predictions, fetch_odds_movement_for_matches
            standings = fetch_standings_for_matches(matches)
            predictions = fetch_predictions()
            odds_movement = fetch_odds_movement_for_matches(matches)

            # 竞彩官单匹配（服务端刮取 500.com）
            try:
                from jingcai_scraper import fetch_jingcai_match_ids, filter_by_jingcai
                jc_list = fetch_jingcai_match_ids()
                if jc_list:
                    matches = filter_by_jingcai(matches, jc_list)
                    source = 'Bzzoiro + 竞彩官方场单'
                else:
                    source = 'Bzzoiro API (竞彩刮取失败)'
            except Exception:
                source = 'Bzzoiro API'
        except Exception:
            standings = {}
            predictions = {}
            source = 'Bzzoiro (部分数据失败)'
    else:
        logging.info('[API] 服务端直连失败，降级模拟数据')
        matches = generate_mock_matches(12)
        source = '模拟数据 (后端API不可用)'
        standings = {}
        predictions = {}

    analyzed = analyze_matches(matches, standings, predictions)
    recommendations = generate_parlay_recommendations(analyzed)
    total_goals_recs = generate_total_goals_recommendations(analyzed)

    try:
        save_predictions(analyzed)
    except Exception:
        pass
    history_stats = get_stats()

    for m in analyzed:
        eid = m.get('raw_event_id', '')
        if eid and eid in odds_movement:
            m['odds_movement'] = odds_movement[eid]

    return jsonify({
        'matches': analyzed,
        'recommendations': recommendations,
        'total_goals_recs': total_goals_recs,
        'history_stats': history_stats,
        'stats': {
            'total_matches': len(analyzed),
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': source
        }
    })


@app.route('/api/analyze', methods=['POST'])
def analyze_data():
    data = request.get_json(silent=True) or {}
    raw_events = data.get('events', [])
    raw_predictions = data.get('predictions', [])
    jc_list = data.get('jingcai_list', [])

    if not raw_events:
        return jsonify({'error': 'no events provided'}), 400

    matches = []
    for e in raw_events:
        league_obj = e.get('league', {})
        league_name_en = ''
        if isinstance(league_obj, dict):
            if league_obj.get('is_women'):
                continue
            league_name_en = league_obj.get('name', '')
        if LEAGUE_NAME_MAP.get(league_name_en) is None and league_name_en:
            continue
        m = _parse_event_to_match(e)
        if m['win_odds'] <= 0 and m['draw_odds'] <= 0 and m['lose_odds'] <= 0:
            continue
        matches.append(m)

    if len(matches) < 3:
        matches = generate_mock_matches(12)
        source = '模拟数据 (真实比赛不足3场)'
    else:
        _assign_match_ids(matches)
        if jc_list:
            matches, jc_applied = _filter_by_jingcai(matches, jc_list)
            if jc_applied:
                source = 'Bzzoiro + 竞彩官方场单'
            else:
                source = 'Bzzoiro API (竞彩场单匹配失败)'
        else:
            source = 'Bzzoiro API (未获取竞彩场单)'

    pred_map = {}
    for p in raw_predictions:
        ev = p.get('event')
        eid = ev.get('id') if isinstance(ev, dict) else (int(ev) if isinstance(ev, (int, str)) else None)
        if eid:
            pred_map[str(eid)] = {
                'prob_home_win': p.get('prob_home_win'),
                'prob_draw': p.get('prob_draw'),
                'prob_away_win': p.get('prob_away_win'),
                'expected_home_goals': p.get('expected_home_goals', 0) or 0,
                'expected_away_goals': p.get('expected_away_goals', 0) or 0,
                'expected_goals': (p.get('expected_home_goals', 0) or 0) + (p.get('expected_away_goals', 0) or 0),
                'confidence': p.get('confidence'),
                'predicted_result': p.get('predicted_result'),
                'prob_over_25': p.get('prob_over_25'),
                'prob_btts': p.get('prob_btts_yes'),
            }

    analyzed = analyze_matches(matches, None, pred_map)
    recommendations = generate_parlay_recommendations(analyzed)
    total_goals_recs = generate_total_goals_recommendations(analyzed)

    try:
        save_predictions(analyzed)
    except Exception:
        pass
    history_stats = get_stats()

    return jsonify({
        'matches': analyzed,
        'recommendations': recommendations,
        'total_goals_recs': total_goals_recs,
        'history_stats': history_stats,
        'stats': {
            'total_matches': len(analyzed),
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': source
        }
    })


def _filter_by_jingcai(matches, jc_list):
    """将 Bzzoiro 场次匹配到竞彩官单，过滤非竞彩场次并覆盖 match_id"""
    if not jc_list:
        return matches, False

    bz_by_league = {}
    for m in matches:
        league_cn = m.get('league', '')
        bz_by_league.setdefault(league_cn, []).append(m)

    jc_by_league = {}
    for item in jc_list:
        if isinstance(item, list) and len(item) >= 2:
            jid, jleague = item[0], item[1]
            jc_by_league.setdefault(jleague, []).append(jid)

    matched = []
    for jleague, jids in jc_by_league.items():
        bz_list = bz_by_league.get(jleague, [])
        if not bz_list:
            continue
        bz_list.sort(key=lambda m: m.get('match_time', '99:99'))
        for idx, jid in enumerate(jids):
            if idx < len(bz_list):
                m = bz_list[idx]
                m['match_id'] = jid
                matched.append(m)

    if matched:
        return matched, True
    return matches, False


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
