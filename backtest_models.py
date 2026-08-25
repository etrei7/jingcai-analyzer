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
    league_id = db.Column(db.String(30), unique=True, nullable=False, comment='第三方联赛ID')
    name = db.Column(db.String(80), nullable=False, comment='联赛名')
    country = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtMatch(db.Model):
    """比赛主表：开赛前锁定的快照，赛后回填结果"""
    __tablename__ = 'bt_matches'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), unique=True, nullable=False, comment='第三方赛事ID/竞彩编号')
    league_id = db.Column(db.String(30), nullable=True, comment='第三方联赛ID')
    league = db.Column(db.String(80), nullable=True, comment='联赛名')
    home_team = db.Column(db.String(120), nullable=False, comment='主队')
    away_team = db.Column(db.String(120), nullable=False, comment='客队')
    commence_utc = db.Column(db.String(25), nullable=True, comment='开赛UTC')
    commence_cst = db.Column(db.String(25), nullable=True, comment='北京时间')
    match_time = db.Column(db.String(5), nullable=True, comment='开赛HH:MM')
    status = db.Column(db.String(10), nullable=True, comment='upcoming/live/finished')
    result = db.Column(db.String(2), nullable=True, comment='H/D/A')
    total_goals = db.Column(db.Integer, nullable=True, comment='总进球数')
    home_score = db.Column(db.Integer, nullable=True, comment='主队比分')
    away_score = db.Column(db.Integer, nullable=True, comment='客队比分')
    fetched_at = db.Column(db.String(25), nullable=True, comment='抓取时间')
    updated_at = db.Column(db.String(25), nullable=True, comment='更新时间')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtOddsSnapshot(db.Model):
    """赔率快照：核心，必须带时间戳，否则无法回测"""
    __tablename__ = 'bt_odds_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    market = db.Column(db.String(10), nullable=False, comment='1X2/AH/OU')
    snapshot_time = db.Column(db.String(25), nullable=True, comment='快照时间')
    home_odds = db.Column(db.Float, nullable=True)
    draw_odds = db.Column(db.Float, nullable=True)
    away_odds = db.Column(db.Float, nullable=True)
    line = db.Column(db.Float, nullable=True, comment='让球/大小球盘口')
    source = db.Column(db.String(30), nullable=True, comment='数据源')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtPrediction(db.Model):
    """AI 推荐（回测单位）"""
    __tablename__ = 'bt_predictions'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    play_type = db.Column(db.String(10), nullable=False, comment='single/moneyline/ou/ah/parlay')
    pick = db.Column(db.String(10), nullable=False, comment='推荐选项:H/D/A 或 大小球 或 让球')
    model_name = db.Column(db.String(50), nullable=False, comment='模型名')
    confidence = db.Column(db.Float, nullable=True, comment='0~1')
    predicted_prob = db.Column(db.Float, nullable=True, comment='模型估计概率 0~1')
    odds_at_prediction = db.Column(db.Float, nullable=True, comment='推荐时赔率')
    combo = db.Column(db.String(10), nullable=True, comment='single/parlay')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtBet(db.Model):
    """投注记录（回测基本单位）：赛前推荐 → 赛后结算"""
    __tablename__ = 'bt_bets'

    id = db.Column(db.Integer, primary_key=True)
    prediction_id = db.Column(db.Integer, nullable=True)
    match_id = db.Column(db.String(50), nullable=False, index=True)
    home_team = db.Column(db.String(120), nullable=True, comment='主队(结算匹配用)')
    away_team = db.Column(db.String(120), nullable=True, comment='客队(结算匹配用)')
    play_type = db.Column(db.String(10), nullable=True)
    pick = db.Column(db.String(10), nullable=True)
    odds = db.Column(db.Float, nullable=True, comment='结算赔率')
    stake = db.Column(db.Float, default=1.0, comment='注额(默认1元)')
    outcome = db.Column(db.String(10), nullable=True, comment='win/lose/void')
    pnl = db.Column(db.Float, nullable=True, comment='盈亏')
    settled_at = db.Column(db.String(25), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BtBacktestSummary(db.Model):
    """战绩汇总（定时物化，面板直接读）"""
    __tablename__ = 'bt_backtest_summary'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(20), nullable=True, comment='时间范围')
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
