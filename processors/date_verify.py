import sqlite3
import json
from datetime import datetime

from config.settings import DB_PATH, ENABLE_DATE_VERIFY, VERIFY_SCOPE
from llm.client import chat
from models.schema import Deal

# ★ v6：哪些 source_type 需要反查（VERIFY_SCOPE=risky 时只查这些）
_RISKY_SOURCE_TYPES = {"search", "web", "manual"}

SYS = """你是 VC 投研事实核查员。给定项目名、轮次和若干搜索摘要，判断该项目【本轮融资】最早/最权威的公布日期与金额。
规则：媒体转载、盘点、年度回顾不算首发，以官宣或最早报道为准；金额不确定时给量级（数千万元/数百万美元/亿元级等）。
只输出 JSON：{"announce_date":"YYYY-MM-DD 或 YYYY-MM 或 unknown","amount":"金额或量级或未披露","confidence":"high/mid/low"}。无解释。"""


def _classify(date_str: str, start: datetime, end: datetime) -> str:
    if not date_str or date_str == "unknown":
        return "unknown"
    try:
        s = date_str[:10]
        dt = datetime.strptime(s, "%Y-%m-%d") if len(s) == 10 else datetime.strptime(date_str[:7], "%Y-%m")
    except Exception:
        return "unknown"

    if dt > end:
        return "unknown"
    window_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt < window_start:
        if len(date_str) == 7 and dt.year == start.year and dt.month == start.month:
            return "in_window"
        return "stale"
    return "in_window"


def _cache(name: str):
    try:
        c = sqlite3.connect(DB_PATH)
        if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='deals'").fetchone():
            c.close()
            return None
        row = c.execute(
            "SELECT verified_date, date_confidence, amount FROM deals WHERE project_name=? AND verified_date != ''",
            (name,),
        ).fetchone()
        c.close()
        return row
    except Exception:
        return None


def _should_verify(d: Deal) -> bool:
    """根据 VERIFY_SCOPE 判断是否需要对当前 Deal 做反查。
    v7: RSS/wechat 源也做基本日期核查（非反查），确保时间窗口正确。"""
    if VERIFY_SCOPE == "all":
        return True
    # risky：只对非 RSS/wechat 源反查
    return any(
        any(rt in (s or "") for rt in _RISKY_SOURCE_TYPES)
        for s in (d.sources or [])
    )


def _check_source_date(d: Deal, start: datetime, end: datetime) -> str:
    """从 Deal 的 source_date 直接判断时间窗口（不调 LLM）。"""
    if not d.source_date:
        return "unknown"
    try:
        s = d.source_date[:10]
        dt = datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return "unknown"
    if dt > end:
        return "unknown"
    if dt < start.replace(hour=0, minute=0, second=0, microsecond=0):
        return "stale"
    return "in_window"


def verify(d: Deal, start: datetime, end: datetime) -> Deal:
    if not ENABLE_DATE_VERIFY:
        d.date_status = "skip"
        d.date_confidence = "skip"
        return d

    if not _should_verify(d):
        # v7: 可信源（RSS/wechat）用 source_date 做基本时间核查，不再盲目信任
        status = _check_source_date(d, start, end)
        d.date_status = status
        d.date_confidence = "high" if status == "in_window" else "mid"
        if status == "stale":
            from loguru import logger
            logger.info(f"[date_verify] {d.project_name} source_date={d.source_date} → stale（剔除）")
        return d

    hit = _cache(d.project_name)
    if hit and hit[0]:
        d.verified_date, d.date_confidence = hit[0], hit[1] or "mid"
        if d.amount in ("", "未披露") and hit[2] not in ("", "未披露", None):
            d.amount = hit[2]
        d.date_status = _classify(d.verified_date, start, end)
        return d

    # 惰性导入避免循环依赖
    from collectors.search_collector import search_queries as _sq

    qs = [f'"{d.project_name}" {d.round} 融资', f'"{d.project_name}" funding round']
    arts = _sq(qs)[:6]
    snip = "\n\n".join(f"[{a.source}] {a.title}\n{a.content[:300]}" for a in arts)
    if not snip:
        d.date_status = "unknown"
        d.date_confidence = "low"
        return d

    try:
        j = json.loads(
            chat("verify", SYS, f"项目：{d.project_name}\n轮次：{d.round}\n\n搜索：\n{snip}", max_tokens=300, json_mode=True)
        )
    except Exception:
        d.date_status = "unknown"
        d.date_confidence = "low"
        return d

    d.verified_date = j.get("announce_date", "") or ""
    d.date_confidence = j.get("confidence", "low")
    amt = j.get("amount", "")
    if amt and amt not in ("未披露", "unknown") and d.amount in ("", "未披露"):
        d.amount = amt
    d.date_status = _classify(d.verified_date, start, end)
    return d
