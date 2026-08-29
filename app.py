import logging
import os
import secrets
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import Config
from models import db
from data_generator import generate_matches as generate_mock_matches
from analysis import analyze_matches, generate_parlay_recommendations, generate_total_goals_recommendations
from scheduler import init_scheduler
from history import save_predictions, get_stats, add_bet_record, get_history_records
from bizzoiro_client import _parse_event_to_match, _assign_match_ids, LEAGUE_NAME_MAP
import backtest_models  # noqa: F401  确保回测表 bt_* 随 db.create_all() 创建

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config.from_object(Config)

# 安全：生产环境（非 DEBUG）禁止使用默认 SECRET_KEY，改用随机生成的会话密钥，
# 避免硬编码密钥泄露导致 session 伪造风险。
if not os.environ.get('FLASK_DEBUG', '0') == '1':
    if app.config.get('SECRET_KEY') == 'jingcai-dev-secret-2024':
        app.config['SECRET_KEY'] = secrets.token_hex(32)
        logging.warning('[安全] 使用默认 SECRET_KEY，已自动替换为随机密钥（重启后会话将失效；建议设置环境变量 SECRET_KEY）')

db.init_app(app)


def _migrate_bt_bets():
    """轻量迁移：为已有数据库的 bt_bets 补充 estimated 列（区分真实/估算赔率），
    避免表结构变更后查询报 "no such column"。并对历史估算赔率玩法（HTFT/CS）回填标记。"""
    try:
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        cols = [c['name'] for c in insp.get_columns('bt_bets')]
        need_backfill = False
        if 'estimated' not in cols:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE bt_bets ADD COLUMN estimated BOOLEAN DEFAULT 0"))
                conn.commit()
            need_backfill = True
            logging.info('[迁移] bt_bets 已补充 estimated 列')
        # 回填历史估算玩法（半全场 HTFT / 比分 CS）为 estimated=1，
        # 修复旧记录被误计入真实赔率 ROI 的问题
        with db.engine.connect() as conn:
            res = conn.execute(text(
                "UPDATE bt_bets SET estimated = 1 WHERE play_type IN ('HTFT','CS') AND (estimated IS NULL OR estimated = 0)"
            ))
            if res.rowcount:
                conn.commit()
                logging.info('[迁移] 回填 %d 条估算赔率记录为 estimated=1', res.rowcount)
    except Exception as e:
        logging.warning('[迁移] bt_bets estimated 列检查/回填失败: %s', e)


with app.app_context():
    db.create_all()
    # 轻量迁移：为已有数据库的 bt_bets 补充 estimated 列（区分真实/估算赔率），
    # 避免表结构变更后查询报 "no such column"。
    _migrate_bt_bets()

init_scheduler(app)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/history')
def history_page():
    return render_template('history.html')


@app.route('/settings')
def settings_page():
    return render_template('settings.html')


@app.route('/api/data')
def get_data():
    # 优先读内存缓存（定时任务预拉取），解决冷启动/多次请求性能问题
    try:
        from cache import get_data as get_cached_data
        force = request.args.get('force', '').lower() == '1'
        payload = get_cached_data(force=force)
        if payload and payload.get('stats', {}).get('source') != '模拟数据 (数据源超时)':
            return jsonify(payload)
        logging.warning('[API] 缓存失效或为模拟数据，重新构建')
    except Exception as e:
        logging.warning('[API] 缓存读取失败，降级直连: %s', e)
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

    # 联赛排名增强：用 thesportsdb(备选 Bzzoiro) 填充每场 home_rank/away_rank
    try:
        from rankings import enhance_matches
        enhance_matches(analyzed)
    except Exception as e:
        logging.warning('[API] 排名增强失败: %s', e)

    recommendations = generate_parlay_recommendations(analyzed)
    total_goals_recs = generate_total_goals_recommendations(analyzed)

    # 仅真实数据源（竞彩官方 / Bzzoiro）写入历史，模拟数据不污染战绩
    if source in ('竞彩官方', 'Bzzoiro API'):
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


@app.route('/api/cache-refresh')
def cache_refresh():
    """前端手动刷新时强制重建缓存（?1 触发）。"""
    try:
        from cache import get_data as get_cached_data
        payload = get_cached_data(force=True)
        # 如果返回模拟数据，标记让前端知道需要重新拉取
        if payload and payload.get('stats', {}).get('source', '').startswith('模拟'):
            return jsonify({'success': True, 'cached': True, 'update_time': payload['stats']['update_time'], 'is_mock': True})
        return jsonify({'success': True, 'cached': True, 'update_time': payload['stats']['update_time']})
    except Exception as e:
        logging.warning('[API] cache-refresh: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/jingcai-token')
def get_jingcai_token():
    from jingcai_scraper import refresh_jingcai_token
    token = refresh_jingcai_token()
    if token:
        return jsonify({'token': token, 'success': True})
    return jsonify({'token': None, 'success': False})


@app.route('/api/jingcai')
def get_jingcai():
    """后端代理竞彩官方场单+赔率：同源返回，规避浏览器 CORS；
    服务器端若能访问 sporttery 则优先，否则返回空（前端降级到缓存源）。"""
    try:
        from jingcai_scraper import fetch_jingcai_matches
        matches = fetch_jingcai_matches()
        return jsonify({'matches': matches or [], 'count': len(matches or []), 'odds_time': ''})
    except Exception as e:
        logging.warning('[API] /api/jingcai 失败: %s', e)
        return jsonify({'matches': [], 'count': 0, 'error': str(e)})


@app.route('/api/realtime')
def get_realtime():
    """实时数据接口：支持 source 切换数据源。返回 matches + analyses(按matchNum索引)。"""
    source = request.args.get('source', 'sporttery')
    matches = []
    source_name = ''

    def _analyzed_payload(matches, source):
        analyzed = analyze_matches(matches, None, {})
        try:
            from rankings import enhance_matches
            enhance_matches(analyzed)
        except Exception:
            pass
        recommendations = generate_parlay_recommendations(analyzed)
        total_goals_recs = generate_total_goals_recommendations(analyzed)
        analyses = {}
        for m in analyzed:
            analyses[m.get('match_id', '')] = {
                'recommendation': {
                    'direction': m.get('predicted_option', ''),
                    'confidence': m.get('confidence_level', ''),
                    'confidenceScore': m.get('home_confidence', m.get('away_confidence', 0)),
                    'detail': m.get('ai_preview', ''),
                },
                'over25_prob': m.get('over25_prob', 0),
                'expected_goals': m.get('expected_goals', 0),
                'market_tendency': m.get('market_tendency', ''),
            }
        try:
            save_predictions(analyzed)
        except Exception:
            pass
        return analyses, recommendations, total_goals_recs

    try:
        if source in ('sporttery', 'jingcai', ''):
            from jingcai_scraper import fetch_jingcai_matches
            matches = fetch_jingcai_matches()
            if matches and len(matches) >= 3:
                source_name = '竞彩官方'
        elif source == 'bzzoiro':
            from bizzoiro_client import fetch_events
            matches = fetch_events(limit=15)
            if matches and len(matches) >= 3:
                source_name = 'Bzzoiro API'
        else:
            from bizzoiro_client import fetch_events
            matches = fetch_events(limit=15)
            if matches and len(matches) >= 3:
                source_name = 'Bzzoiro API'
    except Exception as e:
        logging.warning(f'[realtime] 数据源 {source} 失败: {e}')
        matches = []

    if not matches or len(matches) < 3:
        matches = generate_mock_matches(12)
        source_name = '模拟数据'

    analyses, recommendations, total_goals_recs = _analyzed_payload(matches, source)
    return jsonify({
        'success': True,
        'source': source,
        'source_name': source_name,
        'matches': matches,
        'analyses': analyses,
        'recommendations': recommendations,
        'total_goals_recs': total_goals_recs,
        'history_stats': get_stats(),
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@app.route('/api/team-data')
def get_team_data():
    """球队扩展数据接口：根据对阵球队+联赛返回排名等扩展信息。
    排名来源：thesportsdb 优先（6小时缓存），Bzzoiro standings 备选。按 (home,away,league) 缓存。"""
    home = request.args.get('homeTeam', '')
    away = request.args.get('awayTeam', '')
    league = request.args.get('league', '')

    home_rank = away_rank = None
    home_src = away_src = ''
    try:
        from rankings import get_team_rank
        if league:
            home_rank, home_src = get_team_rank(league, home)
            away_rank, away_src = get_team_rank(league, away)
            if home_rank is None and home:
                # 归一化兜底：队名可能是英文或含连字符
                pass
    except Exception as e:
        logging.warning('[team-data] 排名获取失败: %s', e)

    # 排名详情（含节选数据：积分、近况、净胜球等）
    home_detail = away_detail = None
    try:
        from rankings import get_league_rank_map
        rank_map, _src = get_league_rank_map(league)
    except Exception:
        rank_map = {}

    def _team_detail(name):
        if not rank_map:
            return None
        from rankings import _match_rank, _norm
        # 简化：仅当能匹配到排名时返回基础详情
        r = _match_rank(name, rank_map)
        return {'rank': r} if r is not None else None

    home_detail = _team_detail(home) if home else None
    away_detail = _team_detail(away) if away else None

    return jsonify({
        'success': True,
        'data': {
            'homeTeam': home,
            'awayTeam': away,
            'league': league,
            'homeRank': home_rank,
            'awayRank': away_rank,
            'homeRankSource': home_src,
            'awayRankSource': away_src,
            'teamRanks': {'home': home_rank, 'away': away_rank},
            'recentForm': {'home': [], 'away': []},
            'headToHead': [],
            'keyPlayers': {'home': [], 'away': []},
            'note': '排名来源：thesportsdb（免费，仅前5名）/ Bzzoiro 备选',
        }
    })


@app.route('/api/history')
def get_history():
    """历史战绩读取接口：返回统计 + 完整记录列表。"""
    return jsonify({'success': True, 'data': {'stats': get_stats(), 'records': get_history_records()}})


@app.route('/api/save-history', methods=['POST'])
def save_history():
    """保存用户投注记录（Coze 兼容：matchNum/playType/direction/odds/teams/recommendation）。"""
    payload = request.get_json(silent=True) or {}
    records = payload.get('records', [])
    added = 0
    for r in records:
        try:
            if add_bet_record(r):
                added += 1
        except Exception as e:
            logging.warning(f'[save-history] {e}')
    return jsonify({'success': True, 'saved': len(records), 'added': added, 'history_stats': get_stats()})


@app.route('/api/analyze', methods=['POST'])
def analyze_data():
    data = request.get_json(silent=True) or {}

    # 路径1：竞彩官方数据直接分析
    jc_matches = data.get('jingcai_matches', [])
    if jc_matches:
        client_time = data.get('client_time', '')
        odds_time = data.get('odds_time', '')
        fetch_ts = datetime.now().strftime('%H:%M:%S')
        if odds_time:
            fetch_ts = f'{odds_time[:5]}'
        elif client_time:
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

        # 提取官方让球数据 hhad（若前端已传），仅附加不修改其他字段
        for m in matches:
            line = m.get('hhad_goal_line')
            w = m.get('hhad_win')
            d = m.get('hhad_draw')
            l = m.get('hhad_lose')
            if w or d or l:
                m['official_hcp_line'] = line
                m['official_hcp_win'] = w
                m['official_hcp_draw'] = d
                m['official_hcp_lose'] = l
            m.pop('hhad_goal_line', None); m.pop('hhad_win', None)
            m.pop('hhad_draw', None); m.pop('hhad_lose', None)

        # Bzzoiro 富化：伤病/裁判/天气/球队状态（不改编号赔率）
        try:
            from bizzoiro_client import enrich_jingcai_matches
            matches, _ = enrich_jingcai_matches(matches)
        except Exception:
            pass

        source = '竞彩官方'
        analyzed = analyze_matches(matches, None, {})
        recommendations = generate_parlay_recommendations(analyzed)
        total_goals_recs = generate_total_goals_recommendations(analyzed)
        try: save_predictions(analyzed)
        except Exception: pass
        return jsonify({
            'matches': analyzed, 'recommendations': recommendations,
            'total_goals_recs': total_goals_recs, 'history_stats': get_stats(),
            'stats': {'total_matches': len(analyzed),
                       'update_time': fetch_ts, 'source': source,
                       'data_priority': 'primary',
                       'data_note': '主数据源：中国体育彩票官方赔率'}  # jingcai path
        })

    # 路径2：Bzzoiro 原始数据 + 竞彩场单匹配
    raw_events = data.get('events', [])
    raw_predictions = data.get('predictions', [])
    jc_list = data.get('jingcai_list', [])

    if not raw_events:
        return jsonify({'error': '未提供任何赛事数据，请从赛事看板加载后再分析', 'success': False}), 400

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

    # 仅对真实数据源（Bzzoiro）写入历史，模拟数据不污染战绩
    if 'Bzzoiro' in source:
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


@app.route('/robots.txt')
def robots():
    return """
User-agent: *
Disallow: /api/
Disallow: /robots.txt
Allow: /

# 本站内容仅供娱乐参考，非投注平台，不提供实质购彩服务。
""".strip()


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


@app.route('/api/db-check')
def db_check():
    """临时诊断：输出 Web 进程实际连接的数据库 URI 与回测表行数。"""
    info = {'uri': app.config.get('SQLALCHEMY_DATABASE_URI')}
    try:
        from backtest import compute_summary
        s = compute_summary()
        info['pending'] = s.get('pending')
        info['total_bets'] = s.get('total_bets')
    except Exception as e:
        info['error'] = str(e)
    return jsonify(info)


@app.route('/api/backtest')
def backtest_stats():
    """回测战绩汇总：命中率 + ROI + 累计盈亏 + 样本量。供战绩面板读取。"""
    try:
        from backtest import compute_summary
        play_type = request.args.get('play_type', 'all')
        period = request.args.get('period', 'all')
        s = compute_summary(period=period, play_type=play_type)
        return jsonify(s)
    except Exception as e:
        logging.warning('[API] backtest: %s', e)
        return jsonify({'total_bets': 0, 'total_pnl': 0, 'roi': 0, 'hit_rate': 0, 'pending': 0})


@app.route('/api/backtest/run', methods=['POST'])
def backtest_run():
    """手动触发一次流水线：拉取快照 + 结算已完赛。"""
    try:
        from data_pipeline import run_full
        res = run_full()
        return jsonify({'success': True, **res})
    except Exception as e:
        logging.warning('[API] backtest/run: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    # 生产环境务必通过环境变量设置 SECRET_KEY 与关闭 debug
    if app.config.get('SECRET_KEY') == 'jingcai-dev-secret-2024':
        logging.warning('[安全] 正在使用默认 SECRET_KEY，生产环境请设置环境变量 SECRET_KEY')
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
