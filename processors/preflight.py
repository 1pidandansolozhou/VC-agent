"""
★ v1 新增：启动前预热检查。

确保 RSSHub 和 wewe-rss 本地 Docker 服务已启动并持有窗口内数据，
再进入采集阶段。避免 Docker 刚启动时 RSS 缓存为空导致漏抓。
"""

import time
import requests
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

_WERSS_BASE = "http://localhost:8001"
_RSSHUB_BASE = "http://localhost:1200"
_CHECK_TIMEOUT = 5  # 单次检查超时
_MAX_WAIT_S = 120    # 最多等 2 分钟
_REFRESH_TIMEOUT = 60

# 缓存 token
_token_cache = {"token": None, "expires": 0}


def _get_token() -> Optional[str]:
    """获取 wewe-rss JWT token（带缓存）。"""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]
    try:
        r = requests.post(
            f"{_WERSS_BASE}/api/v1/wx/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=_CHECK_TIMEOUT,
        )
        if r.status_code == 200:
            token = r.json().get("data", {}).get("access_token", "")
            if token:
                _token_cache["token"] = token
                _token_cache["expires"] = now + 3600
                return token
    except Exception:
        pass
    return None


def _log(msg: str):
    print(f"  [preflight] {msg}", flush=True)
    logger.info(f"[preflight] {msg}")


def _wait_service(name: str, url: str, max_wait: int = _MAX_WAIT_S) -> bool:
    """等待服务可达，最多等 max_wait 秒。"""
    _log(f"等待 {name} ({url}) 就绪...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=_CHECK_TIMEOUT)
            if r.status_code < 500:
                elapsed = _MAX_WAIT_S - (deadline - time.time())
                _log(f"  {name} 已就绪 ✓（耗时 {elapsed:.0f}s）")
                return True
        except Exception:
            pass
        time.sleep(3)
        print(f"    .", end="", flush=True)
    _log(f"  {name} 未能在 {max_wait}s 内就绪 ✗")
    return False


def _refresh_werss_articles(window_start: datetime) -> bool:
    """
    尝试触发 wewe-rss 刷新最近文章。

    wewe-rss 在 Docker 刚启动时，窗口内的微信文章可能尚未同步。
    尝试调用 refresh API 让 it 拉取最新文章。
    """
    token = _get_token()
    if not token:
        _log("  wewe-rss 无 token，跳过主动刷新")
        return False

    headers = {"Authorization": f"Bearer {token}"}

    # 尝试多种可能的 refresh 端点（不同版本的 wewe-rss 路径可能不同）
    refresh_endpoints = [
        "/api/v1/wx/mps/refresh",       # 批量刷新所有公众号
        "/api/v1/wx/refresh",           # 简写
    ]

    refreshed = False
    for ep in refresh_endpoints:
        try:
            r = requests.post(f"{_WERSS_BASE}{ep}", headers=headers, timeout=30)
            if r.status_code in (200, 202):
                _log(f"  触发 wewe-rss 刷新成功 ({ep})")
                refreshed = True
                break
        except Exception:
            continue

    if not refreshed:
        # 尝试逐个公众号刷新（通过 get mps list → 逐个 refresh）
        try:
            r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                feeds = (data.get("data", {}) or data).get("list", [])
                refreshed_count = 0
                for f in feeds[:20]:  # 只刷新前 20 个活跃号，避免太慢
                    fid = f.get("id", "")
                    try:
                        rr = requests.post(
                            f"{_WERSS_BASE}/api/v1/wx/mps/{fid}/refresh",
                            headers=headers, timeout=15,
                        )
                        if rr.status_code in (200, 202):
                            refreshed_count += 1
                    except Exception:
                        pass
                if refreshed_count > 0:
                    _log(f"  逐个刷新 {refreshed_count} 个公众号")
                    refreshed = True
        except Exception:
            pass

    return refreshed


def _check_werss_recent_articles(window_start: datetime, min_articles: int = 5) -> tuple[bool, int]:
    """
    检查 wewe-rss 是否有窗口内的近期文章。
    返回 (是否够数, 实际数量)。
    """
    token = _get_token()
    if not token:
        return False, 0

    headers = {"Authorization": f"Bearer {token}"}
    ts_start = int(window_start.timestamp())

    # 用 RSS feed 接口快速抽样检查（取最近 50 篇看窗口内有多少）
    try:
        # 先获取公众号列表
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=5", headers=headers, timeout=10)
        if r.status_code != 200:
            return False, 0
        data = r.json()
        feeds = (data.get("data", {}) or data).get("list", [])

        total_recent = 0
        for f in feeds[:5]:
            fid = f.get("id", "")
            try:
                r2 = requests.get(
                    f"{_WERSS_BASE}/feed/{fid}.xml?limit=50",
                    timeout=10,
                )
                if r2.status_code == 200:
                    import re
                    from datetime import datetime as dt
                    # 简单检查 pubDate 时间戳
                    dates_found = re.findall(r"<pubDate>(.*?)</pubDate>", r2.text)
                    for d in dates_found:
                        try:
                            article_ts = None
                            for fmt in [
                                "%Y-%m-%dT%H:%M:%S%z",
                                "%Y-%m-%dT%H:%M:%S",
                                "%a, %d %b %Y %H:%M:%S %z",
                            ]:
                                try:
                                    article_ts = dt.strptime(d[:31].rstrip("+Z "), fmt).timestamp()
                                    break
                                except ValueError:
                                    continue
                            if article_ts and article_ts >= ts_start:
                                total_recent += 1
                        except Exception:
                            pass
            except Exception:
                pass

        return total_recent >= min_articles, total_recent
    except Exception:
        return False, 0


def ensure_services_ready(start: datetime, end: datetime) -> bool:
    """
    启动前预热：确保 RSSHub 和 wewe-rss 在线，并持有窗口内数据。

    返回 True 表示可以开始采集，False 表示有服务不可用（但仍可继续，采集模块会安静降级）。
    """
    _log("=" * 50)
    _log("  Preflight — 服务预热检查")
    _log(f"  窗口：{start:%Y-%m-%d} → {end:%Y-%m-%d}")
    _log("=" * 50)

    all_ok = True

    # ── 1. RSSHub ──
    if not _wait_service("RSSHub", f"{_RSSHUB_BASE}/36kr/news/latest"):
        _log("  ⚠ RSSHub 未就绪，RSS 采集将降级跳过")
        all_ok = False
    else:
        # RSSHub 就绪后，可以主动访问一次让缓存预热
        try:
            requests.get(f"{_RSSHUB_BASE}/36kr/news/latest", timeout=10)
            requests.get(f"{_RSSHUB_BASE}/cyzone/news", timeout=10)
            requests.get(f"{_RSSHUB_BASE}/qbitai/category/资讯", timeout=10)
            _log("  RSSHub 缓存预热完成")
        except Exception:
            pass

    # ── 2. wewe-rss ──
    if not _wait_service("wewe-rss", f"{_WERSS_BASE}/rss"):
        _log("  ⚠ wewe-rss 未就绪，公众号采集将降级跳过")
        all_ok = False
    else:
        # 尝试主动刷新文章
        _log("  检查 wewe-rss 窗口内文章...")
        ok, count = _check_werss_recent_articles(start)
        if ok:
            _log(f"  窗口内已有 {count} 篇近期文章，跳过刷新")
        else:
            _log(f"  窗口内仅 {count} 篇（<5），触发主动刷新...")
            _refresh_werss_articles(start)
            # 等 15 秒让刷新生效
            _log("  等待刷新结果（15s）...")
            time.sleep(15)
            ok2, count2 = _check_werss_recent_articles(start)
            _log(f"  刷新后窗口内文章：{count2} 篇")
            if not ok2:
                _log("  ⚠ 窗口内文章仍不足，可能 wewe-rss 正在拉取中（继续执行，采集时再补）")

    _log("=" * 50)
    if all_ok:
        _log("  Preflight 通过 ✓ — 开始采集")
    else:
        _log("  Preflight 部分服务不可用 — 继续采集（依赖服务会自动降级）")
    _log("=" * 50)

    return all_ok
