"""
Where you left off (5.0.2): the videos open when Clip Radar closed come back when it opens again.

  - Moments: the VODs you had open are opened again the way Home opens them (the same source and settings), so the
    analysis comes from its cache and your review (kept, skipped, trims, titles) from project.json, as it always did
    when you reopened one by hand. Only videos still on disk: a VOD you deleted is never downloaded again.
  - Export VOD: the videos you had open there are opened again (the copyright scan, flagged words and the music you
    set are already kept with each video).
  - The screen you were on, the video picked in each screen and the part marked for Segments are kept by the page.

Kept in session.json in Clip Radar's data folder. Restoring runs in the background after the server starts, one
video at a time, so the window opens at once.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import paths
import persist

MAX_VODS = 3              # the newest VODs opened again (each one is a job: its analysis, thumbnails)
MAX_LIGHT = 8             # videos opened only to export (a probe each: quick)
_lock = threading.Lock()
state = {"restoring": 0}   # how many are still being opened again (the page waits for them instead of saying "expired")
_KEEP_BODY = ("source", "count", "length", "max_height", "chat_weight", "detect", "events", "emotes", "chat_file",
              "chat_delay", "cookies", "sensitivity", "min_len", "max_len")


def _file() -> str:
    return os.path.join(str(paths.DATA_DIR), "session.json")


def load() -> dict:
    d = persist.read_json(_file(), {}) or {}
    return {"vods": [x for x in d.get("vods", []) if isinstance(x, dict)],
            "light": [x for x in d.get("light", []) if isinstance(x, dict)]}


def _save(d: dict) -> None:
    try:
        os.makedirs(str(paths.DATA_DIR), exist_ok=True)
        persist.write_json(_file(), d)
    except OSError:
        pass


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a or "")) == os.path.normcase(os.path.abspath(b or ""))


def remember_vod(body: dict, video: str, workdir: str) -> None:
    """A VOD opened for Moments (Home's Find clips, Recent, a restore)."""
    entry = {"body": {k: body[k] for k in _KEEP_BODY if k in body}, "video": video, "workdir": workdir, "at": time.time()}
    with _lock:
        d = load()
        d["vods"] = [entry] + [x for x in d["vods"] if not _same(x.get("workdir", ""), workdir)][:MAX_VODS - 1]
        _save(d)


def remember_light(workdir: str, video: str, title: str) -> None:
    """A video opened on Export VOD only to export."""
    entry = {"workdir": workdir, "video": video, "title": title, "at": time.time()}
    with _lock:
        d = load()
        d["light"] = [entry] + [x for x in d["light"] if not _same(x.get("workdir", ""), workdir)][:MAX_LIGHT - 1]
        _save(d)


def restore(S, wait_job=None) -> dict:
    """Open again what was open last time. S: the server module. Returns what was done (for the log and tests)."""
    d = load()
    vods = [x for x in d["vods"] if os.path.isfile(x.get("video") or "") and (x.get("body") or {}).get("source")]
    light = [x for x in d["light"] if os.path.isfile(x.get("video") or "") and x.get("workdir")]
    done = {"vods": 0, "light": 0, "failed": 0}
    state["restoring"] = len(vods) + len(light)
    import vodexport
    for x in light:                           # quick ones first: a probe each
        try:
            vodexport.open_known(S, x["workdir"], x["video"], str(x.get("title") or ""))
            done["light"] += 1
        except Exception:
            done["failed"] += 1
        state["restoring"] -= 1
    for x in reversed(vods):                  # oldest first, so the newest is the last one opened, as before
        pid = S._pid_for(x["workdir"])
        if pid in S.projects and "analysis" in S.projects[pid]:
            state["restoring"] -= 1
            continue
        job = S.new_job("analyze")
        job["body"] = dict(x["body"], restored=True)
        S.run_job(job, lambda j=job: S.do_analyze(j, j["body"]))
        (wait_job or _wait)(job)
        done["vods" if job["status"] == "done" else "failed"] += 1
        state["restoring"] -= 1
    state["restoring"] = 0
    return done


def _wait(job: dict, limit: float = 1800.0) -> None:
    end = time.time() + limit
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.5)


def start(S) -> Optional[threading.Thread]:
    """Restore in the background, so the window opens at once."""
    d = load()
    if not d["vods"] and not d["light"]:
        return None
    t = threading.Thread(target=lambda: _log(S, restore(S)), daemon=True, name="restore")
    t.start()
    return t


def _log(S, done: dict) -> None:
    if done["vods"] or done["light"] or done["failed"]:
        print(f"  Where you left off: {done['vods']} video(s) back in Moments, {done['light']} in Export VOD"
              + (f", {done['failed']} couldn't be opened" if done["failed"] else ""), flush=True)
