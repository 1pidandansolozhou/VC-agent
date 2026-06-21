import os
from typing import List

from models.schema import Deal


def push_feishu(deals: List[Deal]):
    """预留：推送项目到飞书多维表。默认关闭。"""
    # TODO: 接入 lark-oapi
    if os.getenv("ENABLE_FEISHU", "false").lower() != "true":
        return
    raise NotImplementedError("飞书推送预留，本期不接通")
