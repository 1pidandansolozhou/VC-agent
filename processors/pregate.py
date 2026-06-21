"""
★ v1 新增：关键词预过滤门。

进 LLM 前用正则筛出融资信号词，砍 50–70% 不必要的抽取调用，省 token。
"""

import re
from typing import List

from models.schema import Article

# 融资信号词列表（中英文）
_FUNDING_KEYWORDS = re.compile(
    r"(融资|轮|天使|种子|pre-a|pre-seed|a轮|a+轮|领投|跟投|获投|获"
    r"|亿元|万美元|欧元|融资额"
    r"|raised|funding|seed|angel|series a|series a\+"
    r"|investment|million|billion|financing|secured.*funding"
    r"|announces.*round|closes.*round|筹集|募资)",
    re.IGNORECASE,
)

# 明确不相关的信号词（过滤掉盘点/综述/行业报告等）
_EXCLUDE_KEYWORDS = re.compile(
    r"(年度盘点|年终盘点|年度回顾|年度榜单|排行榜|报告|白皮书"
    r"|研报|研究|调研|招聘|裁员|上市|IPO|收购|并购|季度财报)",
    re.IGNORECASE,
)


def _pass_gate(a: Article) -> bool:
    """判断一篇文章是否值得进 LLM 抽取。"""
    text = f"{a.title}\n{a.content}"[:2000]

    # 排除明显不相关的
    if _EXCLUDE_KEYWORDS.search(text):
        return False

    # 必须含融资信号词
    if not _FUNDING_KEYWORDS.search(text):
        return False

    return True


def pre_gate(arts: List[Article]) -> List[Article]:
    """预过滤：只保留含融资信号词的文章。"""
    before = len(arts)
    out = [a for a in arts if a.title and _pass_gate(a)]
    after = len(out)
    if before > 0:
        from loguru import logger
        logger.info(f"[pregate] {before} → {after}（砍掉 {before - after}，{100 - after*100//before}%）")
    return out
