"""
VC 管线预检脚本 (v1)
每次运行管线前执行，检查所有依赖服务状态：
1. wewe-rss 容器 + 微信登录状态
2. 搜索 API 连通性 (Bocha/Exa/Kimi)
3. LLM API
4. 文章时间范围
用法：python preflight.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

WERSS_BASE = "http://localhost:8001"
CHECK_OK = "✅"
CHECK_FAIL = "❌"
CHECK_WARN = "⚠️"


def _log(msg: str):
    print(msg, flush=True)


def check_werss_container() -> bool:
    """检查 wewe-rss 容器是否运行。使用无认证的公开端点。"""
    try:
        r = requests.get(f"{WERSS_BASE}/docs", timeout=5)
        if r.status_code == 200:
            _log(f"  {CHECK_OK} wewe-rss 容器运行中 (HTTP {r.status_code})")
            return True
    except requests.ConnectionError:
        pass
    except Exception as e:
        _log(f"  {CHECK_FAIL} wewe-rss 连接异常: {e}")
        return False

    _log(f"  {CHECK_FAIL} wewe-rss 未启动 — 请先启动 Docker 容器: docker start we-mp-rss")
    return False


def check_werss_login() -> bool:
    """检查 wewe-rss 微信登录状态。"""
    try:
        # 先获取 JWT token
        r = requests.post(
            f"{WERSS_BASE}/api/v1/wx/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=5,
        )
        if r.status_code != 200:
            _log(f"  {CHECK_FAIL} wewe-rss 登录失败: {r.text[:100]}")
            return False

        token = r.json().get("data", {}).get("access_token", "")
        if not token:
            _log(f"  {CHECK_FAIL} 无法获取 JWT token")
            return False

        # 检查微信登录状态
        headers = {"Authorization": f"Bearer {token}"}
        r2 = requests.get(f"{WERSS_BASE}/api/v1/wx/auth/qr/status", headers=headers, timeout=5)
        if r2.status_code == 200:
            data = r2.json().get("data", {})
            login_status = data.get("login_status", False)
            if login_status:
                _log(f"  {CHECK_OK} 微信已登录")
                return True
            else:
                _log(f"  {CHECK_WARN} 微信未登录，需要扫码授权")
                return _trigger_qr_login(token)
    except Exception as e:
        _log(f"  {CHECK_FAIL} 检查登录状态异常: {e}")
    return False


def _trigger_qr_login(token: str) -> bool:
    """触发微信扫码登录并等待用户扫描。"""
    headers = {"Authorization": f"Bearer {token}"}

    # 触发登录
    r = requests.get(f"{WERSS_BASE}/api/v1/wx/auth/qr/code", headers=headers, timeout=10)
    if r.status_code != 200:
        _log(f"  {CHECK_FAIL} 触发扫码失败")
        return False

    data = r.json().get("data", {})
    code_url = data.get("code", "")
    _log(f"  🔑 二维码: http://localhost:8001{code_url}")
    _log(f"  📱 请在浏览器打开或用微信扫描上述二维码...")

    # 等待扫码（最多 3 分钟）
    for i in range(60):
        time.sleep(3)
        r2 = requests.get(f"{WERSS_BASE}/api/v1/wx/auth/qr/status", headers=headers, timeout=5)
        status = r2.json().get("data", {}).get("login_status", False)
        if status:
            _log(f"  {CHECK_OK} 扫码成功！微信已登录")
            return True
        if i % 10 == 9:
            _log(f"  ... 等待扫码中 ({int((i+1)*3)}s)")

    _log(f"  {CHECK_FAIL} 扫码超时（3分钟）")
    return False


def check_search_apis() -> dict:
    """检查搜索 API 连通性。"""
    results = {}

    # Bocha
    bocha_key = os.getenv("BOCHA_API_KEY")
    if bocha_key:
        try:
            r = requests.post(
                "https://api.bocha.cn/v1/web-search",
                headers={"Authorization": f"Bearer {bocha_key}", "Content-Type": "application/json"},
                json={"query": "AI 融资", "count": 1},
                timeout=10,
            )
            ok = r.status_code == 200
            results["Bocha"] = ok
            _log(f"  {CHECK_OK if ok else CHECK_FAIL} Bocha 搜索: {'OK' if ok else f'HTTP {r.status_code}'}")
        except Exception as e:
            results["Bocha"] = False
            _log(f"  {CHECK_FAIL} Bocha 搜索: {e}")
    else:
        _log(f"  {CHECK_WARN} Bocha 搜索: 未配置 BOCHA_API_KEY")

    # Exa
    exa_key = os.getenv("EXA_API_KEY")
    if exa_key:
        try:
            r = requests.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": exa_key, "Content-Type": "application/json"},
                json={"query": "AI startup funding", "numResults": 1, "type": "auto"},
                timeout=10,
            )
            ok = r.status_code == 200
            results["Exa"] = ok
            _log(f"  {CHECK_OK if ok else CHECK_FAIL} Exa 搜索: {'OK' if ok else f'HTTP {r.status_code}'}")
        except Exception as e:
            results["Exa"] = False
            _log(f"  {CHECK_FAIL} Exa 搜索: {e}")
    else:
        _log(f"  {CHECK_WARN} Exa 搜索: 未配置 EXA_API_KEY")

    # Kimi (Moonshot)
    kimi_key = os.getenv("MOONSHOT_API_KEY")
    if kimi_key:
        try:
            r = requests.get(
                "https://api.moonshot.cn/v1/models",
                headers={"Authorization": f"Bearer {kimi_key}"},
                timeout=10,
            )
            ok = r.status_code == 200
            results["Kimi"] = ok
            _log(f"  {CHECK_OK if ok else CHECK_FAIL} Kimi API: {'OK' if ok else f'HTTP {r.status_code}'}")
        except Exception as e:
            results["Kimi"] = False
            _log(f"  {CHECK_FAIL} Kimi API: {e}")
    else:
        _log(f"  {CHECK_WARN} Kimi API: 未配置 MOONSHOT_API_KEY")

    return results


def check_llm_api() -> bool:
    """检查 LLM API 连通性。"""
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


def check_werss_articles(start: datetime, end: datetime) -> dict:
    """检查 wewe-rss 中窗口内文章数量。v1: 带 JWT 认证。"""
    # 先获取 token
    token = None
    try:
        r = requests.post(
            f"{WERSS_BASE}/api/v1/wx/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=5,
        )
        if r.status_code == 200:
            token = r.json().get("data", {}).get("access_token", "")
    except Exception:
        pass

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.get(f"{WERSS_BASE}/api/v1/wx/mps?limit=50", headers=headers, timeout=10)
        if r.status_code != 200:
            _log(f"  {CHECK_WARN} 无法获取公众号列表 (HTTP {r.status_code})")
            return {"mps": 0, "articles": 0}
        data = r.json()
        feeds = data.get("data", {}).get("list", [])
        if not feeds:
            feeds = data.get("list", [])
        active = [f for f in feeds if f.get("status", 1) == 1]
        _log(f"  {CHECK_OK} 公众号数量: {len(active)} 个")
    except Exception as e:
        _log(f"  {CHECK_WARN} 公众号列表获取失败: {e}")
        active = []

    # 统计窗口内文章
    try:
        import sqlite3, subprocess, json

        script = f"""
import sqlite3, json
conn = sqlite3.connect('/app/data/db.db')
cur = conn.cursor()
ts_s = {int(start.timestamp())}
ts_e = {int(end.timestamp())}
cur.execute('SELECT COUNT(*) FROM articles WHERE publish_time BETWEEN ? AND ?', (ts_s, ts_e))
count = cur.fetchone()[0]
cur.execute('SELECT COUNT(DISTINCT mp_id) FROM articles WHERE publish_time BETWEEN ? AND ?', (ts_s, ts_e))
mps = cur.fetchone()[0]
print(json.dumps({{'count': count, 'mps': mps}}))
conn.close()
"""
        r = subprocess.run(
            ["docker", "exec", "we-mp-rss", "/app/env_x86_64/bin/python3", "-c", script],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            info = json.loads(r.stdout.strip())
            count = info.get("count", 0)
            mps = info.get("mps", 0)
            if count >= 10:
                _log(f"  {CHECK_OK} 窗口内文章: {count} 篇（{mps} 个公众号）")
            else:
                _log(f"  {CHECK_WARN} 窗口内文章: {count} 篇（{mps} 个公众号）— 可能不足，建议触发采集")
            return {"mps": len(active), "articles": count, "article_mps": mps}
    except Exception as e:
        _log(f"  {CHECK_WARN} 文章统计失败: {e}")

    return {"mps": len(active), "articles": 0, "article_mps": 0}


def main():
    parser = argparse.ArgumentParser(description="VC 管线预检")
    parser.add_argument("--since", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--until", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--skip-login", action="store_true", help="跳过微信登录检查")
    args = parser.parse_args()

    now = datetime.now()
    start = datetime.fromisoformat(args.since) if args.since else now - timedelta(days=4)
    end = datetime.fromisoformat(args.until) if args.until else now

    _log("=" * 50)
    _log(f"  VC 管线预检 | 窗口 {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    _log("=" * 50)

    all_ok = True

    # 1. wewe-rss 容器
    _log("\n[1/4] wewe-rss 服务检查")
    if not check_werss_container():
        all_ok = False
        _log("\n⚠️ 请先启动 wewe-rss: docker start we-mp-rss")
        sys.exit(1)

    # 2. 微信登录
    if not args.skip_login:
        _log("\n[2/4] 微信登录状态")
        if not check_werss_login():
            _log("\n⚠️ 微信未登录，管线可能缺少公众号数据")
            # 不退出，继续检查其他项

    # 3. 搜索 & LLM API
    _log("\n[3/4] API 连通性")
    search_ok = check_search_apis()
    llm_ok = check_llm_api()
    if not any(search_ok.values()):
        _log(f"  {CHECK_WARN} 所有搜索 API 均不可用，管线可能无法补全信息")
    if not llm_ok:
        _log(f"  {CHECK_FAIL} LLM 不可用，管线无法运行")
        all_ok = False

    # 4. 数据统计
    _log("\n[4/4] 数据概览")
    stats = check_werss_articles(start, end)

    # 总结
    _log(f"\n{'='*50}")
    if all_ok and stats.get("articles", 0) >= 10:
        _log(f"  预检通过 ✅ — 可以运行: python main.py --since {start:%Y-%m-%d} --until {end:%Y-%m-%d}")
    else:
        issues = []
        if stats.get("articles", 0) < 10:
            issues.append("文章数量不足，建议先触发采集")
        if not all_ok:
            issues.append("部分服务不可用")
        _log(f"  预检 {CHECK_WARN} — {'; '.join(issues)}")
    _log(f"{'='*50}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
