"""
★ v1 新增：浏览器关键词搜索模块。

用 Crawl4AI+Playwright 在 Bing CN / Bing EN 上做真实关键词搜索。
★ 不定向爬任何固定站点；只构造搜索引擎 URL → 提取搜索结果链接+摘要。
"""

import asyncio
import os
import urllib.parse
from typing import List, Optional

from models.schema import Article

BING_CN = "https://cn.bing.com/search?q={q}&mkt=zh-CN&setlang=zh-Hans&count=15"
BING_EN = "https://www.bing.com/search?q={q}&setlang=en-US&count=15"

# 要过滤掉的搜索引擎自身 / 广告 / 导航
_SKIP_DOMAINS = [
    "bing.com", "microsoft.com", "msn.com", "baidu.com", "google.com",
    "yahoo.com", "youtube.com", "twitter.com", "facebook.com", "linkedin.com",
    "instagram.com", "t.co", "go.microsoft.com",
]


def _is_junk(url: str) -> bool:
    return any(s in (url or "") for s in _SKIP_DOMAINS)


def _browser_config():
    try:
        from crawl4ai import BrowserConfig
        return BrowserConfig(
            browser_type="chromium",
            headless=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            extra_args=["--disable-blink-features=AutomationControlled"],
        )
    except ImportError:
        return None


async def _search_one(crawler, query: str, engine_url: str, region: str) -> List[Article]:
    url = engine_url.format(q=urllib.parse.quote(query))
    arts: List[Article] = []
    try:
        r = await crawler.arun(url=url, wait_for=2000, timeout=25)
        links = (r.links or {}).get("external", [])
        seen = set()
        for lk in links[:25]:
            href = lk.get("href", "")
            text = lk.get("text", "")
            if not href or _is_junk(href) or href in seen:
                continue
            seen.add(href)
            arts.append(
                Article(
                    title=text or href,
                    url=href,
                    content="",
                    source=f"Bing·{query[:18]}",
                    source_type="web",
                    region_hint=region,
                )
            )
    except Exception:
        pass
    return arts


async def _run_all(
    cn_queries: List[str],
    en_queries: List[str],
    max_cn: Optional[int] = None,
    max_en: Optional[int] = None,
) -> List[Article]:
    from config.settings import BROWSER_SEARCH_MAX_CN, BROWSER_SEARCH_MAX_EN

    max_cn = max_cn or BROWSER_SEARCH_MAX_CN
    max_en = max_en or BROWSER_SEARCH_MAX_EN
    results: List[Article] = []

    cfg = _browser_config()
    if cfg is None:
        # crawl4ai 未安装，静默跳过
        return results

    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return results

    try:
        async with AsyncWebCrawler(config=cfg) as c:
            tasks = []
            for q in cn_queries[:max_cn]:
                tasks.append(_search_one(c, q, BING_CN, "国内"))
            for q in en_queries[:max_en]:
                tasks.append(_search_one(c, q, BING_EN, "海外"))
            for coro in asyncio.as_completed(tasks):
                try:
                    results += await coro
                except Exception:
                    pass
    except Exception:
        pass
    return results


def browser_keyword_search(
    cn_queries: List[str],
    en_queries: List[str],
    max_cn: Optional[int] = None,
    max_en: Optional[int] = None,
) -> List[Article]:
    """同步入口：在 Bing CN/EN 上做关键词搜索，返回搜索结果 Article 列表。"""
    try:
        return asyncio.run(_run_all(cn_queries, en_queries, max_cn, max_en))
    except Exception:
        return []
