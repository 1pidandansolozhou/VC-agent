import os
from pathlib import Path

TZ = "Asia/Shanghai"
TIMEZONE = TZ
WINDOW_DAYS_DEFAULT = 1  # v2: 回溯N天 + 当天。1=昨天00:00到明天23:59，即完整覆盖前一天+当天

DATA_DIR = Path("data")
DB_PATH = os.getenv("VC_DB_PATH", str(DATA_DIR / "vc.sqlite"))
STATE_PATH = DATA_DIR / "state.json"
MANUAL_LINKS_FILE = str(DATA_DIR / "manual_links.txt")
TEMPLATE_PATH = os.getenv("VC_TEMPLATE_PATH", "templates/weekly_template.xlsx")

# 输出归档
OUTPUT_ROOT = Path(os.getenv("OUTPUT_ROOT") or (Path.home() / "Desktop" / "VC雷达"))
FLAT_MODE = os.getenv("FLAT_MODE", "false").lower() == "true"
KEEP_MASTER_SNAPSHOT = os.getenv("KEEP_MASTER_SNAPSHOT", "true").lower() == "true"

# 时间控制
REQUEST_TIMEOUT = int(os.getenv("VC_REQUEST_TIMEOUT", "15"))
MAX_ARTICLES_PER_SOURCE = int(os.getenv("VC_MAX_ARTICLES_PER_SOURCE", "20"))
EXTRACT_WORKERS = int(os.getenv("VC_EXTRACT_WORKERS", "6"))

# 数量阈值（统计参考用）
MIN_DEALS_TOTAL = int(os.getenv("MIN_DEALS_TOTAL", "5"))

# Docker 自动启动
DOCKER_DESKTOP_PATH = os.getenv("DOCKER_DESKTOP_PATH", r"E:\Docker\Docker Desktop\Docker Desktop.exe")

# 预留扩展
ENABLE_ENRICH = os.getenv("ENABLE_ENRICH", "true").lower() == "true"  # v2.2: 默认启用，Kimi联网补全信息不足的项目

# Sink 开关（默认关闭）
ENABLE_NOTION = os.getenv("ENABLE_NOTION", "false").lower() == "true"
ENABLE_FEISHU = os.getenv("ENABLE_FEISHU", "false").lower() == "true"
ENABLE_EMAIL = os.getenv("ENABLE_EMAIL", "false").lower() == "true"
