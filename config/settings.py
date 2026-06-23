import os
from pathlib import Path

TZ = "Asia/Shanghai"
TIMEZONE = TZ
WINDOW_DAYS_DEFAULT = 2  # 每天17:00运行，窗口=今天+昨天

DATA_DIR = Path("data")
DB_PATH = os.getenv("VC_DB_PATH", str(DATA_DIR / "vc.sqlite"))
STATE_PATH = DATA_DIR / "state.json"
MANUAL_LINKS_FILE = str(DATA_DIR / "manual_links.txt")
TEMPLATE_PATH = os.getenv("VC_TEMPLATE_PATH", "templates/weekly_template.xlsx")

# 输出归档
OUTPUT_ROOT = Path(os.getenv("OUTPUT_ROOT") or (Path.home() / "Desktop" / "VC雷达"))
FLAT_MODE = os.getenv("FLAT_MODE", "false").lower() == "true"
KEEP_MASTER_SNAPSHOT = os.getenv("KEEP_MASTER_SNAPSHOT", "true").lower() == "true"

# 时间控制（秒/并发/上限）
REQUEST_TIMEOUT = int(os.getenv("VC_REQUEST_TIMEOUT", "15"))
MAX_ARTICLES_PER_SOURCE = int(os.getenv("VC_MAX_ARTICLES_PER_SOURCE", "20"))
MAX_SEARCH_RESULTS = int(os.getenv("VC_MAX_SEARCH_RESULTS", "120"))
ENABLE_ENRICH = os.getenv("VC_ENABLE_ENRICH", "true").lower() == "true"
EXTRACT_WORKERS = int(os.getenv("VC_EXTRACT_WORKERS", "6"))

# Exa 搜索配置
EXA_NUM_RESULTS = int(os.getenv("EXA_NUM_RESULTS", "15"))
EXA_SEARCH_TYPE = os.getenv("EXA_SEARCH_TYPE", "auto")
EXA_MAX_CHARS = int(os.getenv("EXA_MAX_CHARS", "2000"))

# 博查搜索配置
BOCHA_NUM_RESULTS = int(os.getenv("BOCHA_NUM_RESULTS", "10"))
BOCHA_SUMMARY = os.getenv("BOCHA_SUMMARY", "true").lower() == "true"
BOCHA_FRESHNESS = os.getenv("BOCHA_FRESHNESS", "noLimit")

# Kimi 联网搜索配置
KIMI_SEARCH_MAX_QUERIES = int(os.getenv("KIMI_SEARCH_MAX_QUERIES", "10"))
KIMI_SEARCH_MAX_CHARS = int(os.getenv("KIMI_SEARCH_MAX_CHARS", "1500"))

# 浏览器深抓配置
WEB_CRAWL_WORKERS = int(os.getenv("WEB_CRAWL_WORKERS", "3"))
WEB_CRAWL_TIMEOUT = int(os.getenv("WEB_CRAWL_TIMEOUT", "30"))

# 反向核查
ENABLE_DATE_VERIFY = os.getenv("ENABLE_DATE_VERIFY", "true").lower() == "true"
VERIFY_SCOPE = os.getenv("VERIFY_SCOPE", "risky")  # risky|all — 只对 search/web/manual 源反查

# ★ v1 数量检查 — 门槛降低，RSS 优先
MIN_DEALS_TOTAL = int(os.getenv("MIN_DEALS_TOTAL", "5"))
MIN_DEALS_CN = int(os.getenv("MIN_DEALS_CN", "1"))
MIN_DEALS_GLOBAL = int(os.getenv("MIN_DEALS_GLOBAL", "1"))
MAX_SEARCH_RETRIES = int(os.getenv("MAX_SEARCH_RETRIES", "1"))

# ★ v1 新增 — 浏览器搜索词数限制
BROWSER_SEARCH_MAX_CN = int(os.getenv("BROWSER_SEARCH_MAX_CN", "8"))
BROWSER_SEARCH_MAX_EN = int(os.getenv("BROWSER_SEARCH_MAX_EN", "8"))

# ★ v1 新增 — 回源补全上限
WEB_CRAWL_MAX = int(os.getenv("WEB_CRAWL_MAX", "40"))

# 预留开关
ENABLE_XHS = os.getenv("ENABLE_XHS", "false").lower() == "true"
ENABLE_NOTION = os.getenv("ENABLE_NOTION", "false").lower() == "true"
ENABLE_FEISHU = os.getenv("ENABLE_FEISHU", "false").lower() == "true"
ENABLE_EMAIL = os.getenv("ENABLE_EMAIL", "false").lower() == "true"
