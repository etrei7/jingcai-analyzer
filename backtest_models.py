"""回测闭环数据模型（独立于原有 matches/recommendations/team_stats 表）。
基于 SQLite，PythonAnywhere 免费版可用。用于：
- 记录赛事快照与赔率快照（带时间戳，可回测）
- 记录 AI 推荐与投注，赛后回填结果
- 计算命中率、ROI、累计盈亏，供战绩面板展示
不修改、不删除原有数据表。
"""
from datetime import datetime
from models import db


class BtLeague(db.Model):
    """联赛字典（回测用）"""
    __tablename__ = 'bt_leagues'

    id = db.Column(db.Integer, primary_key=True)
    league_id = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    country = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtMatch(db.Model):
    """比赛主表：开赛前锁定的快照，赛后回填结果"""
    __tablename__ = 'bt_matches'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), unique=True, nullable=False)
    league_id = db.Column(db.String(30), nullable=True)
    league = db.Column(db.String(80), nullable=True)
    home_team = db.Column(db.String(120), nullable=False)
    away_team = db.Column(db.String(120), nullable=False)
    commence_utc = db.Column(db.String(25), nullable=True)
    commence_cst = db.Column(db.String(25), nullable=True)
    match_time = db.Column(db.String(5), nullable=True)
    status = db.Column(db.String(10), nullable=True)
    result = db.Column(db.String(2), nullable=True)
    total_goals = db.Column(db.Integer, nullable=True)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    fetched_at = db.Column(db.String(25), nullable=True)
    updated_at = db.Column(db.String(25), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtOddsSnapshot(db.Model):
    """赔率快照：核心，必须带时间戳，否则无法回测"""
    __tablename__ = 'bt_odds_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    market = db.Column(db.String(10), nullable=False)
    snapshot_time = db.Column(db.String(25), nullable=True)
    home_odds = db.Column(db.Float, nullable=True)
    draw_odds = db.Column(db.Float, nullable=True)
    away_odds = db.Column(db.Float, nullable=True)
    line = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtPrediction(db.Model):
    """AI 推荐（回测单位）"""
    __tablename__ = 'bt_predictions'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    play_type = db.Column(db.String(10), nullable=False)
    pick = db.Column(db.String(10), nullable=False)
    model_name = db.Column(db.String(50), nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    predicted_prob = db.Column(db.Float, nullable=True)
    odds_at_prediction = db.Column(db.Float, nullable=True)
    combo = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtBet(db.Model):
    """投注记录（回测基本单位）：赛前推荐 → 赛后结算"""
    __tablename__ = 'bt_bets'

    id = db.Column(db.Integer, primary_key=True)
    prediction_id = db.Column(db.Integer, nullable=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    home_team = db.Column(db.String(120), nullable=True)
    away_team = db.Column(db.String(120), nullable=True)
    play_type = db.Column(db.String(10), nullable=True)
    pick = db.Column(db.String(10), nullable=True)
    odds = db.Column(db.Float, nullable=True)
    stake = db.Column(db.Float, default=1.0)
    outcome = db.Column(db.String(10), nullable=True)
    pnl = db.Column(db.Float, nullable=True)
    settled_at = db.Column(db.String(25), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtBacktestSummary(db.Model):
    """战绩汇总（定时物化，面板直接读）"""
    __tablename__ = 'bt_backtest_summary'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(20), nullable=True)
    model_name = db.Column(db.String(50), nullable=True)
    play_type = db.Column(db.String(10), nullable=True)
    total_bets = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    voids = db.Column(db.Integer, default=0)
    hit_rate = db.Column(db.Float, default=0)
    total_stake = db.Column(db.Float, default=0)
    total_pnl = db.Column(db.Float, default=0)
    roi = db.Column(db.Float, default=0)
    avg_odds = db.Column(db.Float, default=0)
    computed_at = db.Column(db.String(25), nullable=True)
