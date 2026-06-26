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

    ★ v3: 改为按项目名去重（不再用 source_url 复合键）。
    Excel 导入的基线数据 source_url 为空，管线跑出来的项目有真实 URL，
    复合键永远无法匹配，导致去重失效。现在只要项目名已在库中就跳过。
    """
    conn = sqlite3.connect(db)
    existing = set()
    try:
        rows = conn.execute("SELECT project_name FROM deals").fetchall()
        existing = {r[0].strip() for r in rows}
    except Exception:
        pass
    conn.close()

    new_deals = []
    skipped = []
    for d in deals:
        if d.project_name.strip() in existing:
            skipped.append(d.project_name)
        else:
            new_deals.append(d)

    if skipped:
        from loguru import logger
        logger.info(f"[dedup_against_db] 跳过 {len(skipped)} 个已入库项目: {skipped}")

    return new_deals
