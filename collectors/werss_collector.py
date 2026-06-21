"""
wewe-rss 公众号采集器（v1）
通过 wewe-rss HTTP API 按公众号逐号采集，跳过 RSS page_size 限制。
支持分页（limit=100 + offset）和时间窗口过滤。
v1: 添加 JWT 认证以正确获取全部公众号列表。
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional

import requests
from loguru import logger

from models.schema import Article

# 每个公众号每次最多取 100 篇，可并行
_PER_PAGE = 100
_MAX_WORKERS = 10
_WERSS_BASE = "http://localhost:8001"

# 缓存 token（避免每次调用都登录）
_token_cache = {"token": None, "expires": 0}


def _get_token() -> Optional[str]:
    """获取 wewe-rss JWT token（带缓存）。"""
    import time
    now = time.time()
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
                _token_cache["expires"] = now + 3600  # 缓存1小时
                return token
    except Exception as e:
        logger.debug(f"[werss] 获取 token 失败: {e}")
    return None


def collect_werss(start: datetime, end: datetime) -> List[Article]:
    """从 wewe-rss 逐个公众号全量采集（分页），按时间窗口过滤。"""
    feeds = _get_feeds()
    if not feeds:
        logger.warning("  [werss] 无可用公众号（wewe-rss 未启动或无订阅）")
        return []

    logger.info(f"  [werss] 共 {len(feeds)} 个公众号，开始逐号采集（每号最多 {_PER_PAGE} 篇）")

    all_articles: List[Article] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(feeds))) as pool:
        futs = {
            pool.submit(_fetch_feed_articles, fid, fname, start, end): fname
            for fid, fname in feeds
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                arts = fut.result()
                all_articles.extend(arts)
                if arts:
                    logger.debug(f"    [{name}] {len(arts)} 篇")
            except Exception as e:
                logger.warning(f"    [{name}] 采集异常: {e}")

    logger.info(f"  [werss] 公众号采集完成: {len(all_articles)} 篇")
    return all_articles


def _get_feeds() -> List[tuple]:
    """从 wewe-rss JSON API 获取所有活跃公众号 (id, name)。v1: 带 JWT 认证。"""
    token = _get_token()

    # 优先使用 JSON API（/mps）带认证
    if token:
        try:
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                feeds_data = data.get("data", {})
                feeds_list = feeds_data.get("list", [])
                if not feeds_list and isinstance(data.get("list"), list):
                    feeds_list = data["list"]
                if feeds_list:
                    result = [(d["id"], d["mp_name"]) for d in feeds_list if d.get("status", 1) == 1]
                    logger.info(f"  [werss] JSON API 获取 {len(result)} 个活跃公众号")
                    return result
            logger.warning(f"  [werss] JSON API 返回异常: HTTP {r.status_code}，降级")
        except Exception as e:
            logger.warning(f"  [werss] JSON API 异常 ({e})，降级")

    # 降级1：无认证直接尝试（可能被拒绝）
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", timeout=10)
        if r.status_code == 200:
            data = r.json()
            feeds_list = data.get("data", {}).get("list", data.get("list", []))
            if feeds_list:
                result = [(d["id"], d["mp_name"]) for d in feeds_list if d.get("status", 1) == 1]
                logger.info(f"  [werss] 无认证 API 获取 {len(result)} 个公众号")
                return result
    except Exception:
        pass

    # 降级2：RSS 订阅列表接口
    try:
        r = requests.get(f"{_WERSS_BASE}/rss", timeout=10)
        if r.status_code == 200:
            ids = re.findall(r"/rss/(\w+)", r.text)
            names = re.findall(r"<title>([^<]+)</title>", r.text)
            feeds = [(ids[i], names[i + 1].strip()) for i in range(min(len(ids), len(names) - 1))]
            if feeds:
                logger.info(f"  [werss] RSS 列表获取 {len(feeds)} 个公众号")
                return feeds
    except Exception:
        pass

    # 降级3：直接读 SQLite
    logger.info("  [werss] 降级为 SQLite 直读")
    return _get_feeds_from_db()


def _get_feeds_from_db() -> List[tuple]:
    """兜底：通过 SQLite 获取公众号列表（放入容器内临时查询）。"""
    import subprocess
    script = """
import sqlite3, json
conn = sqlite3.connect('/app/data/db.db')
cur = conn.cursor()
cur.execute("SELECT id, mp_name FROM feeds WHERE status=1 ORDER BY mp_name")
print(json.dumps([{"id": r[0], "name": r[1]} for r in cur.fetchall()], ensure_ascii=False))
conn.close()
"""
    try:
        r = subprocess.run(
            ["docker", "exec", "we-mp-rss", "python3", "-c", script],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            import json
            data = json.loads(r.stdout.strip())
            return [(d["id"], d["name"]) for d in data]
    except Exception:
        pass
    return []


def _fetch_feed_articles(
    feed_id: str, feed_name: str, start: datetime, end: datetime
) -> List[Article]:
    """单个公众号：分页获取全部文章，按时间过滤。"""
    arts: List[Article] = []
    ts_start = int(start.timestamp())
    ts_end = int(end.timestamp())

    for offset in range(0, 9999, _PER_PAGE):
        try:
            url = (
                f"{_WERSS_BASE}/feed/{feed_id}.xml"
                f"?limit={_PER_PAGE}&offset={offset}"
            )
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                break

            items = _parse_rss_items(r.text)
            if not items:
                break  # 没有更多文章

            for item in items:
                pub_ts = item.get("updated")
                if pub_ts and ts_start <= pub_ts <= ts_end:
                    arts.append(Article(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        summary=item.get("description", ""),
                        content=item.get("content", ""),
                        source=feed_name,
                        source_type="wechat",
                        region_hint="国内",
                        published_at=datetime.fromtimestamp(pub_ts) if pub_ts else None,
                    ))

            # 如果这页不满 _PER_PAGE，说明到末尾了
            if len(items) < _PER_PAGE:
                break

        except requests.RequestException as e:
            logger.warning(f"    [{feed_name}] offset={offset} 请求失败: {e}")
            break

    return arts


def _parse_rss_items(xml_text: str) -> List[dict]:
    """从 RSS XML 中解析文章列表。"""
    items = []
    # 正则解析比 ElementTree 更兼容各种 RSS 格式
    for entry in re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL):
        def _extract(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.DOTALL)
            return m.group(1).strip() if m else ""

        def _extract_ts(tag: str) -> Optional[int]:
            val = _extract(tag)
            if not val:
                return None
            # ISO 格式: 2026-06-16T21:33:59+08:00
            for fmt in [
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S",
            ]:
                try:
                    dt = datetime.strptime(val[:25].rstrip("+"), fmt)
                    return int(dt.timestamp())
                except ValueError:
                    continue
            return None

        items.append({
            "title": _extract("title"),
            "link": _extract("link"),
            "description": _extract("description"),
            "content": _extract("content:encoded") or _extract("content"),
            "updated": _extract_ts("pubDate") or _extract_ts("updated") or _extract_ts("date"),
        })
    return items
