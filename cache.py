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
    说明：PythonAnywhere 服务器端直连 sporttery.cn 会 403（白名单限制），
    因此后端缓存仅走 Bzzoiro 数据源；竞彩官方数据由浏览器前端直连并提供。
    """
    matches = []
    source = ''
    data_priority = ''
    data_note = ''
    standings = {}
    predictions = {}

    # Bzzoiro 兜底（后端可直接访问；短超时避免缓存重建卡顿）
    data_priority = 'secondary'
    data_note = '数据源：Bzzoiro 第三方数据'
    try:
        from bizzoiro_client import fetch_events, fetch_standings_for_matches, fetch_predictions
        matches = fetch_events(limit=15)
        if matches and len(matches) >= 3:
            source = 'Bzzoiro API'
            data_priority = 'secondary'
            data_note = '数据源：Bzzoiro 第三方数据'
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
    # 锁释放重建（避免长时间占锁）；带超时保护，防止外部 API 慢导致请求卡死
    logger.info('[cache] 重建数据（冷启动/过期）')
    payload = _build_payload_with_timeout()
    with _lock:
        _cache['data'] = {'ts': now(), 'payload': payload}
    return payload


def _build_payload_with_timeout(timeout=15):
    """在独立线程构建 payload，超时则返回降级 payload（避免阻塞请求）。"""
    result = {'payload': None, 'done': False}

    def target():
        try:
            result['payload'] = _build_payload()
        except Exception as e:
            logger.warning('[cache] 构建失败: %s', e)
        finally:
            result['done'] = True

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if result['done'] and result['payload'] is not None:
        return result['payload']
    # 超时降级
    logger.warning('[cache] 构建超时，返回降级数据')
    try:
        from data_generator import generate_matches as generate_mock_matches
        matches = generate_mock_matches(12)
    except Exception:
        matches = []
    from analysis import analyze_matches, generate_parlay_recommendations, generate_total_goals_recommendations
    analyzed = analyze_matches(matches, {}, {})
    return {
        'matches': analyzed,
        'recommendations': generate_parlay_recommendations(analyzed),
        'total_goals_recs': generate_total_goals_recommendations(analyzed),
        'history_stats': {},
        'stats': {
            'total_matches': len(analyzed),
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': '模拟数据 (数据源超时)',
            'data_priority': 'fallback',
            'data_note': '数据源超时降级',
        }
    }


def warmup():
    """预拉取（供定时任务/启动时调用）。"""
    try:
        get_data(force=True)
        logger.info('[cache] 预拉取完成')
    except Exception as e:
        logger.warning('[cache] 预拉取失败: %s', e)
