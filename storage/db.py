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

# ★ 列名顺序与 DDL 严格一致
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
    return c


def _rebuild_table_if_needed(c: sqlite3.Connection):
    """
    修复 v1 迁移问题：如果 business 列在物理表中的位置与 COLS 不一致
    （ALTER TABLE ADD COLUMN 会把列加到末尾），则重建表。
    """
    info = c.execute("PRAGMA table_info(deals)").fetchall()
    col_names = [r[1] for r in info]

    # 检查 business 是否存在
    if "business" not in col_names:
        c.execute("ALTER TABLE deals ADD COLUMN business TEXT DEFAULT ''")
        info = c.execute("PRAGMA table_info(deals)").fetchall()
        col_names = [r[1] for r in info]

    # 检查列顺序是否与 COLS 一致
    if col_names != COLS:
        # 列顺序不一致（business 被 ALTER TABLE 加到了末尾），重建表
        old_data = c.execute(f"SELECT * FROM deals").fetchall()
        old_cols = col_names

        # 备份 → 删表 → 重建 → 恢复
        c.execute("DROP TABLE deals")
        c.execute(DDL)

        # 按 COLS 顺序重新插入（旧数据可能缺少 business 列）
        for row in old_data:
            old_dict = dict(zip(old_cols, row))
            vals = []
            for col in COLS:
                if col in old_dict:
                    vals.append(old_dict[col])
                elif col == "business":
                    vals.append("")
                elif col == "notion_page_id":
                    vals.append("")
                else:
                    vals.append("")
            placeholders = ",".join(["?"] * len(COLS))
            c.execute(f"INSERT INTO deals VALUES ({placeholders})", vals)

        c.commit()


def upsert(deals: List[Deal], window_tag: str = "", db: str = DB_PATH):
    c = conn(db)
    _rebuild_table_if_needed(c)

    now = datetime.now().isoformat()
    cols_str = ",".join(COLS)
    ph = ",".join(["?"] * len(COLS))

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
        c.execute(f"INSERT OR REPLACE INTO deals ({cols_str}) VALUES ({ph})", vals)
    c.commit()
    c.close()


def all_rows(db: str = DB_PATH) -> List[dict]:
    c = conn(db)
    cur = c.execute(f"SELECT {','.join(COLS)} FROM deals ORDER BY updated_at DESC")
    rows = cur.fetchall()
    c.close()
    return [dict(zip(COLS, r)) for r in rows]


def rows_by_window(window_tag: str, db: str = DB_PATH) -> List[dict]:
    c = conn(db)
    cur = c.execute(
        f"SELECT {','.join(COLS)} FROM deals WHERE first_seen_window=? ORDER BY updated_at DESC",
        (window_tag,),
    )
    rows = cur.fetchall()
    c.close()
    return [dict(zip(COLS, r)) for r in rows]


def all_deals(db: str = DB_PATH) -> List[dict]:
    return all_rows(db)
