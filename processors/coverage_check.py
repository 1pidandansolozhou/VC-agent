"""
★ v6 新增：数量检查 + 补搜决策（Stage 3）。

统计三个指标：
1. 总数 < MIN_DEALS_TOTAL → 触发全量补搜
2. 国内 < MIN_DEALS_CN → 触发 CN 定向补搜
3. 海外 < MIN_DEALS_GLOBAL → 触发 EN 定向补搜

最多执行 MAX_SEARCH_RETRIES 次补搜（防无限循环）。
"""

from typing import List, Tuple

from config.settings import MIN_DEALS_CN, MIN_DEALS_GLOBAL, MIN_DEALS_TOTAL
from models.schema import Deal


def _count(deals: List[Deal]) -> Tuple[List[Deal], List[Deal], List[Deal]]:
    """返回 (in_window, cn, global) 三组列表。"""
    in_win = [d for d in deals if d.date_status != "stale"]
    cn = [d for d in in_win if d.region_class == "国内"]
    gl = [d for d in in_win if d.region_class == "海外"]
    return in_win, cn, gl


def should_retry(deals: List[Deal]) -> List[str]:
    """检查三个指标，返回需要补搜的原因列表（空列表表示无需补搜）。"""
    in_win, cn, gl = _count(deals)
    reasons = []
    if len(in_win) < MIN_DEALS_TOTAL:
        reasons.append(f"总数{len(in_win)}<{MIN_DEALS_TOTAL}")
    if len(cn) < MIN_DEALS_CN:
        reasons.append(f"国内{len(cn)}<{MIN_DEALS_CN}")
    if len(gl) < MIN_DEALS_GLOBAL:
        reasons.append(f"海外{len(gl)}<{MIN_DEALS_GLOBAL}")
    return reasons


def build_retry_queries(deals: List[Deal]) -> Tuple[List[str], List[str]]:
    """根据缺口构造补搜词：缺中文就多加 CN 词，缺英文就多加 EN 词。"""
    in_win, cn, gl = _count(deals)
    from config.sources import RETRY_QUERIES_CN, RETRY_QUERIES_EN

    cn_qs, en_qs = [], []
    if len(cn) < MIN_DEALS_CN:
        cn_qs = RETRY_QUERIES_CN
    if len(gl) < MIN_DEALS_GLOBAL:
        en_qs = RETRY_QUERIES_EN
    if len(in_win) < MIN_DEALS_TOTAL:
        # 总数不足：CN+EN 都加
        cn_qs = cn_qs or RETRY_QUERIES_CN
        en_qs = en_qs or RETRY_QUERIES_EN
    return cn_qs, en_qs
