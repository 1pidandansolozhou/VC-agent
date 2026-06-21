import json
from pathlib import Path
from datetime import datetime, timedelta

from config.settings import STATE_PATH


def get_window(since: str | None = None, until: str | None = None) -> tuple[datetime, datetime]:
    now = datetime.now()
    state_path = Path(STATE_PATH)
    from config.settings import WINDOW_DAYS_DEFAULT

    if since:
        start = datetime.fromisoformat(since)
    elif state_path.exists():
        try:
            start = datetime.fromisoformat(json.loads(state_path.read_text(encoding="utf-8"))["last_run"])
        except Exception:
            start = now - timedelta(days=WINDOW_DAYS_DEFAULT)
    else:
        start = now - timedelta(days=WINDOW_DAYS_DEFAULT)

    # ★ v1 修复：保证窗口至少覆盖 WINDOW_DAYS 天，防止 state.json 中的 last_run 过近导致窗口缩水
    min_start = now - timedelta(days=WINDOW_DAYS_DEFAULT)
    if start > min_start:
        start = min_start

    end = datetime.fromisoformat(until) if until else now
    return start, end


def mark_done(end: datetime) -> None:
    state_path = Path(STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_run": end.isoformat()}, ensure_ascii=False), encoding="utf-8")
