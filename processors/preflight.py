"""
v2.1: 启动前预热 — Docker 自启 + 容器自启 + 微信登录 + 数据新鲜度检测 + 智能刷新。

流程：
  1. 检测并启动 Docker Desktop（如未运行）
  2. 检测并启动 wewe-rss 容器（如未运行）— ★只用 start，绝不 restart（保护微信 session）
  3. 检查微信登录 → 未登录则弹二维码等用户扫码（最多 90s）
  4. ★数据新鲜度检测 — 检查最新文章日期，若滞后则触发刷新 + 轮询等待
  5. 验证窗口内文章数 → 全部就绪 → 放行采集
"""

import os
import subprocess
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

_WERSS_BASE = "http://localhost:8001"
_CONTAINER_NAME = "we-mp-rss"
_CHECK_TIMEOUT = 5
_MAX_WAIT_S = 120          # Docker Desktop 启动最长时间
_QR_TIMEOUT_S = 90          # 扫码等待
_CONTAINER_WAIT_S = 60      # 容器 HTTP 就绪等待
_REFRESH_POLL_MAX_S = 300   # ★ 刷新后轮询等数据到位的最长时间（5分钟）
_REFRESH_POLL_EARLY_S = 30  # 初期轮询间隔
_REFRESH_POLL_LATE_S = 60   # 后期轮询间隔
_TOKEN = None


def _get_token() -> Optional[str]:
    """获取/刷新 wewe-rss JWT token。"""
    global _TOKEN
    try:
        r = requests.post(
            f"{_WERSS_BASE}/api/v1/wx/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=_CHECK_TIMEOUT,
        )
        if r.status_code == 200:
            _TOKEN = r.json().get("data", {}).get("access_token", "")
            return _TOKEN
    except Exception:
        pass
    return _TOKEN


def _log(msg: str):
    print(f"  [preflight] {msg}", flush=True)
    logger.info(f"[preflight] {msg}")


# ═══════════════════════════════════════════════════
# Docker 自动管理
# ═══════════════════════════════════════════════════

def _is_docker_running() -> bool:
    """检测 Docker Desktop 是否在运行。"""
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _start_docker_desktop() -> bool:
    """启动 Docker Desktop 并等待就绪。"""
    if _is_docker_running():
        _log("Docker Desktop 已在运行 ✓")
        return True

    docker_exe = os.getenv("DOCKER_DESKTOP_PATH", r"E:\Docker\Docker Desktop\Docker Desktop.exe")
    exe_path = Path(docker_exe)
    if not exe_path.exists():
        _log(f"⚠ Docker Desktop 未找到: {docker_exe}")
        _log(f"  请设置环境变量 DOCKER_DESKTOP_PATH 指向正确路径")
        return False

    _log(f"启动 Docker Desktop: {docker_exe} ...")
    try:
        if sys.platform == "win32":
            subprocess.Popen([str(exe_path)], creationflags=subprocess.DETACHED_PROCESS)
        else:
            subprocess.Popen([str(exe_path)])
    except Exception as e:
        _log(f"✗ 启动 Docker Desktop 失败: {e}")
        return False

    deadline = time.time() + _MAX_WAIT_S
    while time.time() < deadline:
        time.sleep(3)
        if _is_docker_running():
            elapsed = _MAX_WAIT_S - (deadline - time.time())
            _log(f"Docker Desktop 已就绪 ✓（耗时 {elapsed:.0f}s）")
            return True
        remaining = int(deadline - time.time())
        if remaining % 15 == 0:
            _log(f"等待 Docker Desktop...（剩余 {remaining}s）")

    _log(f"✗ Docker Desktop 未能在 {_MAX_WAIT_S}s 内就绪")
    return False


# ═══════════════════════════════════════════════════
# wewe-rss 容器管理（★ 只用 start，不用 restart — 保护微信 session）
# ═══════════════════════════════════════════════════

def _ensure_werss_container() -> bool:
    """确保 wewe-rss 容器存在并运行，等待 HTTP 就绪。"""
    # 1. 检查容器是否存在
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={_CONTAINER_NAME}", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    if _CONTAINER_NAME not in r.stdout:
        _log(f"✗ 容器 '{_CONTAINER_NAME}' 不存在")
        _log(f"  请先创建容器:")
        _log(f"  docker run -d --name {_CONTAINER_NAME} -p 8001:8001 rachelos/we-mp-rss:latest")
        return False

    # 2. 检查是否在运行 ★ 关键：只用 docker start，不用 docker restart
    r = subprocess.run(
        ["docker", "ps", "--filter", f"name={_CONTAINER_NAME}", "--format", "{{.Status}}"],
        capture_output=True, text=True, timeout=10,
    )
    if "Up" not in r.stdout:
        _log(f"启动容器 {_CONTAINER_NAME}（docker start，保留微信 session）...")
        subprocess.run(["docker", "start", _CONTAINER_NAME], capture_output=True, timeout=30)
    else:
        _log("wewe-rss 容器已在运行 ✓")
        return True

    # 3. 等待 HTTP 就绪
    _log(f"等待 wewe-rss HTTP 就绪...")
    deadline = time.time() + _CONTAINER_WAIT_S
    while time.time() < deadline:
        try:
            r = requests.get(f"{_WERSS_BASE}/docs", timeout=_CHECK_TIMEOUT)
            if r.status_code == 200:
                _log("wewe-rss 已就绪 ✓")
                return True
        except Exception:
            pass
        time.sleep(2)

    _log(f"✗ wewe-rss 容器未能在 {_CONTAINER_WAIT_S}s 内就绪")
    return False


# ═══════════════════════════════════════════════════
# 微信登录管理
# ═══════════════════════════════════════════════════

def _check_wx_login() -> bool:
    """检查微信是否已登录 wewe-rss。"""
    token = _get_token()
    if not token:
        return False

    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/auth/qr/status", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data.get("login_status", False):
                return True
    except Exception:
        pass
    return False


def _trigger_qr_login() -> bool:
    """
    触发微信扫码登录，阻塞等待用户扫码。
    返回 True=登录成功，False=超时/失败。
    """
    token = _get_token()
    if not token:
        _log("无法获取 JWT token，跳过扫码")
        return False

    headers = {"Authorization": f"Bearer {token}"}

    # 获取二维码
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/auth/qr/code", headers=headers, timeout=10)
        if r.status_code != 200:
            _log(f"触发扫码失败 (HTTP {r.status_code})")
            return False
        code_path = r.json().get("data", {}).get("code", "")
    except Exception as e:
        _log(f"获取二维码失败: {e}")
        return False

    qr_url = f"http://localhost:8001{code_path}" if code_path.startswith("/") else code_path
    _log("")
    _log(f"  ╔══════════════════════════════════════╗")
    _log(f"  ║  🔑 微信扫码授权                      ║")
    _log(f"  ║  📱 {qr_url}  ║")
    _log(f"  ║  请在浏览器打开或用微信扫描上方二维码  ║")
    _log(f"  ╚══════════════════════════════════════╝")
    _log("")

    # 轮询等待扫码
    deadline = time.time() + _QR_TIMEOUT_S
    last_report = 0
    while time.time() < deadline:
        time.sleep(3)
        try:
            r = requests.get(f"{_WERSS_BASE}/api/v1/wx/auth/qr/status", headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json().get("data", {})
                if data.get("login_status", False):
                    _log("扫码成功！微信已登录 ✓")
                    return True
        except Exception:
            pass
        elapsed = int(time.time() - (deadline - _QR_TIMEOUT_S))
        if elapsed - last_report >= 30:
            _log(f"... 等待扫码中 ({elapsed}s / {_QR_TIMEOUT_S}s)")
            last_report = elapsed

    _log(f"扫码超时（{_QR_TIMEOUT_S}s）")
    return False


# ═══════════════════════════════════════════════════
# ★ 数据新鲜度检测（v2.1 新增）
# ═══════════════════════════════════════════════════

def _get_latest_article_date() -> Optional[datetime]:
    """
    获取 wewe-rss 中最新一篇文章的发布时间。
    纯诊断函数，不修改任何数据。
    """
    token = _get_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/articles?limit=5", headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        articles = (data.get("data", {}) or data).get("list", [])
        if not articles:
            articles = data.get("data", []) if isinstance(data.get("data"), list) else []

        latest_ts = 0
        for a in articles:
            pt = a.get("publish_time", 0) or 0
            if isinstance(pt, float):
                pt = int(pt)
            if pt > latest_ts:
                latest_ts = pt

        return datetime.fromtimestamp(latest_ts) if latest_ts else None
    except Exception as e:
        logger.debug(f"[preflight] 获取最新文章日期失败: {e}")
        return None


def _wait_for_fresh_data(min_date: datetime, max_wait_s: int = _REFRESH_POLL_MAX_S) -> Tuple[bool, Optional[datetime]]:
    """
    ★ 轮询等待 wewe-rss 数据更新到 min_date 之后。

    参数:
      min_date: 期望的最早日期（如昨天 00:00）
      max_wait_s: 最长等待秒数

    返回:
      (是否有新数据, 当前最新文章日期)
    """
    initial = _get_latest_article_date()
    initial_str = initial.strftime("%m-%d %H:%M") if initial else "N/A"
    min_str = min_date.strftime("%m-%d")

    if initial and initial >= min_date:
        _log(f"数据已达到 {initial_str}，无需等待 ✓")
        return True, initial

    _log(f"数据最新: {initial_str}，目标 ≥ {min_str}，开始等待...")

    deadline = time.time() + max_wait_s
    early_checks = 3  # 前 3 次用较短的间隔
    check_count = 0

    while time.time() < deadline:
        check_count += 1
        interval = _REFRESH_POLL_EARLY_S if check_count <= early_checks else _REFRESH_POLL_LATE_S
        time.sleep(interval)

        latest = _get_latest_article_date()
        latest_str = latest.strftime("%m-%d %H:%M") if latest else "N/A"
        elapsed = int(time.time() - (deadline - max_wait_s))

        if latest and latest >= min_date:
            _log(f"✅ 数据已更新到 {latest_str}（等待 {elapsed}s）")
            return True, latest

        # 即使没达标也打印进展
        if check_count <= 3 or check_count % 3 == 0:
            _log(f"... 等待数据更新 ({elapsed}s/{max_wait_s}s)，当前最新: {latest_str}")

    _log(f"⚠ 等待 {max_wait_s}s 后数据仍未更新到 {min_str}，当前最新：{latest_str if latest else 'N/A'}")
    return False, _get_latest_article_date()


# ═══════════════════════════════════════════════════
# 公众号数据刷新
# ═══════════════════════════════════════════════════

def _refresh_all_accounts() -> Tuple[int, Optional[datetime], Optional[datetime]]:
    """
    刷新全部公众号数据，返回 (刷新数, 刷新前最新日期, 刷新后最新日期)。
    """
    token = _get_token()
    if not token:
        return 0, None, None

    headers = {"Authorization": f"Bearer {token}"}
    before = _get_latest_article_date()

    # 尝试批量刷新
    batch_ok = False
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps/refresh", headers=headers, timeout=30)
        if r.status_code == 200:
            batch_ok = True
    except Exception:
        pass

    if not batch_ok:
        # 逐个刷新
        try:
            r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                feeds = (data.get("data", {}) or data).get("list", [])
                refreshed = 0
                for i, f in enumerate(feeds):
                    fid = f.get("id", "")
                    try:
                        rr = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps/{fid}/refresh", headers=headers, timeout=15)
                        if rr.status_code == 200:
                            refreshed += 1
                    except Exception:
                        pass
                    if (i + 1) % 10 == 0:
                        _log(f"  已刷新 {i + 1}/{len(feeds)}...")
                if refreshed > 0:
                    _log(f"逐个刷新 {refreshed}/{len(feeds)} 个公众号 ✓")
                return refreshed, before, None
        except Exception:
            pass
        return 0, before, None

    # 批量刷新成功
    try:
        r2 = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
        if r2.status_code == 200:
            data = r2.json()
            feeds = (data.get("data", {}) or data).get("list", [])
            return len(feeds), before, None
    except Exception:
        pass

    return 0, before, None


def _count_window_articles(window_start: datetime) -> Tuple[int, int, int]:
    """
    使用 JSON API 统计窗口内文章数。
    返回 (文章数, 订阅公众号总数, 有窗口内文章的公众号数)。
    """
    token = _get_token()
    if not token:
        return 0, 0, 0

    headers = {"Authorization": f"Bearer {token}"}
    ts_start = int(window_start.timestamp())

    total_mps = 0
    try:
        r = requests.get(f"{_WERSS_BASE}/api/v1/wx/mps?limit=100", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            feeds = (data.get("data", {}) or data).get("list", [])
            total_mps = len(feeds)
    except Exception:
        pass

    total_articles = 0
    seen_mps = set()
    offset = 0
    all_old_pages = 0

    while offset < 2000:
        try:
            r = requests.get(
                f"{_WERSS_BASE}/api/v1/wx/articles?limit=100&offset={offset}",
                headers=headers, timeout=15,
            )
            if r.status_code != 200:
                break
            data = r.json()
            articles = (data.get("data", {}) or data).get("list", [])
            if not articles:
                break

            page_in_window = 0
            for a in articles:
                pub_time = a.get("publish_time", 0)
                if isinstance(pub_time, float):
                    pub_time = int(pub_time)
                if pub_time >= ts_start:
                    total_articles += 1
                    page_in_window += 1
                    mp_id = a.get("mp_id", "")
                    if mp_id:
                        seen_mps.add(mp_id)

            if page_in_window == 0:
                all_old_pages += 1
                if all_old_pages >= 2:
                    break
            else:
                all_old_pages = 0

            offset += 100
        except Exception:
            break

    return total_articles, total_mps, len(seen_mps)


# ═══════════════════════════════════════════════════
# ★ 主入口（v2.1 — 含数据新鲜度检测）
# ═══════════════════════════════════════════════════

def ensure_services_ready(start: datetime, end: datetime) -> bool:
    """
    v2.1: 阻塞式预热 — Docker 自启 + 容器自启 + 微信登录 + 数据新鲜度检测 + 智能刷新。

    流程:
      1. Docker Desktop → 未运行则启动
      2. wewe-rss 容器 → 未运行则启动（只用 start，不 restart）
      3. 微信登录 → 检查 + 二维码
      4. ★ 数据新鲜度 → 检查最新文章日期 → 滞后则刷新 → 轮询等待新数据
      5. 数据验证 → 报告窗口内文章数和活跃公众号
    """
    _log("=" * 50)
    _log("  Preflight v2.1 — Docker + 微信 + 数据新鲜度检测")
    _log(f"  目标窗口：{start:%Y-%m-%d} → {end:%Y-%m-%d}")
    _log("=" * 50)

    # ── 1. Docker Desktop ──
    _log("")
    _log("── [1/5] Docker Desktop ──")
    if not _start_docker_desktop():
        _log("=" * 50)
        _log("  ✗ Preflight 失败 — Docker Desktop 不可用")
        _log("=" * 50)
        return False

    # ── 2. wewe-rss 容器（★ 只用 start，不 restart）──
    _log("")
    _log("── [2/5] wewe-rss 容器 ──")
    if not _ensure_werss_container():
        _log("=" * 50)
        _log("  ✗ Preflight 失败 — wewe-rss 容器不可用")
        _log("=" * 50)
        return False

    # ── 3. 微信登录 ──
    _log("")
    _log("── [3/5] 微信登录状态 ──")
    wx_logged_in = _check_wx_login()
    if wx_logged_in:
        _log("微信已登录 ✓")
    else:
        _log("微信未登录，需要扫码授权")
        if _trigger_qr_login():
            wx_logged_in = True
        else:
            _log("")
            _log("  ⚠ 微信扫码超时，管线将继续但可能无法拉取新数据")
            _log("  你可以稍后在 http://localhost:8001 完成登录后重新运行")
            _log("")

    # ── 4. ★ 数据新鲜度检测 + 智能刷新 ──
    _log("")
    _log("── [4/5] 数据新鲜度检测 ──")

    # 期望的截止日期：至少覆盖到昨天 00:00
    # 如果最新文章 < 昨天 00:00 → 说明数据停在更早，需要刷新
    from datetime import timedelta as _td
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    min_expected = today_start - _td(days=1)  # 昨天 00:00

    latest = _get_latest_article_date()
    latest_str = latest.strftime("%Y-%m-%d %H:%M") if latest else "N/A"
    _log(f"wewe-rss 最新文章: {latest_str}")

    needs_refresh = latest is None or latest < min_expected

    if needs_refresh:
        _log(f"⚠ 数据滞后（最新 {latest_str}，期望 ≥ {min_expected:%m-%d}）")

        if wx_logged_in:
            # 触发刷新
            n, before, _ = _refresh_all_accounts()
            if n > 0:
                _log(f"已触发 {n} 个公众号刷新，等待数据拉取...")

                # ★ 轮询等待数据到位（最长 5 分钟）
                ok, new_latest = _wait_for_fresh_data(min_expected)
                if ok:
                    new_str = new_latest.strftime("%Y-%m-%d %H:%M") if new_latest else "?"
                    _log(f"✅ 数据已更新至 {new_str}")
                else:
                    new_str = new_latest.strftime("%Y-%m-%d %H:%M") if new_latest else "N/A"
                    _log(f"⚠ 数据未达预期，将使用现有数据继续（最新: {new_str}）")
            else:
                _log("⚠ 无法触发刷新，继续使用现有数据")
        else:
            _log("微信未登录，跳过刷新（但仍可使用现有数据采集）")
    else:
        _log(f"✅ 数据新鲜度合格（最新 {latest_str}），跳过刷新")

    # ── 5. 数据验证 ──
    _log("")
    _log("── [5/5] 数据验证 ──")
    w_total, w_mps, w_active = _count_window_articles(start)
    _log(f"数据概览: {w_mps} 个订阅公众号 | {w_active} 个有窗口内文章 | 总计 {w_total} 篇")

    # 如果窗口内文章太少但数据本身是新的，再查一次（可能页码还没到）
    if w_total < 5 and wx_logged_in:
        latest_now = _get_latest_article_date()
        if latest_now and latest_now >= min_expected:
            _log("数据已更新但窗口内文章偏少，再等 30s 让分页稳定...")
            time.sleep(30)
            w_total, w_mps, w_active = _count_window_articles(start)
            _log(f"二次检查: {w_active} 个活跃号 | 总计 {w_total} 篇")

    # ── 总结 ──
    _log("")
    _log("=" * 50)
    if w_total >= 3:
        _log(f"  ✅ Preflight 通过 — 服务就绪，窗口内 {w_total} 篇文章")
    elif wx_logged_in:
        _log(f"  ⚠ Preflight 通过（数据偏少）— 窗口内 {w_total} 篇，继续执行")
    else:
        _log(f"  ⚠ Preflight 通过（微信未登录）— 可能缺少公众号数据")
    _log("=" * 50)

    return True
