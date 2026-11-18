"""
Previews (moved from server.py, hardening phase 5d; no changes): the light preview copy of a project's video the
app plays, Layout › Auto-frame's pick and the face it follows, a clip's preview extras (punch-in beats, where the
title stays clear of the face), and the exact preview render (a few seconds rendered the way the export will be).

  preview_path(pr), start_preview(pid)      the preview copy of the video, made once
  preview_extras(pid, c, opts)              what the preview draws on top of a clip (cached)
  _framing_for(pr, c), _auto_layout(pr, c)   Auto-frame
  start_preview_render(pid, c, opts, at)    a few seconds rendered exactly

Everything shared (projects, jobs, the job helpers…) stays in server.py and is read here as S.<name> at call time
(server.py's docstring says why). A test that swaps a name defined here swaps previews.<name>.
"""

from __future__ import annotations

import sys

if "server" not in sys.modules:
    # imported before server: server imports this module itself (at its end), so server is imported first
    # and this copy steps aside (the import then gives back the copy server loaded in full)
    del sys.modules[__name__]
    import server  # noqa: E402,F401

import glob  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import Optional  # noqa: E402
import engine  # noqa: E402
import render  # noqa: E402
import checkflow  # noqa: E402
import framing  # noqa: E402
import locations  # noqa: E402
import render_queue  # noqa: E402
import server as S  # noqa: E402  shared state and helpers, read as S.<name> at call time


def preview_path(pr: dict) -> str:
    """Where a project's preview copy goes (engine.make_preview): in Clip Radar's temporary files, named
    after the video and its size and date, so it's made once per video and cleared with the old temp files."""
    v = pr.get("video", "")
    try:
        st = os.stat(v)
        sig = f"{os.path.abspath(v)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(v)
    d = os.path.join(locations.path("temp"), "previews")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(sig.encode()).hexdigest()[:16] + ".mp4")


def start_preview(pid: str) -> dict:
    """{"ready": True} when the project's preview copy is there; otherwise the job making it."""
    pr = S.projects[pid]
    out = preview_path(pr)
    if os.path.isfile(out):
        return {"ready": True}
    old = S.jobs.get(pr.get("preview_job", ""))
    if old and old["status"] == "running":
        return {"job": old["id"]}
    job = S.new_job("preview")
    pr["preview_job"] = job["id"]

    def go():
        S.job_step(job)("Making a version the preview can play")
        engine.make_preview(pr["video"], out, float((pr.get("info") or {}).get("duration") or 0),
                            S.job_progress(job, 0, 1), S.job_check(job))
        return {"ready": True}
    S.run_job(job, go)
    return {"job": job["id"]}


def _framing_dir(pr: dict) -> str:
    return locations.temp_dir(os.path.basename(pr["workdir"]), "framing")


def _warm_framing(pr: dict) -> None:
    """Look for a facecam or a person while the video opens, so Layout › Auto-frame (which a new video runs by
    itself) has its answer ready instead of starting then: a few seconds on a long VOD, in the background."""
    if not pr.get("video") or not framing.available():
        return

    def run():
        try:
            framing.suggest(pr["video"], float(pr["info"].get("duration") or 0) or 60.0, cache_dir=_framing_dir(pr))
        except Exception as e:
            print(f"  Auto-frame skipped: {str(e)[:160]}", flush=True)
    threading.Thread(target=run, name="framing-warm", daemon=True).start()


def _auto_layout(pr: dict) -> dict:
    """Layout › Auto-frame's pick for a project as render options ({} when it can't tell). For renders
    asked for without a layout: the default Cam + game box is the top-left corner, which on a stream
    without a facecam shows whatever is there (the chat overlay, a corner of the game)."""
    if not pr.get("video") or not framing.available():
        return {}
    try:
        r = framing.suggest(pr["video"], float(pr["info"].get("duration") or 0) or 60.0, cache_dir=_framing_dir(pr))
    except Exception as e:
        print(f"  Auto-frame skipped: {str(e)[:160]}", flush=True)
        return {}
    if not r.get("layout"):
        return {}
    return {k: r[k] for k in ("layout", "cam", "track") if r.get(k)}


def _framing_for(pr: dict, start: float, end: float, ro, log=None) -> Optional[dict]:
    """Follow the face for a whole-frame crop (Layout › Follow face). None when it doesn't apply."""
    if render.effective_layout(ro) != "full" or ro.track != "auto" or not framing.available():
        return None
    try:
        return framing.track(pr["video"], start, end, pr["info"], ro, cache_dir=locations.temp_dir(os.path.basename(pr["workdir"]), "framing"),
                             log=log or (lambda m: print("  " + m, flush=True)))
    except Exception as e:
        print(f"  Framing skipped: {str(e)[:160]}", flush=True)
        return None


PREVIEW_KEYS = ("layout", "cam", "cams", "cam_height", "game_zoom", "game_x", "track", "track_target", "focus_y",
                "aspect", "punch")           # what the punch beats and the followed face depend on (Layout tab)


_preview_cache: dict = {}


_preview_lock = threading.Lock()


PREVIEW_CACHE_MAX = 256


def preview_extras(pid: str, pr: dict, start: float, end: float, opts: dict) -> dict:
    """What Review's live preview needs to draw a clip the way it renders (4.47.1): the punch-in beats, in
    seconds into the clip (render.punch_beats, [] when Punch-in is off), and the face the render follows
    (_framing_for: face_y and face_h as shares of the frame's height, None without one). followed: the crop
    follows the face, which is when a title that hasn't been moved keeps off it (render.title_top).
    Kept per clip and framing, so scrubbing or replaying asks nothing again."""
    o = {k: opts[k] for k in PREVIEW_KEYS if k in (opts or {})}
    key = json.dumps([pid, round(start, 2), round(end, 2), id(pr.get("analysis")), o], sort_keys=True, default=str)
    with _preview_lock:
        hit = _preview_cache.get(key)
    if hit is not None:
        return hit
    ro = S._ropts(o)
    beats = render.punch_beats(pr.get("analysis"), start, end) if ro.punch in render.PUNCH_ZOOM else []
    fr = _framing_for(pr, start, end, ro) if end - start >= 1 else None
    face_y = face_h = None
    if fr and fr.get("face_y") is not None and fr.get("face_h") is not None:
        face_y, face_h = round(float(fr["face_y"]), 4), round(float(fr["face_h"]), 4)
    out = {"punch": ro.punch, "punch_at": [round(float(t), 2) for t in beats], "face_y": face_y, "face_h": face_h,
           "followed": bool(fr and fr.get("path")) and face_y is not None}
    with _preview_lock:
        if len(_preview_cache) >= PREVIEW_CACHE_MAX:
            _preview_cache.pop(next(iter(_preview_cache)))
        _preview_cache[key] = out
    return out


_previews: dict = {}


def start_preview_render(pid: str, c: dict, opts: dict, at: float) -> dict:
    pr = S.projects[pid]
    start_, end_ = float(c["start"]), float(c["end"])
    if end_ - start_ < 1:
        raise RuntimeError("That clip is too short.")
    seg_a = min(max(start_, at - 1.0), max(start_, end_ - 6.0))
    seg_b = min(end_, seg_a + 6.0)
    d = S._find_clip(pr, c.get("id")) or {}
    it = {"start": seg_a, "end": seg_b, "title": str(c.get("title") or "")[:120], "post": "",
          "words": S._clean_words(c.get("words")), "fix_spans": checkflow.render_spans(d) if d else [],
          "keep_fps": [f["fp"] for f in (d.get("safety") or {}).get("flags", []) if f.get("status") in ("accepted", "dismissed")],
          "cancel": False}
    job = S.new_job("preview")
    old = _previews.get(pid)
    if old and old["status"] == "running":
        old["cancel"] = True
    _previews[pid] = job

    def go():
        check = S.job_check(job)
        ro = S._ropts(dict(opts, hw_encode=opts.get("hw_encode", True)))
        ro.crf = 28
        outdir = locations.temp_dir(os.path.basename(pr["workdir"]), "preview")
        os.makedirs(outdir, exist_ok=True)
        for f in sorted(glob.glob(os.path.join(outdir, "preview_*.mp4")))[:-2]:   # keep the last few
            try:
                os.remove(f)
            except OSError:
                pass
        job["step"] = "Getting captions ready"
        words, title, _post, spans, mode = render_queue._render_inputs(it, pr, ro, check)
        if not ro.captions:
            words = []
        fr = None
        if render.effective_layout(ro) == "full" and ro.track == "auto":
            job["step"] = "Finding the face"
            # the whole clip's path (shared with the export, cached), shifted to this stretch
            fr = _framing_for(pr, float(c["start"]), float(c["end"]), ro)
            if fr and fr.get("path"):
                fr = dict(fr, path=framing.shift(fr["path"], seg_a - float(c["start"]), seg_b - seg_a))
        if ro.punch != "off":                # the clip's beats, on this stretch of it
            ro.punch_at = [t - (seg_a - float(c["start"])) for t in render.punch_beats(pr.get("analysis"), float(c["start"]),
                                                                                   float(c["end"]))]
            ro.punch_at = [t for t in ro.punch_at if 0 <= t <= seg_b - seg_a]
        name = f"preview_{int(time.time() * 1000)}.mp4"
        job["step"] = "Rendering"
        render.render_clip(pr["video"], seg_a, seg_b, os.path.join(outdir, name), ro, title=title,
                           progress=S.job_progress(job, 0.1, 1.0), check=check, words=words, censor=spans, censor_mode=mode,
                           framing=fr)
        return {"url": f"/files/{pid}/preview/{name}", "start": seg_a, "end": seg_b}
    S.run_job(job, go)
    return job
