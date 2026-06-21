import os
from typing import List

from models.schema import Deal


def sync_notion(database_id: str | None, deals: List[Deal] | None = None):
    """预留：将 SQLite 中的项目同步到 Notion 数据库。默认关闭。"""
    # TODO: 接入 notion-client，按 project_name upsert 并回写 page_id
    if os.getenv("ENABLE_NOTION", "false").lower() != "true":
        return
    raise NotImplementedError("Notion 同步预留，本期不接通")
