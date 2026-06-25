#!/usr/bin/env python3
"""VC 雷达环境自检脚本：检查依赖、浏览器、RSSHub、wewe-rss、API Key。"""

import os
import socket
import sys
from pathlib import Path


def _check_host_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _check_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _check_playwright_chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return bool(p.chromium.executable_path)
    except Exception:
        return False


def main():
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    from dotenv import load_dotenv

    load_dotenv()

    print("=" * 50)
    print("VC 一级市场项目雷达 · 环境自检")
    print("=" * 50)

    checks = []

    # Python 关键依赖
    print("\n【Python 依赖】")
    for mod in ["requests", "bs4", "feedparser", "openai", "crawl4ai", "loguru", "pydantic", "streamlit", "openpyxl"]:
        ok = _check_module(mod)
        checks.append(ok)
        print(f"  {'✅' if ok else '❌'} {mod}")

    # Playwright Chromium
    print("\n【浏览器】")
    ok = _check_playwright_chromium()
    checks.append(ok)
    print(f"  {'✅' if ok else '❌'} Chromium 已安装")

    # RSSHub
    print("\n【本地服务】")
    ok = _check_host_port("localhost", 1200)
    checks.append(ok)
    print(f"  {'✅' if ok else '❌'} RSSHub (localhost:1200)")

    # wewe-rss
    feed = os.getenv("WEWE_RSS_FEED", "http://localhost:8001/feed/all.atom")
    host, port = "localhost", 8001
    if ":" in feed.split("//")[-1]:
        hp = feed.split("//")[-1].split("/")[0]
        if ":" in hp:
            host, p = hp.split(":")
            port = int(p)
    ok = _check_host_port(host, port)
    checks.append(ok)
    print(f"  {'✅' if ok else '❌'} wewe-rss ({feed})")

    # API Keys
    print("\n【API Key】")
    for k in ["DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "TAVILY_API_KEY"]:
        ok = bool(os.getenv(k))
        checks.append(ok)
        print(f"  {'✅' if ok else '❌'} {k}")

    print("\n" + "=" * 50)
    passed = sum(checks)
    total = len(checks)
    print(f"检查结果：{passed}/{total} 通过")
    if passed == total:
        print("环境就绪，可以运行 python main.py --dry-run")
    else:
        print("有未就绪项，建议运行：python scripts/setup_env.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
