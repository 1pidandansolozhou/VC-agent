from typing import Dict, List, Set

TRACK_TAGS: Dict[str, List[str]] = {
    "AI2C": ["内容创作GenAI", "AI陪伴社交", "AI硬件终端", "健康运动", "生活服务", "效率与教育"],
    "AI2B": ["AI Agent", "金融科技", "数据与BI", "知识管理", "安全合规", "行业优化"],
    "具身": ["整机本体", "具身大脑", "灵巧操作", "感知传感", "数据基建", "场景落地"],
    "前沿科技": ["AI芯片算力", "新型计算", "EDA与设计", "脑机神经", "空间与3D", "航天与能源"],
    "ai4S": ["AI制药", "合成生物", "新材料", "气候地球", "科学基模", "科研工具"],
}

SHEET_BY_TRACK: Dict[str, str] = {
    "AI2C": "AI2C",
    "AI2B": "AI2B",
    "具身": "具身",
    "ai4S": "ai4S",
    "前沿科技": "前沿科技",
}

ALLOWED: Dict[str, Set[str]] = {t: set(v) for t, v in TRACK_TAGS.items()}
