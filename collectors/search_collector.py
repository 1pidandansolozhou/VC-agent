import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Literal, Optional
import requests
from loguru import logger

from config.settings import (
    BOCHA_FRESHNESS,
    BOCHA_NUM_RESULTS,
    BOCHA_SUMMARY,
    EXA_MAX_CHARS,
    EXA_NUM_RESULTS,
    EXA_SEARCH_TYPE,
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


def _search_one(
    query: str,
    region_hint: Literal["国内", "海外", "未知"] = "未知",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Article]:
    """Tavily 搜索（dev key 限流严重，仅作兜底）。"""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return []

    # Tavily 用 days 近似过滤；未传窗口时默认近 14 天
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
    last_err = None
    for attempt in range(2):  # 减少重试（432 重试无意义）
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
            last_err = e
            if attempt < 1:
                time.sleep(1.0)
            continue

    if last_err:
        logger.debug(f"Tavily search failed for '{query[:30]}...': {last_err}")
    return out


def _exa_one(
    query: str,
    region_hint: Literal["国内", "海外", "未知"] = "未知",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Article]:
    key = os.getenv("EXA_API_KEY")
    if not key:
        return []

    # 默认窗口：近 14 天
    if end is None:
        end = datetime.now()
    if start is None:
        start = end - timedelta(days=14)

    out = []
    try:
        payload = {
            "query": query,
            "numResults": EXA_NUM_RESULTS,
            "type": EXA_SEARCH_TYPE,
            "category": "news",
            "startPublishedDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endPublishedDate": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "contents": {
                "text": {"maxCharacters": EXA_MAX_CHARS},
                "highlights": True,
            },
        }
        r = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        for it in data.get("results", []):
            title = it.get("title", "")
            hint = region_hint
            if hint == "未知":
                hint = "国内" if _has_cjk(title) else "海外"

            # 优先用 text，其次 summary，最后 highlights 拼接
            text_parts = []
            text = it.get("text") or ""
            if text:
                text_parts.append(text[:EXA_MAX_CHARS])
            summary = it.get("summary") or ""
            if summary:
                text_parts.append(summary[: min(1000, EXA_MAX_CHARS)])
            highlights = it.get("highlights") or []
            if highlights:
                text_parts.append(" ".join(highlights)[: min(1000, EXA_MAX_CHARS)])
            content = "\n".join(text_parts)[:EXA_MAX_CHARS]

            # 解析发布时间
            published_at = None
            pd = it.get("publishedDate")
            if pd:
                try:
                    published_at = datetime.fromisoformat(pd.replace("Z", "+00:00"))
                except Exception:
                    pass

            out.append(
                Article(
                    title=title,
                    url=it.get("url", ""),
                    content=content,
                    source="exa",
                    source_type="search",
                    region_hint=hint,
                    published_at=published_at,
                )
            )
    except Exception as e:
        logger.warning(f"Exa search failed for '{query}': {e}")
    return out


def _bocha_one(
    query: str,
    region_hint: Literal["国内", "海外", "未知"] = "未知",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Article]:
    key = os.getenv("BOCHA_API_KEY")
    if not key:
        return []

    out = []
    try:
        payload = {
            "query": query,
            "summary": BOCHA_SUMMARY,
            "freshness": BOCHA_FRESHNESS,
            "count": BOCHA_NUM_RESULTS,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        # 博查对并发较敏感，遇到 429 时指数退避重试
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(
                    "https://api.bocha.cn/v1/web-search",
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                r.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                last_err = e
                if r.status_code == 429 and attempt < 2:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                raise
        else:
            raise last_err

        data = r.json()

        # 博查响应：data.webPages.value / data.news.value，做防御性解析
        items: List[dict] = []
        if isinstance(data, dict):
            d = data.get("data") or {}
            for section in ("webPages", "news"):
                sec = d.get(section)
                if isinstance(sec, dict):
                    items.extend(sec.get("value") or [])
                elif isinstance(sec, list):
                    items.extend(sec)

        for it in items:
            if not isinstance(it, dict):
                continue
            title = it.get("name") or it.get("title") or ""
            url = it.get("url") or ""
            if not title or not url:
                continue

            hint = region_hint
            if hint == "未知":
                hint = "国内" if _has_cjk(title) else "海外"

            # 内容优先级：summary > snippet > content > description
            content_parts = []
            for k in ("summary", "snippet", "content", "description"):
                v = it.get(k)
                if v:
                    content_parts.append(str(v))
            content = "\n".join(content_parts)[:2000]

            # 解析发布时间
            published_at = None
            pd = it.get("datePublished") or it.get("dateLastCrawled") or it.get("publishedDate")
            if pd:
                try:
                    published_at = datetime.fromisoformat(str(pd).replace("Z", "+00:00"))
                except Exception:
                    pass

            out.append(
                Article(
                    title=title,
                    url=url,
                    content=content,
                    source="bocha",
                    source_type="search",
                    region_hint=hint,
                    published_at=published_at,
                )
            )
    except Exception as e:
        logger.warning(f"Bocha search failed for '{query}': {e}")
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


def _run_engine(
    engine_name: str,
    fn,
    queries: List[tuple[str, Literal["国内", "海外"]]],
    max_workers: int = 5,
    **kwargs,
) -> List[Article]:
    all_arts: List[Article] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(queries))) as pool:
        futures = {pool.submit(fn, q, r, **kwargs): (q, r) for q, r in queries}
        for fut in as_completed(futures):
            q, r = futures[fut]
            try:
                arts = fut.result()
                logger.info(f"{engine_name} '{q[:30]}...' -> {len(arts)} results")
                all_arts.extend(arts)
            except Exception as e:
                logger.warning(f"{engine_name} query '{q[:30]}...' failed: {e}")
    return all_arts


def collect_search() -> List[Article]:
    """聚合 Bocha + Exa + Kimi + Tavily（Bocha/Exa 优先，Tavily dev key 限流仅兜底）。"""
    cn = [(q, "国内") for q in SEARCH_QUERIES_CN]
    en = [(q, "海外") for q in SEARCH_QUERIES_EN]
    all_queries = cn + en

    start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    exa_key = os.getenv("EXA_API_KEY")
    bocha_key = os.getenv("BOCHA_API_KEY")
    kimi_key = os.getenv("MOONSHOT_API_KEY")

    if not (tavily_key or exa_key or bocha_key or kimi_key):
        logger.warning("No search API key configured, skip search")
        return []

    # 各引擎分别跑并截断，避免单一源占满最终池
    per_engine_cap = max(30, MAX_SEARCH_RESULTS // 2)
    jobs: List[tuple[str, callable]] = []

    # Bocha 优先（中文搜索最强，无 432 限流）
    if bocha_key:
        cn_queries = [(q, "国内") for q in SEARCH_QUERIES_CN]
        jobs.append(("Bocha", lambda: _run_engine("Bocha", _bocha_one, cn_queries, max_workers=2, start=start, end=end)))
    # Exa 其次（海外信息好）
    if exa_key:
        jobs.append(("Exa", lambda: _run_engine("Exa", _exa_one, all_queries, start=start, end=end)))
    # Kimi 联网搜索
    if kimi_key:
        from collectors.kimi_search_collector import collect_kimi_search
        jobs.append(("KimiSearch", lambda: collect_kimi_search(SEARCH_QUERIES_CN[:KIMI_SEARCH_MAX_QUERIES])))
    # Tavily 兜底
    if tavily_key:
        jobs.append(("Tavily", lambda: _run_engine("Tavily", _search_one, all_queries, max_workers=2, start=start, end=end)))

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
    """给 date_verify 复用的通用搜索接口（Bocha → Exa → Kimi → Tavily 并行兜底）。"""
    if start is None or end is None:
        start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    exa_key = os.getenv("EXA_API_KEY")
    bocha_key = os.getenv("BOCHA_API_KEY")
    kimi_key = os.getenv("MOONSHOT_API_KEY")

    all_arts: List[Article] = []
    for q in qs:
        if bocha_key:
            all_arts.extend(_bocha_one(q, start=start, end=end))
        if exa_key:
            all_arts.extend(_exa_one(q, start=start, end=end))
        if kimi_key:
            from collectors.kimi_search_collector import collect_kimi_search
            try:
                all_arts.extend(collect_kimi_search([q])[:3])
            except Exception as e:
                logger.warning(f"Kimi search fallback failed for '{q}': {e}")
        if tavily_key:
            all_arts.extend(_search_one(q, start=start, end=end))
    return _dedup_articles(all_arts)


def search_all(qs: List[str], start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[Article]:
    """给 enricher/补搜用的搜索接口（Bocha → Exa → Tavily，不调 Kimi 以节省成本）。"""
    if start is None or end is None:
        start, end = get_window()

    tavily_key = os.getenv("TAVILY_API_KEY")
    exa_key = os.getenv("EXA_API_KEY")
    bocha_key = os.getenv("BOCHA_API_KEY")

    all_arts: List[Article] = []
    for q in qs:
        # Bocha 优先（中文搜索最强）
        if bocha_key:
            all_arts.extend(_bocha_one(q, start=start, end=end))
        # Exa 其次
        if exa_key:
            all_arts.extend(_exa_one(q, start=start, end=end))
        # Tavily 兜底
        if tavily_key:
            all_arts.extend(_search_one(q, start=start, end=end))
    return _dedup_articles(all_arts)
