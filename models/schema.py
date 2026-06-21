from pydantic import BaseModel, Field
from typing import Optional, Literal, List
from datetime import datetime

Track = Literal["AI2C", "AI2B", "具身", "ai4S", "前沿科技"]


class Article(BaseModel):
    title: str
    url: str
    content: str = ""
    summary: str = ""
    source: str
    source_type: Literal["rss", "wechat", "web", "search", "manual", "xhs", "weibo"]
    region_hint: Literal["国内", "海外", "未知"] = "未知"
    published_at: Optional[datetime] = None
    fingerprint: str = ""


class Deal(BaseModel):
    project_name: str
    track: Track
    sub_tag: str
    founded_year: Optional[str] = None
    title: str
    team: str = ""
    round: str
    amount: str = "未披露"
    valuation: Optional[str] = "未披露"
    investors: str = ""
    business: str = ""  # v1: 一句话业务描述
    region: str = ""
    region_class: Literal["国内", "海外", "未知"] = "未知"
    detail: str = ""
    importance: Literal["high", "mid", "low"] = "mid"
    official_site: str = ""
    verified_date: str = ""
    date_status: Literal["in_window", "stale", "unknown", "skip"] = "unknown"
    date_confidence: Literal["high", "mid", "low", "skip"] = "low"
    source_url: str
    source_date: str
    sources: List[str] = Field(default_factory=list)
    first_seen_window: str = ""
