import random
import logging
import os
from datetime import datetime

from flask import Flask, jsonify, render_template

from config import Config
from models import db
from data_generator import generate_matches as generate_mock_matches
from analysis import analyze_matches, generate_parlay_recommendations, generate_total_goals_recommendations
from scheduler import init_scheduler
from history import save_predictions, get_stats

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()

init_scheduler(app)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/data')
def get_data():
    matches = []
    standings = {}
    predictions = {}

    # 尝试获取真实数据
    from bizzoiro_client import (
        fetch_events, fetch_standings_for_matches, fetch_predictions,
        fetch_odds_movement_for_matches
    )
    matches = fetch_events(limit=15)

    if matches and len(matches) >= 5:
        logging.info('[API] 使用 Bzzoiro 真实数据')

        # 竞彩官单匹配：从 500.com 获取今日竞彩场次编号，过滤非竞彩场次
        try:
            from jingcai_scraper import fetch_jingcai_match_ids, filter_by_jingcai
            jc_list = fetch_jingcai_match_ids()
            if jc_list:
                matches = filter_by_jingcai(matches, jc_list)
                source = 'Bzzoiro + 竞彩官方场单'
            else:
                source = 'Bzzoiro API (竞彩官单刮取失败，显示所有可用场次)'
        except Exception:
            source = 'Bzzoiro API (竞彩官单刮取失败)'

        standings = fetch_standings_for_matches(matches)
        predictions = fetch_predictions()
        odds_movement = fetch_odds_movement_for_matches(matches)
    else:
        logging.info('[API] 真实数据不足，降级为模拟数据')
        matches = generate_mock_matches(12)
        source = '模拟数据 (Bzzoiro API 可用时切换为真实数据)'
        odds_movement = {}

    analyzed = analyze_matches(matches, standings, predictions)
    recommendations = generate_parlay_recommendations(analyzed)
    total_goals_recs = generate_total_goals_recommendations(analyzed)

    # 保存预测并获取战绩
    try:
        save_predictions(analyzed)
    except Exception:
        pass
    history_stats = get_stats()

    # 附加赔率变动数据
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
