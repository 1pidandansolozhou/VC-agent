# VC 监控agent · 构建规格 v1（权威版 / 自包含）

> 给 **Claude Code** 的唯一实现规格。**严格按第 15 节顺序实现、满足第 16 节验收**。
> 同目录：`templates/weekly_template.xlsx`（周报模板，11 列含业务简介）。
> 本文件为 v1 当前架构权威规格，与 `docs/ARCHITECTURE.md` 互补。

## v1 核心特性

| 特性 | 说明 |
|---|---|
| ★ **四阶段闭环** | ROUND-1（五路并行采集）→ STAGE-2（预过滤+去重+LLM抽取+时间核查）→ ROUND-2（信息补全）→ STAGE-3（数量检查+补搜） |
| ★ **五路并行采集** | RSS + 微信公众号(wewe-rss JWT) + Kimi联网搜索 + 浏览器Bing搜索 + 手动链接 |
| ★ **wewe-rss JWT 认证** | Bearer token 登录 `/api/v1/wx/auth/login`，4级降级：JWT API → 无认证 → RSS列表 → 容器SQLite直读 |
| ★ **时间预过滤** | ROUND-1 末尾按窗口剔除旧文，减少后续无效 LLM 调用 |
| ★ **业务简介字段** | Deal 模型含 `business`，Excel 11 列含"业务简介" |
| ★ **赛道感知关键词** | 5 大赛道分别有专属搜索词集，CN+EN 双语分开 |
| ★ **<20 强制补搜** | 总数<20/国内<5/海外<5 分别触发定向补搜，最多1次 |
| ★ **5 路定向补全** | ROUND-2 每项目 5 路搜索：融资细节/团队/投资方/业务/英文备选 |
| ★ **预检脚本** | `preflight.py` 检查 wewe-rss 容器+微信登录+搜索API+LLM+窗口文章数 |

其余（赛道/Tag 定义、Word 精确结构、SQLite+双Excel 存储归档、金额量级规则、预留端口）与现有实现一致。

---

## 0. 需求总清单

| # | 功能 | 验收锚点 |
|---|---|---|
| R1 | 周三&周日 自动运行（`RUN_HOUR` 可配，默认 12:00，Asia/Shanghai） | `scheduler.py` |
| R2 | 手动触发：CLI（`--since/--until/--dry-run/--no-enrich/--max-articles`）+ 看板按钮 | `main.py`/`dashboard.py` |
| R3 | 抓**中国+海外**所有「A 轮及以前」融资，中外双覆盖、尽力不漏 | 第一轮+第二轮+补搜 |
| R4 | 5 赛道（含 **ai4S 独立**）×6 Tag | `taxonomy.py` |
| R5 | 周报 Excel 严格匹配模板（5 sheet/固定 10 列） | `excel_exporter` |
| R6 | 周报 Word 逻辑结构+文字风格匹配历史周报 | `word_exporter`（第 4 节） |
| R7 | 本周综述自动生成 | `summary.py` |
| R8 | 只保留 A 轮及以前；B 轮及以后 → skip | `extractor` 硬规则 |
| R9 | 运行时模型 = Kimi K2.6 + DeepSeek V4，统一接口 | `llm/client.py` |
| R10 | **第一轮**：Kimi联网 + 浏览器关键词(Bing) + 微信RSS + RSSHub，并行捕获 | 第 9 节 |
| R11 | **时间核查阶段**：date_verify 核实真实融资公布日，stale 剔出周报 | `date_verify.py` |
| R12 | **第二轮**：Tavily+Exa+博查 对已确认项目定向补全信息 | 第 11 节 |
| R13 | **<20 强制补搜**：进入周报项目 <20 时强制扩展关键词再搜一轮 | 第 12 节 |
| R14 | **CN+海外分别计数**：分别 <5 时触发定向补搜 | 第 12 节 |
| R15 | 单编排器+多角色+有边界补漏 | 第 6 节 |
| R16 | SQLite 真源 + 总库 Excel（当数据库）+ 按日期归档周报 | 第 13 节 |
| R17 | 输出存本地（分层归档）；预留 Notion/飞书/邮箱/小红书 | 第 13 节 |
| R18 | 本地看板：筛选+手动运行+下载 | `dashboard.py` |
| R19 | 不漏不重：last_run 区间 + 指纹去重 | `window`+`dedup` |
| R20 | 有详有略（importance 控详略+排序） | `extractor` |
| R21 | 字段映射固定：`title`=完整大标题；`detail`=agent 补写 | 第 3 节 |
| R22 | 金额估值：精确>量级>未披露，绝不编造 | `extractor`+`date_verify` |
| R23 | 跨源合并；重复运行不重复 | `merge`+SQLite |
| R24 | 省 token：预过滤门+选择性回源+选择性反查+便宜模型 | 第 14 节 |
| R25 | **浏览器只做关键词搜索（Bing CN+EN）**，不定向爬固定站点 | `browser_search.py` |

---

## 1. 目标

个人本地 Agent：定时/手动，**4 阶段检索流水线**，抓中外 A 轮及以前融资，分类打标，反向核查时效，产出分层归档的周报 Excel+Word，维护累积总库，提供 Streamlit 看板。

---

## 2. 赛道与 Tag

```python
# config/taxonomy.py（与 v4/v1 完全一致）
TRACK_TAGS = {
    "AI2C":  ["内容创作GenAI","AI陪伴社交","AI硬件终端","健康运动","生活服务","效率与教育"],
    "AI2B":  ["AI Agent","金融科技","数据与BI","知识管理","安全合规","行业优化"],
    "具身":  ["整机本体","具身大脑","灵巧操作","感知传感","数据基建","场景落地"],
    "前沿科技":["AI芯片算力","新型计算","EDA与设计","脑机神经","空间与3D","航天与能源"],
    "ai4S":  ["AI制药","合成生物","新材料","气候地球","科学基模","科研工具"],
}
SHEET_BY_TRACK = {"AI2C":"AI2C","AI2B":"AI2B","具身":"具身","ai4S":"ai4S","前沿科技":"前沿科技"}
ALLOWED = {t:set(v) for t,v in TRACK_TAGS.items()}
```

---

## 3. 字段映射（v1：11 列，含业务简介）

| Excel 列 | Deal 字段 | Word 对应 |
|---|---|---|
| 项目名称 | `project_name` | 粗体标题内公司名 |
| 细分tag | `sub_tag` | — |
| 成立时间 | `founded_year` | 团队段 |
| **标题** | **`title`** | **整条粗体标题，含金额或量级** |
| 团队 | `team` | 创始人段 |
| 轮次 | `round` | 融资详情 |
| 融资金额 | `amount` | 融资详情 |
| 投资方 | `investors` | 融资详情 |
| **业务简介** | **`business`** | **一句话业务（v1 新增）** |
| 地区 | `region` | 正文 |
| 具体信息 | `detail` | 痛点→方案→商业模式 正文 |

仅入 SQLite/总库/看板的附加字段：`valuation, region_class, verified_date, date_status, date_confidence, sources, first_seen_window`。

---

## 4. Word 周报精确结构（写死）

```
AI 创业资讯 {起.月.日} — {止.月.日}          ← heading 0
本周综述                                      ← heading 1
  250–350字，连续段落：
  ① 点名 importance=high 的项目（含中外分布）
  ② 归纳 2–3 条赛道主线（明确提中国+海外格局）
  ③ 凝练专业，不用列表，不堆形容词
一、早期项目                                   ← heading 1
  按 importance 高→低，逐个：
  【粗体】{Deal.title}                         ← 整条 title，含金额
  官方网站：{official_site}                    ← 有才写
  {round} · 融资{amount} · 估值{valuation} · {investors} · {region} · 公布{verified_date}
  {detail}
  团队：{team}                                 ← 有才写
  （空行）
```

> 实现前先读历史周报 docx 校准口吻与段落顺序。

---

## 5. LLM 层（与 v1 一致，关键点）

```python
# llm/client.py
TASK_MODEL = {
    "extract":  ("deepseek","deepseek-v4-flash"),   # 批量抽取，最便宜
    "classify": ("deepseek","deepseek-v4-pro"),     # 疑难复核（可选）
    "verify":   ("deepseek","deepseek-v4-flash"),   # 反向核查
    "audit":    ("deepseek","deepseek-v4-flash"),   # 补漏检查
    "enrich":   ("deepseek","deepseek-v4-flash"),   # 第二轮信息补全
    "write":    ("kimi",    "kimi-k2.6"),           # 周报文笔（非 K2.7-Code）
}
```

`kimi_web_search(query)` 函数（第一轮主力）：通过 Kimi `$web_search` builtin，关思考模式（`extra_body={"thinking":{"type":"disabled"}}`），tool_calls 循环到 finish\_reason≠tool\_calls，返回 `[{title,url,snippet,date}]`，约 ¥0.03/次。（实现见 v1 第 10.2 节）

---

## 6. 架构总图（v1 四阶段）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 第一轮 ROUND-1（五路并行，同时启动，结果合并）                          │
│  A. RSSHub(中) + 海外RSS   全量拉，时间窗过滤                          │
│  B. 微信公众号(wewe-rss)   JWT 认证 → 逐号分页采集 → 时间窗过滤         │
│  C. Kimi 联网搜索           CN关键词×N + EN关键词×N（赛道感知）         │
│  D. 浏览器Bing搜索          Bing-CN × CN词 + Bing-EN × EN词（Playwright）│
│  E. 手动链接                逐 URL 抓取                                 │
│                  ↓ 合并 → 时间预过滤（窗口外旧文剔除）                  │
└──────────────────────────┬───────────────────────────────────────────┘
                           ▼ 关键词预过滤门 → 指纹去重 → 回源补全文
                           ▼ 并发 LLM 抽取(DeepSeek-V4-Flash × 6) → 跨源合并
┌──────────────────────────────────────────────────────────────────────┐
│ 时间核查 STAGE-2（date_verify：RSS/wechat 用 source_date 直接判断；    │
│                   search/web/manual 调 Kimi 反查融资公布日，stale 剔出）│
└──────────────────────────┬───────────────────────────────────────────┘
                           ▼ in_window 项目
┌──────────────────────────────────────────────────────────────────────┐
│ 第二轮 ROUND-2（v1：5 路定向搜索补全）                                  │
│  每项目 5 路搜索：融资细节/团队背景/投资方/业务产品/英文备选             │
│  补齐 amount / valuation / investors / team / business / official_site │
│  Bocha/Exa 优先，Web Fetch 兜底                                        │
└──────────────────────────┬───────────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 数量检查 STAGE-3（COUNT CHECK）                                        │
│  统计 in_window 总数 / CN 数 / 海外数                                  │
│  总数 <20 → 触发 ROUND-3（Kimi+浏览器Bing+Bocha/Exa 补搜，最多1次）    │
│  CN <5   → 额外跑 CN 定向补搜                                          │
│  海外 <5 → 额外跑 EN 定向补搜                                          │
└──────────────────────────┬───────────────────────────────────────────┘
                           ▼ 最终 in_window 项目列表
           SQLite upsert (26列) → 周报Excel (11列) + 周报Word + 总库Excel → 桌面归档
                        └─► Streamlit 看板 + [预留] Notion/飞书/邮箱
```

---

## 7. 赛道感知关键词集（v1 核心，赛道分组+中英双语）

```python
# config/sources.py  关键词部分（新增，替换 v1 的通用词列表）

# ── 按赛道分组，搜索时全部合并使用 ──────────────────────────────────────
TRACK_QUERIES_CN = {
    "通用":   ["本周 完成 天使轮 融资","完成 Pre-A 轮 融资","完成 种子轮 融资 AI","完成 A轮 融资"],
    "AI2C":   ["消费AI 融资 天使","AI陪伴 APP 融资","内容生成 AI 初创 融资"],
    "AI2B":   ["企业AI 融资","AI Agent 企业服务 融资","大模型 B端 Pre-A"],
    "具身":   ["具身智能 融资","机器人 天使轮","灵巧手 融资","VLA 初创 融资","人形机器人 早期"],
    "前沿科技":["AI芯片 融资 天使","脑机接口 初创 融资","量子计算 种子轮","光计算 融资"],
    "ai4S":   ["AI制药 融资 天使","合成生物 融资 种子","科学AI 初创 Pre-A","AI新材料 融资"],
}
TRACK_QUERIES_EN = {
    "通用":   ["AI startup raised seed round 2026","startup pre-seed funding AI 2026",
               "raised Series A early stage AI"],
    "AI2C":   ["consumer AI startup seed funding","AI companion app raised funding"],
    "AI2B":   ["enterprise AI agent startup raised","B2B AI SaaS seed round"],
    "具身":   ["robotics startup seed round","embodied AI raised funding","dexterous hand startup",
               "humanoid robot early stage funding"],
    "前沿科技":["AI chip startup angel round","brain computer interface seed","quantum computing startup raised",
               "semiconductor startup early stage funding"],
    "ai4S":   ["AI drug discovery seed funding","synthetic biology startup raised",
               "materials AI startup funding","biotech AI angel round"],
}

# 展开为列表供搜索层直接使用
def all_cn_queries(): return [q for qs in TRACK_QUERIES_CN.values() for q in qs]
def all_en_queries(): return [q for qs in TRACK_QUERIES_EN.values() for q in qs]

# 补搜时用更宽的词
RETRY_QUERIES_CN = ["AI 初创 融资 2026","机器人 融资 早期","硬科技 天使轮 融资","前沿技术 种子轮"]
RETRY_QUERIES_EN = ["AI startup raised funding 2026","tech startup seed round",
                    "early stage funding AI robotics chip"]

# RSS（v1：投资界/机器之心 RSSHub 路由不存在已移除；微信公众号改用 werss_collector JWT 采集）
import os
RSS_FEEDS_CN = {
    "36氪":"http://localhost:1200/36kr/news/latest",
    "创业邦":"http://localhost:1200/cyzone/news",
    "量子位":"http://localhost:1200/qbitai/category/资讯",
    # 微信公众号改用 collectors/werss_collector.py（JWT 认证 → 逐号分页采集 → 时间窗过滤）
}
RSS_FEEDS_GLOBAL = {
    "TechCrunch":"https://techcrunch.com/feed/",
    "TechCrunch Startups":"https://techcrunch.com/category/startups/feed/",
    "Crunchbase News":"https://news.crunchbase.com/feed/",
    "VentureBeat AI":"https://venturebeat.com/category/ai/feed/",
    "EU-Startups":"https://www.eu-startups.com/feed/",
    "Tech.eu":"https://tech.eu/feed/","Sifted":"https://sifted.eu/feed",
}
MANUAL_LINKS_FILE = "data/manual_links.txt"
```

---

## 8. 目录结构（v1 当前）

```
VC agent/
├── config/{taxonomy.py, sources.py, settings.py}
├── models/schema.py
├── llm/client.py                            # 多 Provider 统一 LLM 客户端
├── collectors/
│   ├── werss_collector.py                   # ★ 微信公众号（JWT + 逐号分页 + SQLite 降级）
│   ├── rss_collector.py                     # RSSHub(中) + 海外 RSS 直连
│   ├── kimi_collector.py                    # Kimi 联网搜索（赛道关键词）
│   ├── kimi_search_collector.py             # Kimi 单项目反查
│   ├── browser_search.py                    # 浏览器 Bing CN+EN 关键词搜索
│   ├── search_collector.py                  # Bocha/Exa/Tavily API 搜索
│   ├── web_collector.py                     # URL 抓取 + 全文回源
│   ├── manual.py                            # 手动链接读取
│   └── xhs_collector.py                     # 小红书（桩）
├── processors/
│   ├── pregate.py                           # 关键词预过滤门
│   ├── dedup.py                             # 指纹去重
│   ├── extractor.py                         # LLM 并发抽取（Deal 结构化）
│   ├── merge.py                             # 跨源合并
│   ├── date_verify.py                       # 时间核查（Kimi 反查）
│   ├── enricher.py                          # ROUND-2 信息补全（5 路搜索）
│   ├── coverage_check.py                    # 数量检查 + 补搜决策
│   └── window.py                            # 时间窗口管理
├── exporters/{excel_exporter.py, word_exporter.py, master_exporter.py, summary.py}
├── storage/{db.py, paths.py, notion_sink.py, feishu_sink.py, email_sink.py}
├── templates/weekly_template.xlsx           # 周报模板（11 列）
├── data/{vc.sqlite, state.json, manual_links.txt}
├── docs/ARCHITECTURE.md                     # 系统架构文档
├── preflight.py                             # ★ 预检脚本（服务+API+数据检查）
├── dashboard.py  main.py  scheduler.py
└── scripts/{setup_env.py, check_env.py}
```

---

## 9. ★第一轮：五路并行采集（v1 增强）

### 9.1 RSS（RSSHub 中 + 海外直连）+ 微信公众号（wewe-rss JWT）

```python
# collectors/rss_collector.py — RSSHub(中) + 海外 RSS 直连，时间窗过滤
def collect_rss(start, end): ...

# collectors/werss_collector.py — 微信公众号（★ v1：JWT 认证 + 逐号分页）
# 1. POST /api/v1/wx/auth/login → Bearer token（缓存 1h）
# 2. GET /api/v1/wx/mps → 活跃公众号列表
# 3. 逐号 GET /feed/{id}.xml?limit=100&offset=N → 按窗口时间戳过滤
# 4. 4 级降级：JWT API → 无认证 API → RSS 列表 → 容器 SQLite 直读
def collect_werss(start, end): ...
```

### 9.2 ★浏览器关键词搜索（R25，新模块）

```python
# collectors/browser_search.py
"""
用 Crawl4AI+Playwright 在 Bing CN / Bing EN 上做真实关键词搜索。
★ 不定向爬任何固定站点；只构造搜索引擎 URL → 提取搜索结果链接+摘要。
"""
import asyncio, urllib.parse, re, os
from crawl4ai import AsyncWebCrawler
from models.schema import Article

BING_CN = "https://cn.bing.com/search?q={q}&mkt=zh-CN&setlang=zh-Hans&count=15"
BING_EN = "https://www.bing.com/search?q={q}&setlang=en-US&count=15"

def _is_junk(url):
    """过滤掉搜索引擎自身、广告、导航页"""
    SKIP = ["bing.com","microsoft.com","msn.com","baidu.com","google.com",
            "yahoo.com","youtube.com","twitter.com","facebook.com","linkedin.com"]
    return any(s in (url or "") for s in SKIP)

async def _search_one(crawler, query, engine_url, region):
    url = engine_url.format(q=urllib.parse.quote(query))
    arts = []
    try:
        r = await crawler.arun(url=url, wait_for=2000, timeout=25)
        # 从 Bing 搜索结果 markdown 里提取结果块（标题+链接+摘要）
        links = (r.links or {}).get("external", [])
        seen = set()
        for lk in links[:25]:
            href = lk.get("href",""); text = lk.get("text","")
            if not href or _is_junk(href) or href in seen: continue
            seen.add(href)
            arts.append(Article(title=text or href, url=href, content="",
                source=f"Bing·{query[:18]}", source_type="web", region_hint=region))
    except Exception: pass
    return arts

async def _run_all(cn_queries, en_queries, max_cn=None, max_en=None):
    max_cn = max_cn or int(os.getenv("BROWSER_SEARCH_MAX_CN","8"))
    max_en = max_en or int(os.getenv("BROWSER_SEARCH_MAX_EN","8"))
    results = []
    async with AsyncWebCrawler(headless=True) as c:
        tasks = []
        for q in cn_queries[:max_cn]:
            tasks.append(_search_one(c, q, BING_CN, "国内"))
        for q in en_queries[:max_en]:
            tasks.append(_search_one(c, q, BING_EN, "海外"))
        for coros in asyncio.as_completed(tasks):
            try: results += await coros
            except Exception: pass
    return results

def browser_keyword_search(cn_queries, en_queries, max_cn=None, max_en=None):
    """同步入口，供 main.py 调用"""
    try:
        return asyncio.run(_run_all(cn_queries, en_queries, max_cn, max_en))
    except Exception: return []
```

> 若 Playwright 未安装，`AsyncWebCrawler` 自动降级到 requests+bs4，部分结果变少但不阻断主流程。

### 9.3 Kimi 联网搜索 —— 同 v1 `llm/client.py::kimi_web_search()`

把 Kimi 联网结果转 Article：

```python
# collectors/kimi_collector.py
from llm.client import kimi_web_search
from models.schema import Article
from config.sources import all_cn_queries, all_en_queries

def collect_kimi():
    out = []
    for q in all_cn_queries() + all_en_queries():
        for it in kimi_web_search(q, max_results=8):
            region = "海外" if q == q.encode().decode("ascii","ignore") else "未知"
            out.append(Article(title=it.get("title",""), url=it.get("url",""),
                content=it.get("snippet",""), source="Kimi联网", source_type="search",
                region_hint="国内" if any(c>'\x7f' for c in q) else "海外"))
    return out
```

### 9.4 第一轮并行编排（v1：五路并行 + 时间预过滤）

```python
# 在 main.py 的 stage1_capture() 里
from concurrent.futures import ThreadPoolExecutor
from config.sources import all_cn_queries, all_en_queries

def stage1_capture(start, end):
    """五路并行采集：RSS + 微信公众号 + Kimi + 浏览器Bing + 手动链接。"""
    arts = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_rss   = ex.submit(rss_collector.collect_rss, start, end)
        f_werss = ex.submit(werss_collector.collect_werss, start, end)
        f_kimi  = ex.submit(kimi_collector.collect_kimi)
        f_brow  = ex.submit(browser_search.browser_keyword_search,
                            all_cn_queries(), all_en_queries())
        f_manual= ex.submit(lambda: web_collector.crawl_urls(manual.read_manual_links()))
        arts.extend(f_rss.result())
        arts.extend(f_werss.result())
        arts.extend(f_kimi.result())
        arts.extend(f_brow.result())
        arts.extend(f_manual.result())
    arts += collect_xhs()                   # 桩，默认 []

    # ★ v1：时间预过滤 — 只保留窗口内的文章
    in_window_arts = []
    for a in arts:
        if a.published_at is None:
            in_window_arts.append(a)        # 无日期的保留（搜索源）
        elif start <= a.published_at <= end:
            in_window_arts.append(a)
    return in_window_arts
```

---

## 10. 时间核查阶段（Stage 2）— v1 增强

```python
# 在 main.py 的 stage2_verify() 里
def stage2_verify(arts, start, end):
    arts = pregate.pre_gate(arts)           # 关键词预过滤门
    arts = dedup.dedup(arts)                # 指纹去重
    web_collector.enrich_fulltext(arts)     # 选择性回源补全文（搜索来的短内容）
    deals = merge.merge(extractor.extract_all(arts))  # 并发抽取
    for d in deals:
        date_verify.verify(d, start, end)   # ★ v1：全部源都做时间核查
    return deals
```

核查策略（v1）：
- **RSS/wechat 源**：用 `source_date` 直接窗口判断（高置信度），不再盲目信任
- **search/web/manual 源**：调 Kimi 联网反查融资公布日
- 核查结果标 `date_status`：`in_window` → 进周报；`stale` → 入总库但不进周报；`unknown` → 不误杀，保留

---

## 11. ★第二轮：项目信息核实与补全（Round 2）— v1 增强

> 第一轮捕获的是"有什么项目"，第二轮是"把这些项目的信息填完整"。
> v1 增强：5 路定向搜索 + Web Fetch 兜底，补全 business（业务简介）等新增字段。

```python
# processors/enricher.py（v1：5 路定向搜索 + business 字段）
import json
from llm.client import chat
from collectors.search_collector import search_all   # Bocha/Exa 优先

ENRICH_SYS = """你是 VC 投研数据核实员。给定一个融资项目的已知信息和搜索摘要，
补充缺失字段。只输出 JSON，字段：
amount(融资金额，精确>量级>未披露),
valuation(估值，同上),
investors(投资方，格式：领投·XX，跟投·YY；有才填),
team(创始人姓名+学历/前东家+核心履历),
business(一句话业务：做什么产品/服务，解决什么痛点),
official_site(官网URL，有才填)。
不确定的量级举例：数百万元/数千万元/亿元级/数百万美元/数千万美元/数亿美元。绝不编造。"""

def enrich_deal(d):
    """v1：对单个 Deal 做 5 路定向搜索 + Web Fetch 兜底"""
    missing = [f for f in ("amount","valuation","investors","team","business","official_site")
               if not getattr(d, f) or getattr(d, f) in ("","未披露")]
    if not missing: return d
    # 5 路定向搜索：融资细节 / 团队 / 投资方 / 业务产品 / 英文备选
    qs = [
        f'"{d.project_name}" {d.round} 融资金额 估值',
        f'"{d.project_name}" 创始人 团队 背景',
        f'"{d.project_name}" 投资方 领投 跟投',
        f'"{d.project_name}" 产品 业务 商业模式',
        f'"{d.project_name}" funding round investors' if d.region_class == "海外" else None,
    ]
    qs = [q for q in qs if q]
    arts = []
    for q in qs:
        arts.extend(search_all([q])[:3])
    snip = "\n\n".join(f"[{a.source}] {a.title}\n{a.content[:400]}" for a in arts[:12])
    if not snip: return d  # Web Fetch 兜底由调用方处理
    known = f"项目：{d.project_name} | 轮次：{d.round} | 已知：amount={d.amount}, investors={d.investors}"
    try:
        j = json.loads(chat("enrich", ENRICH_SYS,
            f"{known}\n缺失字段：{missing}\n\n搜索摘要：\n{snip}", max_tokens=500, json_mode=True))
    except Exception: return d
    for f in missing:
        val = j.get(f,"")
        if val and val not in ("","未披露","unknown"): setattr(d, f, val)
    return d

def enrich_all(deals, workers=4):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(enrich_deal, deals))
```

---

## 12. ★数量检查 + 补搜（Stage 3）

```python
# processors/coverage_check.py
"""
检查三个指标，不足时分别触发补搜，最多执行一次全量补搜。
"""
import os
from config.sources import RETRY_QUERIES_CN, RETRY_QUERIES_EN

MIN_TOTAL   = int(os.getenv("MIN_DEALS_TOTAL","20"))   # 总数阈值
MIN_CN      = int(os.getenv("MIN_DEALS_CN","5"))        # 国内项目最低数
MIN_GLOBAL  = int(os.getenv("MIN_DEALS_GLOBAL","5"))    # 海外项目最低数
MAX_RETRIES = int(os.getenv("MAX_SEARCH_RETRIES","1"))  # 补搜最大轮次（防无限循环）

def _count(deals):
    in_win = [d for d in deals if d.date_status != "stale"]
    cn = [d for d in in_win if d.region_class == "国内"]
    gl = [d for d in in_win if d.region_class == "海外"]
    return in_win, cn, gl

def should_retry(deals):
    in_win, cn, gl = _count(deals)
    reasons = []
    if len(in_win) < MIN_TOTAL: reasons.append(f"总数{len(in_win)}<{MIN_TOTAL}")
    if len(cn) < MIN_CN:        reasons.append(f"国内{len(cn)}<{MIN_CN}")
    if len(gl) < MIN_GLOBAL:    reasons.append(f"海外{len(gl)}<{MIN_GLOBAL}")
    return reasons

def build_retry_queries(deals):
    """根据缺口构造补搜词：缺中文就多加 CN 词，缺英文就多加 EN 词"""
    in_win, cn, gl = _count(deals)
    cn_qs, en_qs = [], []
    if len(cn) < MIN_CN:   cn_qs = RETRY_QUERIES_CN
    if len(gl) < MIN_GLOBAL: en_qs = RETRY_QUERIES_EN
    if len(in_win) < MIN_TOTAL:   # 总数不足：CN+EN 都加
        cn_qs = cn_qs or RETRY_QUERIES_CN
        en_qs = en_qs or RETRY_QUERIES_EN
    return cn_qs, en_qs
```

---

## 13. 数据存储与归档（沿用 v1 第 13 节）

三层：**SQLite**（唯一真源）→ **总库 Excel**（全字段，全量重建，原子写+快照）→ **周报 Excel+Word**（仅本窗口 in\_window 项目，剔 stale，按年/月归档，文件名含日期区间）。`FLAT_MODE=true` 时平铺，命名铁律 `YYYY-MM-DD` 零填充+类型前缀。`storage/paths.py` 实现同 v1。

---

## 14. 省 token 策略（沿用 v1 第 12 节 + 新增）

| 优化点 | 做法 |
|---|---|
| 预过滤门 | 进 LLM 前正则筛融资信号词，砍 50–70% 抽取调用 |
| 选择性回源 | 只对「搜索来的、正文<300字」回源 Crawl4AI，上限 `WEB_CRAWL_MAX`=40 |
| 选择性反查 | `VERIFY_SCOPE=risky`：只对 search/web/manual 来的项目反查时效 |
| 便宜模型 | 抽取/核查/补全用 flash；写稿用 Kimi K2.6 |
| Kimi 关思考 | `extra_body={"thinking":{"type":"disabled"}}` |
| **第一轮限制词数** | CN/EN 各最多 `BROWSER_SEARCH_MAX_CN/EN`=8 个关键词进浏览器，Kimi 全量 |
| **第二轮按需补全** | 字段已完整的项目直接跳过 `enrich_deal()`，不消耗额度 |
| **补搜上限** | `MAX_SEARCH_RETRIES`=1，避免无限循环 |
| 综述精简输入 | 只给 `项目名\|区域\|赛道\|轮次\|金额\|importance` 行 |

---

## 15. 主流程编排（v1 完整版）

```python
# main.py
import argparse, os
from datetime import datetime
from dotenv import load_dotenv; load_dotenv()
from loguru import logger
from storage.paths import weekly_paths, log_path
logger.add(str(log_path()), rotation="2 MB", encoding="utf-8")

# 导入（各模块按第 8 节目录）
from processors.window import get_window, mark_done
from processors import pregate, dedup, extractor, merge, date_verify, enricher, coverage_check
from collectors import rss_collector, kimi_collector, browser_search, web_collector
from collectors import manual, search_collector, xhs_collector
from storage import db
from exporters import excel_exporter, master_exporter, word_exporter
from config.sources import all_cn_queries, all_en_queries

def _log_sources():
    import requests
    for name, url in [("RSSHub","http://localhost:1200"),
                      ("wewe-rss", os.getenv("WEWE_RSS_FEED","http://localhost:8001/feed/all.atom"))]:
        try: ok = requests.get(url, timeout=5).status_code < 500
        except Exception: ok = False
        logger.info(f"[本地源] {name}: {'在线✓' if ok else '未启动—可能少抓国内'}")

# ─── Stage 1：并行捕获 ───────────────────────────────────────
def stage1_capture(start, end):
    from concurrent.futures import ThreadPoolExecutor
    logger.info("[ROUND-1] 启动并行采集：Kimi联网 + 浏览器Bing + 微信RSS + RSSHub")
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_rss   = ex.submit(rss_collector.collect_rss, start, end)
        f_kimi  = ex.submit(kimi_collector.collect_kimi)
        f_brow  = ex.submit(browser_search.browser_keyword_search,
                            all_cn_queries(), all_en_queries())
        f_manual= ex.submit(lambda: web_collector.crawl_urls(manual.read_manual_links()))
    arts = f_rss.result() + f_kimi.result() + f_brow.result() + f_manual.result()
    arts += xhs_collector.collect_xhs()      # 默认 []
    logger.info(f"[ROUND-1] 原始文章 {len(arts)} 篇")
    return arts

# ─── Stage 2：时间核查+去重+提取 ────────────────────────────
def stage2_verify(arts, start, end, no_enrich=False):
    arts = pregate.pre_gate(arts)
    arts = dedup.dedup(arts)
    logger.info(f"[STAGE-2] 预过滤+去重后 {len(arts)} 篇")
    web_collector.enrich_fulltext(arts, no_enrich)
    deals = merge.merge(extractor.extract_all(arts))
    for d in deals:
        date_verify.verify(d, start, end)
    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    logger.info(f"[STAGE-2] 提取 {len(deals)}，in_window={len(in_w)}，stale={len(stale)}")
    return deals

# ─── Round 2：项目信息补全 ──────────────────────────────────
def round2_enrich(deals):
    in_w = [d for d in deals if d.date_status != "stale"]
    logger.info(f"[ROUND-2] 对 {len(in_w)} 个确认项目补全信息（Tavily/Exa/博查定向搜）")
    enriched = enricher.enrich_all(in_w)
    stale = [d for d in deals if d.date_status == "stale"]
    return enriched + stale       # stale 不参与补全，直接追回

# ─── Stage 3：数量检查 + 补搜 ──────────────────────────────
def stage3_coverage(deals, start, end, retry_count=0):
    reasons = coverage_check.should_retry(deals)
    max_retries = int(os.getenv("MAX_SEARCH_RETRIES","1"))
    if not reasons or retry_count >= max_retries:
        if reasons:
            logger.warning(f"[STAGE-3] 数量不足（{reasons}），已达最大补搜次数 {max_retries}，输出现有结果")
        return deals
    logger.info(f"[STAGE-3] 触发补搜，原因：{reasons}")
    cn_qs, en_qs = coverage_check.build_retry_queries(deals)
    logger.info(f"[ROUND-3] CN补搜词 {cn_qs}，EN补搜词 {en_qs}")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_kimi  = ex.submit(lambda: kimi_collector.collect_kimi_with_queries(cn_qs+en_qs))
        f_brow  = ex.submit(browser_search.browser_keyword_search, cn_qs, en_qs)
        f_api   = ex.submit(lambda: search_collector.search_all(cn_qs+en_qs))
    extra = f_kimi.result() + f_brow.result() + f_api.result()
    extra_deals = stage2_verify(extra, start, end)
    merged = merge.merge(deals + extra_deals)
    merged = round2_enrich(merged)
    return stage3_coverage(merged, start, end, retry_count+1)  # 递归，最多1次

# ─── 主入口 ────────────────────────────────────────────────
def run(since=None, until=None, dry=False, no_enrich=False, max_articles=None):
    if max_articles: os.environ["VC_MAX_ARTICLES_PER_SOURCE"] = str(max_articles)
    start, end = get_window(since, until)
    win = f"{start:%Y-%m-%d}_至_{end:%Y-%m-%d}"
    dr  = f"{start:%Y.%-m.%-d} — {end:%-m.%-d}"
    logger.info(f"═══ 开始运行，窗口 {win} ═══"); _log_sources()

    arts  = stage1_capture(start, end)
    deals = stage2_verify(arts, start, end, no_enrich)
    deals = round2_enrich(deals)
    deals = stage3_coverage(deals, start, end)

    in_win = [d for d in deals if d.date_status != "stale"]
    stale  = [d for d in deals if d.date_status == "stale"]
    cn_n   = len([d for d in in_win if d.region_class=="国内"])
    gl_n   = len([d for d in in_win if d.region_class=="海外"])
    logger.info(f"═══ 最终：周报项目 {len(in_win)} 个（国内{cn_n}/海外{gl_n}），旧闻剔除 {len(stale)} ═══")

    if dry:
        for d in deals:
            print(f" [{d.date_status}][{d.region_class}] {d.project_name} | {d.track} | {d.round} | {d.amount}")
        return

    db.upsert(deals, window_tag=win)
    wx, wd = weekly_paths(start, end)
    excel_exporter.write_weekly(deals, str(wx))
    word_exporter.write_word(in_win, dr, str(wd))
    master_exporter.rebuild_master()
    logger.info(f"输出完成，查看桌面 VC雷达/")

    for flag, fn_path in [("ENABLE_NOTION","storage.notion_sink:sync_notion"),
                           ("ENABLE_FEISHU","storage.feishu_sink:push_feishu"),
                           ("ENABLE_EMAIL","storage.email_sink:send_email")]:
        if os.getenv(flag) == "true":
            mod, fn = fn_path.split(":"); getattr(__import__(mod, fromlist=[fn]), fn)(in_win)
    mark_done(end)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--since"); p.add_argument("--until")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-enrich", action="store_true")
    p.add_argument("--max-articles", type=int)
    a = p.parse_args()
    run(a.since, a.until, a.dry_run, a.no_enrich, a.max_articles)
```

```python
# scheduler.py
import os
from apscheduler.schedulers.blocking import BlockingScheduler
from main import run
s = BlockingScheduler(timezone="Asia/Shanghai")
s.add_job(run, "cron", day_of_week=os.getenv("RUN_DAYS","wed,sun"),
          hour=int(os.getenv("RUN_HOUR","12")), minute=0)
print(f"⏰ {os.getenv('RUN_DAYS','wed,sun')} {os.getenv('RUN_HOUR','12')}:00 自动运行")
s.start()
```

---

## 16. 构建顺序

1. **scaffold + config**：目录 + `.env` + `.gitignore` + `config/{taxonomy,sources,settings}.py` + `models/schema.py`。
2. **LLM 层**：`llm/client.py`（`chat` + `kimi_web_search`） → 冒烟测两家 key 和 Kimi 联网搜索。
3. **采集层**：`rss_collector`（中外分流）→ `kimi_collector` → **`browser_search`**（★新，Bing CN+EN）→ `search_collector`（Tavily/Exa/博查）→ `manual` → `xhs`(桩)。
4. **处理层**：`pregate` → `window` → `dedup` → `extractor` → `merge` → `date_verify`（含 `should_verify`）→ **`enricher`**（★新）→ **`coverage_check`**（★新）。
5. **存储层**：`storage/{db,paths}.py`（全字段 DDL + 命名/归档/原子写/快照）。
6. **输出层**：先读历史周报 docx 对齐第 4 节结构 → `summary` → `word_exporter` → `excel_exporter`（周报模板）→ `master_exporter`（总库）。
7. **编排**：`main.py`（4 阶段） → `--dry-run` 自检（看日志：各阶段文章数变化、中外分布、stale 被剔、是否触发补搜）→ 完整跑出文件。
8. **看板 + 调度**：`dashboard.py` + `scheduler.py`。
9. **预留 sink**：`notion/feishu/email` 骨架，默认关。
10. **自检脚本**：`scripts/{setup_env,check_env}.py`。

---

## 17. 验收标准

- [ ] **A1** Kimi 联网搜索返回 JSON 结果，DeepSeek 抽取正常 → R9。
- [ ] **A2** `--dry-run` 输出：① 看到 `[ROUND-1]` / `[STAGE-2]` / `[ROUND-2]` / `[STAGE-3]` 日志分段；② B 轮被过滤；③ stale 被标出 → R8/R11。
- [ ] **A3** 浏览器只搜 Bing（CN/EN），代码中**无固定种子站点/VC portfolio URL 列表** → R25。
- [ ] **A4** 结果**同时含国内与海外**项目，日志显示 `国内X/海外Y` → R3/R14。
- [ ] **A5** 当 in\_window < 20 时，日志显示 `触发补搜` 且执行了 ROUND-3；≥20 时不触发 → R13。
- [ ] **A6** 当国内 <5 或海外 <5 时，分别触发定向补搜（日志中 CN/EN 补搜词可见）→ R14。
- [ ] **A7** 第二轮补全生效：日志显示 `对N个确认项目补全信息`；补全前后 deal 字段更丰富 → R12。
- [ ] **A8** 金额优先量级（「数千万元」），完全无才「未披露」，无编造 → R22。
- [ ] **A9** 预过滤门有效：日志 `预过滤+去重后` 比 `原始文章` 少 40%+ → R24。
- [ ] **A10** 桌面出 `周报/年/月/VC周报_起_至_止.{xlsx,docx}` + `00_总库/VC总库_最新.xlsx` → R5/R6/R16。
- [ ] **A11** Word：综述含中外格局；排序按 importance；粗体标题=Excel 标题列同串 → R6/R7/R21。
- [ ] **A12** 总库原子写+快照；重复运行不重复 → R16/R23。
- [ ] **A13** 无 key / 某源限流时主流程不报错（fail-soft） → 健壮性。
- [ ] **A14** `streamlit run dashboard.py` 可筛选/触发/下载 → R18。

---

## 18. Key 汇总

| Key | 必需 | 用途 |
|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | 抽取/反查/补全/补漏 |
| `MOONSHOT_API_KEY` | ✅ | **第一轮 Kimi 联网搜索**（¥0.03/次）+ 周报文笔 |
| `BOCHA_API_KEY` | ⭐强烈建议 | 第二轮/补搜国内中文兜底 |
| `EXA_API_KEY` | ⭐强烈建议 | 第二轮/补搜海外英文兜底 |
| `TAVILY_API_KEY` | ⚪可选 | 海外补充（432 限流自动跳过） |
| 微信读书账号（扫码） | ✅(已自建) | wewe-rss 公众号 RSS |
| `NOTION_*`/`FEISHU_*`/`SMTP_*` | ⚪可选 | 预留推送 |
| Anthropic Key | 🔧仅搭建 | Claude Code 用 |

---

## 19. .env 模板（v1，含新增旋钮）

```bash
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
BOCHA_API_KEY=
EXA_API_KEY=
TAVILY_API_KEY=
# WEWE_RSS_FEED=http://localhost:8001/feed/all.atom?title_include=融资|轮|天使|种子  # v1：werss_collector 改用 JWT API 直连，不再走 RSS feed
# ── 检索轮次旋钮 ──
MIN_DEALS_TOTAL=20              # 低于此数触发补搜
MIN_DEALS_CN=5                  # 国内项目最低数
MIN_DEALS_GLOBAL=5              # 海外项目最低数
MAX_SEARCH_RETRIES=1            # 最多补搜1次（防无限循环）
BROWSER_SEARCH_MAX_CN=8         # 浏览器搜索最多用几个CN词
BROWSER_SEARCH_MAX_EN=8         # 浏览器搜索最多用几个EN词
# ── 省token旋钮 ──
ENABLE_DATE_VERIFY=true
VERIFY_SCOPE=risky
VC_MAX_ARTICLES_PER_SOURCE=20
WEB_CRAWL_MAX=40
EXA_NUM_RESULTS=15
EXA_SEARCH_TYPE=auto
EXA_MAX_CHARS=2000
BOCHA_NUM_RESULTS=10
BOCHA_FRESHNESS=oneWeek
BOCHA_SUMMARY=true
# ── 调度 ──
RUN_DAYS=wed,sun
RUN_HOUR=12
# ── 输出/存储 ──
OUTPUT_ROOT=
FLAT_MODE=false
KEEP_MASTER_SNAPSHOT=true
# ── 预留（默认关）──
ENABLE_XHS=false
ENABLE_NOTION=false
NOTION_API_KEY=
NOTION_DATABASE_ID=
ENABLE_FEISHU=false
ENABLE_EMAIL=false
```

---

## 20. 给 Claude Code 的启动指令

> "读 `Claude_Code_构建规格v1.md` 和 `docs/ARCHITECTURE.md`，了解 v1 当前架构。项目名 **VC 监控agent**，四阶段流水线（ROUND-1 五路并行采集 → STAGE-2 预过滤去重抽取核查 → ROUND-2 五路定向补全 → STAGE-3 数量检查补搜）。
> 
> **v1 关键模块**：①`collectors/werss_collector.py`（JWT 认证 + 4级降级）；②`collectors/browser_search.py`（Bing CN/EN 关键词搜索）；③`processors/enricher.py`（5 路定向搜索补全）；④`processors/coverage_check.py`（数量检查+补搜决策）；⑤`preflight.py`（预检脚本）。
> 
> 预检：`python preflight.py --since YYYY-MM-DD --until YYYY-MM-DD`
> 运行：`python main.py --dry-run` 预览 → `python main.py` 完整输出。
> 验收 A1–A14：阶段分段日志、中外分布、stale 剔除、补搜触发、Excel 11 列、business 字段。"
