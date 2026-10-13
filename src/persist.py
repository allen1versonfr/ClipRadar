"""
What VodClipper remembers between runs:

  workdir/project.json   your review of one VOD: clip numbers, kept/skipped, trims, titles, captions
  DATA_DIR/queue.json    the render queue and Exports list

Pure helpers (no server state), so they can be unit tested on their own.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Optional

PROJECT_V = 1
_write_lock = threading.Lock()

# what you did to a clip in the app; a clip with any of these is yours and never drops out of the list
USER_KEYS = ("state", "userTrim", "userTitle", "userPost", "postText", "wordsEdited", "words_edit")
# kept out of project.json: rebuilt when the project opens
_VOLATILE = ("vod", "pid", "isNew")


def write_json(path: str, data, indent: Optional[int] = None):
    """Write atomically: a crash or power cut leaves the old file or the new one, never half of one."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _write_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        for i in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:        # Windows: a virus scanner or indexer has the file open for a moment
                if i == 4:
                    raise
                threading.Event().wait(0.1 * (i + 1))


def write_text(path: str, text: str):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- clip numbers

MATCH_PEAK_S = 4.0       # peaks this close are the same moment
MATCH_OVERLAP = 0.5      # ... or clips overlapping by half of the shorter one


def same_moment(a: dict, start: float, end: float, peak: float) -> Optional[float]:
    """How close a known moment is to this clip (lower is closer), or None if it's a different moment."""
    dp = abs(float(a["peak"]) - peak)
    ov = min(end, a["end"]) - max(start, a["start"])
    short = min(end - start, a["end"] - a["start"])
    if dp <= MATCH_PEAK_S or (short > 0 and ov >= MATCH_OVERLAP * short):
        return dp
    return None


def number_for(book: dict, start: float, end: float, peak: float, kind: str = "hype",
               taken: Optional[set] = None) -> int:
    """The clip's number: the same moment always gets the same number, and numbers are never reused.
    book = {"next": int, "moments": [{num, start, end, peak, kind}]}, updated in place."""
    taken = taken if taken is not None else set()
    best = None
    for m in book.setdefault("moments", []):
        if m.get("kind", "hype") != kind or m["num"] in taken:
            continue
        d = same_moment(m, start, end, peak)
        if d is not None and (best is None or d < best[0]):
            best = (d, m)
    if best:
        return best[1]["num"]
    num = max(int(book.get("next", 1)), 1)
    book["next"] = num + 1
    book["moments"].append({"num": num, "start": round(start, 2), "end": round(end, 2),
                            "peak": round(peak, 2), "kind": kind})
    return num


def touched(d: dict) -> bool:
    """You did something to this clip, so it stays put whatever the sliders say."""
    return bool(d.get("manual") or d.get("state") in ("kept", "skipped") or d.get("userTrim")
                or d.get("userTitle") or d.get("userPost") or d.get("wordsEdited"))


# ---------------------------------------------------------------- project.json

def project_payload(pr: dict, video_sig: str) -> dict:
    strip = lambda d: {k: v for k, v in d.items() if k not in _VOLATILE}  # noqa: E731
    return {
        "v": PROJECT_V, "video": video_sig, "source": pr.get("source", ""), "title": pr.get("title", ""),
        "streamer": pr.get("streamer", ""),
        "settings": {"count": pr.get("count", 0), "length": pr.get("length", 0),
                     "chat_weight": getattr(pr.get("analysis"), "chat_weight_pct", 60.0),
                     "detect": pr.get("detect")},
        "book": pr.get("book") or {"next": 1, "moments": []},
        "clips": [strip(d) for d in pr.get("clips", [])],
        "gems": [strip(d) for d in pr.get("gems", [])],
        "removed": [strip(d) for d in pr.get("removed", [])],
        "deep_done": bool(pr.get("deep_done")), "deep_model": pr.get("deep_model", ""),
        "deep_lang": pr.get("deep_lang", ""),
    }


def load_project(path: str, video_sig: str) -> Optional[dict]:
    """The saved review for this VOD, or None. A project saved for a different download of the
    video is set aside as project.old.json rather than applied to the wrong frames."""
    data = read_json(path)
    if not isinstance(data, dict) or data.get("v") != PROJECT_V or not isinstance(data.get("clips"), list):
        return None
    if data.get("video") != video_sig:
        try:
            os.replace(path, os.path.join(os.path.dirname(path), "project.old.json"))
        except OSError:
            pass
        return None
    return data


def summary(clips: list) -> dict:
    hype = [c for c in clips if c.get("kind", "hype") != "gem"]
    return {"kept": sum(1 for c in hype if c.get("state") == "kept"),
            "skipped": sum(1 for c in hype if c.get("state") == "skipped"),
            "todo": sum(1 for c in hype if c.get("state", "todo") == "todo")}


# ---------------------------------------------------------------- queue.json

QUEUE_V = 1
_QUEUE_SKIP = ("cancel",)


def queue_payload(items: list) -> dict:
    out = []
    for it in items:
        if it.get("status") == "cancelled":
            continue
        out.append({k: v for k, v in it.items() if k not in _QUEUE_SKIP})
    return {"v": QUEUE_V, "items": out}


def restore_queue(data) -> list:
    """Items to bring back after a restart: finished clips whose file is still there, renders that
    hadn't finished (they start again) and failed ones (with Retry)."""
    if not isinstance(data, dict) or data.get("v") != QUEUE_V:
        return []
    out = []
    for it in data.get("items") or []:
        if not isinstance(it, dict) or not it.get("id") or "start" not in it or "end" not in it:
            continue
        st = it.get("status")
        if st == "done":
            if not it.get("path") or not os.path.isfile(it["path"]):
                continue
        elif st in ("queued", "rendering"):
            it.update(status="queued", progress=0.0, step="Waiting")
        elif st != "error":
            continue
        tt = it.get("tiktok")
        if isinstance(tt, dict) and tt.get("state") == "posting":
            it["tiktok"] = {"state": "error", "mode": tt.get("mode", ""), "at": tt.get("at", 0),
                            "error": "VodClipper closed while this was uploading. Post it again."}
        it["cancel"] = False
        out.append(it)
    return out
