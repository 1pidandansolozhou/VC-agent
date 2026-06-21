# VC 监控agent 系统架构文档 v7

> 最后更新：2026-06-21  
> 项目定位：基于 LLM 的 AI 一级市场融资雷达，自动抓取海内外早期融资信息，输出周报

---

## 1. 系统概述

VC 监控agent 是一个四阶段流水线系统，自动完成"信息采集 → 项目提取 → 时间核查 → 信息补全 → 数量检查 → 周报输出"全流程。系统以 DeepSeek 为 LLM 主力，Moonshot(Kimi) 为联网搜索主力，Bocha/Exa 为搜索兜底，wewe-rss/RSSHub 为固定信息源。

### 核心设计原则

1. **多源冗余**：5 路并行采集，单一源失效不阻断管线
2. **渐进式降级**：浏览器未安装 → fallback；Tavily 432 → Bocha/Exa 顶上；wewe-rss 不可用 → SQLite 直读
3. **时间闭环**：每个项目经 LLM 反查融资公布日，窗口外旧闻自动剔除
4. **数量自发补偿**：周报项目不足时自动触发补搜，最多 1 次

---

## 2. 流水线架构

```
                            ┌──────────────────────┐
                            │   main.py run()      │
                            │   窗口: start → end   │
                            └──────────┬───────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            │                          ▼                          │
            │  ┌──────────────────────────────────────────────┐   │
            │  │         ROUND-1  五路并行采集                  │   │
            │  │                                              │   │
            │  │  ┌──────────┐ ┌──────────┐ ┌─────────────┐  │   │
            │  │  │ RSS采集   │ │ 微信公众号 │ │ Kimi联网搜索│  │   │
            │  │  │ (RSSHub  │ │(wewe-rss │ │(赛道关键词) │  │   │
            │  │  │ +海外直连)│ │ JWT+SQLite)│ │            │  │   │
            │  │  └────┬─────┘ └────┬─────┘ └──────┬──────┘  │   │
            │  │       │            │               │         │   │
            │  │  ┌────┴────────────┴───────┐ ┌─────┴──────┐  │   │
            │  │  │ 浏览器Bing搜索           │ │ 手动链接   │  │   │
            │  │  │ (Crawl4AI+Playwright)   │ │ (web抓取)  │  │   │
            │  │  └────────────┬────────────┘ └─────┬──────┘  │   │
            │  │               └──┬─────────────────┘         │   │
            │  │                  ▼                            │   │
            │  │         时间预过滤（窗口外旧文剔除）            │   │
            │  └──────────────────────────────────────────────┘   │
            │                          │                          │
            │                          ▼                          │
            │  ┌──────────────────────────────────────────────┐   │
            │  │         STAGE-2  提取+核查                    │   │
            │  │                                              │   │
            │  │  关键词预过滤门 → 指纹去重 → 回源补全文       │   │
            │  │       ↓                                      │   │
            │  │  并发 LLM 抽取 (DeepSeek-V4-Flash × 6)        │   │
            │  │       ↓                                      │   │
            │  │  跨源合并 → 时间核查 (Kimi反查每项目)          │   │
            │  │       ↓                                      │   │
            │  │  in_window 项目  │  stale 项目(剔除)          │   │
            │  └──────────────────────────────────────────────┘   │
            │                          │                          │
            │                          ▼                          │
            │  ┌──────────────────────────────────────────────┐   │
            │  │         ROUND-2  信息补全                     │   │
            │  │                                              │   │
            │  │  每项目 5 路定向搜索:                          │   │
            │  │  ├ 融资细节 (amount/valuation)               │   │
            │  │  ├ 团队背景                                   │   │
            │  │  ├ 投资方                                     │   │
            │  │  ├ 业务产品 (business)                        │   │
            │  │  └ 英文备选 (海外项目)                        │   │
            │  │       ↓                                      │   │
            │  │  LLM 补全缺失字段 + Web Fetch 兜底             │   │
            │  └──────────────────────────────────────────────┘   │
            │                          │                          │
            │                          ▼                          │
            │  ┌──────────────────────────────────────────────┐   │
            │  │         STAGE-3  数量检查+补搜                │   │
            │  │                                              │   │
            │  │  总数<20? 国内<5? 海外<5?                     │   │
            │  │       │ 是                                   │   │
            │  │       ▼                                      │   │
            │  │  定向补搜: Kimi + 浏览器Bing + Bocha/Exa      │   │
            │  │       │                                      │   │
            │  │       ▼                                      │   │
            │  │  对补搜结果重走 STAGE-2 → 合并 → ROUND-2      │   │
            │  │       │                                      │   │
            │  │       │ 否 (或达最大补搜次数)                  │   │
            │  │       ▼                                      │   │
            │  │  输出最终 in_window 项目列表                   │   │
            │  └──────────────────────────────────────────────┘   │
            │                          │                          │
            │                          ▼                          │
            │  ┌──────────────────────────────────────────────┐   │
            │  │         输出层                                │   │
            │  │                                              │   │
            │  │  SQLite upsert (26列, 按project_name去重)     │   │
            │  │  ├ 周报 Excel (5赛道 sheet × 11列)            │   │
            │  │  ├ 周报 Word (按赛道分组, Kimi文笔润色)       │   │
            │  │  ├ 总库 Excel (全量历史项目)                  │   │
            │  │  └ 归档桌面: ~/Desktop/VC雷达/               │   │
            │  └──────────────────────────────────────────────┘   │
            └─────────────────────────────────────────────────────┘
```

---

## 3. 模块详解

### 3.1 采集层 (collectors/)

| 模块 | 类型 | 说明 |
|------|------|------|
| `werss_collector.py` | 固定源 | wewe-rss 微信公众号。JWT 登录 → `/api/v1/wx/mps` 获取公众号列表 → 逐号 `/{id}.xml` 分页采集 → 按窗口时间戳过滤。含 4 级降级：JWT API → 无认证 API → RSS 列表 → 容器 SQLite 直读 |
| `rss_collector.py` | 固定源 | RSSHub (localhost:1200) + 海外 RSS 直连。中文源：36氪/创业邦/量子位；海外源：TechCrunch/Crunchbase/VentureBeat/EU-Startups/Tech.eu/Sifted |
| `kimi_collector.py` | 搜索源 | Moonshot Kimi 联网搜索。按赛道关键词分 CN+EN 两组，每词一次 API 调用 |
| `browser_search.py` | 搜索源 | Crawl4AI + Playwright。启动 Chromium → Bing CN+EN 分别搜索赛道关键词 → 解析结果页。未安装浏览器时自动降级 |
| `search_collector.py` | 搜索源 | Bocha (国内)/Exa (中英)/Tavily (海外) API 搜索。Bocha/Exa 优先，Tavily dev key 有限流 |
| `web_collector.py` | 补全文 | URL 抓取 + 全文回源。对搜索来源且 <200 字的文章选择性回源 |
| `manual.py` | 手动源 | `data/manual_links.txt` 每行一个 URL |
| `xhs_collector.py` | 预留 | 小红书采集（桩，默认返回 []） |

### 3.2 处理层 (processors/)

| 模块 | 阶段 | 说明 |
|------|------|------|
| `pregate.py` | STAGE-2 | 关键词预过滤门：正则匹配融资相关关键词（轮次/金额/投资方），砍 50-70% 不必要文章 |
| `dedup.py` | STAGE-2 | 指纹去重：基于 URL + title 相似度 |
| `extractor.py` | STAGE-2 | LLM 并发抽取。SYSTEM prompt 含完整赛道分类 + 15 条硬规则。DeepSeek-V4-Flash × 6 workers |
| `merge.py` | STAGE-2 | 跨源合并：同名项目合并 sources + 取信息最完整的字段 |
| `date_verify.py` | STAGE-2 | 时间核查。可信源(RSS/wechat)用 source_date 直接判断；非可信源(search/web/manual)调 Kimi 反查融资公布日 |
| `enricher.py` | ROUND-2 | 信息补全。每项目 5 路定向搜索 → LLM 补全 amount/valuation/investors/team/business/site |
| `coverage_check.py` | STAGE-3 | 数量检查 + 补搜决策。三指标：总数/国内数/海外数 |
| `window.py` | 入口 | 时间窗口管理 + 完成标记 |

### 3.3 导出层 (exporters/)

| 模块 | 输出 | 说明 |
|------|------|------|
| `excel_exporter.py` | 周报 Excel | 复制模板 → 按 track 分 sheet (AI2C/AI2B/具身/ai4S/前沿科技)，每 sheet 11 列 |
| `word_exporter.py` | 周报 Word | 按赛道分组，Kimi 润色每项目摘要 |
| `master_exporter.py` | 总库 Excel | SQLite 全量历史项目导出 |

### 3.4 持久层 (storage/)

| 模块 | 说明 |
|------|------|
| `db.py` | SQLite ORM。`conn()` 惰性建表+迁移；`upsert()` 按 project_name 去重，26 列；`all_rows()` / `rows_by_window()` 查询 |

### 3.5 LLM 层 (llm/)

| 模块 | 说明 |
|------|------|
| `client.py` | 多 Provider 统一客户端。支持 DeepSeek (deepseek-v4-flash/pro)、Kimi (kimi-k2.6)、OpenAI (gpt-4o-mini)。任务→模型路由，3 次指数退避重试 |

### 3.6 配置层 (config/)

| 模块 | 说明 |
|------|------|
| `settings.py` | 全局配置。DB 路径、模板路径、输出归档、时间控制、搜索配置、开关 |
| `sources.py` | RSS 源、赛道关键词 (CN+EN 双语)、补搜词、手动链接 |
| `taxonomy.py` | 赛道/Tag 分类体系 + Sheet 映射 |

---

## 4. 数据模型

### 4.1 Article（采集文章）

| 字段 | 类型 | 说明 |
|------|------|------|
| title | str | 文章标题 |
| url | str | 文章链接 |
| content | str | 正文 |
| source | str | 来源名 |
| source_type | Literal | rss / wechat / web / search / manual / xhs / weibo |
| region_hint | Literal | 国内 / 海外 / 未知 |
| published_at | datetime? | 发布时间 |
| fingerprint | str | 去重指纹 |

### 4.2 Deal（融资项目）

| 字段 | 类型 | 说明 |
|------|------|------|
| project_name | str (PK) | 项目名称 |
| track | Literal | AI2C / AI2B / 具身 / ai4S / 前沿科技 |
| sub_tag | str | 细分标签 |
| founded_year | str? | 成立年份 |
| title | str | 完整标题 |
| team | str | 团队信息 |
| round | str | 融资轮次 |
| amount | str | 融资金额（精确值或量级） |
| valuation | str? | 估值 |
| investors | str | 投资方 |
| business | str | 一句话业务描述（v7 新增） |
| region | str | 省/市 或 国家 |
| region_class | Literal | 国内 / 海外 / 未知 |
| detail | str | 150-300字详细描述 |
| importance | Literal | high / mid / low |
| official_site | str | 官网 |
| verified_date | str | 核查后的公布日期 |
| date_status | Literal | in_window / stale / unknown / skip |
| date_confidence | Literal | high / mid / low / skip |
| source_url | str | 来源 URL |
| source_date | str | 来源日期 |
| sources | List[str] | 所有来源列表 |
| first_seen_window | str | 首次出现窗口 |

---

## 5. API 依赖与降级策略

| API | 用途 | 降级方案 |
|-----|------|----------|
| DeepSeek (deepseek-v4-flash) | LLM 抽取/反查/补全 | 无 → 管线终止 |
| Moonshot (Kimi k2.6) | 联网搜索 + 文笔 | 搜索：浏览器Bing 兜底；文笔：DeepSeek 代替 |
| Bocha | 国内搜索 | Exa / Tavily |
| Exa | 英文搜索 | Tavily |
| Tavily | 备用搜索 | 忽略（dev key 常 432） |
| wewe-rss | 微信公众号 | SQLite 直读 |
| RSSHub | 国内 RSS | 跳过（海外 RSS 不受影响） |
| Playwright | 浏览器搜索 | 自动降级，不阻断 |

---

## 6. Token 用量估算（单次管线运行）

以典型窗口（4 天）为例，假设：
- ROUND-1 采集 ~350 篇原始文章
- STAGE-2 预过滤后 ~180 篇
- 抽取命中 ~25 个项目
- ~20 个 in_window 项目进入 ROUND-2

### 6.1 LLM Token（DeepSeek）

| 阶段 | 调用次数 | 每调用估算 | 小计 |
|------|---------|-----------|------|
| extract (抽取) | ~180 次 | 输入 ~3K + 输出 ~800 = 3.8K | **~684K** |
| date_verify (核查) | ~25 次 | 输入 ~1.5K + 输出 ~300 = 1.8K | **~45K** |
| enrich (补全) | ~20 次 | 输入 ~3K + 输出 ~500 = 3.5K | **~70K** |
| audit (覆盖检查) | ~5 次 | 输入 ~1K + 输出 ~300 = 1.3K | **~7K** |
| **DeepSeek 小计** | | | **~806K** |

### 6.2 Kimi 联网搜索 Token（Moonshot）

| 阶段 | 调用次数 | 每调用估算 | 小计 |
|------|---------|-----------|------|
| collect_kimi (采集) | ~35 次 | 输入 ~500 + 输出 ~1K = 1.5K | **~53K** |
| date_verify 反查 | ~20 次 | 输入 ~500 + 输出 ~500 = 1K | **~20K** |
| **Moonshot 小计** | | | **~73K** |

### 6.3 周报文笔 Token

| 阶段 | 调用次数 | 每调用估算 | 小计 |
|------|---------|-----------|------|
| word_exporter 润色 | ~20 次 | 输入 ~1.5K + 输出 ~300 = 1.8K | **~36K** |
| **Kimi 文笔小计** | | | **~36K** |

### 6.4 汇总

| 项目 | Token |
|------|-------|
| DeepSeek LLM | ~806K |
| Moonshot 联网搜索 | ~73K |
| Moonshot 文笔 | ~36K |
| **总计** | **~915K tokens/run** |

### 6.5 成本估算

| Provider | 单价 (输入/输出, 每M) | 估算 |
|----------|----------------------|------|
| DeepSeek | ¥0.5 / ¥2 | ~¥0.5-1.0 |
| Moonshot (Kimi) | ¥0 / ¥0 | ¥0（搜索token不计费） |
| Bocha | 按次 | ~¥0.1 |
| Exa | 免费额度内 | ¥0 |
| **单次总成本** | | **~¥0.6-1.1** |

> 注：DeepSeek-V4-Flash 价格极低（¥0.5/M 输入，¥2/M 输出），是管线主要成本来源。一次完整运行约消耗 80-100 万 tokens，成本不超过 ¥1.5。

---

## 7. 时间窗口与日期处理

```
窗口计算：
  --since YYYY-MM-DD (默认: 今天-4天)
  --until YYYY-MM-DD (默认: 今天)

时间核查策略：
  RSS/wechat 源 → source_date 直接判断（高置信度）
  search/web/manual 源 → Kimi 反查融资公布日（需搜索验证）

date_status 分类：
  in_window  → 融资时间在窗口内 → 进入周报
  stale      → 融资时间早于窗口 → 剔除出周报（但保留在 DB）
  unknown    → 无法确定时间 → 保留在周报
  skip       → 跳过核查
```

---

## 8. 赛道分类体系

| 赛道 | 标签数 | 搜索词数(CN+EN) | 说明 |
|------|--------|----------------|------|
| AI2C | 6 | 3+3 | AI 消费：内容创作/陪伴社交/硬件终端/健康/生活/效率 |
| AI2B | 6 | 3+3 | AI 企业：Agent/金融/数据BI/知识管理/安全/行业 |
| 具身 | 6 | 5+5 | 机器人：整机/大脑/灵巧操作/感知/数据/场景 |
| 前沿科技 | 6 | 3+6 | 芯片/新型计算/EDA/脑机/空间3D/航天能源 |
| ai4S | 6 | 3+5 | AI for Science：制药/合成生物/新材料/气候/基模/工具 |

---

## 9. 部署与运维

### 服务依赖

```
┌─────────────┐    ┌─────────────┐    ┌──────────────┐
│ wewe-rss    │    │  RSSHub     │    │  Playwright  │
│ :8001       │    │  :1200      │    │  Chromium    │
│ Docker      │    │  Docker     │    │  本地安装    │
└─────────────┘    └─────────────┘    └──────────────┘
```

### 定时运行

```bash
# scheduler.py 默认周三&周日 12:00 运行
# 环境变量 RUN_DAYS / RUN_HOUR 可调整
python scheduler.py
```

### 输出归档

```
~/Desktop/VC雷达/
├── 2026.6.18 — 6.21_周报.xlsx      # 当次周报 Excel
├── 2026.6.18 — 6.21_周报.docx      # 当次周报 Word
└── VC项目总库.xlsx                  # 全量历史（每次覆盖）
```

### 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DEEPSEEK_API_KEY | 必填 | LLM 主力 |
| MOONSHOT_API_KEY | 必填 | Kimi 搜索+文笔 |
| BOCHA_API_KEY | 强烈建议 | 国内搜索 |
| EXA_API_KEY | 强烈建议 | 海外搜索 |
| VC_DB_PATH | data/vc.sqlite | 数据库路径 |
| VC_TEMPLATE_PATH | templates/weekly_template.xlsx | 模板路径 |
| MIN_DEALS_TOTAL | 20 | 总数阈值 |
| MIN_DEALS_CN | 5 | 国内阈值 |
| MIN_DEALS_GLOBAL | 5 | 海外阈值 |
| EXTRACT_WORKERS | 6 | LLM 并发数 |

---

## 10. 版本历史

| 版本 | 日期 | 关键变化 |
|------|------|----------|
| v0 | - | 初始版本，提交至 GitHub |
| v1 | 2026-06-21 | wewe-rss JWT 认证，business 字段，Excel 11 列模板，Pipeline 稳定化 |
