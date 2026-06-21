"""
★ v6 拆出：Kimi 联网搜索采集模块。

从 kimi_search_collector.py 拆出独立模块：
- collect_kimi() — 第一轮用全部赛道感知关键词
- collect_kimi_with_queries(qs) — 供补搜复用，使用指定查询列表
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from loguru import logger

from config.sources import all_cn_queries, all_en_queries
from models.schema import Article


def _has_cjk(s: str) -> bool:
    import re
    return bool(re.search(r"[一-鿿]", s))


def _kimi_search_one(query: str) -> List[Article]:
    """单次 Kimi 联网搜索，复用 kimi_search_collector 底层逻辑。"""
    from collectors.kimi_search_collector import _kimi_search_one as _inner
    region = "国内" if _has_cjk(query) else "海外"
    return _inner(query, region)


def collect_kimi() -> List[Article]:
    """第一轮：用全部赛道感知关键词跑 Kimi 联网搜索。"""
    qs = all_cn_queries() + all_en_queries()
    return _run_queries(qs)


def collect_kimi_with_queries(qs: List[str]) -> List[Article]:
    """补搜：用指定的查询列表跑 Kimi 联网搜索。"""
    return _run_queries(qs)


def _run_queries(queries: List[str]) -> List[Article]:
    if not queries:
        return []
    from config.settings import KIMI_SEARCH_MAX_QUERIES
    all_arts: List[Article] = []
    queries = list(dict.fromkeys(queries))[:KIMI_SEARCH_MAX_QUERIES]
    with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
        futures = {pool.submit(_kimi_search_one, q): q for q in queries}
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                arts = fut.result()
                logger.info(f"KimiSearch '{q[:30]}...' -> {len(arts)} results")
                all_arts.extend(arts)
            except Exception as e:
                logger.warning(f"KimiSearch query '{q[:30]}...' failed: {e}")
    return all_arts
