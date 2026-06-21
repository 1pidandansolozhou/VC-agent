import asyncio
import os
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config.settings import ENABLE_ENRICH, REQUEST_TIMEOUT, WEB_CRAWL_MAX, WEB_CRAWL_WORKERS, WEB_CRAWL_TIMEOUT
from models.schema import Article


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))


def _region_hint_from_url(url: str) -> str:
    """根据域名预设区域提示，36kr/IT桔子等国内站点即使标题无中文也标国内。"""
    host = url.lower()
    cn_hosts = [
        "36kr", "itjuzi", "pedaily", "cyzone", "iyiou", "pencilnews",
        "lieyunwang", "jiqizhixin", "qbitai", "leiphone", "geekpark",
        "sina", "qq", "ifeng", "163",
    ]
    global_hosts = [
        "techcrunch", "crunchbase", "eu-startups", "tech.eu", "sifted",
        "venturebeat", "wired", "technologyreview", "nature", "ieee",
        "sequoiacap.com", "a16z", "accel", "indexventures", "bvp",
        "benchmark", "lightspeedvp", "bessemertrust",
    ]
    if any(h in host for h in cn_hosts):
        return "国内"
    if any(h in host for h in global_hosts):
        return "海外"
    return "未知"


def _playwright_available() -> bool:
    """检查 playwright 与 chromium 是否已安装。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return bool(p.chromium.executable_path)
    except Exception:
        return False


def _browser_config():
    """模拟真实 Chrome 浏览器，降低被反爬拦截概率。"""
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


def _run_config():
    """页面加载配置：等待 JS 执行完成。"""
    from crawl4ai import CrawlerRunConfig

    return CrawlerRunConfig(
        wait_for="js:() => document.readyState === 'complete'",
        page_timeout=WEB_CRAWL_TIMEOUT * 1000,
    )


async def _crawl_one(crawler, url: str) -> Optional[Article]:
    """用 Crawl4AI 抓单个 URL，失败返回 None。"""
    try:
        r = await crawler.arun(url=url, config=_run_config())
        title = (r.metadata or {}).get("title") or url
        hint = _region_hint_from_url(url)
        if hint == "未知":
            hint = "国内" if _has_cjk(title) else "海外"
        return Article(
            title=title,
            url=url,
            content=r.markdown or "",
            source="web",
            source_type="web",
            region_hint=hint,
        )
    except Exception as e:
        logger.warning(f"Crawl4AI failed {url}: {e}")
        return None


def _requests_one(url: str) -> Optional[Article]:
    """requests + BeautifulSoup 兜底，用于 Crawl4AI 失败或不可用。"""
    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        hint = _region_hint_from_url(url)
        if hint == "未知":
            hint = "国内" if _has_cjk(title) else "海外"

        # 去除脚本/样式/导航/页脚
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = ""
        for sel in ["article", "main", ".content", ".article-content", "#content", ".post"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text("\n", strip=True)
                break
        if not text and soup.body:
            text = soup.body.get_text("\n", strip=True)

        return Article(
            title=title,
            url=url,
            content=text,
            source="web",
            source_type="web",
            region_hint=hint,
        )
    except Exception as e:
        logger.warning(f"Requests fallback failed {url}: {e}")
        return None


async def _crawl_urls(urls: List[str]) -> List[Article]:
    """并发深抓 URL 列表，优先 Crawl4AI，失败则 requests 兜底。"""
    if not urls:
        return []

    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        logger.warning("crawl4ai not installed, use requests fallback")
        return [a for a in (_requests_one(u) for u in urls) if a]

    out: List[Article] = []
    try:
        async with AsyncWebCrawler(config=_browser_config()) as crawler:
            semaphore = asyncio.Semaphore(WEB_CRAWL_WORKERS)

            async def _bounded(u: str) -> Optional[Article]:
                async with semaphore:
                    art = await _crawl_one(crawler, u)
                    # 内容过短时尝试 requests 兜底（扔到线程池避免阻塞 event loop）
                    if art is None or len(art.content) < 200:
                        fallback = await asyncio.to_thread(_requests_one, u)
                        if fallback:
                            return fallback
                    return art

            tasks = [asyncio.create_task(_bounded(u)) for u in urls]
            for t in asyncio.as_completed(tasks):
                art = await t
                if art:
                    out.append(art)
    except Exception as e:
        logger.warning(f"Crawl4AI init failed: {e}, fallback to requests")
        out = [a for a in (_requests_one(u) for u in urls) if a]
    return out


def crawl_urls(urls: List[str]) -> List[Article]:
    """同步入口：深抓指定 URL 列表。"""
    if not urls:
        return []
    return asyncio.run(_crawl_urls(urls))


def _is_wechat_url(url: str) -> bool:
    return "mp.weixin.qq.com" in url


async def _enrich(arts: List[Article]) -> None:
    """对内容短的文章回源补全文。"""
    if not ENABLE_ENRICH or not arts:
        return
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return

    urls_to_enrich = [
        a for a in arts
        if len(a.content) < 200 and a.url and not _is_wechat_url(a.url)
    ][:WEB_CRAWL_MAX]  # v1：限制回源上限
    if not urls_to_enrich:
        return

    try:
        async with AsyncWebCrawler(config=_browser_config()) as crawler:
            semaphore = asyncio.Semaphore(WEB_CRAWL_WORKERS)

            async def _bounded(a: Article):
                async with semaphore:
                    try:
                        r = await crawler.arun(url=a.url, config=_run_config())
                        if r.markdown:
                            a.content = r.markdown
                    except Exception as e:
                        logger.warning(f"Enrich crawl failed {a.url}: {e}")

            await asyncio.gather(*[asyncio.create_task(_bounded(a)) for a in urls_to_enrich])
    except Exception as e:
        logger.warning(f"Enrich unavailable (playwright/browser missing): {e}")


def enrich_fulltext(arts: List[Article]) -> None:
    """同步入口：回源补全。"""
    if arts:
        asyncio.run(_enrich(arts))


# 注意：v1 不再使用固定种子页 / 搜索结果深抓（已替换为 browser_search.py 的 Bing 关键词搜索）。
# 保留 crawl_urls() 供手动补漏链接使用。
