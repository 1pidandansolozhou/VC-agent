import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Literal, Optional
import requests
from loguru import logger

from config.settings import (
    KIMI_SEARCH_MAX_QUERIES,
    MAX_SEARCH_RESULTS,
    REQUEST_TIMEOUT,
)
from config.sources import SEARCH_QUERIES_CN, SEARCH_QUERIES_EN
from models.schema import Article
from processors.window import get_window


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))


def _is_funding_related(title: str) -> bool:
    t = title.lower()
    kw = [
        "融资", "轮", "天使", "种子", "pre-a", "a轮", "领投", "跟投", "获投", "获", "亿元", "万美元", "投资",
        "raised", "funding", "seed", "angel", "series a", "investment", "round", "million", "billion", "financing"
    ]
    return any(k in t for k in kw)


def _tavily_one(
    query: str,
    region_hint: Literal["国内", "海外", "未知"] = "未知",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Article]:
    """Tavily 搜索（dev key 限流，仅作兜底）。"""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return []

    if end is None:
        end = datetime.now()
    if start is None:
        start = end - timedelta(days=14)
    days = max(1, min(30, (end - start).days + 1))

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "advanced",
        "max_results": 15,
        "days": days,
    }

    out = []
    for attempt in range(2):
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 432:
                logger.debug(f"Tavily 432 rate limit for '{query[:30]}...', skip")
                return out
            r.raise_for_status()
            data = r.json()
            for it in data.get("results", []):
                title = it.get("title", "")
                hint = region_hint
                if hint == "未知":
                    hint = "国内" if _has_cjk(title) else "海外"
                out.append(
                    Article(
                        title=title,
                        url=it.get("url", ""),
                        content=it.get("content", ""),
                        source="tavily",
                        source_type="search",
                        region_hint=hint,
                    )
                )
            return out
        except Exception as e:
            if attempt < 1:
                time.sleep(1.0)
            else:
                logger.debug(f"Tavily search failed for '{query[:30]}...': {e}")
    return out


def _dedup_articles(arts: List[Article]) -> List[Article]:
    seen = set()
    out = []
    for a in arts:
        key = (a.title.strip().lower()[:80], a.url.split("?")[0])
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def _run_tavily(queries: List[tuple[str, Literal["国内", "海外"]]], **kwargs) -> List[Article]:
    all_arts: List[Article] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_tavily_one, q, r, **kwargs): (q, r) for q, r in queries}
        for fut in as_completed(futures):
            q, r = futures[fut]
            try:
                arts = fut.result()
                all_arts.extend(arts)
            except Exception as e:
                logger.warning(f"Tavily query '{q[:30]}...' failed: {e}")
    return all_arts


def collect_search() -> List[Article]:
    """聚合搜索：Kimi 联网 + Tavily 兜底。Bocha/Exa 已废弃（key 失效）。"""
    cn = [(q, "国内") for q in SEARCH_QUERIES_CN]
    en = [(q, "海外") for q in SEARCH_QUERIES_EN]
    all_queries = cn + en

    start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    kimi_key = os.getenv("MOONSHOT_API_KEY")

    if not (tavily_key or kimi_key):
        logger.warning("No search API key configured, skip search")
        return []

    per_engine_cap = max(30, MAX_SEARCH_RESULTS // 2)
    jobs: List[tuple[str, callable]] = []

    # Kimi 联网搜索（中文主力）
    if kimi_key:
        from collectors.kimi_search_collector import collect_kimi_search
        jobs.append(("KimiSearch", lambda: collect_kimi_search(SEARCH_QUERIES_CN[:KIMI_SEARCH_MAX_QUERIES])))

    # Tavily 兜底
    if tavily_key:
        jobs.append(("Tavily", lambda: _run_tavily(all_queries, start=start, end=end)))

    all_arts: List[Article] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                arts = fut.result()
                logger.info(f"{name} aggregated -> {len(arts)} results")
                all_arts += [a for a in arts if a.title][:per_engine_cap]
            except Exception as e:
                logger.warning(f"{name} failed: {e}")

    deduped = _dedup_articles(all_arts)
    filtered = [a for a in deduped if _is_funding_related(a.title)]
    if len(filtered) > MAX_SEARCH_RESULTS:
        logger.info(f"Search capped to {MAX_SEARCH_RESULTS} (raw {len(all_arts)}, deduped {len(deduped)})")
        filtered = filtered[:MAX_SEARCH_RESULTS]
    else:
        logger.info(f"Search total: {len(all_arts)} raw -> {len(deduped)} deduped -> {len(filtered)} funding-related")
    return filtered


def search_queries(qs: List[str], start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[Article]:
    """给 date_verify 复用的搜索接口（Kimi → Tavily 兜底）。"""
    if start is None or end is None:
        start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    kimi_key = os.getenv("MOONSHOT_API_KEY")

    all_arts: List[Article] = []
    for q in qs:
        if kimi_key:
            from collectors.kimi_search_collector import collect_kimi_search
            try:
                all_arts.extend(collect_kimi_search([q])[:3])
            except Exception as e:
                logger.warning(f"Kimi search fallback failed for '{q}': {e}")
        if tavily_key:
            all_arts.extend(_tavily_one(q, start=start, end=end))
    return _dedup_articles(all_arts)


def search_all(qs: List[str], start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[Article]:
    """给 enricher/补搜用的搜索接口（Kimi → Tavily）。"""
    if start is None or end is None:
        start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    kimi_key = os.getenv("MOONSHOT_API_KEY")

    all_arts: List[Article] = []
    for q in qs:
        if kimi_key:
            from collectors.kimi_search_collector import collect_kimi_search
            try:
                all_arts.extend(collect_kimi_search([q])[:3])
            except Exception as e:
                logger.warning(f"Kimi search fallback failed for '{q}': {e}")
        if tavily_key:
            all_arts.extend(_tavily_one(q, start=start, end=end))
    return _dedup_articles(all_arts)
