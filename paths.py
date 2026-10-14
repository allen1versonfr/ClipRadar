"""Where VodClipper keeps things, both when run from source and as a built .exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
IS_WIN = sys.platform == "win32"

# read-only resources (ui/, bundled tools/) - inside the exe bundle when frozen
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# folder containing VodClipper.exe (or the source files)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def _data_dir() -> Path:
    if not FROZEN:
        return APP_DIR
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local") if IS_WIN \
        else os.path.join(Path.home(), ".local", "share")
    return Path(base) / "VodClipper"


DATA_DIR = _data_dir()
TOOLS_WRITE = DATA_DIR / "tools"           # where downloaded/updated tools go


def tools_dirs() -> list:
    out = []
    for d in (TOOLS_WRITE, APP_DIR / "tools", RES_DIR / "tools"):
        if d not in out:
            out.append(d)
    return out


def _work_root() -> Path:
    env = os.environ.get("VODCLIPPER_WORK")
    if env:
        return Path(env)
    if not FROZEN:
        return APP_DIR / "projects"
    videos = Path.home() / "Videos"
    return (videos if videos.is_dir() else Path.home()) / "VodClipper"


WORKROOT = _work_root()
LOG_FILE = DATA_DIR / "vodclipper.log"


def ensure_dirs():
    for d in (DATA_DIR, TOOLS_WRITE, WORKROOT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
