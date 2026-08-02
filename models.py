from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Match(db.Model):
    """竞彩足球赛事表"""
    __tablename__ = 'matches'

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(10), unique=True, nullable=False, comment='赛事编号')
    league = db.Column(db.String(50), nullable=False, comment='联赛名称')
    match_time = db.Column(db.String(5), nullable=False, comment='比赛时间(HH:MM)')
    home_team = db.Column(db.String(100), nullable=False, comment='主队')
    away_team = db.Column(db.String(100), nullable=False, comment='客队')
    win_odds = db.Column(db.Float, nullable=False, comment='胜赔率')
    draw_odds = db.Column(db.Float, nullable=False, comment='平赔率')
    lose_odds = db.Column(db.Float, nullable=False, comment='负赔率')
    handicap = db.Column(db.String(10), nullable=False, comment='让球盘口')
    confidence_level = db.Column(db.String(10), nullable=False, comment='AI信心等级: 高/中/低')
    confidence_score = db.Column(db.Float, nullable=False, comment='信心分数')
    over_under_tendency = db.Column(db.String(20), nullable=False, comment='大小球倾向')
    expected_goals = db.Column(db.String(10), nullable=False, comment='预期进球区间')
    hotness_label = db.Column(db.String(20), nullable=False, comment='热度标签')
    bookmaker_intent = db.Column(db.String(20), nullable=False, comment='庄家意图')
    recommended_score = db.Column(db.String(10), nullable=False, comment='推荐比分')
    result = db.Column(db.String(10), nullable=True, comment='赛后结果: 胜/平/负')
    result_score = db.Column(db.String(10), nullable=True, comment='赛后比分')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Recommendation(db.Model):
    """AI推荐方案表"""
    __tablename__ = 'recommendations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, comment='方案名称')
    plan_type = db.Column(db.String(50), nullable=False, comment='方案类型')
    combo_odds = db.Column(db.Float, nullable=False, comment='组合赔率')
    risk_level = db.Column(db.String(20), nullable=False, comment='风险等级')
    matches_json = db.Column(db.Text, nullable=False, comment='包含比赛的JSON')
    result = db.Column(db.String(20), nullable=True, comment='推荐结果: 命中/未命中')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TeamStat(db.Model):
    """球队统计数据表"""
    __tablename__ = 'team_stats'

    id = db.Column(db.Integer, primary_key=True)
    team_name = db.Column(db.String(100), nullable=False, comment='球队名称')
    league = db.Column(db.String(50), nullable=False, comment='所属联赛')
    avg_goals_scored = db.Column(db.Float, nullable=False, comment='场均进球')
    avg_goals_conceded = db.Column(db.Float, nullable=False, comment='场均失球')
    recent_form = db.Column(db.String(50), nullable=True, comment='近期战绩')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
