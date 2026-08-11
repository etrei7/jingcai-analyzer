"""
竞彩场次刮削器：从 500.com 获取每日竞彩官方场单
配合 Bzzoiro API 进行数据匹配
"""
import re, logging, requests
from datetime import datetime, timedelta, timezone
from collections import OrderedDict

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

LEAGUE_REVERSE_MAP = {
    '英超': 'Premier League', '西甲': 'La Liga', '德甲': 'Bundesliga', '意甲': 'Serie A',
    '法甲': 'Ligue 1', '中超': 'Chinese Super League', '日职': 'J1 League',
    '韩K': 'K League 1', '韩K联': 'K League 1', '澳超': 'A-League',
    '荷甲': 'Eredivisie', '葡超': 'Primeira Liga', '巴甲': 'Brasileirão Série A',
    '阿甲': 'Liga Profesional de Fútbol', 'MLS': 'Major League Soccer',
    '墨超': 'Liga MX Apertura', '土超': 'Super Lig', '比甲': 'Pro League',
    '苏超': 'Scottish Premiership', '瑞典超': 'Allsvenskan', '丹超': 'Danish Superliga',
    '挪超': 'Eliteserien',
    '英冠': 'Championship', '德乙': '2. Bundesliga', '意乙': 'Serie B',
    '西乙': 'La Liga 2', '法乙': 'Ligue 2', '日乙': 'J2 League',
    '英联杯': 'Carabao Cup', '足总杯': 'FA Cup', '国王杯': 'Copa del Rey',
    '意杯': 'Coppa Italia', '德国杯': 'DFB Pokal', '巴西杯': 'Copa do Brasil',
    '欧冠': 'Champions League', '欧联': 'Europa League', '欧协联': 'Conference League',
    '亚冠': 'AFC Champions League', '解放者杯': 'Copa Libertadores',
    '欧超杯': 'UEFA Super Cup',
}


def fetch_jingcai_match_ids():
    """从 500.com 竞彩页面获取今日竞彩官方场单编号和所属联赛。
    返回: [(编号, 联赛中文名), ...] 例如 [('周二001', '欧冠'), ...]
    """
    try:
        r = requests.get('https://trade.500.com/jczq/', timeout=15,
                         headers={'User-Agent': 'Mozilla/5.0'})
        r.encoding = 'gbk'
        html = r.text

        # 按竞彩编号切割页面
        parts = re.split(r'(周[一二三四五六日]\d{3})', html)

        seen = {}
        matches = []
        for i in range(1, len(parts) - 1, 2):
            mid = parts[i]
            segment = parts[i + 1][:500]

            # 提取联赛名
            league_match = re.findall(r'>([\u4e00-\u9fff]{2,6})<', segment)
            league = next((l for l in league_match if l not in ('手机版', '登录', '注册', '欢迎您',
                           '红包', '余额', '隐藏', '搜索', '竞彩足球', '投注详情', '扫描二维码下载',
                           '立即投注', '选号', '清空', '胆码', '比分', '赛事类型', '全部', '过关方式',
                           '单关', '混合过关', '自由过关', '奖金优化', '比赛历史')), '未知')

            # 去重：同一编号只保留有联赛名的
            if mid in seen:
                if seen[mid] == '未知' and league != '未知':
                    seen[mid] = league
            else:
                seen[mid] = league

        matches = [(mid, lea) for mid, lea in seen.items() if lea != '未知']

        logger.info(f'[竞彩刮削] 500.com 获取 {len(matches)} 场')
        return matches
    except Exception as e:
        logger.warning(f'[竞彩刮削] 500.com 失败: {e}')
        return []


def filter_by_jingcai(bizzoiro_matches, jingcai_list):
    """将 Bzzoiro 场次匹配到竞彩官单。
    匹配策略：同日期 + 同联赛 + 时间排序去对应。
    未匹配到的场次被排除。
    """
    if not jingcai_list:
        return bizzoiro_matches  # 刮取失败时不过滤

    # 获取今天日期（CST）
    today = datetime.now(CST).strftime('%Y-%m-%d')

    # 按联赛分组 Bzzoiro 数据
    bz_by_league = OrderedDict()
    for m in bizzoiro_matches:
        league_cn = m.get('league', '')
        bz_by_league.setdefault(league_cn, []).append(m)

    # 按联赛分组竞彩编号
    jc_by_league = OrderedDict()
    for jid, jleague in jingcai_list:
        jc_by_league.setdefault(jleague, []).append(jid)

    # 匹配
    matched_matches = []
    for jleague, jids in jc_by_league.items():
        bz_list = bz_by_league.get(jleague, [])
        # 按时间排序
        bz_list.sort(key=lambda m: m.get('match_time', '99:99'))
        for idx, jid in enumerate(jids):
            if idx < len(bz_list):
                m = bz_list[idx]
                m['match_id'] = jid   # 覆盖为竞彩编号
                matched_matches.append(m)
            # 多余竞彩编号（无对应Bzzoiro数据）跳过

    logger.info(f'[竞彩匹配] 竞彩{len(jingcai_list)}场 → Bzzoiro匹配{len(matched_matches)}场')

    if matched_matches:
        return matched_matches
    return bizzoiro_matches
