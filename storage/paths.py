import os
import shutil
from datetime import datetime
from pathlib import Path

from config.settings import FLAT_MODE, KEEP_MASTER_SNAPSHOT, OUTPUT_ROOT


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def master_path() -> Path:
    if FLAT_MODE:
        return _ensure(OUTPUT_ROOT) / "总库_VC总库_最新.xlsx"
    return _ensure(OUTPUT_ROOT / "00_总库") / "VC总库_最新.xlsx"


def weekly_paths(start: datetime, end: datetime) -> tuple[Path, Path]:
    tag = f"{start:%Y-%m-%d}_至_{end:%Y-%m-%d}"
    if FLAT_MODE:
        base = _ensure(OUTPUT_ROOT)
        return base / f"周报_{tag}.xlsx", base / f"周报_{tag}.docx"
    sub = _ensure(OUTPUT_ROOT / "周报" / f"{end:%Y}" / f"{end:%m}月")
    return sub / f"VC周报_{tag}.xlsx", sub / f"VC周报_{tag}.docx"


def log_path() -> Path:
    if FLAT_MODE:
        return _ensure(OUTPUT_ROOT) / "run.log"
    return _ensure(OUTPUT_ROOT / "_日志") / "run.log"


def snapshot_master() -> None:
    """每次写总库前，把旧总库快照存档，可回滚。"""
    mp = master_path()
    if KEEP_MASTER_SNAPSHOT and mp.exists():
        dst = _ensure(OUTPUT_ROOT) if FLAT_MODE else _ensure(OUTPUT_ROOT / "_archive")
        name = (
            f"快照_VC总库_{datetime.now():%Y-%m-%d}.xlsx"
            if FLAT_MODE
            else f"VC总库_快照_{datetime.now():%Y-%m-%d}.xlsx"
        )
        shutil.copy(mp, dst / name)


def atomic_replace(tmp: str, final: str) -> None:
    os.replace(tmp, final)
