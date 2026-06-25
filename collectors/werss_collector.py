"""
wewe-rss 公众号采集器（v2 — JSON API 直读）

v1 使用 RSS XML 接口 /feed/{id}.xml，但该接口无视 limit 参数，
每个号只返回 ~15 篇，导致大量窗口内文章漏抓。

v2 改用 JSON API /api/v1/wx/articles，支持真实分页(limit+offset)，
按 publish_time 倒序，客户端时间窗口过滤，确保不丢文章。
"""

import time as time_mod
from datetime import datetime
from typing import List, Optional

import requests
from loguru import logger

from models.schema import Article

_PER_PAGE = 100
_WERSS_BASE = "http://localhost:8001"

# JWT 缓存
_token_cache = {"token": None, "expires": 0}


def _get_token() -> Optional[str]:
    now = time_mod.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]
    try:
        r = requests.post(
            f"{_WERSS_BASE}/api/v1/wx/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=5,
        )
        if r.status_code == 200:
            token = r.json().get("data", {}).get("access_token", "")
            if token:
                _token_cache["token"] = token
                _token_cache["expires"] = now + 3600
                return token
    except Exception as e:
        logger.debug(f"[werss] 获取 token 失败: {e}")
    return None


def collect_werss(start: datetime, end: datetime) -> List[Article]:
    """
    ★ v2: 使用 JSON API /api/v1/wx/articles 分页拉取全部文章，
    客户端按时间窗口过滤。彻底解决 RSS XML 接口 limit 被无视的 Bug。
    """
    token = _get_token()
    if not token:
        logger.warning("  [werss] 无法获取 JWT token，公众号采集跳过")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    ts_start = int(start.timestamp())
    ts_end = int(end.timestamp())
    all_articles: List[Article] = []

    # 先确认有多少公众号（用于日志）
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            feeds = (data.get("data", {}) or data).get("list", [])
            active = [f for f in feeds if f.get("status", 1) == 1]
            logger.info(f"  [werss] JSON API 获取 {len(active)} 个活跃公众号")
    except Exception:
        active = []

    # 分页拉取全部文章（按 publish_time 倒序）
    offset = 0
    total_fetched = 0
    all_old_pages = 0  # 连续全过期页计数

    while True:
        try:
            r = requests.get(
                f"{_WERSS_BASE}/api/v1/wx/articles",
                params={"limit": _PER_PAGE, "offset": offset},
                headers=headers,
                timeout=15,
            )
            if r.status_code != 200:
                logger.warning(f"  [werss] articles API HTTP {r.status_code} at offset={offset}")
                break
        except requests.RequestException as e:
            logger.warning(f"  [werss] articles API 请求失败 at offset={offset}: {e}")
            break

        data = r.json()
        articles_data = (data.get("data", {}) or data).get("list", [])
        if not articles_data:
            articles_data = data.get("data", []) if isinstance(data.get("data"), list) else []
        if not articles_data:
            break

        batch_count = 0
        oldest_in_page = None
        for a in articles_data:
            pub_ts = a.get("publish_time", 0)
            if isinstance(pub_ts, float):
                pub_ts = int(pub_ts)
            if oldest_in_page is None or pub_ts < oldest_in_page:
                oldest_in_page = pub_ts

            if pub_ts < ts_start:
                continue  # 过期文章，跳过但不停分页
            if pub_ts > ts_end:
                continue  # 未来文章，跳过

            batch_count += 1
            title = a.get("title", "")
            url = a.get("url", "")
            description = a.get("description", "") or ""
            mp_name = a.get("mp_name", "")

            # 正文
            content = a.get("content", "") or ""
            if not content:
                content_html = a.get("content_html", "") or ""
                if content_html:
                    import re
                    content = re.sub(r"<[^>]+>", "", content_html)
            if not content:
                content = description

            all_articles.append(
                Article(
                    title=title,
                    url=url,
                    summary=description,
                    content=content,
                    source=mp_name,
                    source_type="wechat",
                    region_hint="国内",
                    published_at=datetime.fromtimestamp(pub_ts) if pub_ts else None,
                )
            )

        total_fetched += len(articles_data)
        offset += _PER_PAGE

        # 停止条件：连续 3 页全部过期（说明所有号的窗口内文章都已收完）
        if batch_count == 0 and oldest_in_page and oldest_in_page < ts_start:
            all_old_pages += 1
            if all_old_pages >= 3:
                break
        else:
            all_old_pages = 0

        # 安全上限
        if offset >= 5000:
            logger.warning(f"  [werss] 达到安全上限 5000 篇，停止分页")
            break

    logger.info(f"  [werss] 公众号采集完成: {len(all_articles)} 篇（扫描 {total_fetched} 篇）")
    return all_articles
