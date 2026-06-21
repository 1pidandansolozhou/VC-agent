from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Literal
import feedparser
import requests
from loguru import logger

from config.settings import MAX_ARTICLES_PER_SOURCE, REQUEST_TIMEOUT
from config.sources import RSS_FEEDS_CN, RSS_FEEDS_GLOBAL
from models.schema import Article


def _parse_time(e) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            try:
                return datetime(*e[k][:6])
            except Exception:
                pass
    return None


def _fetch_feed(name_url: tuple[str, str]) -> tuple[str, str, bytes | None]:
    name, url = name_url
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "vc-radar/1.0"})
        r.raise_for_status()
        return name, url, r.content
    except Exception as e:
        logger.warning(f"Fetch feed failed {name}: {e}")
        return name, url, None


def _collect(
    feeds: dict,
    region: Literal["国内", "海外"],
    start: datetime,
    end: datetime,
    max_per_source: int,
) -> List[Article]:
    out = []
    fetched = {}

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(feeds)))) as pool:
        for name, url, content in pool.map(_fetch_feed, feeds.items()):
            if content is not None:
                fetched[name] = (url, content)

    for name, (url, content) in fetched.items():
        try:
            feed = feedparser.parse(content)
        except Exception as e:
            logger.warning(f"RSS feed parse failed {name}: {e}")
            continue

        is_wx = ":8001" in url or ":4000" in url or "feed/all" in url
        for idx, e in enumerate(feed.entries):
            if idx >= max_per_source:
                break
            try:
                t = _parse_time(e)
                if t and not (start <= t <= end):
                    continue
                # 正文优先级：Atom content > RSS content:encoded > summary > title
                body = ""
                content_list = e.get("content")
                if content_list and isinstance(content_list, list):
                    body = content_list[0].get("value", "")
                if not body:
                    body = e.get("content", "") if isinstance(e.get("content"), str) else ""
                if not body:
                    body = e.get("summary", "")
                if not body:
                    body = e.get("title", "")

                out.append(
                    Article(
                        title=e.get("title", ""),
                        url=e.get("link", ""),
                        summary=e.get("summary", ""),
                        content=body,
                        source=name,
                        source_type="wechat" if is_wx else "rss",
                        region_hint=region,
                        published_at=t,
                    )
                )
            except Exception as e:
                logger.warning(f"RSS entry skipped in {name}: {e}")
    return out


def collect_rss(
    start: datetime,
    end: datetime,
    max_per_source: int = MAX_ARTICLES_PER_SOURCE,
) -> List[Article]:
    if max_per_source is None:
        max_per_source = MAX_ARTICLES_PER_SOURCE
    cn = _collect(RSS_FEEDS_CN, "国内", start, end, max_per_source)
    global_ = _collect(RSS_FEEDS_GLOBAL, "海外", start, end, max_per_source)
    return cn + global_
