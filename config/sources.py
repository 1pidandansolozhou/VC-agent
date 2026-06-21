import os

# ========== RSS 源（中外分流）==========
RSS_FEEDS_CN = {
    "36氪": "http://localhost:1200/36kr/news/latest",
    "创业邦": "http://localhost:1200/cyzone/news",
    "量子位": "http://localhost:1200/qbitai/category/资讯",
    # 注意：投资界 /pedaily/news 在此 RSSHub 版本不存在（404），暂时移除
    # 注意：机器之心 /jiqizhixin/information 在此 RSSHub 版本不存在（404），暂时移除
    # 微信公众号改用 werss_collector 直接从 SQLite 全量读取（见 collectors/werss_collector.py）
}

RSS_FEEDS_GLOBAL = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "EU-Startups": "https://www.eu-startups.com/feed/",
    "Tech.eu": "https://tech.eu/feed/",
    "Sifted": "https://sifted.eu/feed",
}

RSS_FEEDS = {**RSS_FEEDS_CN, **RSS_FEEDS_GLOBAL}

# ========== ★ v6 — 赛道感知关键词集（中英双语）==========
# 用于第一轮 Kimi 联网搜索和浏览器 Bing 搜索
TRACK_QUERIES_CN = {
    "通用":   ["本周 完成 天使轮 融资", "完成 Pre-A 轮 融资", "完成 种子轮 融资 AI", "完成 A轮 融资"],
    "AI2C":   ["消费AI 融资 天使", "AI陪伴 APP 融资", "内容生成 AI 初创 融资"],
    "AI2B":   ["企业AI 融资", "AI Agent 企业服务 融资", "大模型 B端 Pre-A"],
    "具身":   ["具身智能 融资", "机器人 天使轮", "灵巧手 融资", "VLA 初创 融资", "人形机器人 早期"],
    "前沿科技":["AI芯片 融资 天使", "脑机接口 初创 融资", "量子计算 种子轮", "光计算 融资"],
    "ai4S":   ["AI制药 融资 天使", "合成生物 融资 种子", "科学AI 初创 Pre-A", "AI新材料 融资"],
}
TRACK_QUERIES_EN = {
    "通用":   ["AI startup raised seed round 2026", "startup pre-seed funding AI 2026",
               "raised Series A early stage AI"],
    "AI2C":   ["consumer AI startup seed funding", "AI companion app raised funding"],
    "AI2B":   ["enterprise AI agent startup raised", "B2B AI SaaS seed round"],
    "具身":   ["robotics startup seed round", "embodied AI raised funding", "dexterous hand startup",
               "humanoid robot early stage funding"],
    "前沿科技":["AI chip startup angel round", "brain computer interface seed", "quantum computing startup raised",
               "semiconductor startup early stage funding"],
    "ai4S":   ["AI drug discovery seed funding", "synthetic biology startup raised",
               "materials AI startup funding", "biotech AI angel round"],
}


def all_cn_queries():
    """展开全部 CN 搜索词，去重保序"""
    seen = set()
    out = []
    for qs in TRACK_QUERIES_CN.values():
        for q in qs:
            if q not in seen:
                seen.add(q)
                out.append(q)
    return out


def all_en_queries():
    """展开全部 EN 搜索词，去重保序"""
    seen = set()
    out = []
    for qs in TRACK_QUERIES_EN.values():
        for q in qs:
            if q not in seen:
                seen.add(q)
                out.append(q)
    return out


# ========== 补搜用更宽的词（v6）==========
RETRY_QUERIES_CN = ["AI 初创 融资 2026", "机器人 融资 早期", "硬科技 天使轮 融资", "前沿技术 种子轮"]
RETRY_QUERIES_EN = ["AI startup raised funding 2026", "tech startup seed round",
                    "early stage funding AI robotics chip"]

# ========== 第二轮信息补全/反向核查搜索词（保留，给 search_collector 复用）==========
SEARCH_QUERIES_CN = [
    # 直接搜融资事件（补 RSS 遗漏）
    "完成 天使轮 融资 2026年6月",
    "完成 Pre-A 轮 融资 2026年6月",
    "完成 A轮 融资 AI 2026年6月",
    "完成 种子轮 融资 2026年6月",
    "获 亿元 融资 天使轮",
    "获 数千万元 融资 天使轮",
    "获投 天使轮 AI",
    "完成新一轮融资 天使轮",
    # 赛道定向
    "中国 AI 初创 种子轮 融资 2026",
    "具身智能 融资 机器人 种子轮",
    "人形机器人 融资 天使轮",
    "AI制药 融资 种子轮 A轮",
    "合成生物 融资 A轮",
    "半导体 芯片 天使轮 融资 2026",
    "自动驾驶 融资 Pre-A",
    "脑机接口 融资 早期",
    "新材料 融资 A轮",
    "量子计算 融资 种子轮",
    "商业航天 融资 天使轮",
    "AI Agent 融资 种子轮",
    "多模态大模型 融资 A轮",
    "AI芯片 融资 天使轮",
    "边缘AI芯片 融资",
    "消费AI 融资 天使轮",
]

SEARCH_QUERIES_EN = [
    "AI startup raised seed round 2026",
    "robotics startup pre-seed funding 2026",
    "chip semiconductor startup angel round 2026",
    "AI startup raised Series A early stage 2026",
    "deep learning startup seed funding",
    "humanoid robot startup funding 2026",
    "AI drug discovery startup seed round",
    "synthetic biology startup funding 2026",
    "fintech startup pre-seed funding",
    "autonomous driving startup seed round",
    "brain computer interface startup funding",
    "climate tech startup seed round 2026",
    "quantum computing startup angel round",
    "space tech startup funding 2026",
    "edge AI chip startup funding",
    "AI agent startup seed funding",
    "multimodal LLM startup Series A",
    "enterprise AI startup funding 2026",
    "healthcare AI startup seed round",
    "biotech startup Series A 2026",
]

SEARCH_QUERIES = SEARCH_QUERIES_CN + SEARCH_QUERIES_EN

# ========== 手动补漏链接 ==========
MANUAL_LINKS_FILE = "data/manual_links.txt"
