"""
VC 一级市场项目雷达 · v1 四阶段流水线

阶段：
  1. stage1_capture  — 并行捕获（RSS + Kimi + Bing浏览器 + 手动链接）
  2. stage2_verify   — 预过滤 → 去重 → 选择性回源 → 抽取 → 合并 → 时间核查
  3. round2_enrich   — 项目信息补全（Tavily/Exa/博查定向搜索补齐缺失字段）
  4. stage3_coverage — 数量检查（≤20/国内≤5/海外≤5 → 补搜）
"""

import argparse
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from loguru import logger

from config.settings import (
    ENABLE_ENRICH,
    EXTRACT_WORKERS,
    MAX_SEARCH_RETRIES,
    MIN_DEALS_CN,
    MIN_DEALS_GLOBAL,
    MIN_DEALS_TOTAL,
    OUTPUT_ROOT,
)
from config.sources import all_cn_queries, all_en_queries
from collectors import browser_search, kimi_collector, rss_collector, web_collector, werss_collector
from collectors.manual import read_manual_links
from collectors.xhs_collector import collect_xhs
from exporters.excel_exporter import write_weekly
from exporters.master_exporter import rebuild_master
from exporters.word_exporter import write_word
from models.schema import Deal
from processors import (
    coverage_check,
    date_verify,
    dedup,
    enricher,
    extractor,
    merge,
    pregate,
)
from processors.window import get_window, mark_done
from storage import db
from storage.paths import log_path, weekly_paths

logger.add(str(log_path()), rotation="2 MB", encoding="utf-8")


def _log(msg: str):
    print(msg, flush=True)
    logger.info(msg)


def _host_port(url: str) -> tuple[str, int]:
    p = urlparse(url)
    host = p.hostname or "localhost"
    port = p.port or (443 if p.scheme == "https" else 80)
    return host, port


def _source_reachable(url: str, timeout: float = 2.0) -> bool:
    host, port = _host_port(url)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _log_sources():
    """检查本地服务（RSSHub / wewe-rss）状态。"""
    from config.sources import RSS_FEEDS_CN

    for name, url in RSS_FEEDS_CN.items():
        if "localhost" in url or "127.0.0.1" in url:
            status = "在线✓" if _source_reachable(url) else "未启动—可能少抓国内"
            _log(f"  {name}: {status}")

    # 单独检查 wewe-rss（微信公众号，改用 werss_collector）
    werss_url = "http://localhost:8001/feed/all.atom"
    status = "在线✓" if _source_reachable(werss_url) else "未启动—公众号数据为空"
    _log(f"  微信公众号(wewe-rss): {status}")


# ─── Stage 1：并行捕获 ───────────────────────────────────────

def stage1_capture(start: datetime, end: datetime) -> list:
    """五路并行采集：RSS + 微信公众号全量 + Kimi联网 + 浏览器Bing + 手动链接。"""
    _log("[ROUND-1] 启动并行采集：RSS + 微信公众号 + Kimi + 浏览器Bing + 手动链接")

    arts = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_rss = ex.submit(rss_collector.collect_rss, start, end)
        f_werss = ex.submit(werss_collector.collect_werss, start, end)
        f_kimi = ex.submit(kimi_collector.collect_kimi)
        f_brow = ex.submit(
            browser_search.browser_keyword_search,
            all_cn_queries(),
            all_en_queries(),
        )
        f_manual = ex.submit(lambda: web_collector.crawl_urls(read_manual_links()))

        arts.extend(f_rss.result())
        arts.extend(f_werss.result())
        arts.extend(f_kimi.result())
        arts.extend(f_brow.result())
        arts.extend(f_manual.result())

    arts += collect_xhs()  # 桩，默认 []

    # ★ v1: 时间预过滤 — 只保留窗口内的文章（published_at 为 None 的保留，交给后续判断）
    in_window_arts = []
    stale_count = 0
    for a in arts:
        if a.published_at is None:
            in_window_arts.append(a)  # 无日期的保留（搜索源通常没有日期）
        elif start <= a.published_at <= end:
            in_window_arts.append(a)
        else:
            stale_count += 1
    if stale_count:
        _log(f"  [ROUND-1] 时间预过滤：剔除 {stale_count} 篇窗口外旧文，保留 {len(in_window_arts)} 篇")
    arts = in_window_arts

    _log(f"  [ROUND-1] 原始文章 {len(arts)} 篇（其中微信公众号 {len([a for a in arts if a.source_type=='wechat'])} 篇）")
    return arts


# ─── Stage 2：时间核查+去重+提取 ────────────────────────────

def stage2_verify(arts: list, start: datetime, end: datetime, no_enrich: bool = False) -> list:
    """预过滤 → 去重 → 选择性回源 → 并发抽取 → 合并 → 时间核查。"""
    _log("[STAGE-2] 预过滤+去重+抽取+时间核查")

    # 关键词预过滤门（砍 50-70%）
    arts = pregate.pre_gate(arts)
    arts = dedup.dedup(arts)
    _log(f"  [STAGE-2] 预过滤+去重后 {len(arts)} 篇")

    # 选择性回源补全文（仅搜索来且 <200 字的）
    _log("  选择性回源补全文...")
    web_collector.enrich_fulltext(arts)

    # 并发 LLM 抽取
    _log("  LLM 并发抽取早期项目...")
    total = len(arts)
    all_deals = []
    if total > 0:
        completed = 0
        with ThreadPoolExecutor(max_workers=min(EXTRACT_WORKERS, total)) as pool:
            futures = {pool.submit(extractor.extract, a): a for a in arts}
            for fut in as_completed(futures):
                completed += 1
                if total > 20 and (completed % 10 == 0 or completed == total):
                    _log(f"    抽取进度: {completed}/{total}")
                try:
                    all_deals.extend(fut.result())
                except Exception as e:
                    _log(f"    抽取异常: {e}")

    deals = merge.merge(all_deals)
    _log(f"  [STAGE-2] 命中早期项目: {len(deals)} 个")

    # 时间核查
    for d in deals:
        date_verify.verify(d, start, end)
    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    _log(f"  [STAGE-2] 时间核查：in_window={len(in_w)}，stale={len(stale)}")
    if stale:
        _log(f"    旧闻: {[s.project_name for s in stale]}")
    return deals


# ─── Round 2：项目信息补全 ──────────────────────────────────

def round2_enrich(deals: list) -> list:
    """对 in_window 项目定向补全 amount/valuation/investors/team/official_site 缺失字段。"""
    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    _log(f"[ROUND-2] 对 {len(in_w)} 个确认项目补全信息（Tavily/Exa/博查定向搜索）")
    enriched = enricher.enrich_all(in_w)
    return enriched + stale


# ─── Stage 3：数量检查 + 补搜 ─────────────────────────────

def stage3_coverage(deals: list, start: datetime, end: datetime, retry_count: int = 0) -> list:
    """检查数量，不足时触发补搜（最多 MAX_SEARCH_RETRIES 次）。"""
    reasons = coverage_check.should_retry(deals)
    max_retries = MAX_SEARCH_RETRIES

    if not reasons or retry_count >= max_retries:
        if reasons:
            _log(
                f"[STAGE-3] ⚠ 数量不足（{reasons}），已达最大补搜次数 {max_retries}，输出现有结果"
            )
        in_w = [d for d in deals if d.date_status != "stale"]
        cn_n = len([d for d in in_w if d.region_class == "国内"])
        gl_n = len([d for d in in_w if d.region_class == "海外"])
        _log(f"[STAGE-3] 最终：周报项目 {len(in_w)} 个（国内{cn_n}/海外{gl_n}）")
        return deals

    _log(f"[STAGE-3] ▶ 触发补搜，原因：{reasons}（第 {retry_count + 1}/{max_retries} 次）")
    cn_qs, en_qs = coverage_check.build_retry_queries(deals)
    _log(f"  [ROUND-3] 补搜词：CN={cn_qs}，EN={en_qs}")

    # 并行补搜：Kimi + 浏览器Bing + 搜索API
    extra = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_kimi = ex.submit(kimi_collector.collect_kimi_with_queries, cn_qs + en_qs)
        f_brow = ex.submit(
            browser_search.browser_keyword_search, cn_qs, en_qs
        )
        f_api = ex.submit(
            lambda: search_all_retry(cn_qs + en_qs)
        )
        extra.extend(f_kimi.result())
        extra.extend(f_brow.result())
        extra.extend(f_api.result())

    _log(f"  [ROUND-3] 补搜新增 {len(extra)} 条原始文章")

    # 对新增文章走 stage2_verify + merge
    extra_deals = stage2_verify(extra, start, end)
    merged = merge.merge(deals + extra_deals)
    merged = enricher.enrich_all(merged)
    return stage3_coverage(merged, start, end, retry_count + 1)


def search_all_retry(qs: list) -> list:
    """补搜时专用的搜索 API 调用。"""
    from collectors.search_collector import search_all
    return search_all(qs)


# ─── 主入口 ────────────────────────────────────────────────

def run(since=None, until=None, dry=False, no_enrich=False, max_articles=None):
    if max_articles:
        os.environ["VC_MAX_ARTICLES_PER_SOURCE"] = str(max_articles)

    start, end = get_window(since, until)
    win = f"{start:%Y-%m-%d}_至_{end:%Y-%m-%d}"
    dr = f"{start.year}.{start.month}.{start.day} — {end.month}.{end.day}"

    _log(f"{'='*50}")
    _log(f"  VC 雷达 v1 运行开始 | 窗口 {win}")
    _log(f"{'='*50}")

    # 本地源检查
    _log("[0/8] 本地源状态检查...")
    _log_sources()

    # Stage 1：并行捕获
    arts = stage1_capture(start, end)

    # Stage 2：时间核查
    deals = stage2_verify(arts, start, end, no_enrich)

    # Round 2：项目信息补全
    deals = round2_enrich(deals)

    # Stage 3：数量检查 + 补搜
    deals = stage3_coverage(deals, start, end)

    # 最终统计
    in_win = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    cn_n = len([d for d in in_win if d.region_class == "国内"])
    gl_n = len([d for d in in_win if d.region_class == "海外"])
    _log(f"{'='*50}")
    _log(f"  最终：周报项目 {len(in_win)} 个（国内{cn_n}/海外{gl_n}），旧闻剔除 {len(stale)}")
    _log(f"{'='*50}")

    # Dry-run 模式
    if dry:
        _log("\n--- DRY RUN 结果 ---")
        for d in deals:
            _log(
                f"  [{d.date_status}][{d.region_class}] {d.project_name} | "
                f"{d.track} | {d.round} | {d.amount}"
            )
        return

    # 写入存储
    _log("[7/8] 写入 SQLite / 生成周报+总库...")
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
    _log(f"[8/8] ✅ 输出目录：{OUTPUT_ROOT}")

    # 预留 sink
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
    p.add_argument("--no-enrich", action="store_true")
    p.add_argument("--max-articles", type=int)
    a = p.parse_args()
    run(a.since, a.until, a.dry_run, a.no_enrich, a.max_articles)
