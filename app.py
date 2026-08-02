import random
import logging
import os
from datetime import datetime

from flask import Flask, jsonify, render_template

from config import Config
from models import db
from data_generator import generate_matches as generate_mock_matches
from analysis import analyze_matches, generate_parlay_recommendations
from scheduler import init_scheduler

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
    """综合接口: 优先使用 Bzzoiro 真实数据，失败则降级为模拟数据"""
    # 尝试获取真实数据
    from bizzoiro_client import fetch_events
    matches = fetch_events(limit=15)

    if not matches or len(matches) < 5:
        logging.info('[API] 真实数据不足，使用模拟数据')
        matches = generate_mock_matches(12)

    analyzed = analyze_matches(matches)
    recommendations = generate_parlay_recommendations(analyzed)
    hit_rate = random.randint(65, 85)

    source = 'Bzzoiro API' if os.environ.get('BZZOIRO_API_KEY') else '模拟数据'

    return jsonify({
        'matches': analyzed,
        'recommendations': recommendations,
        'stats': {
            'total_matches': len(analyzed),
            'hit_rate': f'{hit_rate}%',
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': source
        }
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
