"""
★ v1 增强：第二轮项目信息核实与补全（Round 2）。

v1 改进：
- 5 路定向搜索：融资细节 / 团队 / 投资方 / 业务产品 / 英文备选
- Web Fetch 兜底：搜索失败时直接抓取项目 URL 原文
- 更丰富的 LLM 提示词：业务痛点、核心产品、对标竞品
- Bocha/Exa 优先（Tavily dev key 有 432 限流问题）
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


def _search_single_query(q: str) -> list:
    """单条查询：优先 Bocha（国内）→ Exa（海外）→ Tavily（兜底）。"""
    import os
    from collectors.search_collector import _bocha_one, _exa_one, _search_one

    results = []
    # Bocha 优先（中文搜索最强，无 432 问题）
    if os.getenv("BOCHA_API_KEY"):
        try:
            results.extend(_bocha_one(q))
        except Exception as e:
            logger.debug(f"[enricher] Bocha failed for '{q[:30]}': {e}")

    # Exa 补充（海外信息）
    if os.getenv("EXA_API_KEY"):
        try:
            results.extend(_exa_one(q))
        except Exception as e:
            logger.debug(f"[enricher] Exa failed for '{q[:30]}': {e}")

    # Tavily 兜底（dev key 容易 432）
    if os.getenv("TAVILY_API_KEY") and not results:
        try:
            results.extend(_search_one(q))
        except Exception as e:
            logger.debug(f"[enricher] Tavily failed for '{q[:30]}': {e}")

    return results


def _search_enrich_queries(d: Deal) -> List[str]:
    """返回拼接好的搜索查询列表。"""
    name = d.project_name
    rnd = d.round or "融资"
    return [q.format(name=name, round=rnd) for q in ENRICH_QUERIES]


def enrich_deal(d: Deal) -> Deal:
    """对单个 Deal 补全缺失字段：多路搜索 + LLM 提取 + Web Fetch 兜底。"""
    missing = [f for f in ("amount", "valuation", "investors", "team", "business", "official_site")
               if not getattr(d, f) or getattr(d, f) in ("", "未披露")]
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

    # 4) LLM 提取
    known = f"项目：{d.project_name} | 轮次：{d.round} | 已知：amount={d.amount}, investors={d.investors}, team={d.team}"
    try:
        j = json.loads(chat(
            "enrich", ENRICH_SYS,
            f"{known}\n缺失字段：{missing}\n\n搜索摘要（共{len(unique_arts)}条结果）：\n{snip}",
            max_tokens=600, json_mode=True,
        ))
    except Exception:
        logger.warning(f"[enricher] LLM 解析失败: {d.project_name}")
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
