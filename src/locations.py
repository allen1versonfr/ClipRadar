"""
Where VodClipper keeps things (3.11), each changeable in Settings › Storage:

  projects    a folder per video: your review, analysis, transcripts, thumbnails
  downloads   downloaded videos and chat (empty: inside each project folder, as before)
  clips       rendered clips (empty: a "tiktok" folder inside each project folder, as before)
  temp        previews, framing data and the live recording buffer (safe to clear)
  logs        vodclipper.log

Defaults are where things always went, so upgrading changes nothing. A folder on a drive that isn't
plugged in falls back to the default until it's back, and says so.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import paths

KEYS = ("projects", "downloads", "clips", "temp", "logs")
LABELS = {"projects": "Projects", "downloads": "Downloaded videos", "clips": "Rendered clips",
          "temp": "Temporary files and cache", "logs": "Logs"}
LOW_SPACE = 5 * 1024 ** 3          # warn under 5 GB free
_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "locations.json")  # noqa: E731


def defaults() -> dict:
    return {"projects": str(paths.WORKROOT), "downloads": "", "clips": "",
            "temp": str(Path(paths.DATA_DIR) / "temp"), "logs": str(paths.DATA_DIR)}


def load_raw() -> dict:
    try:
        with open(_FILE(), encoding="utf-8") as f:
            d = json.load(f)
        return {k: str(d.get(k) or "") for k in KEYS if isinstance(d, dict)}
    except (OSError, ValueError):
        return {}


def _drive_present(p: str) -> bool:
    """False when the folder is on a drive (or network share) that isn't there right now."""
    anchor = Path(p).anchor
    return not anchor or os.path.exists(anchor)


def resolve(key: str) -> dict:
    """{"path", "set" (chosen by you), "ok", "problem"}. path is "" for downloads/clips when they live
    inside each project folder."""
    dflt = defaults()[key]
    chosen = load_raw().get(key, "")
    if not chosen:
        return {"path": dflt, "set": False, "ok": True, "problem": ""}
    if not _drive_present(chosen):
        return {"path": dflt, "set": True, "ok": False, "chosen": chosen,
                "problem": f"{chosen} isn't available (is the drive plugged in?). Using the default until it's back."}
    try:
        os.makedirs(chosen, exist_ok=True)
    except OSError as e:
        return {"path": dflt, "set": True, "ok": False, "chosen": chosen, "problem": f"Can't use {chosen}: {e.strerror or e}."}
    return {"path": chosen, "set": True, "ok": True, "problem": ""}


def path(key: str) -> str:
    return resolve(key)["path"]


def check(folder: str) -> dict:
    """Can VodClipper use this folder? {"ok", "error", "free"}: it must exist (or be creatable) and be writable."""
    folder = (folder or "").strip().strip('"')
    if not folder:
        return {"ok": True, "error": "", "free": None}
    if not os.path.isabs(folder):
        return {"ok": False, "error": "Choose a full folder path (like D:\\Clips).", "free": None}
    if not _drive_present(folder):
        return {"ok": False, "error": "That drive isn't available.", "free": None}
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f".vc_write_test_{uuid.uuid4().hex[:6]}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        return {"ok": False, "error": f"VodClipper can't write there ({e.strerror or e}).", "free": None}
    return {"ok": True, "error": "", "free": free_space(folder)}


def free_space(folder: str) -> Optional[int]:
    p = folder
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def save(patch: dict) -> dict:
    """Change locations. Returns {"ok", "errors": {key: message}}. Nothing is moved: new work goes to the
    new place, and existing projects stay where they are (VodClipper still finds them)."""
    cur = load_raw()
    errors = {}
    for k in KEYS:
        if k not in (patch or {}):
            continue
        v = str(patch[k] or "").strip().strip('"')
        if v and os.path.normcase(os.path.abspath(v)) == os.path.normcase(os.path.abspath(defaults()[k] or "")):
            v = ""                               # the default: store nothing, so it follows the app
        if v:
            c = check(v)
            if not c["ok"]:
                errors[k] = c["error"]
                continue
        if k == "projects" and cur.get("projects") != v:
            old = path("projects")
            hist = [h for h in _history() if h != old] + [old]
            cur["_history"] = hist[-8:]
        cur[k] = v
    with _lock:
        try:
            os.makedirs(str(paths.DATA_DIR), exist_ok=True)
            tmp = _FILE() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(cur, _history=cur.get("_history", _history())), f, indent=1)
            os.replace(tmp, _FILE())
        except OSError as e:
            return {"ok": False, "errors": dict(errors, _file=str(e))}
    return {"ok": not errors, "errors": errors}


def _history() -> list:
    try:
        with open(_FILE(), encoding="utf-8") as f:
            return [h for h in (json.load(f).get("_history") or []) if isinstance(h, str)]
    except (OSError, ValueError, AttributeError):
        return []


def project_roots() -> list:
    """Where projects are now, and where they were before you moved the Projects folder."""
    out = []
    for p in [path("projects")] + _history()[::-1]:
        if p and p not in out and os.path.isdir(p):
            out.append(p)
    return out


def download_dir(workdir: str) -> str:
    """Where a project's downloaded video goes."""
    root = path("downloads")
    return os.path.join(root, os.path.basename(workdir)) if root else workdir


def clips_dir(workdir: str) -> str:
    root = path("clips")
    return os.path.join(root, os.path.basename(workdir)) if root else os.path.join(workdir, "tiktok")


def temp_dir(*parts: str) -> str:
    d = os.path.join(path("temp"), *parts)
    os.makedirs(d, exist_ok=True)
    return d


def problems() -> list:
    return [dict(resolve(k), key=k, label=LABELS[k]) for k in KEYS if not resolve(k)["ok"]]


def _size(d: str) -> int:
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def view(sizes: bool = True) -> dict:
    out = []
    for k in KEYS:
        r = resolve(k)
        shown = r["path"] or ""
        where = shown or ("Inside each project folder" if k in ("downloads", "clips") else "")
        free = free_space(shown or path("projects"))
        out.append({"key": k, "label": LABELS[k], "path": shown, "where": where, "chosen": r.get("chosen", load_raw().get(k, "")),
                    "set": r["set"], "ok": r["ok"], "problem": r["problem"], "default": defaults()[k],
                    "free": free, "low": free is not None and free < LOW_SPACE,
                    "bytes": _size(shown) if sizes and shown and k in ("temp", "logs") else None})
    return {"locations": out}


def clear_temp(keep: tuple = ()) -> dict:
    """Empty the temporary folder (previews, framing data, finished live buffers). Never touches videos
    you opened, downloads, projects or clips. keep: folders in use (running live sessions)."""
    root = path("temp")
    freed, failed = 0, 0
    if not os.path.isdir(root):
        return {"freed": 0, "failed": 0}
    keep_n = {os.path.normcase(os.path.abspath(k)) for k in keep}
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.normcase(os.path.abspath(p)) in keep_n:
            continue
        size = _size(p) if os.path.isdir(p) else (os.path.getsize(p) if os.path.exists(p) else 0)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            freed += size
        except OSError:
            failed += 1
    return {"freed": freed, "failed": failed}


def stale_partials(roots: list, older_than: float = 24 * 3600) -> list:
    """Leftovers of downloads that stopped long ago (yt-dlp .part/.ytdl files)."""
    out, now = [], time.time()
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        for d in os.listdir(r):
            pd = os.path.join(r, d)
            if not os.path.isdir(pd):
                continue
            for f in os.listdir(pd):
                if f.endswith((".part", ".ytdl")) or ".part-Frag" in f:
                    fp = os.path.join(pd, f)
                    try:
                        if now - os.path.getmtime(fp) > older_than:
                            out.append(fp)
                    except OSError:
                        pass
    return out


def memory() -> dict:
    """{"free", "total"} in bytes, or Nones when it can't be read."""
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return {"free": int(m.ullAvailPhys), "total": int(m.ullTotalPhys)}
        else:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0]) * 1024
            return {"free": info.get("MemAvailable"), "total": info.get("MemTotal")}
    except Exception:
        pass
    return {"free": None, "total": None}
