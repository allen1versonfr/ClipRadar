"""Where Clip Radar keeps things, both when run from source and as a built .exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Clip Radar was called VodClipper before 4.0. Settings made for the old name keep working.
OLD_NAME, DATA_NAME, CLIPS_NAME = "VodClipper", "ClipRadar", "Clip Radar"
for _k, _v in list(os.environ.items()):
    if _k.startswith("VODCLIPPER_"):
        os.environ.setdefault("CLIPRADAR_" + _k[len("VODCLIPPER_"):], _v)
MIGRATION: dict = {}          # what moved from the old name this start (shown in the log)


def _move_folder(old: Path, new: Path, wait: float = 30.0) -> bool:
    """Rename the old folder to the new name. Right after an in-app update the old installer may still be
    running from inside it for a moment, so keep trying for a little while."""
    import time
    end = time.time() + wait
    while True:
        try:
            os.replace(old, new)
            return True
        except OSError:
            if time.time() >= end or new.exists():
                return False
            time.sleep(0.5)


def _rename_target(base: Path, new_name: str) -> Path:
    """base/<new name>, moving base/VodClipper there the first time (an upgrade from VodClipper)."""
    new, old = base / new_name, base / OLD_NAME
    if FROZEN and not new.exists() and old.is_dir():
        if _move_folder(old, new):
            MIGRATION[str(old)] = str(new)
        else:
            MIGRATION[str(old)] = ""          # couldn't move it yet: keep using it, try again next start
            return old
    return new

# read-only resources (ui/, bundled tools/) - inside the exe bundle when frozen
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# folder containing ClipRadar.exe (or the source files)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
# PyInstaller's macOS executable lives in Contents/MacOS; updates replace the
# containing .app bundle, while APP_DIR continues to mean the executable folder.
_EXECUTABLE = Path(sys.executable).resolve()
APP_BUNDLE = (_EXECUTABLE.parents[2] if FROZEN and IS_MAC and len(_EXECUTABLE.parents) >= 3
              and _EXECUTABLE.parents[2].suffix == ".app" else None)


def _data_dir() -> Path:
    if os.environ.get("CLIPRADAR_DATA"):
        return Path(os.environ["CLIPRADAR_DATA"])
    if not FROZEN:
        return APP_DIR
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local")
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.path.join(Path.home(), ".local", "share")
    return _rename_target(Path(base), DATA_NAME)


DATA_DIR = _data_dir()
TOOLS_WRITE = DATA_DIR / "tools"           # where downloaded/updated tools go
MODELS_DIR = DATA_DIR / "models"           # Whisper caption models

# Keep caption models inside Clip Radar's own folder (so uninstalling removes them)
# instead of the shared %USERPROFILE%\.cache\huggingface.
if FROZEN:
    os.environ.setdefault("HF_HOME", str(MODELS_DIR))


def tools_dirs() -> list:
    out = []
    extra = [] if FROZEN else [APP_DIR.parent / "tools"]      # repo-root tools/ when run from source
    system = [Path(p) for p in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")] if IS_MAC else []
    for d in [TOOLS_WRITE, APP_DIR / "tools", RES_DIR / "tools", *extra, *system]:
        if d not in out:
            out.append(d)
    return out


def _videos_folder() -> Path:
    """The user's media folder (Windows Videos, macOS Movies)."""
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
    return Path.home() / ("Movies" if IS_MAC else "Videos")


def _work_root() -> Path:
    env = os.environ.get("CLIPRADAR_WORK")
    if env:
        return Path(env)
    if not FROZEN:
        return APP_DIR / "projects"
    videos = _videos_folder()
    if IS_MAC:
        try:
            videos.mkdir(parents=True, exist_ok=True)
        except OSError:
            videos = Path.home()
    return _rename_target(videos if videos.is_dir() else Path.home(), CLIPS_NAME)


WORKROOT = _work_root()
LOG_FILE = DATA_DIR / "clipradar.log"


def fix_moved_paths():
    """After the folders moved from the old name, point saved file paths (the Exports list, storage
    settings, project folders) at the new place. Plain text replacement inside the JSON files."""
    import json
    moved = {o: n for o, n in MIGRATION.items() if n}
    if not moved:
        return 0
    pairs = []
    for o, n in moved.items():
        for a, b in ((o, n), (o.replace("\\", "/"), n.replace("\\", "/"))):
            pairs.append((json.dumps(a)[1:-1], json.dumps(b)[1:-1]))
    files = [DATA_DIR / f for f in ("queue.json", "locations.json", "export.json")]
    for root in {WORKROOT}:
        try:
            for d in os.listdir(root):
                for f in ("meta.json", "project.json"):
                    files.append(Path(root) / d / f)
        except OSError:
            pass
    fixed = 0
    for f in files:
        try:
            if not f.is_file() or f.stat().st_size > 50_000_000:
                continue
            txt = f.read_text(encoding="utf-8")
            new = txt
            for a, b in pairs:
                new = new.replace(a, b)
            if new != txt:
                tmp = f.with_suffix(f.suffix + ".tmp")
                tmp.write_text(new, encoding="utf-8")
                os.replace(tmp, f)
                fixed += 1
        except OSError:
            pass
    return fixed


def ensure_dirs():
    for d in (DATA_DIR, TOOLS_WRITE, WORKROOT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def register_folders():
    """Tell the uninstaller where Clip Radar keeps things (HKCU\\Software\\ClipRadar)."""
    if not (IS_WIN and FROZEN):
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ClipRadar") as k:
            winreg.SetValueEx(k, "ClipsFolder", 0, winreg.REG_SZ, str(WORKROOT))
            winreg.SetValueEx(k, "DataFolder", 0, winreg.REG_SZ, str(DATA_DIR))
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\VodClipper")    # the old name's entry
        except OSError:
            pass
    except Exception:
        pass
