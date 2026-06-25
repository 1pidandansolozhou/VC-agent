import json
from pathlib import Path
from datetime import datetime, timedelta

from config.settings import STATE_PATH, WINDOW_DAYS_DEFAULT


def get_window(since: str | None = None, until: str | None = None) -> tuple[datetime, datetime]:
    """
    ★ v2 修复：窗口始终对齐到整天边界。

    默认窗口 = 前一天 00:00:00 → 当天 23:59:59
    例如：6/25 运行 → 窗口覆盖 6/24 00:00 ~ 6/25 23:59

    --since 可手动扩展起始日（用于补抓缺失数据）。
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since:
        start = datetime.fromisoformat(since).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # 默认：前一天 00:00:00
        start = today_start - timedelta(days=WINDOW_DAYS_DEFAULT)

    if until:
        end = datetime.fromisoformat(until).replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        # 默认：当天 23:59:59
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    return start, end


def mark_done(end: datetime) -> None:
    state_path = Path(STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_run": end.isoformat()}, ensure_ascii=False), encoding="utf-8")
