from pathlib import Path
from typing import List

from config.sources import MANUAL_LINKS_FILE


def read_manual_links() -> List[str]:
    p = Path(MANUAL_LINKS_FILE)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip().startswith("http")]
