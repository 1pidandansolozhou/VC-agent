#!/usr/bin/env python3
"""VC 雷达环境初始化脚本：安装依赖、浏览器、启动 RSSHub，检查 wewe-rss。"""

import os
import socket
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], desc: str) -> bool:
    """运行命令并打印结果，失败返回 False。"""
    print(f"\n▶ {desc}")
    print(f"  {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        print(f"  ✅ {desc} 完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ {desc} 失败: {e}")
        return False


def _check_host_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _check_wechat_rss():
    """检查 wewe-rss 是否可达。"""
    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("WEWE_RSS_FEED", "http://localhost:8001/feed/all.atom")
    host = "localhost"
    port = 8001
    if ":" in url.split("//")[-1]:
        host_port = url.split("//")[-1].split("/")[0]
        if ":" in host_port:
            host, p = host_port.split(":")
            port = int(p)

    print(f"\n▶ 检查 wewe-rss ({url})")
    if _check_host_port(host, port):
        print("  ✅ wewe-rss 可达")
    else:
        print(f"  ⚠️  wewe-rss 未启动或不可达（{host}:{port}）")
        print("     请自行启动 wewe-rss 容器，默认地址 http://localhost:8001/feed/all.atom")
        print("     部署后从今天起开始累积公众号文章，无法回溯之前内容。")


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    print("=" * 50)
    print("VC 一级市场项目雷达 · 环境初始化")
    print("=" * 50)

    ok = True

    # 1. pip 依赖
    ok &= _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], "安装 Python 依赖")

    # 2. Playwright Chromium
    ok &= _run([sys.executable, "-m", "playwright", "install", "chromium"], "安装 Chromium 浏览器")

    # 3. 启动 RSSHub
    ok &= _run(["docker-compose", "up", "-d", "rsshub"], "启动 RSSHub")

    # 4. 检查 RSSHub 端口
    print("\n▶ 检查 RSSHub (localhost:1200)")
    if _check_host_port("localhost", 1200):
        print("  ✅ RSSHub 已监听 1200 端口")
    else:
        print("  ⚠️  RSSHub 未在 1200 端口响应，请检查 docker 状态")

    # 5. 检查 wewe-rss
    _check_wechat_rss()

    # 6. 检查 .env key
    print("\n▶ 检查 API Key 配置")
    from dotenv import load_dotenv

    load_dotenv()
    keys = {
        "DEEPSEEK_API_KEY": "LLM 抽取/核查",
        "MOONSHOT_API_KEY": "周报文笔/Kimi 搜索",
        "TAVILY_API_KEY": "海外搜索兜底",
    }
    for k, desc in keys.items():
        if os.getenv(k):
            print(f"  ✅ {k} 已配置（{desc}）")
        else:
            print(f"  ⚠️  {k} 未配置（{desc}）")

    print("\n" + "=" * 50)
    if ok:
        print("环境初始化完成。接下来可以运行：")
        print("  python main.py --dry-run")
    else:
        print("部分步骤失败，请根据上方提示修复后重试。")
    print("=" * 50)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
