#!/usr/bin/env python
"""离线定时任务入口（给 PythonAnywhere Scheduled Task 用）。

背景：`scheduler.py` 用的是进程内 APScheduler，PA 免费版会在空闲时卸载 web worker，
被卸载期间定时任务不会执行——线上"每日结算/资讯刷新"因此长期空转。这个脚本把同一批
任务改成"由外部 cron 触发的一次性进程"，不依赖 web worker 存活。

用法（PA Bash Console 或 Scheduled Task 命令）：
    cd ~/jingcai-analyzer && python tools/daily_job.py --task pipeline
    cd ~/jingcai-analyzer && python tools/daily_job.py --task news

建议 PA Scheduled Task（Tasks 页面）：
    pipeline  每天 03:10（周一 07:10 也要跑一次，清周末）
    news      每 2 小时

退出码：0 成功；1 有异常（PA 任务页会显示为失败）。
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [job] %(message)s',
)
logger = logging.getLogger('daily_job')


def _app():
    """导入 app 并返回 Flask 实例（导入本身会跑 db.create_all 等初始化）。"""
    from app import app
    return app


def task_pipeline():
    from data_pipeline import run_full
    app = _app()
    with app.app_context():
        res = run_full()
    try:
        from backtest import clear_summary_cache
        clear_summary_cache()
    except Exception:
        pass
    return res


def task_news():
    from news_ingest import refresh
    return {'news': refresh()}


def task_settle():
    from data_pipeline import settle_finished
    app = _app()
    with app.app_context():
        return settle_finished()


TASKS = {
    'pipeline': task_pipeline,
    'news': task_news,
    'settle': task_settle,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--task', default='pipeline', choices=sorted(TASKS))
    ap.add_argument('--json', action='store_true', help='只输出 JSON 结果')
    args = ap.parse_args()

    started = datetime.now().isoformat(timespec='seconds')
    try:
        res = TASKS[args.task]()
        out = {'ok': True, 'task': args.task, 'started': started, 'result': res}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, default=str))
        else:
            logger.info('任务 %s 完成: %s', args.task, res)
        return 0
    except Exception as e:
        logger.exception('任务 %s 失败: %s', args.task, e)
        if args.json:
            print(json.dumps({'ok': False, 'task': args.task,
                              'started': started, 'error': str(e)},
                             ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
