# VC 监控agent v1

基于 LLM 的 AI 一级市场融资雷达：四阶段流水线自动抓取海内外早期融资项目，
输出 Excel + Word 周报，并支持 Streamlit 看板。

## 架构概览（v1 四阶段流水线）

```
┌─────────────────────────────────────────────────────────────┐
│  ROUND-1（五路并行采集）                                      │
│  ├ RSS (RSSHub CN + 海外直连)                                │
│  ├ 微信公众号 (wewe-rss JWT → SQLite 时间窗口过滤)             │
│  ├ Kimi 联网搜索 (赛道感知 CN+EN 关键词)                       │
│  ├ 浏览器 Bing 搜索 (Crawl4AI + Playwright)                   │
│  └ 手动补漏链接 (data/manual_links.txt)                       │
│       ↓ 时间预过滤（窗口外旧文剔除）                             │
├─────────────────────────────────────────────────────────────┤
│  STAGE-2（预过滤 + 去重 + LLM 抽取 + 时间核查）                 │
│  ├ 关键词预过滤门（砍 50-70% 不必要调用）                       │
│  ├ 指纹去重                                                   │
│  ├ 并发 LLM 抽取 (DeepSeek-V4-Flash × 6 workers)              │
│  ├ 跨源合并                                                   │
│  └ 时间核查（Kimi 反查每项目融资公布日，stale 剔出周报）         │
│       ↓ in_window 项目                                        │
├─────────────────────────────────────────────────────────────┤
│  ROUND-2（信息补全）                                           │
│  ├ 5 路定向搜索：融资细节/团队/投资方/业务/英文备选              │
│  ├ Bocha/Exa 优先（Tavily 备用）                               │
│  ├ Web Fetch 兜底（搜不到时直抓项目 URL）                       │
│  └ LLM 补全 amount/valuation/investors/team/business/site     │
│       ↓                                                       │
├─────────────────────────────────────────────────────────────┤
│  STAGE-3（数量检查 + 补搜）                                    │
│  ├ 总数<20 / 国内<5 / 海外<5 → 触发定向补搜（最多1次）          │
│  ├ 补搜源：Kimi + 浏览器Bing + 搜索API (Bocha/Exa)             │
│  └ 对补搜结果重走 STAGE-2                                      │
│       ↓ 最终 in_window 项目列表                                │
├─────────────────────────────────────────────────────────────┤
│  输出                                                         │
│  ├ SQLite upsert (26 列，含 business 业务简介)                  │
│  ├ 周报 Excel (5 赛道 sheet，11 列)                            │
│  ├ 周报 Word (按赛道分组)                                      │
│  └ 总库 Excel (全量历史项目)                                   │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 环境初始化

```bash
python scripts/setup_env.py       # 安装依赖 + 浏览器 + 检查服务
python scripts/check_env.py       # 环境自检
```

### 预检

```bash
python preflight.py --since 2026-06-17 --until 2026-06-21
# 检查：wewe-rss 容器 + 微信登录 + 搜索API + LLM + 窗口内文章数
```

### 运行

```bash
python main.py --dry-run                          # 预览命中项目（推荐先跑）
python main.py                                     # 完整运行 → 桌面/VC雷达/
python main.py --since 2026-06-17 --no-enrich      # 跳过信息补全
python main.py --max-articles 10                   # 每源最多 10 条
```

### 定时与看板

```bash
python scheduler.py           # 定时运行（默认周三&周日 12:00）
streamlit run dashboard.py    # 本地看板
```

## 服务依赖

| 服务 | 用途 | 启动方式 |
|------|------|----------|
| wewe-rss (localhost:8001) | 微信公众号文章 | `docker start we-mp-rss` |
| RSSHub (localhost:1200) | 36氪/创业邦/量子位 RSS | `docker-compose up -d rsshub` |
| Playwright Chromium | 浏览器 Bing 搜索 | `python -m playwright install chromium` |

## API Key 配置（`.env`）

```bash
DEEPSEEK_API_KEY=...     # LLM 抽取/反查/补全（主力）
MOONSHOT_API_KEY=...     # Kimi 联网搜索 + 周报文笔
BOCHA_API_KEY=...        # 国内中文搜索（强烈建议）
EXA_API_KEY=...          # 中英文搜索兜底（强烈建议）
TAVILY_API_KEY=...       # 海外搜索备用（dev key 有限流）
```

## 数据来源

| 来源 | 覆盖 | 说明 |
|------|------|------|
| wewe-rss 微信公众号 | 国内 VC 媒体 | JWT 认证，从容器 SQLite 直接读取 |
| RSSHub | 36氪/创业邦/量子位 | localhost:1200 |
| 海外 RSS | TechCrunch/Crunchbase/VentureBeat 等 | 直连 |
| Kimi 联网搜索 | 中英文赛道关键词 | Moonshot API，¥0.03/次 |
| 浏览器 Bing | CN + EN 赛道关键词 | Crawl4AI + Playwright |
| Bocha/Exa 搜索 | 信息补全 + 补搜 | Bocha 国内优先，Exa 海外优先 |
| 手动链接 | data/manual_links.txt | 每行一个 URL |

## v1 关键特性

| 特性 | 说明 |
|------|------|
| ★ 四阶段闭环 | ROUND-1 → STAGE-2 → ROUND-2 → STAGE-3，数量不足自动补搜 |
| ★ 赛道感知搜索 | 5 大赛道（AI2C/AI2B/具身/前沿科技/ai4S）专属中英关键词 |
| ★ wewe-rss JWT 认证 | 稳定跨 Docker 重启，预检 + 管线均走 Bearer token |
| ★ 时间预过滤 | ROUND-1 末尾按窗口剔除旧文，减少后续无效 LLM 调用 |
| ★ 业务简介字段 | Deal 模型含 business，Excel 11 列含"业务简介" |
| ★ 5 路定向补全 | 融资细节/团队/投资方/业务/英文 - Bocha/Exa 优先 |
| ★ 关键词预过滤门 | LLM 抽取前正则砍 50-70% 无关文章 |
| ★ 数量检查 | 总数<20/国内<5/海外<5 触发定向补搜 |

## 项目结构

```
VC agent/
├── main.py              # 主入口（四阶段管线）
├── preflight.py         # 预检脚本
├── scheduler.py         # 定时调度
├── dashboard.py         # Streamlit 看板
├── config/
│   ├── settings.py      # 全局配置 + 环境变量
│   ├── sources.py       # RSS 源 + 赛道关键词 + 搜索词
│   └── taxonomy.py      # 赛道/tag 分类体系 + sheet 映射
├── collectors/
│   ├── werss_collector.py    # 微信公众号（JWT + SQLite）
│   ├── rss_collector.py      # RSSHub + 海外 RSS
│   ├── kimi_collector.py     # Kimi 联网搜索
│   ├── browser_search.py     # 浏览器 Bing 搜索
│   ├── search_collector.py   # Bocha/Exa/Tavily API 搜索
│   ├── web_collector.py      # URL 抓取 + 全文回源
│   ├── kimi_search_collector.py # Kimi 单项目反查
│   ├── manual.py             # 手动链接读取
│   └── xhs_collector.py      # 小红书（桩）
├── processors/
│   ├── pregate.py       # 关键词预过滤门
│   ├── dedup.py         # 指纹去重
│   ├── extractor.py     # LLM 并发抽取（Deal 结构化）
│   ├── merge.py         # 跨源合并
│   ├── date_verify.py   # 时间核查（Kimi 反查）
│   ├── enricher.py      # ROUND-2 信息补全（5 路搜索）
│   ├── coverage_check.py # 数量检查 + 补搜词构造
│   └── window.py        # 时间窗口管理
├── exporters/
│   ├── excel_exporter.py    # 周报 Excel（5 sheet × 11 列）
│   ├── word_exporter.py     # 周报 Word
│   └── master_exporter.py   # 总库 Excel
├── storage/
│   └── db.py            # SQLite 持久化（26 列 upsert）
├── llm/
│   └── client.py        # 多 Provider LLM 客户端（DeepSeek/Kimi/OpenAI）
├── models/
│   └── schema.py        # Pydantic 数据模型（Article/Deal）
├── templates/
│   └── weekly_template.xlsx  # 周报模板（11 列）
└── data/
    └── vc.sqlite         # 持久化数据库
```

## 常见问题

**Q: 只有海外项目，没有国内？**
A: 确认 wewe-rss（`docker start we-mp-rss`）和 RSSHub（`docker-compose up -d rsshub`）已启动。运行 `python preflight.py` 检查。

**Q: 浏览器搜索报错？**
A: 安装 Chromium：`python -m playwright install chromium`。未安装会自动降级。

**Q: 如何人工补漏？**
A: 编辑 `data/manual_links.txt`，每行一个 URL，下次运行自动抓取。

**Q: 搜索结果不够多？**
A: STAGE-3 自动触发补搜。可在 `.env` 调整 `MIN_DEALS_TOTAL`/`MIN_DEALS_CN`/`MIN_DEALS_GLOBAL`。

**Q: Excel 列错位了？**
A: v1 模板已修复为 11 列（含"业务简介"），重新运行即可。如果模板是旧版，删除 `templates/weekly_template.xlsx` 后重新生成。
