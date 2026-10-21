"""
Automatic cleanup (Settings › Storage › Clean up automatically). Three rules, each one opt-in except
the first, and each careful about what it may touch:

  temp            temporary files nobody has used for TEMP_DAYS days (exact previews, framing data,
                  finished live buffers) and downloads that stopped over a day ago. On by default:
                  it's the same as Clear temporary files, minus anything recent.
  skipped         a clip rendered on its own (hands-free live clipping) that you then skip: its file
                  goes, since you said you don't want it. Off by default. Clips you rendered yourself
                  are never touched by this.
  vods            a downloaded VOD you're finished with. Off by default. A VOD only goes when:
                    - every suggested clip is kept or skipped (nothing left to review),
                    - every kept clip has rendered successfully (none waiting, rendering or failed:
                      a retry needs the source),
                    - it isn't open, isn't a live recording, and hasn't been opened for `vod_days` days.
                  Only the downloaded video and its analysis go. Rendered clips, your review
                  (project.json) and thumbnails stay.

Nothing here raises: problems are counted and reported (last run), and the app carries on.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

import paths
import persist

DEFAULTS = {"temp": True, "skipped": False, "vods": False, "vod_days": 7}
TEMP_DAYS = 3
_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "cleanup.json")  # noqa: E731
last: dict = {"at": 0, "freed": 0, "removed": [], "failed": [], "kept": []}


def clean(d: dict) -> dict:
    d = d if isinstance(d, dict) else {}
    out = dict(DEFAULTS)
    for k in ("temp", "skipped", "vods"):
        if k in d:
            out[k] = bool(d[k])
    try:
        out["vod_days"] = int(min(max(int(d.get("vod_days", DEFAULTS["vod_days"])), 1), 365))
    except (TypeError, ValueError):
        pass
    return out


def load() -> dict:
    with _lock:
        return clean(persist.read_json(_FILE(), {}) or {})


def save(patch: dict) -> dict:
    cur = load()
    cur.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
    cur = clean(cur)
    with _lock:
        try:
            persist.write_json(_FILE(), cur)
        except OSError:
            pass
    return cur


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p or ""))


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def vod_verdict(pdir: str, queue_items: list, open_dirs: set, days: int, now: Optional[float] = None) -> str:
    """'' when this project's VOD may go, else why it stays (for the report and the tests)."""
    import engine
    now = now or time.time()
    if not engine._has_vod(pdir):
        return "no video"
    if _norm(pdir) in open_dirs:
        return "open"
    meta = _read(os.path.join(pdir, "meta.json"))
    if meta.get("live"):
        return "live recording"
    proj = _read(os.path.join(pdir, "project.json"))
    clips = [c for c in proj.get("clips", []) if c.get("kind", "hype") != "gem"]
    if not clips:
        return "not reviewed"
    if any(c.get("state", "todo") == "todo" for c in clips):
        return "clips left to review"
    mine = [it for it in queue_items if _norm(it.get("workdir")) == _norm(pdir)
            or (it.get("video") and _norm(os.path.dirname(it["video"])) == _norm(pdir))]
    if any(it.get("status") in ("queued", "rendering", "error") for it in mine):
        return "renders waiting, running or failed"
    done = {it.get("clip_id") for it in mine if it.get("status") == "done"}
    if any(c.get("state") == "kept" and c.get("id") not in done for c in clips):
        return "kept clips not rendered"
    seen = max([os.path.getmtime(p) for p in (os.path.join(pdir, "project.json"), os.path.join(pdir, "meta.json"))
                if os.path.exists(p)] or [now])
    if now - seen < days * 86400:
        return f"opened in the last {days} days"
    return ""


def delete_vod(pdir: str) -> tuple:
    """The downloaded video and analysis of one project. Rendered clips, project.json and thumbnails stay."""
    import engine
    files = list(engine._vod_files(pdir))
    a = os.path.join(pdir, "analysis.npz")
    if os.path.isfile(a):
        files.append(a)
    clip_dirs = {_norm(os.path.dirname(f)) for f in engine._clip_files(pdir)}
    files = [f for f in files if _norm(os.path.dirname(f)) not in clip_dirs]       # never an exported clip
    return engine._remove(files)


def sweep(roots: list, queue_items: list, open_dirs: set, live_dirs: tuple = (), cfg: Optional[dict] = None,
          log=lambda m: None) -> dict:
    """Run the rules that are on. Returns (and remembers) what was freed, removed, kept and failed."""
    cfg = cfg or load()
    rep = {"at": time.time(), "freed": 0, "removed": [], "failed": [], "kept": []}
    try:
        if cfg["temp"]:
            import locations
            r = locations.clear_temp(keep=tuple(live_dirs), older_than=TEMP_DAYS * 86400)
            rep["freed"] += r.get("freed", 0)
            if r.get("failed"):
                rep["failed"].append(f"{r['failed']} temporary item(s) in use")
            import engine
            part = locations.stale_partials([locations.path("downloads")] + list(roots))
            freed, failed = engine._remove(part)
            rep["freed"] += freed
            rep["failed"] += failed
        if cfg["vods"]:
            import engine
            opened = {_norm(d) for d in open_dirs}
            for root in roots:
                for pdir in engine._project_dirs(root):
                    why = vod_verdict(pdir, queue_items, opened, cfg["vod_days"])
                    if why:
                        if why not in ("no video", "not reviewed"):
                            rep["kept"].append({"project": os.path.basename(pdir), "why": why})
                        continue
                    title = _read(os.path.join(pdir, "meta.json")).get("title") or os.path.basename(pdir)
                    freed, failed = delete_vod(pdir)
                    rep["freed"] += freed
                    rep["failed"] += failed
                    if freed:
                        rep["removed"].append(title)
                        log(f"  Cleaned up the finished VOD \"{title}\" ({freed // 1_000_000} MB)")
    except Exception as e:                                   # report, never crash the app
        rep["failed"].append(f"cleanup stopped: {e}")
    last.clear()
    last.update(rep)
    if rep["failed"]:
        log(f"  Cleanup couldn't remove: {', '.join(map(str, rep['failed'][:5]))}")
    return rep


def skipped_render(item: dict, cfg: Optional[dict] = None) -> bool:
    """Delete the file of a clip that rendered on its own and was then skipped. True when removed."""
    cfg = cfg or load()
    if not cfg["skipped"] or not item.get("auto") or item.get("status") != "done":
        return False
    path = item.get("path") or ""
    if not path or not os.path.isfile(path):
        return False
    import engine
    stem = os.path.splitext(path)[0]
    extra = [stem + ext for ext in (".srt", ".txt") if os.path.isfile(stem + ext)]
    freed, failed = engine._remove([path] + extra)
    if failed:
        last.setdefault("failed", []).extend(failed)
    return not failed
