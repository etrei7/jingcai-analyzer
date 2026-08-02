import logging
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def daily_settlement():
    """每日结算任务——更新比赛结果并统计推荐命中率（当前为模拟占位）"""
    logger.info('[定时任务] 开始执行每日结算...')
    logger.info('[定时任务] 检查并更新比赛结果 (result, result_score 字段)...')
    logger.info('[定时任务] 统计 AI 推荐命中率...')
    logger.info('[定时任务] 每日结算完成（当前使用模拟数据，后续接入真实数据后生效）')


def init_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        daily_settlement,
        'cron',
        hour=2,
        minute=0,
        id='daily_settlement'
    )
    scheduler.start()
    app.extensions['scheduler'] = scheduler
    logger.info('[定时任务] APScheduler 已启动，每日凌晨 2:00 执行结算')
