"""
★ v1 增强：第二轮项目信息核实与补全（Round 2）。

v1 改进：
- 5 路定向搜索：融资细节 / 团队 / 投资方 / 业务产品 / 英文备选
- Web Fetch 兜底：搜索失败时直接抓取项目 URL 原文
- 更丰富的 LLM 提示词：业务痛点、核心产品、对标竞品
- Kimi 联网 + Tavily 兜底搜索（Bocha/Exa 已废弃）
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests
from loguru import logger

from llm.client import chat
from models.schema import Deal

ENRICH_SYS = """你是 VC 投研数据核实员。给定一个融资项目的已知信息和搜索摘要，
补充缺失字段。只输出 JSON，字段：
amount(融资金额，精确>量级>未披露),
valuation(估值，同上),
investors(投资方，格式：领投·XX，跟投·YY；有才填),
team(创始人姓名+学历/前东家+核心履历),
business(一句话业务：做什么产品/服务，解决什么痛点),
official_site(官网URL，有才填)。
不确定的量级举例：数百万元/数千万元/亿元级/数百万美元/数千万美元/数亿美元。
绝不编造。已知的字段原样返回。"""

# 每个项目的搜索查询模板
ENRICH_QUERIES = [
    '"{name}" {round} 融资 金额',
    '"{name}" 创始人 团队 背景',
    '"{name}" 投资方 领投 参投',
    '"{name}" 业务 产品 解决方案',
    '"{name}" funding round investors',
]


def _web_fetch(url: str, timeout: int = 10) -> Optional[str]:
    """直接抓取 URL 页面文本（兜底方案）。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        # 简单的 HTML 文本提取
        text = r.text
        # 去掉 script/style
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:3000] if len(text) > 3000 else text
    except Exception as e:
        logger.debug(f"[enricher] web_fetch failed for {url}: {e}")
        return None


def _browser_search(query: str, max_results: int = 3) -> List[str]:
    """
    ★ v2.2: 浏览器搜索兜底（DuckDuckGo Lite，无 API key 依赖）。
    当 Kimi 联网搜索无结果时自动降级到此处。
    返回搜索摘要文本列表。
    """
    snippets = []
    try:
        encoded = requests.utils.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.debug(f"[enricher] browser_search HTTP {r.status_code}")
            return []

        # 解析 DuckDuckGo Lite 的简洁 HTML 结果
        # 格式: <a href="...">title</a><span>snippet</span>
        html = r.text
        # 提取所有结果行：每行包含一个链接和可能的摘要
        links = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', html)
        # 提取摘要文本（在 <span class="result-snippet"> 或纯文本中）
        snippets_raw = re.findall(r'<span[^>]*class="[^"]*result-snippet[^"]*"[^>]*>(.+?)</span>', html)
        # 也尝试提取 <td> 中的纯文本结果
        if not snippets_raw:
            snippets_raw = re.findall(r'<td[^>]*class="[^"]*result-snippet[^"]*"[^>]*>(.+?)</td>', html)

        for i, (href, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets_raw[i]).strip() if i < len(snippets_raw) else ""
            if title_clean:
                snippets.append(f"标题：{title_clean}\n摘要：{snippet}\n链接：{href}")

        logger.debug(f"[enricher] browser_search '{query[:30]}' → {len(snippets)} results")
    except Exception as e:
        logger.debug(f"[enricher] browser_search failed for '{query[:30]}': {e}")

    # 如果 DuckDuckGo 也失败，尝试直接抓 Bing
    if not snippets:
        try:
            encoded = requests.utils.quote(query)
            url = f"https://www.bing.com/search?q={encoded}&setlang=zh-cn"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            r = requests.get(url, headers=headers, timeout=15)
            text = re.sub(r'<script[^>]*>.*?</script>', '', r.text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 200:
                snippets.append(f"搜索结果摘要：{text[:2500]}")
            logger.debug(f"[enricher] Bing fallback → {len(text)} chars")
        except Exception as e:
            logger.debug(f"[enricher] Bing fallback failed: {e}")

    return snippets


def _search_single_query(q: str) -> list:
    """单条查询：Kimi 联网 → DuckDuckGo 浏览器兜底。"""
    import os
    from collectors.kimi_search_collector import collect_kimi_search

    results = []
    # Kimi 联网搜索（中文主力）
    if os.getenv("MOONSHOT_API_KEY"):
        try:
            kimi_results = collect_kimi_search([q])
            results.extend(kimi_results)
        except Exception as e:
            logger.debug(f"[enricher] Kimi failed for '{q[:30]}': {e}")

    # ★ v2.2: DuckDuckGo 浏览器兜底（Kimi 无结果或失败时）
    if not results:
        logger.info(f"[enricher] Kimi 无结果，降级到浏览器搜索: '{q[:40]}'")
        try:
            snippets = _browser_search(q, max_results=3)
            if snippets:
                from models.schema import Article
                for s in snippets[:3]:
                    results.append(Article(
                        title=q,
                        url="",
                        content=s,
                        source="ddg_browser",
                        source_type="web",
                    ))
        except Exception as e:
            logger.debug(f"[enricher] browser_search failed: {e}")

    return results


def _safe_json_call(system: str, user: str, project_name: str) -> Optional[dict]:
    """
    ★ v2 加固: 多策略 JSON 提取 + 重试。
    处理 LLM 返回的 markdown 代码块、裸文本中嵌入的 JSON、纯文本等。
    """
    raw = ""
    for attempt in range(2):
        try:
            raw = chat(
                "enrich" if attempt == 0 else "classify",
                system,
                user + ("\n\n★ 严格只输出 JSON 对象，不要 markdown 代码块，不要解释。"
                        if attempt > 0 else ""),
                max_tokens=600,
                json_mode=True,
            )
        except Exception as e:
            logger.warning(f"[enricher] {project_name} LLM 调用失败 (attempt {attempt+1}): {e}")
            continue

        # 策略1: 直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 策略2: 剥离 markdown ```json ... ``` 代码块
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned, flags=re.IGNORECASE)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 策略3: 从文本中提取第一个完整 JSON 对象
        m = re.search(r'\{[^{}]*\}', raw)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

        # 策略4: 宽松匹配 — 找最外层的 {...}
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

        # 如果第一次没成功，重试一次
        if attempt == 0:
            logger.debug(f"[enricher] {project_name} JSON 解析失败，重试中... raw[:100]={raw[:100]}")
            continue
        else:
            # 最后兜底：尝试从文本中直接提取 business
            biz_match = re.search(r'(?:business|业务)[：:]\s*["\']?(.+?)(?:"|\'|$)', raw, re.MULTILINE | re.IGNORECASE)
            if biz_match:
                biz = biz_match.group(1).strip()[:100]
                logger.info(f"[enricher] {project_name} 兜底提取 business = {biz}")
                return {"business": biz}

            logger.warning(f"[enricher] {project_name} LLM 解析失败，raw[:200]={raw[:200]}")
            return None

    return None


def _search_enrich_queries(d: Deal) -> List[str]:
    """返回拼接好的搜索查询列表。"""
    name = d.project_name
    rnd = d.round or "融资"
    return [q.format(name=name, round=rnd) for q in ENRICH_QUERIES]


def enrich_deal(d: Deal) -> Deal:
    """对单个 Deal 补全缺失字段：多路搜索 + LLM 提取 + Web Fetch 兜底。"""
    missing = [f for f in ("amount", "valuation", "investors", "team", "business", "official_site")
               if not getattr(d, f) or getattr(d, f) in ("", "未披露")]
    # ★ v2.2: detail 过短也触发补全（说明公众号原文信息不足）
    if len(getattr(d, "detail", "") or "") < 80:
        if "business" not in missing:
            missing.append("business")
    if not missing:
        return d

    # 1) 多路搜索
    queries = _search_enrich_queries(d)
    all_arts = []
    for q in queries[:4]:  # 前 4 条中文查询
        try:
            results = _search_single_query(q)
            all_arts.extend(results)
        except Exception as e:
            logger.debug(f"[enricher] query '{q[:30]}' error: {e}")
        time.sleep(0.3)  # 避免触发限流

    # 去重
    seen_urls = set()
    unique_arts = []
    for a in all_arts:
        key = a.url.split("?")[0] if hasattr(a, 'url') else str(a)
        if key not in seen_urls:
            seen_urls.add(key)
            unique_arts.append(a)

    # 2) Web Fetch 兜底（搜索无结果时尝试抓原文）
    if len(unique_arts) < 3 and d.source_url:
        fetched = _web_fetch(d.source_url)
        if fetched:
            # 构造伪 Article 用于统一处理
            from models.schema import Article
            unique_arts.append(Article(
                title=d.project_name,
                url=d.source_url,
                content=fetched,
                source="web_fetch",
                source_type="web",
            ))

    if not unique_arts:
        return d

    # 3) 拼接搜索摘要
    snippets = []
    for a in unique_arts[:8]:
        title = getattr(a, 'title', '') or ''
        content = getattr(a, 'content', '') or ''
        source = getattr(a, 'source', 'unknown')
        snippets.append(f"[{source}] {title}\n{content[:500]}")

    snip = "\n\n".join(snippets)
    if len(snip) < 50:
        return d

    # 4) LLM 提取（加固 JSON 解析，防 markdown 包裹 / 纯文本返回）
    known = f"项目：{d.project_name} | 轮次：{d.round} | 已知：amount={d.amount}, investors={d.investors}, team={d.team}"
    j = _safe_json_call(
        ENRICH_SYS,
        f"{known}\n缺失字段：{missing}\n\n搜索摘要（共{len(unique_arts)}条结果）：\n{snip}",
        d.project_name,
    )
    if j is None:
        return d

    # 5) 填充字段
    for f in missing:
        val = j.get(f, "")
        if val and val not in ("", "未披露", "unknown", "null", "None"):
            setattr(d, f, str(val))
            logger.info(f"[enricher] {d.project_name} 补齐 {f} = {str(val)[:80]}")

    return d


def enrich_all(deals: List[Deal], workers: int = 3) -> List[Deal]:
    """批量补全，仅对 in_window 项目执行。降低并发数避免搜索 API 限流。"""
    in_w = [d for d in deals if d.date_status != "stale"]
    stale = [d for d in deals if d.date_status == "stale"]
    if not in_w:
        return deals

    logger.info(f"[ROUND-2] 对 {len(in_w)} 个确认项目补全信息（v1 增强搜索）")
    enriched = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(enrich_deal, d): d for d in in_w}
        from concurrent.futures import as_completed
        for fut in as_completed(futures):
            try:
                enriched.append(fut.result())
            except Exception as e:
                original = futures[fut]
                logger.warning(f"[enricher] {original.project_name} 补全异常: {e}")
                enriched.append(original)

    logger.info(f"[ROUND-2] 补全完成：{len(enriched)} 个项目已处理")
    return enriched + stale
