"""
VC 监控agent · v1 四阶段流水线

架构：
  1. stage1_capture  — 三路并行采集：RSSHub + 微信公众号(wewe-rss) + 浏览器Bing搜索
  2. stage2_extract  — RSS/公众号全量进LLM抽取 → 浏览器搜索预过滤后抽取 → 去重合并
                      → 时间筛选：RSS/公众号项目信任源时间；浏览器项目严格Kimi反查→窗口外丢弃
  3. round2_enrich   — 浏览器搜索 + Kimi联网 补全缺失字段
  4. stage3_coverage  — 数量≥5条即输出，不足补搜
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
from collectors import browser_search, rss_collector, web_collector, werss_collector
from collectors.manual import read_manual_links
from collectors.xhs_collector import collect_xhs
from exporters.excel_exporter import write_weekly
from exporters.master_exporter import rebuild_master
from exporters.word_exporter import write_word
from models.schema import Deal
from processors import (
    coverage_check,
    date_verify,
    enricher,
    extractor,
    merge,
    pregate,
)
from processors.dedup import dedup, reset_seen
from processors.preflight import ensure_services_ready
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

    werss_url = "http://localhost:8001/feed/all.atom"
    status = "在线✓" if _source_reachable(werss_url) else "未启动—公众号数据为空"
    _log(f"  微信公众号(wewe-rss): {status}")


# ═══════════════════════════════════════════════════════════════
# Stage 1：三路并行采集
# ═══════════════════════════════════════════════════════════════

def stage1_capture(start: datetime, end: datetime) -> list:
    """三路并行采集：RSSHub + 微信公众号(wewe-rss) + 浏览器Bing搜索。"""

    _log("[ROUND-1] 三路并行采集：RSSHub + 微信公众号 + 浏览器Bing搜索")

    arts = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_rss = ex.submit(rss_collector.collect_rss, start, end)
        f_werss = ex.submit(werss_collector.collect_werss, start, end)
        f_brow = ex.submit(
            browser_search.browser_keyword_search,
            all_cn_queries(),
            all_en_queries(),
        )
        f_manual = ex.submit(lambda: web_collector.crawl_urls(read_manual_links()))

        arts.extend(f_rss.result())
        arts.extend(f_werss.result())
        arts.extend(f_brow.result())
        arts.extend(f_manual.result())

    arts += collect_xhs()  # 桩

    # ★ 时间预过滤：RSS/公众号文章已在采集时做了窗口过滤，这里做二次保险
    # 浏览器搜索文章 published_at=None，全部保留（后续 date_verify 严格核查）
    in_window_arts = []
    stale_count = 0
    for a in arts:
        if a.published_at is None:
            in_window_arts.append(a)  # 浏览器搜索源，无明确发布日期，后续核查
        elif start <= a.published_at <= end:
            in_window_arts.append(a)
        else:
            stale_count += 1

    if stale_count:
        _log(f"  [ROUND-1] 时间预过滤：剔除 {stale_count} 篇窗口外旧文")

    arts = in_window_arts
    rss_count = len([a for a in arts if a.source_type == 'rss'])
    wechat_count = len([a for a in arts if a.source_type == 'wechat'])
    browser_count = len([a for a in arts if a.source_type in ('web', 'search')])

    _log(f"  [ROUND-1] 窗口内文章 {len(arts)} 篇 "
         f"（RSS {rss_count} + 公众号 {wechat_count} + 浏览器/搜索 {browser_count}）")
    return arts


# ═══════════════════════════════════════════════════════════════
# Stage 2：LLM 抽取 + 时间筛选
# ═══════════════════════════════════════════════════════════════

def stage2_extract(arts: list, start: datetime, end: datetime) -> list:
    """
    RSS/公众号文章 → 全量进 LLM 抽取（不过滤！全文读取）
    浏览器搜索文章 → 预过滤 → LLM 抽取
    → 去重 → 跨源合并
    → 时间筛选：RSS/公众号项目信任源时间；浏览器项目严格 Kimi 反查
    """
    _log("[STAGE-2] LLM抽取 + 时间筛选")

    # ── 2a. 预过滤：RSS/公众号全保留；浏览器搜索走关键词预过滤 ──
    arts = pregate.pre_gate(arts)
    arts = dedup(arts)

    rss_wechat_n = len([a for a in arts if a.source_type in ('rss', 'wechat')])
    other_n = len(arts) - rss_wechat_n
    _log(f"  [STAGE-2] 预过滤+去重后 {len(arts)} 篇（RSS/公众号 {rss_wechat_n} + 搜索源 {other_n}）")

    # ── 2b. 回源补全文：RSS/公众号文章全文已较完整，搜索源补全文 ──
    _log("  [STAGE-2] 回源补全文...")
    web_collector.enrich_fulltext(arts)

    # ── 2c. 并发 LLM 抽取 ──
    _log("  [STAGE-2] LLM 并发抽取早期项目（RSS/公众号全文读取）...")
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
    _log(f"  [STAGE-2] LLM 命中早期项目: {len(deals)} 个")

    # ── 2d. 时间筛选 ──
    # ★ 核心逻辑：
    #   - RSS/公众号项目：信任采集时的时间窗口过滤 → 直接标 in_window
    #   - 浏览器搜索/web/manual 项目：严格 Kimi 反查融资公布日 → 窗口外直接丢弃
    rss_trusted = 0
    browser_verified = 0
    browser_discarded = 0

    for d in deals:
        # 判断项目来源：检查 sources 列表中的来源类型
        src_types = set()
        for s in (d.sources or []):
            s_lower = s.lower() if s else ""
            if any(k in s_lower for k in ('36氪', 'cyzone', '创业邦', '量子位', 'qbitai',
                                            'techcrunch', 'crunchbase', 'venturebeat',
                                            'eu-startups', 'tech.eu', 'sifted')):
                src_types.add('rss')
            elif any(k in s_lower for k in ('wechat', '微信', '公众号', 'mp.weixin')):
                src_types.add('wechat')
            elif any(k in s_lower for k in ('kimi', 'moonshot', 'bing', 'bocha', 'exa', 'tavily')):
                src_types.add('search')

        is_trusted = bool(src_types & {'rss', 'wechat'}) and not (src_types & {'search'})

        if is_trusted:
            # RSS/公众号项目：信任源时间，直接标 in_window
            d.date_status = "in_window"
            d.date_confidence = "high"
            d.verified_date = d.source_date or ""
            rss_trusted += 1
        else:
            # 浏览器搜索/web 项目：严格 Kimi 反查
            date_verify.verify(d, start, end)
            browser_verified += 1
            if d.date_status == "stale":
                browser_discarded += 1

    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]

    _log(f"  [STAGE-2] 时间筛选：RSS/公众号信任 {rss_trusted} 个 | "
         f"浏览器反查 {browser_verified} 个（丢弃 {browser_discarded} 个窗口外）")
    _log(f"  [STAGE-2] in_window={len(in_w)}，stale={len(stale)}")
    if stale:
        _log(f"    丢弃旧闻: {[s.project_name for s in stale]}")

    return deals


# ═══════════════════════════════════════════════════════════════
# Round 2：信息补全（浏览器搜索 + Kimi 联网）
# ═══════════════════════════════════════════════════════════════

def round2_enrich(deals: list) -> list:
    """对 in_window 项目，用浏览器搜索 + Kimi 联网补全缺失字段。"""
    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    _log(f"[ROUND-2] 对 {len(in_w)} 个确认项目补全信息（浏览器搜索 + Kimi 联网）")
    enriched = enricher.enrich_all(in_w)
    return enriched + stale


# ═══════════════════════════════════════════════════════════════
# Stage 3：数量检查 + 补搜
# ═══════════════════════════════════════════════════════════════

def stage3_coverage(deals: list, start: datetime, end: datetime, retry_count: int = 0) -> list:
    """数量≥5即输出，不足时触发浏览器补搜。"""
    reasons = coverage_check.should_retry(deals)
    max_retries = MAX_SEARCH_RETRIES

    if not reasons or retry_count >= max_retries:
        in_w = [d for d in deals if d.date_status != "stale"]
        cn_n = len([d for d in in_w if d.region_class == "国内"])
        gl_n = len([d for d in in_w if d.region_class == "海外"])
        if reasons:
            _log(f"[STAGE-3] 数量={len(in_w)}（国内{cn_n}/海外{gl_n}），已达补搜上限，输出现有结果")
        else:
            _log(f"[STAGE-3] ✅ 数量达标：周报项目 {len(in_w)} 个（国内{cn_n}/海外{gl_n}）")
        return deals

    _log(f"[STAGE-3] ▶ 触发补搜，原因：{reasons}（第 {retry_count + 1}/{max_retries} 次）")
    cn_qs, en_qs = coverage_check.build_retry_queries(deals)
    _log(f"  [ROUND-3] 补搜词：CN={cn_qs}，EN={en_qs}")

    # 补搜：浏览器搜索 + Bocha/Exa
    extra = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_brow = ex.submit(browser_search.browser_keyword_search, cn_qs, en_qs)
        f_api = ex.submit(lambda: search_all_retry(cn_qs + en_qs))
        extra.extend(f_brow.result())
        extra.extend(f_api.result())

    _log(f"  [ROUND-3] 补搜新增 {len(extra)} 条原始文章")

    extra_deals = stage2_extract(extra, start, end)
    merged = merge.merge(deals + extra_deals)
    merged = enricher.enrich_all(merged)
    return stage3_coverage(merged, start, end, retry_count + 1)


def search_all_retry(qs: list) -> list:
    """补搜时专用的搜索 API 调用。"""
    from collectors.search_collector import search_all
    return search_all(qs)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def run(since=None, until=None, dry=False, no_enrich=False, max_articles=None):
    if max_articles:
        os.environ["VC_MAX_ARTICLES_PER_SOURCE"] = str(max_articles)

    start, end = get_window(since, until)
    win = f"{start:%Y-%m-%d}_至_{end:%Y-%m-%d}"
    dr = f"{start.year}.{start.month}.{start.day} — {end.month}.{end.day}"

    _log(f"{'='*50}")
    _log(f"  VC 监控agent v1 | 窗口 {win}")
    _log(f"  数据源：RSSHub + 微信公众号 + 浏览器Bing搜索")
    _log(f"  逻辑：RSS/公众号全量LLM抽取 → 浏览器严格时间核查 → 联网补全")
    _log(f"{'='*50}")

    # ★ v1：每次运行清空去重历史，防止跨运行误去重
    reset_seen()

    # ★ v1：启动预热 — 确保 Docker 服务就绪 + RSS 数据刷新
    _log("[0/5] 服务预热检查：等待 RSSHub / wewe-rss 就绪并刷新数据...")
    ensure_services_ready(start, end)

    # Stage 1：三路并行采集
    arts = stage1_capture(start, end)

    # Stage 2：LLM 抽取 + 时间筛选
    deals = stage2_extract(arts, start, end)

    # Round 2：信息补全
    if not no_enrich:
        deals = round2_enrich(deals)

    # Stage 3：数量检查 + 补搜
    deals = stage3_coverage(deals, start, end)

    # 最终统计
    in_win = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    cn_n = len([d for d in in_win if d.region_class == "国内"])
    gl_n = len([d for d in in_win if d.region_class == "海外"])
    rss_n = len([d for d in in_win if any(
        k in str(d.sources).lower() for k in ('36氪', 'cyzone', '创业邦', '量子位',
                                               'techcrunch', 'crunchbase', 'venturebeat',
                                               'eu-startups', 'tech.eu', 'sifted', 'wechat', '微信')
    )])
    _log(f"{'='*50}")
    _log(f"  最终：周报项目 {len(in_win)} 个（国内{cn_n}/海外{gl_n}，RSS源{rss_n}）")
    _log(f"  旧闻剔除 {len(stale)} 个")
    _log(f"{'='*50}")

    # Dry-run 模式
    if dry:
        _log("\n--- DRY RUN 结果 ---")
        for d in deals:
            _log(
                f"  [{d.date_status}][{d.region_class}] {d.project_name} | "
                f"{d.track} | {d.round} | {d.amount} | 来源:{d.sources}"
            )
        return

    # 写入存储
    _log("[4/5] 写入 SQLite / 生成周报+总库...")
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
    _log(f"[5/5] ✅ 输出目录：{OUTPUT_ROOT}")

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
