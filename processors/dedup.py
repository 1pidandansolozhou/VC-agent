import hashlib
import re
import sqlite3
from pathlib import Path
from typing import List

from config.settings import DB_PATH
from models.schema import Article, Deal


def _fp(a: Article) -> str:
    title_part = re.sub(r"\s+", "", a.title.lower())[:60]
    url_part = a.url.split("?")[0]
    return hashlib.md5(f"{title_part}{url_part}".encode()).hexdigest()


def dedup(arts: List[Article], db: str = DB_PATH, reset: bool = False) -> List[Article]:
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS seen(fp TEXT PRIMARY KEY)")

    # ★ v1 修复：每次管线运行开始时清空历史指纹，防止跨运行误去重
    if reset:
        conn.execute("DELETE FROM seen")
        conn.commit()

    seen, out = set(), []
    for a in arts:
        a.fingerprint = _fp(a)
        if a.fingerprint in seen:
            continue
        if conn.execute("SELECT 1 FROM seen WHERE fp=?", (a.fingerprint,)).fetchone():
            continue
        seen.add(a.fingerprint)
        out.append(a)

    if seen:
        conn.executemany("INSERT OR IGNORE INTO seen VALUES(?)", [(f,) for f in seen])
        conn.commit()
    conn.close()
    return out


def reset_seen(db: str = DB_PATH) -> None:
    """★ v1：每次管线运行前清空历史去重指纹，防止跨运行误去重。"""
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS seen(fp TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM seen")
    conn.commit()
    conn.close()


def dedup_against_db(deals: List[Deal], db: str = DB_PATH) -> List[Deal]:
    """
    检查 Deal 是否已存在于 SQLite 数据库中。

    ★ v2: 改为按 (项目名 + source_url) 去重，而非仅按项目名。
    仅跳过「完全相同的 URL 已入库」的项目，允许同项目不同来源的文章重复采集。
    这样每周报告能看到所有窗口内项目，不会因上轮已入库而丢失。
    """
    conn = sqlite3.connect(db)
    existing = set()
    try:
        rows = conn.execute("SELECT project_name, source_url FROM deals").fetchall()
        existing = {(r[0], r[1]) for r in rows}
    except Exception:
        pass
    conn.close()

    new_deals = []
    skipped = []
    for d in deals:
        key = (d.project_name.strip(), d.source_url.strip())
        if key in existing:
            skipped.append(d.project_name)
        else:
            new_deals.append(d)

    if skipped:
        from loguru import logger
        logger.info(f"[dedup_against_db] 跳过 {len(skipped)} 个已入库项目: {skipped}")

    return new_deals
