# VC 一级市场项目雷达 v6

按 `Claude_Code_构建规格v6.md` 实现的本地 Agent：四阶段流水线抓取海内外早期融资项目，
输出 Excel + Word 周报，并提供 Streamlit 看板。

## 架构概览（v6 四阶段）

```
第一轮 ROUND-1（并行采集）
  A. Kimi联网搜索  × 赛道感知CN+EN关键词
  B. 浏览器Bing搜索 × CN+EN关键词（Crawl4AI+Playwright，不爬固定站点）
  C. RSS（RSSHub 中文 + 海外直连 + 微信公众号）
  D. 手动补漏链接
       ↓ 合并 → 预过滤门 → 指纹去重 → 并发LLM抽取 → 跨源合并
第二轮 STAGE-2（时间核查）
      对每个项目反查真实融资公布日，stale 剔出周报
       ↓ 仅 in_window 项目
第三轮 ROUND-2（信息补全）
      定向搜索补齐 amount/valuation/investors/team/official_site 缺失字段
       ↓
第四轮 STAGE-3（数量检查 + 补搜）
      总数<20 / 国内<5 / 海外<5 → 触发补搜（最多1次）
       ↓ 最终 in_window 项目列表
SQLite upsert → 周报Excel + 周报Word + 总库Excel → 桌面归档
```

## 快速开始

### 方式一：一键初始化（推荐）

```bash
python scripts/setup_env.py      # 安装依赖+浏览器+启动 RSSHub+检查 wewe-rss
python scripts/check_env.py      # 环境自检
```

### 方式二：手动分步

1. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

2. 安装 Chromium 浏览器（浏览器搜索必须）
   ```bash
   python -m playwright install chromium
   ```
   
   > 安装后 `main.py` 会用 Crawl4AI + Playwright 在 Bing CN/EN 上做真实关键词搜索。
   > 未安装时自动 fallback 降级，不阻断主流程但搜索结果变少。

3. 启动 RSSHub（中文 RSS 源必须）
   ```bash
   docker-compose up -d rsshub
   ```
   
   > 中文源（36氪、投资界、创业邦等）依赖 RSSHub。未启动时只抓到海外直连源。

4. 启动 wewe-rss（微信公众号源必须）
   > 默认地址 `http://localhost:8001/feed/all.atom`，可在 `.env` 修改 `WEWE_RSS_FEED`。

### 配置 API Key（`.env`）

```bash
DEEPSEEK_API_KEY=...        # LLM 抽取 / 反查 / 补全
MOONSHOT_API_KEY=...        # Kimi 联网搜索（第一轮主力）+ 周报文笔
TAVILY_API_KEY=...          # 海外/英文搜索兜底（可选）
EXA_API_KEY=...             # 中英文搜索兜底（强烈建议）
BOCHA_API_KEY=...           # 国内中文搜索兜底（强烈建议）
```

### 运行

```bash
python main.py --dry-run                         # 预览命中项目（推荐先跑这个验证）
python main.py                                   # 完整运行并生成文件到桌面
python main.py --since 2026-06-09 --no-enrich    # 跳过慢速回源补全
python main.py --max-articles 10                 # 每 RSS 源最多 10 条
```

### 定时与看板

```bash
python scheduler.py          # 按 RUN_DAYS/RUN_HOUR（默认周三&周日 12:00）自动运行
streamlit run dashboard.py   # 本地看板
```

## v6 相对之前的关键变化

| 变化 | 说明 |
|------|------|
| ★ **四阶段流水线** | ROUND-1 → STAGE-2 → ROUND-2 → STAGE-3，每步有独立日志 |
| ★ **赛道感知关键词** | 5 大赛道分别有专属搜索词集，CN+EN 双语分开 |
| ★ **浏览器只做关键词搜索** | Crawl4AI+Playwright 在 Bing CN/EN 做关键词搜索，不定向爬固定站点 |
| ★ **数量检查 + 补搜** | 总数<20 / 国内<5 / 海外<5 时触发定向补搜 |
| ★ **第二轮信息补全** | 对已确认项目定向搜索补齐缺失字段 |
| ★ **关键词预过滤门** | LLM 抽取前正则过滤，砍 50-70% 不必要的调用 |

## 数据来源说明（v6）

1. **第一轮采集**（四路并行，同时启动）：
   - **Kimi 联网搜索**：赛道感知 CN+EN 关键词，¥0.03/次
   - **浏览器 Bing 搜索**：Crawl4AI+Playwright，Bing CN + Bing EN，赛道关键词
   - **RSS**（RSSHub 中文 + 海外直连 + 微信公众号）
   - **手动补漏链接**：`data/manual_links.txt` 每行一个 URL
2. **第二轮补全**：Tavily + Exa + 博查 定向搜索每个项目的融资信息
3. **补搜**：数量不足时用更宽的关键词再搜一轮

## 配置参数

见 `.env` 文件中的注释和 `Claude_Code_构建规格v6.md` 第 19 节。

## 常见问题

**Q: 为什么只有海外项目，没有国内项目？**
A: 国内源依赖 RSSHub（localhost:1200）和 wewe-rss（默认 localhost:8001）。
运行 `main.py` 时第一步会显示本地源状态。请确保：
   ```bash
   docker-compose up -d rsshub
   ```
   且 wewe-rss 容器已启动。同时 Kimi 联网搜索 CN 关键词也是国内项目的主要来源。

**Q: 浏览器搜索需要安装什么？**
A: v6 使用 Crawl4AI + Playwright：
   ```bash
   python -m playwright install chromium
   ```
   未安装时自动降级，不会阻断主流程，但搜索结果变少。

**Q: 如何人工补漏？**
A: 在 `data/manual_links.txt` 中每行添加一个 URL，下次运行会自动抓取。

**Q: 搜索结果不够多怎么办？（<20 个）**
A: v6 内置补搜机制：当周报项目 <20 / 国内 <5 / 海外 <5 时自动触发补搜。
可在 `.env` 调整 `MIN_DEALS_TOTAL`、`MIN_DEALS_CN`、`MIN_DEALS_GLOBAL` 阈值。

**Q: 和旧版（v4/v5）有什么区别？**
A: 见上文"v6 相对之前的关键变化"表格。最核心的改变是：
- 不再爬固定种子站点
- 改为 4 阶段流水线 + 数量检查闭环
- 关键词按赛道分组（而非通用列表）
