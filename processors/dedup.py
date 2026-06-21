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


def dedup(arts: List[Article], db: str = DB_PATH) -> List[Article]:
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS seen(fp TEXT PRIMARY KEY)")

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
