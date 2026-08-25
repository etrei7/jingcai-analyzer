"""内存缓存模块：缓存赛事分析结果，避免每次请求都重新拉取外部 API。
解决 PythonAnywhere 免费版访问慢、冷启动卡顿的问题。
竞彩官方数据优先级最高，Bzzoiro 兜底。数据逻辑（分析、渲染、保存）不变，仅做结果缓存。
"""
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 全局缓存
_cache = {}
_lock = threading.Lock()
# 默认缓存有效期（秒）；竞彩官方赔率更新快，用较短 TTL
_CACHE_TTL = 45


def now():
    return time.time()


def _build_payload():
    """从主/备数据源构建完整的分析 payload（与 app.get_data 逻辑一致，但集中在此）。
    使用竞彩官方优先，Bzzoiro 兜底，模拟数据最终兜底。
    """
    matches = []
    source = ''
    data_priority = ''
    data_note = ''
    standings = {}
    predictions = {}

    # 竞彩官方为主（后端直连）
    try:
        from jingcai_scraper import fetch_jingcai_matches
        matches = fetch_jingcai_matches()
        if matches and len(matches) >= 3:
            source = '竞彩官方'
            data_priority = 'primary'
            data_note = '主数据源：中国体育彩票官方赔率'
    except Exception as e:
        logger.warning('[cache] 竞彩官方拉取失败: %s', e)
        matches = []

    # Bzzoiro 兜底
    if not matches or len(matches) < 3:
        data_priority = 'secondary'
        data_note = '备用源：Bzzoiro 第三方数据（竞彩官方API不可用）'
        try:
            from bizzoiro_client import fetch_events, fetch_standings_for_matches, fetch_predictions
            matches = fetch_events(limit=15)
            if matches and len(matches) >= 3:
                source = 'Bzzoiro API'
                try:
                    standings = fetch_standings_for_matches(matches)
                    predictions = fetch_predictions()
                except Exception:
                    pass
        except Exception as e:
            logger.warning('[cache] Bzzoiro 拉取失败: %s', e)
            matches = []

    # 模拟数据最终兜底
    if not matches or len(matches) < 3:
        from data_generator import generate_matches as generate_mock_matches
        matches = generate_mock_matches(12)
        source = '模拟数据'
        data_priority = 'fallback'
        data_note = '降级源：模拟数据（所有外部API不可用）'

    from analysis import analyze_matches, generate_parlay_recommendations, generate_total_goals_recommendations
    analyzed = analyze_matches(matches, standings, predictions)
    recommendations = generate_parlay_recommendations(analyzed)
    total_goals_recs = generate_total_goals_recommendations(analyzed)

    try:
        from history import save_predictions, get_stats
        save_predictions(analyzed)
        history_stats = get_stats()
    except Exception as e:
        logger.warning('[cache] 保存预测/统计失败: %s', e)
        history_stats = {}

    return {
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
    }


def get_data(force=False, ttl=None):
    """读取缓存数据；缓存过期或 force 时重建。"""
    ttl = ttl if ttl is not None else _CACHE_TTL
    with _lock:
        cached = _cache.get('data')
        if cached and (now() - cached['ts']) < ttl and not force:
            logger.info('[cache] 命中缓存 age=%.1fs', now() - cached['ts'])
            return cached['payload']
    # 锁释放重建（避免长时间占锁）
    logger.info('[cache] 重建数据（冷启动/过期）')
    payload = _build_payload()
    with _lock:
        _cache['data'] = {'ts': now(), 'payload': payload}
    return payload


def warmup():
    """预拉取（供定时任务/启动时调用）。"""
    try:
        get_data(force=True)
        logger.info('[cache] 预拉取完成')
    except Exception as e:
        logger.warning('[cache] 预拉取失败: %s', e)
