import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

from config.settings import DB_PATH
from models.schema import Deal

DDL = """CREATE TABLE IF NOT EXISTS deals(
 project_name TEXT PRIMARY KEY, track TEXT, sub_tag TEXT, round TEXT,
 amount TEXT, valuation TEXT, investors TEXT, business TEXT DEFAULT '', region TEXT, region_class TEXT,
 founded_year TEXT, title TEXT, team TEXT, detail TEXT, importance TEXT,
 official_site TEXT, verified_date TEXT, date_status TEXT, date_confidence TEXT,
 source_url TEXT, source_date TEXT, sources TEXT, first_seen_window TEXT,
 notion_page_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT)"""

COLS = [
    "project_name", "track", "sub_tag", "round", "amount", "valuation", "investors", "business",
    "region", "region_class", "founded_year", "title", "team", "detail", "importance", "official_site",
    "verified_date", "date_status", "date_confidence", "source_url", "source_date", "sources",
    "first_seen_window", "notion_page_id", "created_at", "updated_at",
]


def conn(db: str = DB_PATH):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.execute(DDL)
    # v7 迁移：旧表可能缺少 business 列
    cols = [r[1] for r in c.execute("PRAGMA table_info(deals)").fetchall()]
    if "business" not in cols:
        c.execute("ALTER TABLE deals ADD COLUMN business TEXT DEFAULT ''")
    return c


def upsert(deals: List[Deal], window_tag: str = "", db: str = DB_PATH):
    c = conn(db)
    now = datetime.now().isoformat()
    for d in deals:
        prev = c.execute(
            "SELECT notion_page_id, created_at, first_seen_window FROM deals WHERE project_name=?",
            (d.project_name,),
        ).fetchone()
        pid, created, fsw = (prev[0], prev[1], prev[2]) if prev else ("", now, window_tag or "")
        vals = [
            d.project_name, d.track, d.sub_tag, d.round, d.amount, d.valuation, d.investors, d.business, d.region,
            d.region_class, d.founded_year, d.title, d.team, d.detail, d.importance, d.official_site,
            d.verified_date, d.date_status, d.date_confidence, d.source_url, d.source_date,
            json.dumps(d.sources, ensure_ascii=False), fsw, pid, created, now,
        ]
        c.execute(f"INSERT OR REPLACE INTO deals VALUES ({','.join(['?'] * len(COLS))})", vals)
    c.commit()
    c.close()


def all_rows(db: str = DB_PATH) -> List[dict]:
    c = conn(db)
    cur = c.execute(f"SELECT {','.join(COLS)} FROM deals ORDER BY updated_at DESC")
    rows = cur.fetchall()
    c.close()
    return [dict(zip(COLS, r)) for r in rows]


def rows_by_window(window_tag: str, db: str = DB_PATH) -> List[dict]:
    """按窗口标签查询周报应包含的项目，避免同窗口重跑时因无新文章而覆盖出空周报。"""
    c = conn(db)
    cur = c.execute(
        f"SELECT {','.join(COLS)} FROM deals WHERE first_seen_window=? ORDER BY updated_at DESC",
        (window_tag,),
    )
    rows = cur.fetchall()
    c.close()
    return [dict(zip(COLS, r)) for r in rows]


# 兼容旧引用
def all_deals(db: str = DB_PATH) -> List[dict]:
    return all_rows(db)
