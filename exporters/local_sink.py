import shutil
import pathlib
from typing import List


def save_to_desktop(files: List[str], folder: str = "VC雷达") -> str:
    d = pathlib.Path.home() / "Desktop" / folder
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy(f, d / pathlib.Path(f).name)
    return str(d)
