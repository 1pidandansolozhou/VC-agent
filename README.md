# VC 监控agent v2.2

基于 LLM 的 AI 一级市场融资雷达：wewe-rss 单源采集 + 智能补全 + 每日自动运行，
输出 Excel + Word 周报。

## 架构概览（v2.2 四步管线）

```
┌─────────────────────────────────────────────────────────────┐
│  [0/3] 预热（Preflight v2.1）                                 │
│  ├ Docker Desktop 自启 + wewe-rss 容器自启（start 不 restart）  │
│  ├ 微信扫码登录（90s 超时）                                    │
│  ├ ★ 数据新鲜度检测 → 滞后则全量刷新 → 轮询等数据到位（5min）    │
│  └ 窗口内文章数验证                                           │
│       ↓                                                       │
├─────────────────────────────────────────────────────────────┤
│  [1/3] 采集 — wewe-rss 全量（~53个VC公众号）                   │
│  ├ JSON API 分页（limit=100）                                 │
│  ├ 客户端时间窗口过滤（前一天 00:00 → 当天 23:59）              │
│  └ 连续3页全过期 → 停止分页                                    │
│       ↓                                                       │
├─────────────────────────────────────────────────────────────┤
│  [2/3] 抽取 — LLM 集中抽取                                    │
│  ├ 指纹去重（标题+URL MD5）                                    │
│  ├ DeepSeek-V4-Flash 并发抽取（6 workers）                     │
│  └ 同名合并 → 公众号源全部信任（date_status=in_window）         │
│       ↓                                                       │
├─────────────────────────────────────────────────────────────┤
│  [2.5/3] ★ 智能补全（仅信息不足项目）                          │
│  ├ 充分性检查: amount/valuation/investors/team/business/       │
│  │              official_site/detail(<80字触发)               │
│  ├ 充足 → 跳过                                                │
│  └ 不足 → Kimi 联网搜索 → DuckDuckGo/Bing 浏览器兜底          │
│          → DeepSeek 结构化提取 → 填充缺失字段                  │
│       ↓                                                       │
├─────────────────────────────────────────────────────────────┤
│  [3/3] 输出                                                   │
│  ├ SQLite upsert（26 列）                                     │
│  ├ 周报 Excel（5 赛道 sheet，11 列）                           │
│  ├ 周报 Word（按赛道分组 + DeepSeek AI 摘要）                   │
│  └ 总库 Excel（全量历史项目）                                  │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 前置条件

- Docker Desktop（wewe-rss 容器）
- Python 3.13 + Conda
- DeepSeek API Key（LLM 抽取/摘要）
- Moonshot/Kimi API Key（联网搜索补全）

### 运行

```bash
python main.py                    # 日常运行（昨天+今天）
python main.py --since 2026-06-20 # 补抓指定日期起
python main.py --dry-run          # 预览不写入
```

### 定时调度

```bash
python scheduler.py    # 每日 12:00 自动运行
```

## 服务依赖

| 服务 | 用途 | 启动方式 |
|------|------|----------|
| wewe-rss (localhost:8001) | 微信公众号文章（唯一数据源） | preflight 自动 `docker start` |
| Docker Desktop | 运行 wewe-rss 容器 | preflight 自动启动 |

## API Key 配置（`.env`）

```bash
DEEPSEEK_API_KEY=...     # LLM 抽取/摘要（主力）
MOONSHOT_API_KEY=...     # Kimi 联网搜索（智能补全）
OPENAI_API_KEY=...       # GPT 备用路由（可选）
```

## 数据来源

| 来源 | 覆盖 | 说明 |
|------|------|------|
| wewe-rss 微信公众号 | ~53 个 VC 公众号 | 唯一数据源，JWT 认证 JSON API |
| Kimi 联网搜索 | 智能补全 | 仅对信息不足项目触发 |
| DuckDuckGo/Bing | 搜索兜底 | Kimi 无结果时自动降级 |

## v2.2 关键特性

| 特性 | 说明 |
|------|------|
| ★ 每日运行 | 每天执行，窗口=前一天00:00→当天23:59 |
| ★ Docker 自启 | preflight 自动启动 Docker Desktop + wewe-rss 容器 |
| ★ 数据新鲜度检测 | 检查最新文章日期 → 滞后则刷新 → 轮询等待数据到位 |
| ★ 微信 session 保护 | docker start（不 restart），避免微信掉线 |
| ★ 智能补全 | 信息不足项目自动 Kimi 联网搜索 + 浏览器兜底 |
| ★ 浏览器搜索兜底 | DuckDuckGo → Bing 多级降级，无 API key 依赖 |
| ★ LLM 周报摘要 | DeepSeek-V4-Pro 生成 250-350 字综述 |
| ★ 窗口对齐整天 | start/end 强制对齐 00:00:00 / 23:59:59 |

## 项目结构

```
VC agent/
├── main.py                  # 主入口（四步管线）
├── preflight.py             # 独立预检脚本
├── scheduler.py             # 定时调度（每日）
├── dashboard.py             # Streamlit 看板
├── config/
│   ├── settings.py          # 全局配置
│   ├── sources.py           # 手动链接配置
│   └── taxonomy.py          # 赛道/tag 分类体系
├── collectors/
│   ├── werss_collector.py   # ★ 微信公众号采集（JSON API）
│   ├── kimi_search_collector.py # Kimi 联网搜索
│   └── ...                  # 其他采集器（v2 已停用，保留不删）
├── processors/
│   ├── preflight.py         # ★ 预热模块（Docker+微信+数据新鲜度）
│   ├── dedup.py             # 指纹去重
│   ├── extractor.py         # LLM 并发抽取
│   ├── merge.py             # 跨源合并
│   ├── enricher.py          # ★ 智能补全（Kimi搜索+浏览器兜底）
│   └── window.py            # 时间窗口管理
├── exporters/
│   ├── excel_exporter.py    # 周报 Excel
│   ├── word_exporter.py     # 周报 Word（含 AI 摘要）
│   ├── summary.py           # ★ LLM 周报摘要生成
│   └── master_exporter.py   # 总库 Excel
├── storage/
│   └── db.py                # SQLite 持久化
├── llm/
│   └── client.py            # 多 Provider LLM 客户端
├── models/
│   └── schema.py            # Pydantic 数据模型
├── scripts/
│   ├── check_articles.py    # 数据库检查
│   └── refresh_and_check.py # 公众号刷新测试
├── data/
│   ├── vc.sqlite            # 持久化数据库
│   └── state.json           # 运行状态
└── templates/
    └── weekly_template.xlsx # 周报模板
```

## 版本更新

### v2.2 (2026-06-25)
- **智能补全**: 信息不足项目自动 Kimi 联网搜索 + DuckDuckGo/Bing 浏览器兜底
- **充分性判断**: 新增 detail 字段长度检查（<80 字符触发补全）
- **Kimi → DeepSeek**: 周报摘要从 Kimi(kimi-k2.6) 切换到 DeepSeek(deepseek-v4-pro)，Kimi 专用于联网搜索

### v2.1 (2026-06-25)
- **窗口修复**: get_window() 对齐到整天边界（昨天 00:00 → 今天 23:59）
- **数据新鲜度检测**: preflight 检查最新文章日期 → 滞后则刷新 → 轮询等待
- **容器保护**: docker start 替代 restart，保护微信 session 不丢失
- **LLM 修复**: write 任务从 Kimi（欠费）切换到 DeepSeek（余额 ¥12.76）

### v2.0 (2026-06-25)
- **单源简化**: 删除海外 RSS/RSSHub/Bing 搜索/Kimi 搜索/Bocha/Exa/Tavily
- **仅 wewe-rss**: ~53 个微信公众号为唯一数据源
- **每日运行**: 调度器改为每天执行
- **Docker 自启**: preflight 自动管理 Docker Desktop + wewe-rss 容器
- **微信扫码**: 管线中途弹二维码等用户扫码登录

### v1.0 (2026-06-17)
- 五路并行采集：RSS + 微信公众号 + Kimi + Bing + 手动链接
- 四阶段闭环：采集 → 抽取核查 → 补全 → 数量检查补搜
- Bocha/Exa/Tavily 搜索 API + 浏览器 Bing 搜索
- Streamlit 看板

## 常见问题

**Q: 没有抓到我关注的公众号文章？**
A: 确认 wewe-rss 容器运行中且微信已登录。管线会在 preflight 阶段自动检测数据新鲜度并刷新。

**Q: 微信扫码超时了？**
A: 扫码窗口 90 秒。超时后管线继续用现有数据运行。可在 `http://localhost:8001` 手动登录后重新运行。

**Q: 想关闭智能补全？**
A: 设置环境变量 `ENABLE_ENRICH=false`，或在 `.env` 中配置。

**Q: 如何人工补漏？**
A: 编辑 `data/manual_links.txt`，每行一个 URL，或使用 `--since YYYY-MM-DD` 补抓历史数据。
