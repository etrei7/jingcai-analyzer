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
    predicted_prob = db.Column(db.Float, nullable=True)  # 模型概率（用于 Brier/log-loss 校准评估）
    stake = db.Column(db.Float, default=1.0)
    outcome = db.Column(db.String(10), nullable=True)
    pnl = db.Column(db.Float, nullable=True)
    settled_at = db.Column(db.String(25), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # 是否为估算赔率（非真实市场赔率）。1X2/AH 为真实，CS/HTFT 为模型估算。
    # 估算赔率不参与"可投注价值"的 ROI 统计，避免虚构高赔率撑高盈利。
    estimated = db.Column(db.Boolean, default=False)
    # 是否为竞彩官方开售场次。战绩面板只统计竞彩场次，过滤体彩不开的 Bzzoiro 场次。
    jingcai = db.Column(db.Boolean, default=False)
    # 推荐时的信心等级（高/中/低），用于按等级做历史命中率校准
    confidence_level = db.Column(db.String(10), nullable=True)
    # 联赛名（同类场次经验校准：按联赛分段的命中率）
    league = db.Column(db.String(50), nullable=True)
    # 记录时是否已有资讯 / 官方首发（用于「有资讯 vs 无资讯」A/B 回测对比）
    news_flag = db.Column(db.Boolean, default=False)
    lineup_flag = db.Column(db.Boolean, default=False)


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


class BtParlay(db.Model):
    """串关方案记录（回测单位）：单场命中率 ≠ 串关命中率（乘法关系），
    故独立建表追踪每套串关的结算结果与累计盈亏。"""
    __tablename__ = 'bt_parlays'

    id = db.Column(db.Integer, primary_key=True)
    plan_date = db.Column(db.String(10), index=True)      # 生成日期 YYYY-MM-DD
    name = db.Column(db.String(120), nullable=True)
    plan_type = db.Column(db.String(60), nullable=True)
    risk_level = db.Column(db.String(20), nullable=True)
    combo_odds = db.Column(db.Float, nullable=True)
    stake = db.Column(db.Float, default=1.0)              # 回测单位本金
    stake_pct = db.Column(db.Float, default=0)            # 建议投入比例(%)
    source = db.Column(db.String(30), nullable=True)
    legs_json = db.Column(db.Text, nullable=True)         # 各场明细 JSON
    signature = db.Column(db.String(200), index=True)     # 去重签名
    outcome = db.Column(db.String(10), nullable=True)     # win/lose
    pnl = db.Column(db.Float, nullable=True)
    earliest_time = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.String(25), nullable=True)
    settled_at = db.Column(db.String(25), nullable=True)
