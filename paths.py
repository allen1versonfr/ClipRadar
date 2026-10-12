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
    if os.environ.get("VODCLIPPER_DATA"):
        return Path(os.environ["VODCLIPPER_DATA"])
    if not FROZEN:
        return APP_DIR
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local")
    else:
        base = os.path.join(Path.home(), ".local", "share")
    return Path(base) / "VodClipper"


DATA_DIR = _data_dir()
TOOLS_WRITE = DATA_DIR / "tools"           # where downloaded/updated tools go
MODELS_DIR = DATA_DIR / "models"           # Whisper caption models

# Keep caption models inside VodClipper's own folder (so uninstalling removes them)
# instead of the shared %USERPROFILE%\.cache\huggingface.
if FROZEN:
    os.environ.setdefault("HF_HOME", str(MODELS_DIR))


def tools_dirs() -> list:
    out = []
    for d in (TOOLS_WRITE, APP_DIR / "tools", RES_DIR / "tools"):
        if d not in out:
            out.append(d)
    return out


def _videos_folder() -> Path:
    """The user's real Videos folder (it may be moved, e.g. into OneDrive)."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                            ("d4", ctypes.c_ubyte * 8)]

            u = uuid.UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC")  # FOLDERID_Videos
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path and os.path.isdir(path):
                    return Path(path)
        except Exception:
            pass
    return Path.home() / "Videos"


def _work_root() -> Path:
    env = os.environ.get("VODCLIPPER_WORK")
    if env:
        return Path(env)
    if not FROZEN:
        return APP_DIR / "projects"
    videos = _videos_folder()
    return (videos if videos.is_dir() else Path.home()) / "VodClipper"


WORKROOT = _work_root()
LOG_FILE = DATA_DIR / "vodclipper.log"


def ensure_dirs():
    for d in (DATA_DIR, TOOLS_WRITE, WORKROOT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def register_folders():
    """Tell the uninstaller where VodClipper keeps things (HKCU\\Software\\VodClipper)."""
    if not (IS_WIN and FROZEN):
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\VodClipper") as k:
            winreg.SetValueEx(k, "ClipsFolder", 0, winreg.REG_SZ, str(WORKROOT))
            winreg.SetValueEx(k, "DataFolder", 0, winreg.REG_SZ, str(DATA_DIR))
    except Exception:
        pass
