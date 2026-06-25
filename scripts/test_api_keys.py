#!/usr/bin/env python3
"""测试 .env 中 5 个 API Key 的连通性和有效性。"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from openai import OpenAI


def test_deepseek():
    try:
        c = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")
        r = c.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
        return ("DeepSeek", True, f"model={r.model}, text={r.choices[0].message.content!r}")
    except Exception as e:
        return ("DeepSeek", False, str(e))


def test_moonshot():
    try:
        base_url = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
        c = OpenAI(api_key=os.getenv("MOONSHOT_API_KEY"), base_url=base_url)
        r = c.chat.completions.create(
            model="kimi-k2.6",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
        return ("Moonshot", True, f"model={r.model}, text={r.choices[0].message.content!r}")
    except Exception as e:
        return ("Moonshot", False, str(e))


def test_tavily():
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": os.getenv("TAVILY_API_KEY"), "query": "AI startup funding", "max_results": 2},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return ("Tavily", True, f"results={len(data.get('results', []))}")
    except Exception as e:
        return ("Tavily", False, str(e))


def main():
    load_dotenv()

    print("=" * 50)
    print("API Key 连通性测试")
    print("=" * 50)

    tests = [test_deepseek, test_moonshot, test_tavily]
    results = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in tests}
        for fut in as_completed(futures):
            name, ok, msg = fut.result()
            results.append((name, ok, msg))

    for name, ok, msg in sorted(results):
        icon = "✅" if ok else "❌"
        print(f"{icon} {name}: {msg}")

    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 50)
    print(f"通过 {passed}/{len(results)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
