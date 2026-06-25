"""
VC 管线预检脚本 (v2)
每次运行管线前执行，自动管理 Docker/wewe-rss/微信登录/公众号刷新。

用法：python preflight.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

from processors.preflight import ensure_services_ready

CHECK_OK = "✅"
CHECK_FAIL = "❌"
CHECK_WARN = "⚠️"


def _log(msg: str):
    print(msg, flush=True)


def check_llm_api() -> bool:
    """检查 DeepSeek LLM API 连通性（抽取仍需要）。"""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        _log(f"  {CHECK_WARN} LLM: 未配置 DEEPSEEK_API_KEY")
        return False
    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            },
            timeout=10,
        )
        ok = r.status_code == 200
        _log(f"  {CHECK_OK if ok else CHECK_FAIL} DeepSeek LLM: {'OK' if ok else f'HTTP {r.status_code}'}")
        return ok
    except Exception as e:
        _log(f"  {CHECK_FAIL} DeepSeek LLM: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="VC 管线预检 v2")
    parser.add_argument("--since", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--until", help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    now = datetime.now()
    start = datetime.fromisoformat(args.since) if args.since else now - timedelta(days=1)
    end = datetime.fromisoformat(args.until) if args.until else now

    _log("=" * 50)
    _log(f"  VC 管线预检 v2 | 窗口 {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    _log("=" * 50)

    # 运行完整预热流程（Docker → 容器 → 微信登录 → 公众号刷新）
    ok = ensure_services_ready(start, end)

    # 额外检查 LLM API
    _log("\n[额外] LLM API 连通性")
    llm_ok = check_llm_api()
    if not llm_ok:
        _log(f"  {CHECK_FAIL} LLM 不可用，管线无法运行")
        sys.exit(1)

    _log(f"\n{'='*50}")
    if ok:
        _log(f"  预检通过 ✅ — 可以运行: python main.py")
    else:
        _log(f"  预检 {CHECK_WARN} — 部分服务不可用")
    _log(f"{'='*50}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
