"""
v6：VC portfolio 采集已去除（v6 第 2 节明确 "不定向爬固定站点"）。
保留桩函数兼容 imports。
"""

from typing import List

from models.schema import Article


def collect_vc_portfolios() -> List[Article]:
    return []
