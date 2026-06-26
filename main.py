"""
VC 监控agent · v2.2 单源日常管线

架构：
  [0/3] 预热 — Docker自启 + wewe-rss容器 + 微信登录 + 数据新鲜度检测
  [1/3] 采集 — wewe-rss 全量采集（~40个公众号，前一天+当天）
  [2/3] 抽取 — LLM集中抽取 → 去重合并（公众号源全部信任）
  [2.5/3] 补全 — Kimi联网搜索补全信息不足的项目
  [3/3] 输出 — SQLite + Excel周报 + Word周报 + 总库
"""

import argparse
import json
import os

from dotenv import load_dotenv

load_dotenv()

from loguru import logger

from config.settings import (
    ENABLE_ENRICH,
    EXTRACT_WORKERS,
    OUTPUT_ROOT,
)
from collectors import werss_collector
from exporters.excel_exporter import write_weekly
from exporters.master_exporter import rebuild_master
from exporters.word_exporter import write_word
from models.schema import Deal
from processors import extractor, merge
from processors.dedup import dedup, reset_seen, dedup_against_db
from processors.preflight import ensure_services_ready
from processors.window import get_window, mark_done
from storage import db
from storage.paths import log_path, weekly_paths

logger.add(str(log_path()), rotation="2 MB", encoding="utf-8")


def _log(msg: str):
    print(msg, flush=True)
    logger.info(msg)


# ═══════════════════════════════════════════════════════════════
# Stage 1：微信公众号全量采集
# ═══════════════════════════════════════════════════════════════

def stage1_capture(start, end):
    """从 wewe-rss 全量采集所有公众号窗口内文章。"""
    _log("[1/3] 微信公众号全量采集（~40个公众号，前一天+当天）")
    arts = werss_collector.collect_werss(start, end)
    _log(f"  [1/3] 采集完成: {len(arts)} 篇文章")
    return arts


# ═══════════════════════════════════════════════════════════════
# Stage 2：LLM 抽取 + 去重合并
# ═══════════════════════════════════════════════════════════════

def stage2_extract(arts):
    """文章去重 → LLM批量抽取 → 跨源合并。公众号源全部信任时间。"""
    _log("[2/3] LLM 抽取早期项目")

    # 去重
    arts = dedup(arts)
    _log(f"  [2/3] 去重后 {len(arts)} 篇")

    # LLM 批量抽取
    total = len(arts)
    if total == 0:
        _log("  [2/3] 无文章可抽取")
        return []

    if total > 1:
        _log(f"  [2/3] 并发抽取中（{EXTRACT_WORKERS} workers）...")
    deals = extractor.extract_all(arts, workers=EXTRACT_WORKERS)

    # 合并同名项目
    deals = merge.merge(deals)
    _log(f"  [2/3] 合并后 {len(deals)} 个项目")

    # ★ v3: DB去重 — 跳过已入库项目（Excel导入基线 + 历史管线输出）
    before = len(deals)
    deals = dedup_against_db(deals)
    if before != len(deals):
        _log(f"  [2/3] DB去重: {before} → {len(deals)} (跳过 {before - len(deals)} 个)")

    # 公众号源全部信任 — 直接标 in_window
    for d in deals:
        d.date_status = "in_window"
        d.date_confidence = "high"
        d.verified_date = d.source_date or ""

    in_window = len([d for d in deals if d.date_status != "stale"])
    _log(f"  [2/3] 有效项目: {in_window} 个（公众号源全部信任）")

    return deals


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def run(since=None, until=None, dry=False, max_articles=None):
    if max_articles:
        os.environ["VC_MAX_ARTICLES_PER_SOURCE"] = str(max_articles)

    start, end = get_window(since, until)
    win = f"{start:%Y-%m-%d}_至_{end:%Y-%m-%d}"
    dr = f"{start.year}.{start.month}.{start.day} — {end.month}.{end.day}"

    _log(f"{'='*50}")
    _log(f"  VC 监控agent v2 | 窗口 {win}")
    _log(f"  数据源：微信公众号（wewe-rss，约40个VC公众号）")
    _log(f"{'='*50}")

    # 每次运行清空去重历史
    reset_seen()

    # ── 预热：Docker + 容器 + 微信登录 + 公众号刷新 ──
    _log("[0/3] 服务预热...")
    ensure_services_ready(start, end)

    # ── Stage 1：采集 ──
    arts = stage1_capture(start, end)

    # ── Stage 2：抽取 ──
    deals = stage2_extract(arts)

    # ── Stage 2.5：智能补全（Kimi 联网搜索缺失信息）──
    if ENABLE_ENRICH:
        from processors.enricher import enrich_all
        in_win_before = len([d for d in deals if d.date_status != "stale"])
        _log(f"[2.5/3] 智能补全 — 检查 {in_win_before} 个项目，不足者 Kimi 联网搜索...")
        deals = enrich_all(deals)
        _log(f"  [2.5/3] 补全完成: {in_win_before} 个项目已检查")

    # ── 最终统计 ──
    in_win = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    cn_n = len([d for d in in_win if d.region_class == "国内"])
    gl_n = len([d for d in in_win if d.region_class == "海外"])
    _log(f"{'='*50}")
    _log(f"  最终：周报项目 {len(in_win)} 个（国内{cn_n}/海外{gl_n}）")
    if stale:
        _log(f"  旧闻剔除 {len(stale)} 个")
    _log(f"{'='*50}")

    # ── Dry-run 模式 ──
    if dry:
        _log("\n--- DRY RUN 结果 ---")
        for d in deals:
            _log(
                f"  [{d.date_status}][{d.region_class}] {d.project_name} | "
                f"{d.track} | {d.round} | {d.amount} | 来源:{d.sources}"
            )
        return

    # ── 输出 ──
    _log("[3/3] 写入 SQLite / 生成周报+总库...")
    db.upsert(deals, window_tag=win)

    weekly_deals = deals
    if not deals:
        rows = db.rows_by_window(win)
        weekly_deals = []
        for r in rows:
            fields = {k: v for k, v in r.items() if k in Deal.model_fields}
            fields["sources"] = json.loads(r["sources"]) if r.get("sources") else []
            weekly_deals.append(Deal(**fields))

    weekly_in_win = [d for d in weekly_deals if d.date_status != "stale"]
    wx, wd = weekly_paths(start, end)
    write_weekly(weekly_deals, str(wx))
    write_word(weekly_in_win, dr, str(wd))
    mx = rebuild_master()
    _log(f"  周报: {wx.name} / {wd.name}")
    _log(f"  总库: {mx}")
    _log(f"  输出目录：{OUTPUT_ROOT}")

    # 预留 Sink
    if os.getenv("ENABLE_NOTION", "false").lower() == "true":
        from storage.notion_sink import sync_notion
        sync_notion(os.getenv("NOTION_DATABASE_ID"))
    if os.getenv("ENABLE_FEISHU", "false").lower() == "true":
        from storage.feishu_sink import push_feishu
        push_feishu(in_win)
    if os.getenv("ENABLE_EMAIL", "false").lower() == "true":
        from storage.email_sink import send_email
        send_email([str(wx), str(wd)])

    mark_done(end)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-enrich", action="store_true")  # v2: 保留参数兼容但不再使用
    p.add_argument("--max-articles", type=int)
    a = p.parse_args()
    run(a.since, a.until, a.dry_run, a.max_articles)
