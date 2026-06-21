import os
from typing import List

from models.schema import Article


def collect_xhs() -> List[Article]:
    if os.getenv("ENABLE_XHS", "false").lower() != "true":
        return []
    raise NotImplementedError("小红书接口预留，本期不接通")
