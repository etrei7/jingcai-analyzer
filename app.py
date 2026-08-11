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

    # 优先：竞彩官方 API（真实场单 + 赔率）
    try:
        from jingcai_scraper import fetch_jingcai_matches
        matches = fetch_jingcai_matches()
        if matches and len(matches) >= 3:
            source = '竞彩官方'
            data_priority = 'primary'
            data_note = '主数据源：中国体育彩票官方赔率'
            logging.info('[API] 竞彩官方 %d 场', len(matches))
    except Exception:
        pass

    # 次选：Bzzoiro API
    if not matches or len(matches) < 3:
        data_priority = 'secondary'
        data_note = '备用源：Bzzoiro 第三方数据（竞彩官方API不可用）'
        try:
            from bizzoiro_client import fetch_events
            matches = fetch_events(limit=15)
            if matches and len(matches) >= 3:
                source = 'Bzzoiro API'
                logging.info('[API] Bzzoiro %d 场', len(matches))
        except Exception:
            matches = []

    # 最后：模拟数据
    if not matches or len(matches) < 3:
        matches = generate_mock_matches(12)
        source = '模拟数据'
        data_priority = 'fallback'
        data_note = '降级源：模拟数据（所有外部API不可用）'

    # 附加数据（standings + predictions 仅 Bzzoiro 模式有效）
    standings = {}
    predictions = {}
    if source == 'Bzzoiro API':
        try:
            from bizzoiro_client import fetch_standings_for_matches, fetch_predictions
            standings = fetch_standings_for_matches(matches)
            predictions = fetch_predictions()
        except Exception:
            pass

    analyzed = analyze_matches(matches, standings, predictions)
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
            'source': source,
            'data_priority': data_priority,
            'data_note': data_note,
        }
    })
    # /api/data end


@app.route('/api/jingcai-token')
def get_jingcai_token():
    from jingcai_scraper import refresh_jingcai_token
    token = refresh_jingcai_token()
    if token:
        return jsonify({'token': token, 'success': True})
    return jsonify({'token': None, 'success': False})


@app.route('/api/analyze', methods=['POST'])
def analyze_data():
    data = request.get_json(silent=True) or {}

    # 路径1：竞彩官方数据直接分析
    jc_matches = data.get('jingcai_matches', [])
    if jc_matches:
        client_time = data.get('client_time', '')
        fetch_ts = datetime.now().strftime('%H:%M:%S')
        if client_time:
            try:
                ct = datetime.fromisoformat(client_time.replace('Z', '+00:00'))
                fetch_ts = ct.astimezone().strftime('%H:%M:%S')
            except Exception:
                pass
        matches = jc_matches
        # 补充缺失字段
        for m in matches:
            m.setdefault('handicap', '0')
            m.setdefault('league_id', None)
            m.setdefault('home_team_id', None)
            m.setdefault('away_team_id', None)
            m.setdefault('match_time', m.get('time', '')[:5])
            m.setdefault('raw_event_id', m.get('match_id', ''))
            m.setdefault('injuries', {'home': [], 'away': [], 'home_count': 0, 'away_count': 0})
            m.setdefault('referee', {'name': '待定', 'strictness': '未知', 'avg_yellows': 0, 'avg_reds': 0, 'games': 0})
            m.setdefault('weather', {'code': None, 'desc': '未知', 'temp': None, 'wind': None, 'impact': '无明显影响'})
            m.setdefault('travel_distance_km', None)
            m.setdefault('is_derby', False)
            m.setdefault('venue_name', ''); m.setdefault('venue_city', ''); m.setdefault('venue_capacity', None)
            m.setdefault('home_coach', ''); m.setdefault('away_coach', '')
            m.setdefault('ai_preview', '')
            m.setdefault('home_rank', None); m.setdefault('away_rank', None)
            m.setdefault('home_form', ''); m.setdefault('away_form', '')
            m.setdefault('home_xgd', None); m.setdefault('away_xgd', None)
            m.setdefault('home_confidence', 0); m.setdefault('away_confidence', 0)
            m.setdefault('home_breakdown', {}); m.setdefault('away_breakdown', {})
            m.setdefault('expected_total', 0); m.setdefault('top3_goals', [])
            m.setdefault('handicap_line', 0); m.setdefault('handicap_win_odds', 0); m.setdefault('handicap_draw_odds', 0); m.setdefault('handicap_lose_odds', 0)

        # Bzzoiro 双源富化（客户端传数据，按队名匹配）
        bz_events = data.get('bz_events', [])
        bz_predictions = {}
        if bz_events:
            bz_preds_raw = data.get('bz_predictions', [])
            bz_pred_map = {}
            for p in bz_preds_raw:
                ev = p.get('event', {})
                eid = str(ev.get('id', '')) if isinstance(ev, dict) else str(p.get('event', ''))
                if eid: bz_pred_map[eid] = p
            for jm in matches:
                jh, ja = jm.get('home_team', ''), jm.get('away_team', '')
                for bz in bz_events:
                    if bz.get('home_team', '') == jh and bz.get('away_team', '') == ja:
                        ua = bz.get('unavailable_players') or {}
                        hl, al = ua.get('home', []), ua.get('away', [])
                        if hl or al:
                            jm['injuries'] = {'home': hl, 'away': al, 'home_count': len(hl), 'away_count': len(al)}
                        ref = bz.get('referee') or {}
                        if ref.get('name') and ref['name'] != '待定':
                            jm['referee'] = ref
                        wc = bz.get('weather_code')
                        if wc is not None:
                            jm['weather'] = {'code': wc, 'desc': str(wc), 'temp': bz.get('temperature_c'), 'wind': bz.get('wind_speed')}
                        eid = str(bz.get('id', ''))
                        if eid in bz_pred_map:
                            p = bz_pred_map[eid]
                            bz_predictions[jm['match_id']] = {
                                'prob_home_win': p.get('prob_home_win'), 'prob_draw': p.get('prob_draw'),
                                'prob_away_win': p.get('prob_away_win'),
                                'expected_home_goals': p.get('expected_home_goals', 0) or 0,
                                'expected_away_goals': p.get('expected_away_goals', 0) or 0,
                                'expected_goals': (p.get('expected_home_goals', 0) or 0) + (p.get('expected_away_goals', 0) or 0),
                                'confidence': p.get('confidence'), 'predicted_result': p.get('predicted_result'),
                            }
                        break
            source = '竞彩官方 + Bzzoiro' if bz_predictions else '竞彩官方 (Bzzoiro未匹配)'
        else:
            source = '竞彩官方'

        analyzed = analyze_matches(matches, None, bz_predictions)
        recommendations = generate_parlay_recommendations(analyzed)
        total_goals_recs = generate_total_goals_recommendations(analyzed)
        try: save_predictions(analyzed)
        except Exception: pass
        return jsonify({
            'matches': analyzed, 'recommendations': recommendations,
            'total_goals_recs': total_goals_recs, 'history_stats': get_stats(),
            'stats': {'total_matches': len(analyzed),
                       'update_time': fetch_ts, 'source': source}  # jingcai path
        })

    # 路径2：Bzzoiro 原始数据 + 竞彩场单匹配
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
