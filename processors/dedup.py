import hashlib
import re
import sqlite3
from pathlib import Path
from typing import List

from config.settings import DB_PATH
from models.schema import Article


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
