"""
Clip Radar local server. Run:  python server.py
Opens http://127.0.0.1:7860 in your browser. Everything stays on your PC.
"""

from __future__ import annotations

import sys

# Windows consoles default to cp1252; stream titles and chat are full of emoji.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import copy
import glob
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np

import engine
import live
import paths
import studio
import titles
import hotkey
import safety
import tools_setup
import updater
import audio_events
import chatsignal
import chattitles
import cleanup
import checkflow
import detect
import emotes
import exports
import framing
import sources
import gems
import hashtags
import learning
import persist
import twitchauth

from version import VERSION
HERE = paths.RES_DIR
import locations  # noqa: E402
WORKROOT = locations.path("projects")        # where new projects go (Settings › Storage › Projects)
PORT = int(os.environ.get("CLIPRADAR_PORT", "7860"))
paths.ensure_dirs()
os.makedirs(WORKROOT, exist_ok=True)


def _set_workroot():
    global WORKROOT
    WORKROOT = locations.path("projects")
    os.makedirs(WORKROOT, exist_ok=True)


def _storage_all() -> dict:
    """Storage use across every Projects folder (now and before it was moved)."""
    tot = {"vods": {"count": 0, "bytes": 0}, "clips": {"count": 0, "bytes": 0}, "other_bytes": 0, "workroot": WORKROOT}
    for root in locations.project_roots() or [WORKROOT]:
        st = engine.storage_summary(root)
        for k in ("vods", "clips"):
            tot[k]["count"] += st[k]["count"]
            tot[k]["bytes"] += st[k]["bytes"]
        tot["other_bytes"] += st["other_bytes"]
    return tot


def _allowed_roots() -> list:
    out = list(locations.project_roots()) + [WORKROOT]
    for k in ("downloads", "clips", "temp", "logs"):
        p_ = locations.path(k)
        if p_:
            out.append(p_)
    return [os.path.realpath(x) for x in out if x]

MODE = {"window": False}          # True when shown in Clip Radar's own app window
last_ping = [time.time()]


def shutdown_all(wait: float = 6.0):
    """Cancel running jobs (stops ffmpeg/downloads cleanly), save what's open, then exit the process.
    Renders that didn't finish stay in the queue and start again next time."""
    SHUTTING_DOWN[0] = True
    for j in list(jobs.values()):
        if j["status"] == "running":
            j["cancel"] = True
    for sess in list(live_sessions.values()):
        threading.Thread(target=sess.stop, daemon=True).start()
    for it in list(queue):
        if it["status"] == "rendering":
            it["cancel"] = True
    end = time.time() + wait
    while time.time() < end and (any(j["status"] == "running" for j in jobs.values())
                                 or any(it["status"] == "rendering" for it in queue)):
        time.sleep(0.2)
    flush_saves()
    os._exit(0)


SHUTTING_DOWN = [False]


shutdown_hook = [shutdown_all]

projects: dict = {}
jobs: dict = {}


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

def new_job(kind: str) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "step": "Starting", "progress": 0.0,
           "log": [], "result": None, "error": None, "cancel": False, "started": time.time()}
    jobs[jid] = job
    for old in [k for k, j in jobs.items() if j["status"] != "running" and time.time() - j["started"] > 3600]:
        jobs.pop(old, None)
    return job


def job_logger(job):
    def log(msg: str):
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")
        job["log"] = job["log"][-300:]
        try:
            print(f"  {msg}", flush=True)
        except Exception:
            pass
    return log


def job_progress(job, lo: float, hi: float):
    def p(x: float):
        v = round(lo + (hi - lo) * max(0.0, min(1.0, x)), 4)
        job["progress"] = v
        # time left from how fast it's actually going (only once there's enough to go on)
        now = time.time()
        if "_t0" not in job or v < job.get("_p0", 0):
            job["_t0"], job["_p0"] = now, v
            job.pop("eta", None)
            return
        el, done = now - job["_t0"], v - job["_p0"]
        if el >= 4 and done >= 0.03:
            eta = (1 - v) / (done / el)
            job["eta"] = round(eta if "eta" not in job else 0.7 * job["eta"] + 0.3 * eta, 1)
    return p


def job_check(job):
    def c():
        if job["cancel"]:
            raise engine.Cancelled()
    return c


def job_step(job):
    def s(text: str):
        job["step"] = text
    return s


def run_job(job, fn):
    def target():
        try:
            job["result"] = fn()
            job["status"] = "done"
            job["progress"] = 1.0
            job["step"] = "Done"
        except engine.Cancelled:
            if job.get("pause"):
                job["status"] = "paused"            # Resume starts it again; downloads carry on where they stopped
                job["step"] = "Paused"
                job_logger(job)("Paused.")
            else:
                job["status"] = "cancelled"
                job["step"] = "Cancelled"
                job_logger(job)("Cancelled.")
        except Exception as e:
            job["status"] = "error"
            job["error"] = ("Your PC ran out of memory. Close some programs (or let other work finish), then try again."
                            if isinstance(e, MemoryError) or "out of memory" in str(e).lower() else str(e) or type(e).__name__)
            job_logger(job)(f"Error: {job['error'].splitlines()[0]}")
            if not isinstance(e, RuntimeError):
                traceback.print_exc()
    threading.Thread(target=target, daemon=True).start()


# --------------------------------------------------------------------------
# project helpers
# --------------------------------------------------------------------------

def curve_preview(score, points: int = 1400) -> list:
    s = np.asarray(score, dtype=np.float32)
    if len(s) <= points:
        return s.round(2).tolist()
    edges = np.linspace(0, len(s), points + 1).astype(int)
    return [round(float(s[a:b].max()), 2) for a, b in zip(edges[:-1], edges[1:])]


# ---- saving: your review of each VOD (project.json) and the render queue (queue.json)

QUEUE_FILE = str(paths.DATA_DIR / "queue.json")
_save_timers: dict = {}
_save_lock = threading.Lock()


def _pid_for(workdir: str) -> str:
    """The same VOD gets the same project id every time it's opened."""
    key = os.path.normcase(os.path.realpath(workdir)).encode("utf-8", "replace")
    return hashlib.sha1(key).hexdigest()[:8]


def save_project(pid: str):
    pr = projects.get(pid)
    if not pr or pr.get("live") or "analysis" not in pr:
        return
    try:
        data = json.loads(json.dumps(persist.project_payload(pr, _project_sig(pr))))
    except RuntimeError:            # a background job changed a clip mid-copy: try again in a moment
        save_project_soon(pid)
        return
    except (TypeError, ValueError) as e:
        print(f"  Couldn't save the project: {e}", flush=True)
        return
    try:
        persist.write_json(os.path.join(pr["workdir"], "project.json"), data)
        engine._save_meta(pr["workdir"], review=persist.summary(data["clips"]))
    except OSError as e:
        print(f"  Couldn't save the project: {e}", flush=True)


def save_queue():
    with queue_lock:
        data = json.loads(json.dumps(persist.queue_payload(queue)))
    try:
        persist.write_json(QUEUE_FILE, data)
    except OSError as e:
        print(f"  Couldn't save the render queue: {e}", flush=True)


def _run_save(key, fn):
    with _save_lock:
        _save_timers.pop(key, None)
    try:
        fn()
    except Exception:
        traceback.print_exc()


def _save_later(key, fn, delay: float):
    with _save_lock:
        old = _save_timers.pop(key, None)
        if old:
            old.cancel()
        t = threading.Timer(delay, _run_save, args=(key, fn))
        t.daemon = True
        _save_timers[key] = t
        t.start()


def save_project_soon(pid: str, delay: float = 1.0):
    _save_later(("project", pid), lambda: save_project(pid), delay)


def save_queue_soon(delay: float = 0.3):
    _save_later(("queue",), save_queue, delay)


def flush_saves():
    """Write everything that's waiting to be saved, right now (used when quitting)."""
    with _save_lock:
        pending = list(_save_timers.values())
        _save_timers.clear()
    for t in pending:
        t.cancel()
        _run_save(*t.args)
    save_queue()


TX_MARGIN = 8.0   # seconds transcribed around each clip: trims keep captions, and cuts can move to a sentence


def _chat_window(pr: dict, start: float, end: float) -> list:
    d = pr.get("chat_delay", 6.0)
    lo, hi = start + d - 2, end + d + 8
    return [m[1] for m in chatsignal.human(pr["chat"]) if lo <= m[0] <= hi and isinstance(m[1], str)]


def _chat_title(pr: dict, start: float, end: float) -> str:
    """Settings › Titles: a title from a phrase many chatters typed around the clip ('' if none)."""
    d = pr.get("chat_delay", 6.0)
    lo, hi = start + d - 2, end + d + 8
    msgs = [(m[1], m[2] if len(m) > 2 else "") for m in chatsignal.human(pr.get("chat") or [])
            if lo <= m[0] <= hi and isinstance(m[1], str)]
    es = getattr(pr.get("analysis"), "_emotes", None)
    names = set(getattr(es, "lower", None) or {}) if es else set()
    return chattitles.find(msgs, names | set(engine.HYPE), _streamer_name(pr))["title"]


TITLE_PREFS = str(paths.DATA_DIR / "titles.json")


def title_prefs() -> dict:
    """Settings › Titles and captions: which title goes on new clips, and hashtags on or off."""
    d = persist.read_json(TITLE_PREFS, {}) or {}
    return {"pick": d.get("pick") if d.get("pick") in ("a", "b") else "a", "hashtags": bool(d.get("hashtags", True)),
            "chat_titles": bool(d.get("chat_titles", True))}


def set_title_prefs(body: dict) -> dict:
    cur = title_prefs()
    if body.get("pick") in ("a", "b"):
        cur["pick"] = body["pick"]
    if "hashtags" in body:
        cur["hashtags"] = bool(body["hashtags"])
    if "chat_titles" in body:
        cur["chat_titles"] = bool(body["chat_titles"])
    try:
        persist.write_json(TITLE_PREFS, cur)
    except OSError:
        pass
    return cur


def _streamer_name(pr: dict) -> str:
    return pr.get("streamer_display") or pr.get("streamer") or ""


def _title_clip(pr: dict, d: dict, words_abs: list, used: set):
    rel = [{"start": w["start"] - d["start"], "end": w["end"] - d["start"], "word": w["word"]}
           for w in words_abs if d["start"] - 0.2 <= w["start"] <= d["end"]]
    tp = title_prefs()
    ct = _chat_title(pr, d["start"], d["end"]) if tp["chat_titles"] else ""
    info = titles.generate(rel, _chat_window(pr, d["start"], d["end"]),
                           peak_rel=d.get("peak", d["start"]) - d["start"], used=used,
                           streamer=_streamer_name(pr), with_tags=tp["hashtags"], pick=tp["pick"], chat_title=ct)
    cfg = safety.load_config()
    if not d.get("user_title") and not d.get("userTitle"):
        d["title"] = safety.mask_text(info["title"], cfg)
        d["post"] = safety.mask_text(info["post"], cfg)
    # both title options, the description and the tags are kept with the clip (and saved with the VOD)
    d["title_a"] = safety.mask_text(info["title_a"], cfg)
    d["title_b"] = safety.mask_text(info["title_b"], cfg)
    d["title_b_named"] = info["title_b_named"]
    d["description"] = safety.mask_text(info["description"], cfg)
    # Settings › Hashtags › In post captions: the clip's own tags plus your defaults (all posts, this
    # streamer, this platform)
    d["hashtags"] = hashtags.post_tags(info["hashtags"], _streamer_name(pr), exports.load()["preset"])
    if not d.get("user_title") and not d.get("userTitle"):
        d["post"] = safety.mask_text(titles.compose(info["description"], d["hashtags"], tp["hashtags"]), cfg)
    d["titles"] = [safety.mask_text(t, cfg) for t in info["titles"]]
    d["vibe"] = info["vibe"]
    d["emoji"] = info["emoji"]
    if cfg.get("auto", True) or d.get("safety"):
        checkflow.check(d, cfg, pr["info"].get("has_audio", True))
    used.add(d["title"].lower())


def rescan_all():
    """Content-check settings changed: check every clip that was checked before again.
    Decisions made under different rules come back for review (checkflow handles that)."""
    cfg = safety.load_config()
    for pid, pr in list(projects.items()):
        for d in pr.get("clips", []) + pr.get("gems", []):
            if d.get("listened") and (d.get("safety") or cfg.get("auto", True)):
                checkflow.check(d, cfg, pr["info"].get("has_audio", True))
        save_project_soon(pid)


def clip_dicts(pid: str, clips: list, kind: str = "hype") -> list:
    pr = projects[pid]
    tdir = os.path.join(pr["workdir"], "thumbs")
    os.makedirs(tdir, exist_ok=True)
    out, used = [], set()
    for c in clips:
        d = asdict(c) if not isinstance(c, dict) else dict(c)
        t = d["start"] + (d["end"] - d["start"]) * 0.6
        name = f"t_{pr.get('thumb_prefix', '')}{int(t)}.jpg"
        path = os.path.join(tdir, name)
        if not os.path.exists(path):
            try:
                engine.make_thumb(pr["video"], t, path)
            except Exception:
                pass
        d["thumb"] = f"/files/{pid}/thumbs/{name}"
        d["pid"] = pid
        d["vod"] = f"/vod/{pid}"
        d.setdefault("words", [])
        d.setdefault("listened", False)
        d.setdefault("kind", kind)
        d.setdefault("orig", [d["start"], d["end"]])       # the cut as found, for "Restore original cut"
        d["fps"] = pr["info"].get("fps", 30.0)
        # quick first title from chat alone; replaced once the clip has been listened to
        _title_clip(pr, d, [], used)
        out.append(d)
    return out


def _origin(pr: dict) -> dict:
    """Where the video came from: platform, what kind of video, who made it, and links back to it."""
    m = engine._meta(pr.get("workdir", ""))
    c = sources.classify(m.get("source") or pr.get("source") or "")
    plat = m.get("platform") or c["platform"]
    return {"platform": plat, "platform_label": sources.PLATFORMS.get(plat, "Web"),
            "kind": m.get("kind") or c["kind"], "label": m.get("label") or c["label"],
            "uploader": m.get("uploader", ""), "uploaded": m.get("uploaded", ""),
            "url": m.get("source") or pr.get("source", ""), "start": float(m.get("start") or 0),
            "link": sources.link_template(m)}


def project_view(pid: str) -> dict:
    pr = projects[pid]
    an = pr["analysis"]
    return {
        "pid": pid, "title": pr["title"], "source": pr["source"], "duration": an.duration,
        "width": pr["info"]["width"], "height": pr["info"]["height"],
        "has_chat": an.has_chat, "has_audio": an.has_audio, "chat_count": len(pr["chat"]),
        "chat_weight": an.chat_weight_pct, "chat_stats": an.chat_stats or {},
        "events": an.has_events, "estimate": engine.estimate(an),
        "streamer": pr.get("streamer", ""), "streamer_name": _streamer_name(pr),
        "learning": learning.describe(pr.get("streamer", "")),
        "curve": curve_preview(an.score), "clips": pr["clips"], "gems": pr.get("gems", []),
        "deep": deep_status(pr), "length_mode": pr.get("length", 0),
        "vod": f"/vod/{pid}", "vcodec": pr["info"].get("vcodec", ""),
        "ext": os.path.splitext(pr["video"])[1].lower(),
        "settings": {"count": pr.get("count", 0), "length": pr.get("length", 0),
                     "chat_weight": an.chat_weight_pct},
        "restored": bool(pr.get("restored")),
        "pick_note": pr.get("pick_note", ""),
        "review": persist.summary(pr["clips"]),
        "detect": pr.get("detect") or detect.settings({}),
        "detect_text": detect.describe(pr.get("detect") or {}, an.chat_delay if getattr(an, "delay_sure", False) else None,
                                       an.has_chat),
        "chat_delay": an.chat_delay,
        "origin": _origin(pr),
        "emotes_known": getattr(an, "emotes_known", 0),
        "presets": [{"key": k, "label": v["label"], "hint": v["hint"], "chat": v["chat"], "min": v["min"], "max": v["max"]}
                    for k, v in detect.PRESETS.items()],
    }


def _pick(pr: dict, count: int, length: float, det: Optional[dict] = None) -> list:
    an = pr["analysis"]
    pr["count"] = count
    det = det or pr.get("detect") or detect.settings({})
    tr = learning.trim_offsets(pr.get("streamer", ""))
    clips = engine.pick_clips(an, count=count, length=length, chat=pr["chat"], chat_delay=pr["chat_delay"],
                              adjust=learning.adjuster(pr.get("streamer", "")),
                              min_len=det["min_len"], max_len=det["max_len"], sensitivity=det["sensitivity"],
                              back=detect.preset(det["preset"])["back"], offsets=(tr["start"], tr["end"]))
    # a number you chose is never cut short quietly
    pr["pick_note"] = (f"Found {len(clips)} of the {count} clips you asked for: that's every separate moment "
                       "in this video that stands out at all." if 0 < len(clips) < count else "")
    return clips


def _number(pr: dict, clips: list, kind: str = "hype", taken: Optional[set] = None):
    """Give engine.Clips their lasting numbers (the same moment keeps its number for good)."""
    taken = set() if taken is None else taken
    book = pr.setdefault("book", {"next": 1, "moments": []})
    for c in clips:
        c.id = persist.number_for(book, c.start, c.end, c.peak, kind, taken)
        taken.add(c.id)


def _project_sig(pr: dict) -> str:
    """Which video a saved review belongs to: its size, so downloading the same VOD again still matches."""
    try:
        return str(os.path.getsize(pr["video"]))
    except OSError:
        return ""


def _revive(pid: str, d: dict) -> dict:
    """A clip from project.json, ready for the app again."""
    d = dict(d)
    d["pid"] = pid
    d["vod"] = f"/vod/{pid}"
    d["thumb"] = re.sub(r"^/files/\w+/", f"/files/{pid}/", d.get("thumb", ""))
    pr = projects[pid]
    m = re.match(r"^/files/\w+/thumbs/([\w.-]+)$", d["thumb"])
    if m and not os.path.exists(os.path.join(pr["workdir"], "thumbs", m.group(1))):
        try:                     # thumbnails go with Delete VODs; make them again
            os.makedirs(os.path.join(pr["workdir"], "thumbs"), exist_ok=True)
            engine.make_thumb(pr["video"], d["start"] + (d["end"] - d["start"]) * 0.6,
                              os.path.join(pr["workdir"], "thumbs", m.group(1)))
        except Exception:
            pass
    d.setdefault("kind", "hype")
    d.setdefault("words", [])
    d["fps"] = pr["info"].get("fps", 30.0)
    if isinstance(d.get("safety"), dict) and "status" not in d["safety"]:
        checkflow.refresh(d, None, pr["info"].get("has_audio", True))     # saved by 3.2: add statuses
    return d


_dl_cond = threading.Condition()
_dl_running = [0]


class _download_slot:
    """Settings › Performance › Downloads at once: wait for a free slot before downloading."""

    def __init__(self, job, step, check):
        self.job, self.step, self.check = job, step, check

    def __enter__(self):
        with _dl_cond:
            while _dl_running[0] >= exports.load()["downloads"]:
                self.step("Waiting for another download to finish")
                self.check()
                _dl_cond.wait(1.0)
            _dl_running[0] += 1
        self.step("Getting the VOD")
        return self

    def __exit__(self, *a):
        with _dl_cond:
            _dl_running[0] -= 1
            _dl_cond.notify_all()
        return False


def do_analyze(job, body: dict):
    log, step, check = job_logger(job), job_step(job), job_check(job)
    src = (body.get("source") or "").strip()
    if not src:
        raise RuntimeError("Paste a Twitch, YouTube or X video link, or the path to a video file.")
    step("Getting the VOD")
    with _download_slot(job, step, check), twitchauth.cookie_file() as cookie:
        s = engine.acquire(src, WORKROOT, chat_file=body.get("chat_file") or "",
                           max_height=int(body.get("max_height", 1080)),
                           cookies_browser=body.get("cookies") or "", cookie_file=cookie,
                           log=log, progress=job_progress(job, 0.0, 0.62), step=step, check=check)
    step("Finding the best moments")
    # a VOD you opened before keeps its own detection settings; otherwise use what was asked for
    saved = persist.load_project(os.path.join(s.workdir, "project.json"), _project_sig({"video": s.video}))
    asked = dict(body.get("detect") or {})
    if "chat_delay" in body and "chat_delay" not in asked:
        asked["chat_delay"] = body["chat_delay"]
    det = detect.settings(((saved or {}).get("settings") or {}).get("detect") or asked)
    es = None
    if body.get("emotes", True) and s.chat:
        step("Loading 7TV, BTTV and FFZ emotes")
        es = emotes.for_channel(engine._meta(s.workdir).get("streamer_id", ""), s.streamer, log)
        step("Finding the best moments")
    an = engine.analyze_cached(s.workdir, s.video, s.chat, chat_delay=det["chat_delay"],
                               chat_weight_pct=float(body.get("chat_weight", detect.preset(det["preset"])["chat"])),
                               log=log, progress=job_progress(job, 0.62, 0.94), check=check,
                               events=bool(body.get("events", True)), emote_set=es,
                               weights=detect.preset(det["preset"]))
    delay = an.chat_delay
    pid = _pid_for(s.workdir)
    old = projects.get(pid)
    if old:      # opened again while it was still open: stop its background work first
        for k in ("listen_job", "deep_job"):
            j = jobs.get(old.get(k, ""))
            if j and j["status"] == "running":
                j["cancel"] = True
    info = engine.probe(s.video)
    length = float(body.get("length", 0) or 0)
    count = int(body.get("count", 0) or 0)
    projects[pid] = {"video": s.video, "chat": s.chat, "title": s.title, "workdir": s.workdir,
                     "analysis": an, "info": info, "clips": [], "chat_delay": delay,
                     "source": src, "clips_ver": 0, "streamer": s.streamer, "length": length,
                     "count": count, "tx_lock": threading.Lock(), "gems": [], "detect": det,
                     "book": {"next": 1, "moments": []}}
    pr = projects[pid]
    pr["streamer_display"] = engine._meta(s.workdir).get("streamer_display", "")
    engine._save_meta(s.workdir, duration=an.duration)
    _load_transcripts(pr)
    est = engine.estimate(an)
    if saved:
        # opened before: bring back the clips, their numbers and everything you did to them
        st = saved.get("settings") or {}
        pr["count"] = int(st.get("count", count) or 0)
        pr["length"] = float(st.get("length", length) or 0)
        an.combine(float(st.get("chat_weight", an.chat_weight_pct)), detect.preset(det["preset"]))
        pr["book"] = saved.get("book") or pr["book"]
        pr["clips"] = [_revive(pid, d) for d in saved["clips"]]
        pr["gems"] = [_revive(pid, d) for d in saved.get("gems", [])]
        pr["removed"] = [dict(d, pid=pid) for d in saved.get("removed", [])]
        pr.update(deep_done=bool(saved.get("deep_done")), deep_model=saved.get("deep_model", ""),
                  deep_lang=saved.get("deep_lang", ""), restored=True)
        r = persist.summary(pr["clips"])
        log(f"Picked up where you left off: {r['kept']} kept, {r['skipped']} skipped, {r['todo']} to review.")
    else:
        clips = _pick(pr, count, length)
        _number(pr, clips)
        step("Making thumbnails")
        pr["clips"] = clip_dicts(pid, clips)
        for d in pr["clips"]:
            d["len_mode"] = length
        if clips:
            log(f"Found {len(clips)} clip{'s' if len(clips) != 1 else ''} worth a look "
                f"(about {est['strong']} strong moment{'s' if est['strong'] != 1 else ''} in this stream).")
        else:
            log("Nothing stood out strongly. Click the timeline to add clips yourself.")
        tuned = sum(1 for c in clips if abs(c.tuned) > 0.05)
        if tuned:
            log(f"Ranking tuned to what you kept and skipped before ({learning.describe(s.streamer)['using']}).")
    save_project_soon(pid, 0.1)
    model, lang = _deep_model(body.get("deep_model") or "auto"), body.get("language") or ""
    if saved and pr["deep_done"] and pr["gems"]:
        pass                                    # quiet gems came back with the project
    elif _covered(pr, model, lang) >= 0.98:
        _update_gems(pid, model, lang)          # deep scanned before: the quiet gems are ready right away
        pr.update(deep_done=True, deep_model=model, deep_lang=lang)
    elif body.get("deep_scan"):
        try:
            start_deep_scan(pid, model, lang)
        except RuntimeError as e:
            log(f"Deep scan skipped: {e}")
    return project_view(pid)


# ---- deep scan: transcribe the whole VOD, then find quiet gems

def _deep_model(choice: str) -> str:
    if choice and choice != "auto":
        return choice
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "small"
    except Exception:
        pass
    return "base"


def deep_status(pr: dict) -> dict:
    j = jobs.get(pr.get("deep_job", ""))
    st = {"state": "none", "progress": 0.0, "step": "", "model": pr.get("deep_model", ""), "error": "",
          "gems": len(pr.get("gems", [])), "can": engine.whisper_available() and pr["info"].get("has_audio", False)}
    if pr.get("deep_done"):
        st["state"] = "done"
        st["progress"] = 1.0
    if j:
        st.update(progress=j["progress"], step=j["step"])
        if j["status"] == "running":
            st["state"] = "running"
        elif j["status"] == "error":
            st.update(state="error", error=j.get("error") or "")
        elif j["status"] == "cancelled" and not pr.get("deep_done"):
            st["state"] = "cancelled"
    return st


def _covered(pr: dict, model: str, lang: str) -> float:
    tx = pr.get("tx", {}).get(f"{model}|{lang}")
    if not tx:
        return 0.0
    return sum(b - a for a, b in tx["covered"]) / max(1.0, pr["analysis"].duration)


def _update_gems(pid: str, model: str, lang: str):
    pr = projects[pid]
    tx = pr.get("tx", {}).get(f"{model}|{lang}")
    if not tx:
        return
    words = tx["words"]
    taken = [(c["start"], c["end"]) for c in pr["clips"]]
    found, _curve = gems.find(pr["analysis"], words, taken, chat=pr["chat"], chat_delay=pr["chat_delay"])
    old = {g["id"]: g for g in pr.get("gems", [])}
    out, used = [], {c.get("title", "").lower() for c in pr["clips"]}
    fresh = []
    _number(pr, found, "gem")
    gone = _removed_nums(pr)
    found = [g for g in found if g.id not in gone]
    for g in found:
        o = old.pop(g.id, None)
        if o:
            out.append(o)
        else:
            fresh.append(g)
    # gems you already did something with stay, even if a later pass ranks them out
    out += [g for g in old.values() if persist.touched(g)]
    for d in clip_dicts(pid, fresh, kind="gem"):
        ws = [w for w in words if d["start"] - TX_MARGIN <= w["start"] <= d["end"] + TX_MARGIN]
        d["words"] = ws
        _title_clip(pr, d, ws, used)
        d["listened"] = True
        d["kind"] = "gem"
        out.append(d)
    out.sort(key=lambda d: -d.get("score", 0))
    pr["gems"] = out
    save_project_soon(pid)


def start_deep_scan(pid: str, model: str, lang: str):
    pr = projects[pid]
    if not engine.whisper_available():
        raise RuntimeError("Deep scan needs Whisper, which isn't available in this install.")
    if not pr["info"].get("has_audio"):
        raise RuntimeError("This video has no sound to transcribe.")
    old = jobs.get(pr.get("deep_job", ""))
    if old and old["status"] == "running":
        return old
    model = _deep_model(model)
    job = new_job("deepscan")
    pr.update(deep_job=job["id"], deep_model=model, deep_lang=lang, deep_done=False)
    dur = pr["analysis"].duration

    def go():
        check, log = job_check(job), job_logger(job)
        log(f"Deep scan with Whisper '{model}': transcribing the whole stream.")
        step_s = 60.0
        a, last_gems, t0 = 0.0, 0.0, time.time()
        stats, bad_run, bad_from = {}, 0, 0.0
        job["step"] = "Deep scan: getting Whisper ready"
        while a < dur:
            check()
            # titles for the suggested clips come first: let a running listening job finish
            while (jobs.get(pr.get("listen_job", "")) or {}).get("status") == "running":
                check()
                time.sleep(0.5)
            b = min(dur, a + step_s)
            before = stats.get("unreadable", 0)
            ensure_words(pr, a, b, model, lang, check, stats)
            if stats.get("unreadable", 0) > before:
                bad_from = a if not bad_run else bad_from
                bad_run += 1
            else:
                bad_run = 0
            if bad_run >= 3:
                # not "done, no gems": say the sound couldn't be read, so Retry means something
                raise RuntimeError(f"Couldn't read the video's sound from {engine.fmt_ts(bad_from)}. "
                                   "The file may be damaged or still downloading.")
            job["progress"] = round(b / dur, 4)
            took = time.time() - t0
            left = took / max(b, 1) * (dur - b)
            job["step"] = f"Deep scan {int(b / dur * 100)}%" + (f", about {engine.fmt_ts(left)} left" if b > 120 else "")
            if b - last_gems >= 900 or b >= dur:
                _update_gems(pid, model, lang)           # gems show up while the scan goes on
                last_gems = b
            a = b
        pr["deep_done"] = True
        _save_transcripts(pr)
        if stats.get("unreadable"):
            log(f"Couldn't read the sound in {stats['unreadable']} short stretch(es); they were skipped.")
        n = len(pr.get("gems", []))
        log(f"Deep scan done: {n} quiet gems.")
        return {"gems": n}
    run_job(job, go)
    return job


def _tx_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "transcripts.json")


def _video_sig(pr: dict) -> str:
    try:
        st = os.stat(pr["video"])
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _load_transcripts(pr: dict):
    """Transcripts made before for this VOD (deep scans, captions) so nothing is transcribed twice."""
    try:
        with open(_tx_file(pr), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("video") != _video_sig(pr):
            return
        pr["tx"] = {k: {"words": v["words"], "covered": [tuple(x) for x in v["covered"]]}
                    for k, v in data.get("tx", {}).items()}
    except (OSError, ValueError, KeyError, TypeError):
        pass


def _save_transcripts(pr: dict):
    if pr.get("live") or not pr.get("tx"):
        return
    tmp = _tx_file(pr) + ".tmp"
    try:
        with pr["tx_lock"]:
            data = {"video": _video_sig(pr), "tx": {k: {"words": v["words"], "covered": v["covered"]}
                                                     for k, v in pr["tx"].items()}}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _tx_file(pr))
    except OSError:
        pass


# ---- transcript cache: each stretch of the VOD is transcribed once, then reused
#      for titles, the live caption preview, and the final render

def _tx(pr: dict, model: str, lang: str) -> dict:
    return pr.setdefault("tx", {}).setdefault(f"{model}|{lang}", {"words": [], "covered": []})


def _vocab(pr: dict) -> str:
    """Names and words Whisper should expect for this stream: the streamer, the channel's emotes, your list."""
    es = getattr(pr.get("analysis"), "_emotes", None)
    return exports.prompt(_streamer_name(pr), list(es.by_name) if es else None, exports.load().get("vocab"))


def ensure_words(pr: dict, a: float, b: float, model: str, lang: str, check=lambda: None,
                 stats: Optional[dict] = None) -> list:
    """Transcript words (absolute times) for [a, b] of the VOD, transcribing any missing parts.
    A stretch whose sound can't be read isn't marked as done (so it's tried again later) and is
    counted in stats["unreadable"]."""
    dur = pr["analysis"].duration if "analysis" in pr else pr.get("duration") or b
    a, b = max(0.0, a), min(dur, b)
    with pr["tx_lock"]:
        tx = _tx(pr, model, lang)
        gaps, cur = [], a
        for ca, cb in sorted(tx["covered"]):
            if cb <= cur:
                continue
            if ca > cur:
                gaps.append((cur, min(ca, b)))
            cur = max(cur, cb)
            if cur >= b:
                break
        if cur < b:
            gaps.append((cur, b))
        for ga, gb in gaps:
            if gb - ga < 0.5:
                continue
            check()
            pad_a, pad_b = max(0.0, ga - 1.0), gb + 1.0   # a little overlap so edge words aren't cut
            audio = engine.extract_audio(pr["video"], pad_a, pad_b - pad_a)
            if audio is None:
                if stats is not None:
                    stats["unreadable"] = stats.get("unreadable", 0) + 1
                continue
            new = []
            if audio.size >= 1600:
                for w in engine.transcribe(audio, model, lang, lambda m: None, prompt=_vocab(pr)):
                    st, en = w["start"] + pad_a, w["end"] + pad_a
                    mid = (st + en) / 2
                    if ga <= mid < gb:
                        new.append({"start": round(st, 3), "end": round(en, 3), "word": w["word"]})
            tx["words"] = sorted([w for w in tx["words"] if not (ga <= (w["start"] + w["end"]) / 2 < gb)] + new,
                                 key=lambda w: w["start"])
            tx["covered"].append((ga, gb))
            # merge intervals
            merged = []
            for ca, cb in sorted(tx["covered"]):
                if merged and ca <= merged[-1][1] + 0.01:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], cb))
                else:
                    merged.append((ca, cb))
            tx["covered"] = merged
        return [w for w in tx["words"] if a <= w["start"] < b]


def start_listening(pid: str, order: list, model: str, lang: str, on_done=None):
    """Background job: transcribe each suggested clip and give it a proper title."""
    pr = projects[pid]
    old = jobs.get(pr.get("listen_job", ""))
    if old and old["status"] == "running":
        old["cancel"] = True
    job = new_job("listen")
    pr["listen_job"] = job["id"]
    can_listen = engine.whisper_available() and pr["info"]["has_audio"]

    def go():
        check = job_check(job)
        ver = pr.get("clips_ver", 0)
        ids = [i for i in order if any(c["id"] == i for c in pr["clips"])]
        ids += [c["id"] for c in pr["clips"] if c["id"] not in ids]
        todo = [i for i in ids if not next((c for c in pr["clips"] if c["id"] == i), {}).get("listened")]
        for n, cid in enumerate(todo):
            check()
            if pr.get("clips_ver", 0) != ver:
                return
            d = next((c for c in pr["clips"] if c["id"] == cid), None)
            if not d or d.get("listened"):
                continue
            job["step"] = f"Listening to clip {n + 1} of {len(todo)}"
            words = []
            if can_listen:
                gated = bool(pr.get("live"))
                try:
                    if gated:
                        job["step"] = "Waiting for other moments to be listened to" if live_gate.queued() or live_gate.busy else job["step"]
                        live_gate.acquire(_live_prio(d), check)
                        job["step"] = f"Listening to clip {n + 1} of {len(todo)}"
                    try:
                        words = ensure_words(pr, d["start"] - TX_MARGIN, d["end"] + TX_MARGIN, model, lang, check)
                    finally:
                        if gated:
                            live_gate.release()
                except engine.Cancelled:
                    raise
                except Exception as e:
                    job_logger(job)(f"Couldn't transcribe clip: {str(e)[:160]}")
            used = {c["title"].lower() for c in pr["clips"] if c is not d and c.get("listened")}
            sess = live_sessions.get(pr.get("live", ""))
            if sess:   # live moments are separate projects; don't repeat titles across the stream
                used |= {projects[m]["clips"][0]["title"].lower() for m in sess.moments
                         if m != pid and m in projects and projects[m]["clips"] and projects[m]["clips"][0].get("listened")}
            if words and not d.get("trimmed") and not d.get("manual") and not d.get("userTrim") \
                    and d.get("state") != "kept":
                # never start or stop mid-sentence
                fixed = (d.get("len_mode") or pr.get("length") or 0) > 0
                a2, b2 = engine.refine_with_words(d["start"], d["end"], d.get("peak", d["start"]), words,
                                                  max_move=3.0 if fixed else 8.0)
                a2, b2 = max(0.0, a2), min(pr["analysis"].duration, b2)
                if b2 - a2 >= 3 and (abs(a2 - d["start"]) > 0.05 or abs(b2 - d["end"]) > 0.05):
                    d["start"], d["end"] = round(a2, 2), round(b2, 2)
                    d["orig"] = [d["start"], d["end"]]
                    if d.get("abs_start") is not None and d.get("at") is not None:
                        off = d["at"] - d.get("peak", 0)
                        d["abs_start"], d["abs_end"] = round(off + d["start"], 1), round(off + d["end"], 1)
            d["words"] = words
            _title_clip(pr, d, words, used)
            d["listened"] = True
            job["progress"] = (n + 1) / max(1, len(todo))
            if not pr.get("live"):
                save_project_soon(pid, 3.0)
        if not pr.get("live"):
            _save_transcripts(pr)
        if on_done:
            try:
                on_done(pid)
            except Exception:
                traceback.print_exc()
        return {"ok": True}
    run_job(job, go)
    return job


# ---- learning from what you keep, skip and post

def _find_clip(pr: dict, cid) -> Optional[dict]:
    for d in pr.get("clips", []) + pr.get("gems", []):
        if d.get("id") == cid:
            return d
    return None


def _apply_clip_state(pr: dict, d: dict, b: dict):
    if b.get("state") in ("todo", "kept", "skipped"):
        d["state"] = b["state"]
    if "start" in b and "end" in b:
        st, en = float(b["start"]), float(b["end"])
        dur = pr["analysis"].duration if "analysis" in pr else None
        if dur:
            st, en = max(0.0, st), min(dur, en)
        if en - st >= 1:
            d["start"], d["end"] = round(st, 3), round(en, 3)
    for k in ("userTrim", "userTitle", "userPost", "wordsEdited"):
        if k in b:
            d[k] = bool(b[k])
    if "title" in b:
        d["title"] = str(b["title"] or "")[:120]
    if "postText" in b:
        d["postText"] = str(b["postText"] or "")[:2200]
    if "words_edit" in b:
        ws = _clean_words(b["words_edit"])
        if ws is None:
            d.pop("words_edit", None)
        else:
            d["words_edit"] = ws
    if not d.get("wordsEdited"):
        d.pop("words_edit", None)
    checkflow.mark_changed(d)


def _check_view(d: dict) -> dict:
    keys = ("id", "start", "end", "title", "postText", "userPost", "userTitle", "userTrim", "state", "safety", "check")
    return {k: d.get(k) for k in keys}


def _drop_from_queue(pid: str, cid) -> int:
    """Take a clip's unfinished renders out of the queue (finished files stay in Exports)."""
    n = 0
    with queue_lock:
        for it in list(queue):
            if it.get("pid") == pid and it.get("clip_id") == cid and it["status"] != "done":
                if it["status"] == "rendering":
                    it["cancel"] = True
                else:
                    queue.remove(it)
                n += 1
    if n:
        save_queue_soon()
    return n


def _removed_nums(pr: dict) -> set:
    return {m["num"] for m in (pr.get("book") or {}).get("moments", []) if m.get("removed")}


def record_feedback(pid, cid, action: str):
    pr = projects.get(pid)
    if not pr or action not in ("keep", "skip", "post", "undo"):
        return
    d = _find_clip(pr, cid)
    if not d or not d.get("feat"):
        return
    at = d.get("at", d.get("peak", 0))
    key = f"{pr.get('live') or pr.get('source', '')}|{int(at)}"
    learning.record(pr.get("streamer", ""), key, d["feat"], action)
    _refresh_live_learning()
    if action == "skip":
        # Settings › Storage: a clip that rendered on its own (hands-free) and you then skipped
        with queue_lock:
            mine = [x for x in queue if x.get("pid") == pid and x.get("clip_id") == cid and x.get("auto")]
        for it in mine:
            if cleanup.skipped_render(it):
                with queue_lock:
                    if it in queue:
                        queue.remove(it)
                save_queue_soon(0)


def _refresh_live_learning():
    for sess in list(live_sessions.values()):
        if sess.status in ("starting", "waiting", "recording", "reconnecting"):
            sess.adjust = learning.adjuster(sess.channel or "")


# ---- render queue: kept clips render one after another in the background

queue: list = []
queue_lock = threading.Lock()
queue_wake = threading.Event()


def _safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    return text[:60].rstrip(". ")


def _frac(v, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return default


def _clean_words(ws) -> Optional[list]:
    """Caption words edited in the app (absolute times), or None when the clip wasn't edited."""
    if not isinstance(ws, list):
        return None
    out = []
    for w in ws[:5000]:
        try:
            st, en, txt = float(w["start"]), float(w["end"]), str(w["word"]).strip()[:60]
        except (KeyError, TypeError, ValueError):
            continue
        if txt:
            item = {"start": st, "end": max(en, st + 0.05), "word": txt}
            if w.get("g") is not None:
                item["g"] = str(w["g"])[:24]
            if w.get("edited"):
                item["edited"] = True
            out.append(item)
    return sorted(out, key=lambda w: w["start"])


def _ropts(o: dict, ex: Optional[dict] = None) -> engine.RenderOpts:
    ex = ex or {}
    return engine.RenderOpts(
        fps=int(ex.get("fps", 30)), crf=int(ex.get("crf", 20)), scale=float(ex.get("scale", 1.0)),
        vcodec=str(ex.get("vcodec", "h264")), vbitrate=int(ex.get("vbitrate", 0)), acodec=str(ex.get("acodec", "aac")),
        abitrate=int(ex.get("abitrate", 192)), container=str(ex.get("container", "mp4")),
        loudness=float(ex.get("loudness", -14.0)), denoise=bool(ex.get("denoise")), voice=bool(ex.get("voice")),
        cap_outline=int(o.get("cap_outline", 100)), cap_shadow=int(o.get("cap_shadow", 100)),
        layout=o.get("layout", "split"),
        cam=tuple(float(v) for v in o.get("cam", [0, 0, 0.25, 0.25])),
        cam_height=float(o.get("cam_height", 0.33)),
        game_zoom=float(o.get("game_zoom", 1.0)),
        game_x=float(o.get("game_x", 0.5)),
        captions=bool(o.get("captions", True)),
        caption_pos=o.get("caption_pos", "auto"),
        caption_color=o.get("caption_color", "#FFE600"),
        caption_size=int(o.get("caption_size", 100)),
        words_per_line=int(o.get("words_per_line", 3)),
        uppercase=bool(o.get("uppercase", True)),
        caption_style=str(o.get("caption_style", "pop")),
        caption_font=str(o.get("caption_font", "classic")),
        caption_anim=bool(o.get("caption_anim", True)),
        cap_x=_frac(o.get("cap_x"), 0.5),
        cap_y=_frac(o.get("cap_y"), 0.7),
        title_style=str(o.get("title_style", "box")),
        title_font=str(o.get("title_font", "classic")),
        title_size=int(o.get("title_size", 100)),
        title_x=_frac(o.get("title_x"), 0.5),
        title_y=_frac(o.get("title_y"), 0.05),
        whisper_model=o.get("whisper_model", "small"),
        language=o.get("language", ""),
        hw_encode=bool(o.get("hw_encode", True)),
        aspect=o.get("aspect") if o.get("aspect") in engine.SHAPES else "9:16",
        track="off" if o.get("track") == "off" else "auto",
        track_target=o.get("track_target") if o.get("track_target") in framing.TARGETS else "auto",
        focus_y=_frac(o.get("focus_y"), 0.4),
    )


def _framing_for(pr: dict, start: float, end: float, ro, log=None) -> Optional[dict]:
    """Follow the face for a whole-frame crop (Layout › Follow face). None when it doesn't apply."""
    if engine.effective_layout(ro) != "full" or ro.track != "auto" or not framing.available():
        return None
    try:
        return framing.track(pr["video"], start, end, pr["info"], ro, cache_dir=locations.temp_dir(os.path.basename(pr["workdir"]), "framing"),
                             log=log or (lambda m: print("  " + m, flush=True)))
    except Exception as e:
        print(f"  Framing skipped: {str(e)[:160]}", flush=True)
        return None


def queue_public(it: dict) -> dict:
    d = {k: v for k, v in it.items() if k not in ("opts", "cancel", "path", "words", "sig")}
    d["duration"] = round(it["end"] - it["start"], 2)
    return d


def queue_add(pid: str, c: dict, opts: dict) -> dict:
    start_, end_ = float(c["start"]), float(c["end"])
    if end_ - start_ < 1:
        raise RuntimeError("That clip is too short.")
    pr = projects.get(pid) or {}
    d = _find_clip(pr, c.get("id")) if pr else None
    if d:
        why = checkflow.blocks_render(d)
        if why:
            raise RuntimeError(why)
        if d.get("orig") and not pr.get("live") and not d.get("manual") and (
                abs(start_ - d["orig"][0]) > 0.3 or abs(end_ - d["orig"][1]) > 0.3):
            # how you trimmed it teaches where this streamer's clips should start and end
            try:
                learning.record_trim(pr.get("streamer", ""), f"{pr.get('source', '')}|{int(d.get('peak', start_))}",
                                     start_ - d["orig"][0], end_ - d["orig"][1])
            except Exception:
                pass
    it = {"id": uuid.uuid4().hex[:8], "pid": pid, "clip_id": c.get("id"), "rank": int(c.get("rank") or 0),
          "start": start_, "end": end_, "title": str(c.get("title") or "")[:120],
          "post": str(c.get("post") or "")[:2200], "thumb": c.get("thumb", ""),
          "source_title": pr.get("title", ""),
          # enough to render it after a restart, when the project isn't open any more
          "video": pr.get("video", ""), "workdir": pr.get("workdir", ""), "live": pr.get("live", ""),
          "opts": dict(opts or {}), "status": "queued", "progress": 0.0, "step": "Waiting",
          "error": "", "cancel": False, "created": time.time(),
          "words": _clean_words(c.get("words")),
          # content check: fixes you applied, and flags you chose to leave alone (not censored)
          "fix_spans": checkflow.render_spans(d) if d else [],
          "keep_fps": [f["fp"] for f in ((d or {}).get("safety") or {}).get("flags", [])
                       if f.get("status") in ("accepted", "dismissed")]}
    try:
        it["link"] = sources.link_at(engine._meta(pr.get("workdir", "")), start_)    # back to the source, at this moment
    except Exception:
        it["link"] = ""
    prefs = exports.load()
    ex = exports.settings(prefs)
    it["export"] = ex
    it["name_fields"] = exports.fields_for(dict(d or {}, title=it["title"]), pr, ex["preset"], number=it["clip_id"])
    it["sig"] = json.dumps([pid, it["clip_id"], round(start_, 2), round(end_, 2), it["title"], it["opts"], ex], sort_keys=True,
                           default=str)
    mx = exports.PRESETS[ex["preset"]].get("max_len")
    if mx and end_ - start_ > mx:
        it["warning"] = (f"{exports.PRESETS[ex['preset']]['label']} allows up to {engine.fmt_ts(mx)}; "
                         f"this clip is {engine.fmt_ts(end_ - start_)}. Trim it, or pick another preset.")
    with queue_lock:
        # the same clip sent twice (double click, two windows): render it once
        dup = next((x for x in queue if x.get("sig") == it["sig"] and x["status"] in ("queued", "rendering")), None)
        if dup:
            return dup
        queue.append(it)
    queue_wake.set()
    save_queue_soon()
    return it


_render_projects: dict = {}


def _render_project(it: dict) -> dict:
    """The project a queued clip comes from. After a restart it isn't open, so rebuild the little
    a render needs (the video, its folder and any transcript made before)."""
    pr = projects.get(it["pid"])
    if pr:
        return pr
    video = it.get("video") or ""
    if not video or not os.path.exists(video):
        raise RuntimeError("The VOD for this clip was deleted.")
    pr = _render_projects.get(it["pid"])
    if not pr or pr["video"] != video:
        info = engine.probe(video)
        pr = {"video": video, "workdir": it.get("workdir") or os.path.dirname(video),
              "title": it.get("source_title", ""), "info": info, "duration": info["duration"],
              "tx_lock": threading.Lock(), "live": it.get("live", "")}
        if not pr["live"]:
            _load_transcripts(pr)
        _render_projects[it["pid"]] = pr
    return pr


def queue_busy() -> bool:
    return any(it["status"] in ("queued", "rendering") for it in queue)


def _file_name(it: dict, outdir: str) -> str:
    """The clip's file is named after its title, chosen once: a retry replaces its own file, and only
    another clip with the same title gets " (2)"."""
    if it.get("name") and os.path.normcase(it.get("folder") or "") == os.path.normcase(outdir):
        return it["name"]
    ex = it.get("export") or {}
    ext = ex.get("container", "mp4") if ex.get("container") in ("mp4", "mov", "webm") else "mp4"
    if it.get("name_fields"):
        base = exports.file_name(ex.get("template", exports.DEFAULT_TEMPLATE), dict(it["name_fields"], title=it["title"] or "clip"))
    else:
        base = _safe_name(it["title"]) if it["title"].strip() else f"clip {engine.fmt_ts(it['start']).replace(':', '-')}"
    # Settings › Hashtags › In file names: the real tags, '#' and all, chosen with the name (so a retry keeps them)
    base = hashtags.with_tags(base, hashtags.file_tags((it.get("name_fields") or {}).get("streamer", "")))
    with queue_lock:
        claimed = {os.path.normcase(x["name"]) for x in queue if x is not it and x.get("name")
                   and os.path.normcase(x.get("folder") or "") == os.path.normcase(outdir)}
    name, n = f"{base}.{ext}", 2
    while os.path.normcase(name) in claimed or os.path.exists(os.path.join(outdir, name)):
        name = f"{base} ({n}).{ext}"
        n += 1
    it["name"], it["folder"] = name, outdir
    save_queue_soon()
    return name


MB_PER_SECOND = 1.5      # generous upper bound for a 1080x1920 clip


def _check_space(outdir: str, seconds: float):
    need = seconds * MB_PER_SECOND * 1e6 + 200e6
    try:
        free = shutil.disk_usage(outdir).free
    except OSError:
        return
    if free < need:
        raise OSError(28, f"No space left on device ({free // 1_000_000} MB free, "
                          f"about {int(need // 1_000_000)} MB needed)")


def _check_output(path: str, expect: float):
    """A finished render must be a real video of the right length before it counts as done."""
    try:
        size = os.path.getsize(path)
        info = engine.probe(path) if size > 1000 else None
    except Exception:
        info = None
    if not info or abs(info["duration"] - expect) > max(1.0, expect * 0.05):
        try:
            os.remove(path)
        except OSError:
            pass
        got = f" ({info['duration']:.1f}s instead of {expect:.1f}s)" if info else ""
        raise BrokenOutput(f"The rendered file came out broken{got}.")


class BrokenOutput(RuntimeError):
    pass


def _render_inputs(it: dict, pr: dict, ro, check) -> tuple:
    """Captions, title and censoring for a render of it['start']..it['end'] (a clip, or a few seconds
    of one for the exact preview). Returns (words, title, post, spans, mode)."""
    cfg = safety.load_config()
    censoring = cfg.get("enabled", True) and cfg.get("censor", "off") != "off"
    words = None
    if it.get("words") is not None:
        # captions corrected by hand in the app
        words = [dict(w, start=w["start"] - it["start"], end=w["end"] - it["start"])
                 for w in it["words"] if it["start"] - 0.2 <= w["start"] < it["end"]]
    elif (ro.captions or censoring) and pr["info"]["has_audio"] and engine.whisper_available():
        it["step"] = "Listening for captions"
        abs_words = ensure_words(pr, it["start"], it["end"], ro.whisper_model, ro.language, check)
        words = [{"start": w["start"] - it["start"], "end": w["end"] - it["start"], "word": w["word"]}
                 for w in abs_words if w["start"] < it["end"]]
    # content check: flag, and (if switched on) mask and bleep what it finds
    title, post = it["title"], it.get("post", "")
    spans, mode = [], "off"
    if cfg.get("enabled", True):
        res = safety.scan(words or [], title, post, cfg)
        it["safety"] = {"level": res["level"], "flags": res["flags"][:20]}
        if censoring:
            # flags you left alone or dismissed in Review aren't censored
            keep = set(it.get("keep_fps") or [])
            res_c = dict(res, flags=[f for f in res["flags"]
                                     if checkflow.fingerprint(f, {"start": it["start"]}) not in keep])
            words, spans, mode = safety.censored(words or [], res_c, cfg)
            title, post = safety.mask_text(title, cfg), safety.mask_text(post, cfg)
            it["censored"] = sum(1 for f in res_c["flags"] if f["cat"] in cfg.get("censor_categories", []))
    # fixes applied in Review: mute or bleep those words, and mask them in the captions
    spans = [(a, b, mode) for a, b in spans]
    for a, b, kind in it.get("fix_spans") or []:
        ra, rb = a - it["start"], b - it["start"]
        if rb > 0 and ra < it["end"] - it["start"]:
            spans.append((max(0.0, ra), rb, kind))
            if words:
                words = [dict(w, word=safety.mask(w["word"])) if ra - 0.02 <= w["start"] <= rb else w for w in words]
            it["censored"] = it.get("censored", 0) + 1
    if words:
        words = exports.replace_words(words, exports.load().get("replace"))   # Settings › Captions › Replace words
    if not ro.captions:
        words = []
    return words, title, post, spans, mode


def _render_item(it: dict):
    pr = _render_project(it)
    if not os.path.exists(pr["video"]):
        raise RuntimeError("The VOD for this clip was deleted.")
    ex = it.get("export") or {}
    ro = _ropts(it["opts"], ex)
    subs = ex.get("subtitles", "burn")
    outdir = locations.clips_dir(pr["workdir"])          # Settings › Storage › Rendered clips
    os.makedirs(outdir, exist_ok=True)
    name = _file_name(it, outdir)
    length = it["end"] - it["start"]
    _check_space(outdir, length)
    enc = engine.video_encoder(ro)[0]
    skey = exports.speed_key(enc, ex)
    known = exports.speed(skey)
    it["eta"] = round(length / known + 3, 1) if known else None      # before it starts: from earlier renders
    enc_t0 = [None]

    def check():
        if it["cancel"]:
            raise engine.Cancelled()

    def prog(x):
        it["progress"] = round(max(0.0, min(1.0, x)), 3)
        now = time.time()
        if x >= 0.3:                                   # encoding: time left from the speed so far
            if enc_t0[0] is None:
                enc_t0[0] = now
            frac = (x - 0.3) / 0.7
            el = now - enc_t0[0]
            if frac > 0.04 and el > 1.5:
                it["eta"] = round((1 - frac) * el / frac + 2, 1)

    if TEST_HOOKS.get("fail_next_render"):
        TEST_HOOKS["fail_next_render"] = False
        raise OSError(28, "No space left on device")

    it["source_title"] = pr["title"]
    fr = None
    if engine.effective_layout(ro) == "full" and ro.track == "auto":
        it["step"] = "Finding the face"
        fr = _framing_for(pr, it["start"], it["end"], ro)
        check()
        if fr is not None:
            it["framing"] = {"followed": bool(fr.get("path")), "reason": fr.get("reason", ""), "confidence": fr.get("confidence", 0)}
    it["step"] = "Getting captions ready"
    # Don't leave the queue at 0% while transcript/safety preparation is running.
    it["progress"] = 0.05
    words, title, post, spans, mode = _render_inputs(it, pr, ro, check)
    it["progress"] = 0.25
    srt_words = list(words or []) if ro.captions else []
    if subs == "srt":
        words = []                                  # subtitles as a file only, not burned in
    path = os.path.join(outdir, name)
    expect = min(it["end"], pr["info"]["duration"] or it["end"]) - max(0.0, it["start"])
    for attempt in (1, 2):
        it["step"] = "Rendering" if attempt == 1 else "Rendering again"
        it["progress"] = 0.0
        t_start = time.time()
        enc_t0[0] = None
        engine.render_clip(pr["video"], it["start"], it["end"], path, ro, title=title,
                           progress=prog, check=check, words=words, censor=spans, censor_mode=mode,
                           log=lambda m: print("  " + m, flush=True), framing=fr)
        took = time.time() - (enc_t0[0] or t_start)
        if took > 0.5:
            exports.record_speed(skey, length / took)
        it["step"] = "Checking the file"
        it["eta"] = 1
        if TEST_HOOKS.get("break_next_output"):
            TEST_HOOKS["break_next_output"] = False
            with open(path, "r+b") as f:
                f.truncate(500)
        try:
            _check_output(path, expect)
            _check_sound(it, pr, path)
            break
        except BrokenOutput as e:
            print(f"  {e}" + (" Trying once more." if attempt == 1 else ""), flush=True)
            if attempt == 2:
                raise
    if subs in ("burn_srt", "srt") and srt_words:
        try:
            engine.write_srt(srt_words, ro, os.path.splitext(path)[0] + ".srt", length)
            it["srt"] = True
        except OSError:
            pass
    it["eta"] = 0
    # caption to paste when posting, saved next to the video
    if post:
        it["post"] = post
        try:
            persist.write_text(os.path.splitext(path)[0] + ".txt", post + "\n")
        except OSError:
            pass
    it["path"] = path
    it["url"] = f"/export/{it['id']}?v={int(time.time() * 1000)}"


TEST_HOOKS = {"fail_next_render": False, "break_next_output": False}


def export_view(sample: Optional[dict] = None) -> dict:
    prefs = exports.load()
    st = exports.settings(prefs)
    st["custom_changed"] = bool(prefs.get("custom"))
    smp = sample if isinstance(sample, dict) else {}
    fields = exports.fields_for({"title": smp.get("title") or "Chat could not stop laughing", "hashtags": smp.get("hashtags") or ["#twitch", "#funny"]},
                                {"title": smp.get("source") or "Friday stream", "source": smp.get("url") or "https://www.twitch.tv/videos/1",
                                 "streamer": smp.get("streamer") or "streamer"}, st["preset"], number=smp.get("number") or 7)
    return {"prefs": prefs, "settings": st, "describe": exports.describe(st),
            "presets": [{"key": k, "label": v["label"], "note": v["note"]} for k, v in exports.PRESETS.items()],
            "advanced": {k: list(v) for k, v in exports.ADVANCED.items()}, "tokens": list(exports.TOKENS),
            "preview_name": exports.file_name(prefs["template"], fields) + "." + st["container"]}


def hashtags_view(streamer: str = "") -> dict:
    """Settings › Hashtags, with a preview of a file name and each platform's post tags."""
    cfg = hashtags.load()
    who = (streamer or "").strip() or "Kai Cenat"
    prefs = exports.load()
    st = exports.settings(prefs)
    fields = exports.fields_for({"title": "Chat could not stop laughing"}, {"title": "Friday stream", "streamer": who,
                                                                            "source": "https://www.twitch.tv/videos/1"},
                                st["preset"], number=7)
    base = exports.file_name(prefs["template"], fields)
    auto = titles.hashtags("funny", who) if title_prefs()["hashtags"] else []
    return {"config": cfg, "suggestions": hashtags.GENERAL_SUGGESTIONS, "streamer": who,
            "preview": {"file": hashtags.with_tags(base, hashtags.file_tags(who, cfg)) + "." + st["container"],
                        "post": {k: hashtags.post_tags(auto, who, k, cfg) for k in hashtags.PLATFORMS},
                        "platform": st["preset"], "post_on": title_prefs()["hashtags"]}}


def perf_view() -> dict:
    sp = exports.all_speeds()
    enc = engine._encoder_cache.get("hw")
    mem = locations.memory()
    return {"cpus": os.cpu_count() or 0, "mem_free": mem["free"], "mem_total": mem["total"],
            "waiting_for_memory": _mem_short() and MAX_WORKERS[0] > 1, "encoder": (enc[0] if enc else "libx264 (CPU)") if "hw" in engine._encoder_cache else "not checked yet",
            "speeds": {k: v for k, v in sp.items() if "|" not in k}, "workers": MAX_WORKERS[0],
            "priority": exports.load()["priority"]}


def _check_sound(it: dict, pr: dict, path: str):
    """A clip from a video with sound must come out with sound; one that's nearly silent gets a warning."""
    if not pr["info"].get("has_audio"):
        return
    peak = engine.audio_peak(path)
    if peak is None:
        try:
            os.remove(path)
        except OSError:
            pass
        raise BrokenOutput("The rendered file came out without sound.")
    if peak < -60:
        it["warning"] = ("This clip came out almost silent. If it shouldn't be, check the stream at this point "
                         "(Twitch mutes some music).")


def _compare_settings(pr: dict, det: dict, cw: float, count: int, length: float) -> dict:
    """Run the clip finder with other settings without changing anything, and compare."""
    an = pr["analysis"]
    old_cw, old_w = an.chat_weight_pct, an.weights
    try:
        an.combine(cw, detect.preset(det["preset"]))
        clips = _pick(dict(pr), count, length, det)
    finally:
        an.combine(old_cw, old_w)
    cur = [c for c in pr["clips"] if not c.get("manual")]
    same = 0
    matched = set()
    for c in clips:
        hit = next((d for d in cur if d["id"] not in matched and persist.same_moment(d, c.start, c.end, c.peak) is not None), None)
        if hit:
            matched.add(hit["id"])
            same += 1
    return {"count": len(clips), "same": same, "added": len(clips) - same, "dropped": len(cur) - same,
            "text": detect.describe(det, an.chat_delay if getattr(an, "delay_sure", False) else None, an.has_chat),
            "top": [{"start": c.start, "end": c.end, "quality": c.quality, "reason": c.reason} for c in clips[:5]]}


def strip_data(pr: dict, a: float, b: float) -> dict:
    an = pr["analysis"]
    dur = an.duration
    a, b = max(0.0, a), min(dur, b)
    if b - a > 3600:                       # an hour at most per request: the timeline asks for what it shows
        b = a + 3600
    i0, i1 = int(a), int(math.ceil(b))
    hz = engine.FINE_HZ
    out = {"a": a, "b": b, "hz": hz, "wave": [], "events": {}, "chat": []}
    if an.fine is not None:
        out["wave"] = [round(float(x), 1) for x in an.fine[int(a * hz):int(b * hz)]]
    elif an.loud is not None:
        out["hz"] = 1
        out["wave"] = [round(float(x), 1) for x in an.loud[i0:i1]]
    out["start"] = i0
    if an.has_events:
        for k in ("laugh", "scream", "cheer"):
            out["events"][k] = [round(float(x), 2) for x in an.event_level(k)[i0:i1]]
    if an.has_chat:
        out["chat"] = [round(float(x), 2) for x in an.chat_z[i0:i1]]
    if getattr(an, "emote_z", None) is not None and float(an.emote_z.max() if len(an.emote_z) else 0) > 0:
        out["emotes"] = [round(float(x), 2) for x in an.emote_z[i0:i1]]
    return out


_previews: dict = {}


def start_preview_render(pid: str, c: dict, opts: dict, at: float) -> dict:
    pr = projects[pid]
    start_, end_ = float(c["start"]), float(c["end"])
    if end_ - start_ < 1:
        raise RuntimeError("That clip is too short.")
    seg_a = min(max(start_, at - 1.0), max(start_, end_ - 6.0))
    seg_b = min(end_, seg_a + 6.0)
    d = _find_clip(pr, c.get("id")) or {}
    it = {"start": seg_a, "end": seg_b, "title": str(c.get("title") or "")[:120], "post": "",
          "words": _clean_words(c.get("words")), "fix_spans": checkflow.render_spans(d) if d else [],
          "keep_fps": [f["fp"] for f in (d.get("safety") or {}).get("flags", []) if f.get("status") in ("accepted", "dismissed")],
          "cancel": False}
    job = new_job("preview")
    old = _previews.get(pid)
    if old and old["status"] == "running":
        old["cancel"] = True
    _previews[pid] = job

    def go():
        check = job_check(job)
        ro = _ropts(dict(opts, hw_encode=opts.get("hw_encode", True)))
        ro.crf = 28
        outdir = locations.temp_dir(os.path.basename(pr["workdir"]), "preview")
        os.makedirs(outdir, exist_ok=True)
        for f in sorted(glob.glob(os.path.join(outdir, "preview_*.mp4")))[:-2]:   # keep the last few
            try:
                os.remove(f)
            except OSError:
                pass
        job["step"] = "Getting captions ready"
        words, title, _post, spans, mode = _render_inputs(it, pr, ro, check)
        if not ro.captions:
            words = []
        fr = None
        if engine.effective_layout(ro) == "full" and ro.track == "auto":
            job["step"] = "Finding the face"
            # the whole clip's path (shared with the export, cached), shifted to this stretch
            fr = _framing_for(pr, float(c["start"]), float(c["end"]), ro)
            if fr and fr.get("path"):
                fr = dict(fr, path=framing.shift(fr["path"], seg_a - float(c["start"]), seg_b - seg_a))
        name = f"preview_{int(time.time() * 1000)}.mp4"
        job["step"] = "Rendering"
        engine.render_clip(pr["video"], seg_a, seg_b, os.path.join(outdir, name), ro, title=title,
                           progress=job_progress(job, 0.1, 1.0), check=check, words=words, censor=spans, censor_mode=mode,
                           framing=fr)
        return {"url": f"/files/{pid}/preview/{name}", "start": seg_a, "end": seg_b}
    run_job(job, go)
    return job


def friendly_render_error(e: Exception) -> tuple:
    """(what went wrong and what to do, in plain words; technical details)."""
    raw = str(e) or type(e).__name__
    low = raw.lower()
    errno_ = getattr(e, "errno", None) or getattr(e, "winerror", None)
    rules = [
        (errno_ in (28, 112) or "no space left" in low or "not enough space" in low,
         "Your drive is full. Free up space (Settings › Storage: Clear temporary files, Delete VODs) or choose another "
         "folder for rendered clips, then retry."),
        (errno_ in (13, 32) or "permission denied" in low or "being used by another process" in low,
         "The clip's file is open in another program, like a video player. Close it, then retry."),
        ("vod for this clip was deleted" in low or "no such file" in low and "input" not in low,
         "The stream recording for this clip was deleted, so it can't be rendered any more."),
        ("cannot allocate memory" in low or isinstance(e, MemoryError) or "out of memory" in low,
         "Your PC ran out of memory. Close some programs, then retry."),
        ("error initializing filter 'subtitles'" in low or "libass" in low or "fontconfig" in low,
         "The captions couldn't be drawn. Try the Classic font in the Text tab, then retry."),
        ("error while opening encoder" in low or "nvenc" in low or "qsv" in low or "amf" in low,
         "The video encoder failed. Turn off \"Use the GPU to encode\" in the Layout tab, then retry."),
        ("failed to configure input pad" in low or "error reinitializing filters" in low
         or "error initializing complex filter" in low or "invalid too big or non positive size" in low,
         "The clip's layout couldn't be built from this video (a crop or the framing lands outside the picture). "
         "Press Reset in the Layout tab or choose another layout, then retry."),
        ("came out broken" in low,
         "The finished file came out broken twice. Retry; if it keeps happening, turn off "
         "\"Use the GPU to encode\" in the Layout tab."),
        ("invalid data found" in low or "moov atom" in low or "end of file" in low,
         "The source video is damaged or incomplete, so this part couldn't be read."),
        ("timed out" in low or "timeout" in low,
         "Rendering took too long and was stopped. Retry; if it keeps happening, try a shorter clip."),
    ]
    for hit, msg in rules:
        if hit:
            return msg, raw[-1500:]
    first = raw.strip().splitlines()[-1] if raw.strip() else type(e).__name__
    return f"Rendering stopped with an error: {first[:200]}", raw[-1500:]


def _after_render(it: dict):
    """Once a clip is ready: a finished VOD may now be cleaned up (Settings › Storage, off by default)."""
    if cleanup.load()["vods"]:
        run_cleanup()


def run_cleanup(wait: bool = False):
    """Settings › Storage › Clean up automatically: the rules that are on (cleanup.py has them)."""
    def go():
        with queue_lock:
            items = [dict(x) for x in queue]
        open_dirs = {pr["workdir"] for pr in list(projects.values()) if pr.get("workdir")}
        live_dirs = tuple(os.path.dirname(sess.seg_dir) for sess in live_list())
        cleanup.sweep(locations.project_roots() or [WORKROOT], items, open_dirs, live_dirs, log=_say)
    if wait:
        go()
    else:
        threading.Thread(target=go, daemon=True).start()


MAX_WORKERS = [1]


def apply_perf(prefs: Optional[dict] = None):
    """Settings › Performance: renders at once, and keeping the PC responsive versus finishing faster."""
    p = prefs or exports.load()
    MAX_WORKERS[0] = p["renders"]
    if p["priority"] == "responsive":
        engine.RENDER_PRIORITY[0] = 0x00004000 if sys.platform == "win32" else 0     # BELOW_NORMAL_PRIORITY_CLASS
        engine.RENDER_THREADS[:] = ["-threads", str(max(2, (os.cpu_count() or 4) // 2))]
    else:
        engine.RENDER_PRIORITY[0] = 0
        engine.RENDER_THREADS[:] = []
    while len(_workers) < MAX_WORKERS[0]:
        i = len(_workers)
        _workers.append(threading.Thread(target=queue_worker, args=(i,), daemon=True))
        _workers[-1].start()
    queue_wake.set()


_workers: list = []


def queue_eta() -> Optional[float]:
    """Time until everything in the queue is rendered (None when there's nothing to go on yet)."""
    total, unknown = 0.0, False
    with queue_lock:
        items = [x for x in queue if x["status"] in ("queued", "rendering")]
    for x in items:
        if x["status"] == "rendering":
            if x.get("eta") is None:
                unknown = True
            else:
                total += x["eta"]
        else:
            ex = x.get("export") or {}
            sp = exports.speed(exports.speed_key(engine.video_encoder(_ropts(x.get("opts") or {}, ex))[0], ex))
            if not sp:
                unknown = True
            else:
                total += (x["end"] - x["start"]) / sp + 3
    if not items:
        return 0.0
    return None if unknown and total == 0 else round(total / max(1, min(MAX_WORKERS[0], len(items))), 1)


MEM_RESERVE = 1.0 * 1024 ** 3      # leave this much memory free before starting another render at once


def _mem_short() -> bool:
    m = locations.memory()
    return m["free"] is not None and m["free"] < MEM_RESERVE


def queue_worker(idx: int = 0):
    while True:
        queue_wake.wait(2.0)
        queue_wake.clear()
        if idx >= MAX_WORKERS[0]:
            continue                                  # fewer renders at once now: this worker rests
        while True:
            with queue_lock:
                has_queued = any(x["status"] == "queued" for x in queue)
                has_rendering = any(x["status"] == "rendering" for x in queue)
            if not has_queued:
                break
            if idx > 0 and has_rendering and _mem_short():
                # Wait only when there is actual competing work; don't pin an idle worker
                # in a memory loop when the queue is empty.
                time.sleep(3)
                continue
            with queue_lock:
                it = next((x for x in queue if x["status"] == "queued"), None)
                if it and it.get("live"):
                    # live moments waiting together: the strongest renders first
                    it = max((x for x in queue if x["status"] == "queued" and x.get("live")),
                             key=lambda x: x.get("prio", 0), default=it)
                if it:
                    it["status"] = "rendering"
                    it["progress"] = 0.0
            if not it:
                break
            if SHUTTING_DOWN[0]:
                it["status"] = "queued"
                return
            save_queue_soon()
            t0 = time.time()
            try:
                _render_item(it)
                it["status"] = "done"
                it["progress"] = 1.0
                it["step"] = f"Done in {time.time() - t0:.0f}s"
                save_queue_soon(0)
            except engine.Cancelled:
                with queue_lock:
                    if SHUTTING_DOWN[0]:
                        it.update(status="queued", progress=0.0, step="Waiting", cancel=False)
                    elif it in queue:
                        queue.remove(it)
                save_queue_soon(0)
                continue
            except Exception as e:
                it["status"] = "error"
                it["error"], it["detail"] = friendly_render_error(e)
                it["attempts"] = it.get("attempts", 0) + 1
                print(f"  Render failed: {it['error']} ({it['detail'][:300]})", flush=True)
                if not isinstance(e, (RuntimeError, OSError)):
                    traceback.print_exc()
                save_queue_soon(0)
                continue
            try:
                _after_render(it)
            except Exception:
                traceback.print_exc()
            _live_rendered(it)


# --------------------------------------------------------------------------
# live mode
# --------------------------------------------------------------------------

live_sessions: dict = {}
SERVER_PORT = [PORT]


LIVE_ON = ("starting", "waiting", "recording", "reconnecting")


def live_active():
    return next((s for s in live_sessions.values() if s.status in LIVE_ON), None)


def live_list() -> list:
    return [s for s in live_sessions.values() if s.status in LIVE_ON]


def _same_source(a: str, b: str) -> bool:
    ca, cb = sources.classify(a), sources.classify(b)
    return (ca["platform"], ca["id"] or ca["url"].lower()) == (cb["platform"], cb["id"] or cb["url"].lower())


def sources_view() -> dict:
    """Everything going on, per source (Home › Sources): VODs being opened and streams being clipped."""
    out = []
    for j in sorted(jobs.values(), key=lambda j: j["started"]):
        if j["kind"] != "analyze" or (j["status"] in ("done", "cancelled") and time.time() - j["started"] > 1800):
            continue
        body = j.get("body") or {}
        c = sources.classify(body.get("source", ""))
        res = j.get("result") or {}
        out.append({"type": "vod", "id": j["id"], "status": j["status"], "step": j["step"], "progress": round(j["progress"], 3),
                    "eta": j.get("eta"), "source": body.get("source", ""), "label": c["label"],
                    "title": res.get("title") or (os.path.basename(body.get("source", "")) if c["platform"] == "local" else c["url"]),
                    "pid": res.get("pid", ""), "error": (j.get("error") or "").splitlines()[0] if j.get("error") else "",
                    "started": j["started"]})
    for sess in live_sessions.values():
        if sess.status not in LIVE_ON and time.time() - sess.started > 6 * 3600:
            continue
        out.append({"type": "live", "id": sess.id, "status": sess.status, "title": sess.title, "source": sess.source,
                    "label": sources.classify(sess.source)["label"] if not sess.channel else "Twitch livestream",
                    "recorded": sess.recorded, "moments": len(sess.moments), "chat_state": sess.chat_state(),
                    "error": sess.error, "started": sess.started})
    p = exports.load()
    return {"items": out, "limits": {"downloads": p["downloads"], "lives": p["lives"]}}


def on_hotkey():
    """Ctrl+Alt+C anywhere in Windows: clip the last 30 seconds of the live stream."""
    sess = live_active()
    if sess and sess.status in ("recording", "reconnecting"):
        sess.clip_now(30)
        sess.hotkey_clips = getattr(sess, "hotkey_clips", 0) + 1
        hotkey.beep(True)
    else:
        hotkey.beep(False)


class PriorityGate:
    """Lets one job at a time through, the highest priority first (live moments waiting for Whisper:
    the moment you clipped yourself, then the strongest)."""

    def __init__(self, slots: int = 1):
        self.cv = threading.Condition()
        self.slots, self.busy, self.waiting, self.n = slots, 0, [], 0

    def acquire(self, prio: float, check=None):
        with self.cv:
            self.n += 1
            key = (-float(prio), self.n)
            self.waiting.append(key)
            try:
                while self.busy >= self.slots or min(self.waiting) != key:
                    self.cv.wait(0.5)
                    if check:
                        check()
            except BaseException:
                self.waiting.remove(key)
                self.cv.notify_all()
                raise
            self.waiting.remove(key)
            self.busy += 1

    def release(self):
        with self.cv:
            self.busy -= 1
            self.cv.notify_all()

    def queued(self) -> int:
        with self.cv:
            return len(self.waiting)


live_gate = PriorityGate(1)


def _live_prio(d: dict) -> float:
    return 1000.0 if d.get("manual") else float(d.get("quality") or 0)


def _live_rendered(it: dict):
    """Hands-free: how long rendering took, and the whole way from the moment on stream to the file."""
    sess = live_sessions.get(it.get("live") or "")
    if not sess or it.get("status") != "done":
        return
    now = time.time()
    sess.note_latency("render", now - it.get("created", now))
    lat = it.get("lat") or {}
    if it.get("aired"):
        lat["render"] = round(now - it.get("created", now), 2)
        lat["total"] = round(now - it["aired"], 2)
        sess.note_latency("total", lat["total"])
        sess.last_lat = lat


def live_on_moment(sess, m: dict):
    """A live session found a moment: register it as a small project, title it, maybe render it."""
    mid = uuid.uuid4().hex[:8]
    pr = {"video": m["video"], "chat": m["chat"], "title": f"{sess.title}, moment {m['number']}",
          "workdir": sess.dir, "analysis": m["analysis"], "info": engine.probe(m["video"]), "clips": [],
          "chat_delay": sess.chat_delay, "source": sess.source, "clips_ver": 0, "live": sess.id,
          "thumb_prefix": f"m{m['number']}_", "streamer": sess.channel or "", "length": sess.length,
          "tx_lock": threading.Lock()}
    projects[mid] = pr
    clip = m["clip"]
    clip.id = m["number"]
    pr["clips"] = clip_dicts(mid, [clip])
    d = pr["clips"][0]
    d.update(moment=m["number"], manual=m["manual"], live=sess.id, created=time.time(),
             at=round(m["offset"] + clip.peak, 1), abs_start=round(m["offset"] + clip.start, 1),
             abs_end=round(m["offset"] + clip.end, 1), len_mode=sess.length, lat=dict(m.get("lat") or {}))
    sess.moments.append(mid)
    extra = sess.extra
    aired = m.get("aired")

    def done(_pid):
        now = time.time()
        d["lat"]["title"] = round(now - d["created"], 2)
        sess.note_latency("title", d["lat"]["title"])
        if aired and not extra.get("auto_render"):
            d["lat"]["total"] = round(now - aired, 2)       # ready to look at: cut, captioned and titled
            sess.note_latency("total", d["lat"]["total"])
            sess.last_lat = dict(d["lat"])
        if not extra.get("auto_render"):
            return
        cfg = safety.load_config()
        sf = d.get("safety") or {}
        if cfg.get("enabled", True) and cfg.get("hold", True) and sf.get("level") == "high":
            # hands-free never renders a clip that could get the account in trouble: it waits for you
            worst = next((f for f in sf.get("flags", []) if f["severity"] == 3), {})
            d["held"] = f"Held for review: {worst.get('label', 'flagged')}" + (
                f" ({worst.get('text')})" if worst.get("text") else "")
            sess.log(f"Moment {m['number']} held back by the content check")
            return
        why = checkflow.blocks_render(d, cfg)
        if why:
            d["held"] = "Held for review: flags not reviewed yet"
            sess.log(f"Moment {m['number']} held back: {why}")
            return
        c = dict(d, rank=m["number"])
        c["post"] = d.get("post", "")
        it = queue_add(mid, c, extra.get("opts") or {})
        it["prio"] = _live_prio(d)
        it["auto"] = True                      # rendered on its own (hands-free), not by you
        it["lat"], it["aired"] = dict(d["lat"]), aired
        d["auto_queued"] = True
    start_listening(mid, [d["id"]], extra.get("model") or "small", extra.get("language") or "", on_done=done)


def _live_delay(v):
    """Chat delay for live: a number of seconds, or "auto" (empty) to work it out from the stream."""
    if v in (None, "", "auto"):
        return "auto"
    try:
        return min(max(float(v), 0.0), 30.0)
    except (TypeError, ValueError):
        return "auto"


def start_live_session(src: str, body: dict):
    """Create and start a live session. body holds every setting so 'keep watching' can start the next one."""
    sid = uuid.uuid4().hex[:8]
    sess = live.LiveSession(
        sid, src, WORKROOT, temp_root=locations.path("temp"), length=float(body.get("length", 0) or 0), events=bool(body.get("events", True)),
        sensitivity=body.get("frequency") or body.get("sensitivity", "normal"),
        chat_delay=_live_delay(body.get("chat_delay", "auto")), min_quality=int(body.get("min_quality") or 0),
        chat_weight=float(body.get("chat_weight", 60)), max_height=int(body.get("max_height", 1080)),
        cookies=body.get("cookies") or "", warmup=float(body.get("warmup", 90)),
        buffer_minutes=float(body.get("buffer_minutes", 15)),
        wait_for_live=bool(body.get("wait_for_live", False)),
        on_moment=live_on_moment, log=lambda m: print("  [live] " + m, flush=True))
    if body.get("chat_channel"):
        sess.channel = live.parse_channel(body["chat_channel"]) or sess.channel
        if sess.channel and sess.title == "stream (live)":
            sess.title = f"{sess.channel} (live)"
            engine._save_meta(sess.dir, title=sess.title, source=sess.source, live=True)
    sess.adjust = learning.adjuster(sess.channel or "")
    sess.weights = detect.preset(body.get("preset"))
    if sess.channel and body.get("emotes", True):
        def load_emotes(se=sess):
            # 7TV/BTTV/FFZ emotes make chat reactions count the same way they do for VODs
            es = emotes.for_channel(login=se.channel, log=se.log)
            if es:
                se.scorer.hype = detect.weigher(es)
                se.emote_set = es
        threading.Thread(target=load_emotes, daemon=True).start()
    hands_free = bool(body.get("hands_free", body.get("auto_render")))
    sess.extra = {"auto_render": hands_free, "opts": dict(body.get("opts") or {}),
                  "model": body.get("model") or "small", "language": body.get("language") or "",
                  "keep_watching": bool(body.get("keep_watching")),
                  "params": dict(body, source=src), "user_stopped": False}
    live_sessions[sid] = sess
    sess.start()
    if sess.extra["keep_watching"]:
        threading.Thread(target=_watch_for_next, args=(sess,), daemon=True).start()
    return sess


def _watch_for_next(sess):
    """'Keep watching': when a stream ends on its own, wait for the channel's next stream."""
    while sess.status not in ("ended", "stopped", "error"):
        time.sleep(1)
    if sess.status != "ended" or sess.extra.get("user_stopped") or not sess.extra.get("keep_watching"):
        return
    params = dict(sess.extra["params"])
    params.update(wait_for_live=True, opts=sess.extra.get("opts"),
                  hands_free=sess.extra.get("auto_render"), frequency=sess.frequency, min_quality=sess.min_quality,
                  model=sess.extra.get("model"), language=sess.extra.get("language"))
    try:
        nxt = start_live_session(params["source"], params)
        sess.extra["next"] = nxt.id
        sess.log("Waiting for the next stream.")
    except Exception as e:
        sess.log(f"Couldn't keep watching: {e}")


def live_view(sess) -> dict:
    v = sess.public()
    v["clips"] = [projects[mid]["clips"][0] for mid in sess.moments if mid in projects and projects[mid]["clips"]]
    v["auto_render"] = bool(sess.extra.get("auto_render"))
    v["hands_free"] = v["auto_render"]
    v["keep_watching"] = bool(sess.extra.get("keep_watching"))
    v["held"] = sum(1 for c in v["clips"] if c.get("held"))
    v["hotkey_clips"] = getattr(sess, "hotkey_clips", 0)
    v["next"] = sess.extra.get("next", "")
    v["listening"] = any(j["status"] == "running" and j["kind"] == "listen" for j in
                         (jobs.get(projects[mid].get("listen_job", "")) or {"status": "", "kind": ""}
                          for mid in sess.moments if mid in projects))
    return v


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Clip Radar"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _local_only(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._err("forbidden", 403)
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", origin):
                self._err("forbidden", 403)
                return False
        return True

    def _send_file(self, path: str, ctype=None, cache=False):
        if not os.path.isfile(path):
            return self._err("not found", 404)
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.lower().endswith(".mkv"):
            ctype = "video/webm"  # lets Chrome/Edge try to play Matroska
        rng = self.headers.get("Range")
        start, end, code = 0, size - 1, 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._local_only():
            return
        u = urlparse(self.path)
        p = unquote(u.path)
        q = parse_qs(u.query)

        if p in ("/", "/index.html"):
            return self._send_file(str(HERE / "ui" / "index.html"), "text/html; charset=utf-8")
        if p == "/favicon.ico":
            return self._send_file(str(HERE / "ui" / "icon.svg"), "image/svg+xml", cache=True)
        if p.startswith("/ui/"):
            base = os.path.realpath(HERE / "ui")
            path = os.path.realpath(os.path.join(base, p[4:]))
            if path.startswith(base + os.sep):
                # no long caching: after an update the window must pick up the new app.js/app.css
                ctype = "font/woff" if path.endswith(".woff") else None
                return self._send_file(path, ctype, cache=path.endswith((".svg", ".ico", ".png", ".woff")))
            return self._err("forbidden", 403)
        if p == "/api/status":
            ts = tools_setup.status()
            return self._json({
                "ffmpeg": ts["ffmpeg"],
                "ytdlp": ts["ytdlp_exe"] or _has("yt_dlp", load=False),
                "whisper": _has("faster_whisper", load=False),
                "laughter": _has("onnxruntime", load=False) and audio_events.model_path() is not None,
                "twitchdownloader": ts["twitchdownloader"],
                "can_download_tools": ts["can_download"],
                "workroot": WORKROOT,
                "locations_warn": [x["problem"] for x in locations.problems()],
                "window": MODE["window"],
                "frozen": paths.FROZEN,
                "version": VERSION,
                "running": busy(),
                "live": (live_active().id if live_active() else ""),
                "lives": [{"id": x.id, "title": x.title, "status": x.status} for x in live_list()],
                "hotkey": hotkey.status(),
            })
        if p == "/api/update":
            return self._json(updater.view())
        if p == "/api/update/versions":
            return self._json(updater.versions(refresh="refresh" in (urlparse(self.path).query or ""), log=_say))
        if p == "/api/safety":
            return self._json(safety.public_config())
        if p == "/api/hotkey":
            return self._json(hotkey.status())
        if p == "/api/ping":
            last_ping[0] = time.time()
            return self._json({"ok": True})
        if p == "/api/recent":
            rec_ = [r_ for root in (locations.project_roots() or [WORKROOT]) for r_ in engine.recent_projects(root)]
            return self._json(sorted(rec_, key=lambda x: -x["updated"])[:8])
        m = re.fullmatch(r"/api/live/(\w+)", p)
        if m:
            sess = live_sessions.get(m.group(1))
            return self._json(live_view(sess)) if sess else self._err("That live session is gone.", 404)
        if p == "/api/twitch":
            return self._json(twitchauth.status(refresh="refresh" in q))
        if p == "/api/learning":
            return self._json(learning.summary())
        if p == "/api/titles":
            return self._json(title_prefs())
        if p == "/api/hashtags":
            return self._json(hashtags_view((q.get("streamer") or [""])[0]))
        if p == "/api/cleanup":
            return self._json({"config": cleanup.load(), "last": cleanup.last})
        if p == "/api/learning/explain":
            return self._json(learning.explain((q.get("streamer") or [""])[0]))
        if p == "/api/queue":
            with queue_lock:
                items = [queue_public(it) for it in queue]
            return self._json({"items": items, "eta": queue_eta(), "workers": MAX_WORKERS[0]})
        if p == "/api/export":
            return self._json(export_view())
        if p == "/api/sources":
            return self._json(sources_view())
        if p == "/api/locations":
            return self._json(locations.view())
        m = re.fullmatch(r"/api/framing/(\w+)", p)
        if m:
            # Layout › Auto-frame: what fits this video (a facecam, a person filling the frame, or neither)
            pr = projects.get(m.group(1))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            if not framing.available():
                return self._json({"layout": "", "reason": "Face finding isn't available in this copy.", "available": False})
            try:
                res = framing.suggest(pr["video"], float(pr["info"].get("duration") or 0) or 60.0,
                                      cache_dir=locations.temp_dir(os.path.basename(pr["workdir"]), "framing"))
            except Exception as e:
                return self._err(f"Couldn't look for faces: {str(e)[:160]}")
            return self._json(dict(res, available=True))
        if p == "/api/perf":
            return self._json(perf_view())
        m = re.fullmatch(r"/api/strip/(\w+)", p)
        if m:
            # what the editing timeline draws for a stretch of the video: loudness, laughter, chat
            pr = projects.get(m.group(1))
            if not pr or "analysis" not in pr:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                a, b = float(q.get("a", ["0"])[0]), float(q.get("b", ["0"])[0])
            except ValueError:
                return self._err("bad range")
            return self._json(strip_data(pr, a, b))
        m = re.fullmatch(r"/api/project/(\w+)", p)
        if m:
            if m.group(1) not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            return self._json(project_view(m.group(1)))
        m = re.fullmatch(r"/api/clips/(\w+)", p)
        if m:
            pr = projects.get(m.group(1))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            lj = jobs.get(pr.get("listen_job", ""))
            return self._json({"clips": pr["clips"], "gems": pr.get("gems", []), "deep": deep_status(pr),
                               "listening": bool(lj and lj["status"] == "running"),
                               "step": lj["step"] if lj else ""})
        if p == "/api/storage":
            st = _storage_all()
            st["uninstaller"] = bool(uninstaller_path())
            return self._json(st)
        m = re.fullmatch(r"/api/job/(\w+)", p)
        if m:
            job = jobs.get(m.group(1))
            return self._json(job) if job else self._err("That job is gone. Start it again.", 404)
        m = re.fullmatch(r"/export/(\w+)", p)
        if m:
            with queue_lock:
                it = next((x for x in queue if x["id"] == m.group(1)), None)
            if not it or it["status"] != "done":
                return self._err("not found", 404)
            return self._send_file(it.get("path", ""))
        m = re.fullmatch(r"/vod/(\w+)", p)
        if m and m.group(1) in projects:
            return self._send_file(projects[m.group(1)]["video"])
        m = re.fullmatch(r"/api/frame/(\w+)", p)
        if m and m.group(1) in projects:
            pr = projects[m.group(1)]
            try:
                t = float(q.get("t", ["0"])[0])
            except ValueError:
                t = 0.0
            out = os.path.join(pr["workdir"], "thumbs", f"frame_{pr.get('thumb_prefix', '')}{int(t)}.jpg")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if not os.path.exists(out):
                try:
                    engine.make_thumb(pr["video"], t, out, width=1280)
                except Exception as e:
                    return self._err(str(e), 500)
            return self._send_file(out, "image/jpeg", cache=True)
        m = re.fullmatch(r"/files/(\w+)/(.+)", p)
        workdir = ""
        if m:
            if m.group(1) in projects:
                workdir = projects[m.group(1)]["workdir"]
            else:   # Exports after a restart: the clip's VOD isn't open, but its folder is known
                with queue_lock:
                    workdir = next((x.get("workdir") or "" for x in queue if x.get("pid") == m.group(1)), "")
        if m and workdir:
            base = os.path.realpath(workdir)
            if m.group(2).startswith("preview/"):          # exact previews live with the temporary files
                base = os.path.realpath(locations.temp_dir(os.path.basename(workdir)))
            path = os.path.realpath(os.path.join(base, m.group(2)))
            if not path.startswith(base + os.sep):
                return self._err("forbidden", 403)
            return self._send_file(path, cache="/thumbs/" in path.replace("\\", "/"))
        return self._err("not found", 404)

    def do_POST(self):
        if not self._local_only():
            return
        p = urlparse(self.path).path
        try:
            body = self._body()
        except Exception:
            return self._err("bad request")
        if not isinstance(body, dict):
            return self._err("bad request")
        try:
            return self._post(p, body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            raise
        except (KeyError, TypeError, ValueError) as e:
            traceback.print_exc()
            return self._err(f"Bad request ({type(e).__name__}: {str(e)[:120]})")
        except Exception as e:
            traceback.print_exc()
            return self._err(f"Something went wrong: {str(e)[:200]}", 500)

    def _post(self, p: str, body: dict):

        if p == "/api/analyze":
            src = (body.get("source") or "").strip()
            dup = next((j for j in jobs.values() if j["kind"] == "analyze" and j["status"] == "running"
                        and _same_source((j.get("body") or {}).get("source", ""), src)), None)
            if dup:
                return self._json({"job": dup["id"], "already": True})     # opening it already
            job = new_job("analyze")
            job["body"] = dict(body)
            run_job(job, lambda: do_analyze(job, body))
            return self._json({"job": job["id"]})

        if p == "/api/locations":
            if live_list() or busy():
                return self._err("Wait for downloads, renders and live clipping to finish, then change folders.", 409)
            res = locations.save(body or {})
            _set_workroot()
            return self._json(dict(locations.view(), errors=res["errors"]))

        if p == "/api/storage/clear_cache":
            keep = [os.path.dirname(sess.seg_dir) for sess in live_list()]
            res = locations.clear_temp(keep=tuple(keep))
            part = locations.stale_partials([locations.path("downloads")] + locations.project_roots())
            freed2, _ = engine._remove(part)
            res["freed"] += freed2
            return self._json(dict(res, locations=locations.view()["locations"]))

        if p == "/api/sources/pause":
            job = jobs.get(body.get("job", ""))
            if job and job["kind"] == "analyze" and job["status"] == "running":
                job["pause"] = job["cancel"] = True
                job["step"] = "Pausing"
            return self._json(sources_view())

        if p == "/api/sources/resume":
            old = jobs.get(body.get("job", ""))
            if not old or old["kind"] != "analyze" or old["status"] not in ("paused", "error", "cancelled"):
                return self._err("Nothing to resume.")
            job = new_job("analyze")
            job["body"] = dict(old.get("body") or {})
            run_job(job, lambda: do_analyze(job, job["body"]))
            jobs.pop(old["id"], None)
            return self._json({"job": job["id"]})

        if p == "/api/sources/forget":
            job = jobs.get(body.get("job", ""))
            if job and job["status"] != "running":
                jobs.pop(job["id"], None)
            sess = live_sessions.get(body.get("live", ""))
            if sess and sess.status not in LIVE_ON:
                sess.started = 0                    # drops out of the list
            return self._json(sources_view())

        if p == "/api/setup_tools":
            want = [w for w in body.get("want", []) if w in ("ffmpeg", "twitchdownloader", "ytdlp", "laughter")]
            if not want:
                return self._err("Nothing to download.")
            job = new_job("setup")

            def go():
                log, step = job_logger(job), job_step(job)
                step("Downloading tools")
                tools = [w for w in want if w != "laughter"]
                res = tools_setup.setup(tools, log=log, progress=job_progress(job, 0, 0.8 if "laughter" in want else 1),
                                        check=job_check(job)) if tools else {}
                if "laughter" in want and not audio_events.available():
                    audio_events.download(log, progress=job_progress(job, 0.8 if tools else 0, 1))
                return res
            run_job(job, go)
            return self._json({"job": job["id"]})

        if p == "/api/pick_file":
            kind = body.get("kind", "video")
            return self._json({"path": pick_file(kind)})

        if p == "/api/update/check":
            return self._json(updater.check(log=_say))
        if p == "/api/update/token":
            try:
                updater.set_token(str(body.get("token") or ""))
            except ValueError as e:
                return self._err(str(e))
            return self._json(updater.check(log=_say))
        if p == "/api/update/settings":
            updater.set_prefs(bool(body.get("auto", True)))
            return self._json(updater.view())
        if p == "/api/update/install":
            if body.get("version") and not updater.newer(str(body["version"]), VERSION):
                return self._err("You can only install a version newer than the one you have.", 400)
            if not updater.installed():
                return self._err("This copy of Clip Radar wasn't installed with Setup, so it can't update itself. "
                                 "Download the new version from GitHub instead.", 400)
            if body.get("version") and updater.view()["state"] not in ("downloading", "installing"):
                try:
                    updater.choose(str(body["version"]))      # a particular newer version, from All versions
                except ValueError as e:
                    return self._err(str(e), 400)
            v = updater.view()
            if not v["available"]:
                return self._err("You already have the latest version.", 400)
            if v["state"] in ("downloading", "installing"):
                return self._json(v)
            if busy():
                return self._err("Finish or stop what's running (a download, render or live recording), then update.", 409)
            if not _update_running.acquire(blocking=False):
                return self._json(updater.view())
            with updater._lock:     # show progress right away
                updater.state.update(state="downloading", progress=0.0, error="")
            threading.Thread(target=_install_update, daemon=True).start()
            return self._json(updater.view())

        if p == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: shutdown_hook[0]()).start()
            return

        if p == "/api/delete":
            what = body.get("what")
            if what not in ("vods", "clips"):
                return self._err("Unknown delete request.")
            if live_active():
                return self._err("Stop live clipping first, then delete.", 409)
            if busy():
                return self._err("Wait for the current download or render to finish (or cancel it), then try again.", 409)
            # stop background listening so it isn't reading a VOD while it's deleted
            for j in jobs.values():
                if j["kind"] in ("listen", "deepscan") and j["status"] == "running":
                    j["cancel"] = True
            end_wait = time.time() + 8
            while time.time() < end_wait and any(j["kind"] in ("listen", "deepscan") and j["status"] == "running"
                                                 for j in jobs.values()):
                time.sleep(0.1)
            fn = engine.delete_vods if what == "vods" else engine.delete_clips
            res = {"deleted": 0, "freed": 0, "failed": []}
            for attempt in (1, 2):
                for root in locations.project_roots() or [WORKROOT]:
                    r_ = fn(root)
                    if attempt == 1:
                        res["deleted"] += r_["deleted"]
                    res["freed"] += r_["freed"]
                    if attempt == 2:
                        res["failed"] += r_["failed"]
                if attempt == 1:
                    left = engine.storage_summary(WORKROOT)["vods" if what == "vods" else "clips"]["bytes"]
                    if not left:
                        break
                    # Windows can keep a file open for a moment after it was read (a preview, a scan): try once more
                    print(f"  Some files were busy ({left // 1_000_000} MB); trying again", flush=True)
                    time.sleep(2.5)
            closed = []
            if what == "vods":
                # projects whose downloaded VOD is gone can't be previewed or rendered any more
                for pid, pr in list(projects.items()):
                    if not os.path.exists(pr["video"]):
                        projects.pop(pid, None)
                        closed.append(pid)
            with queue_lock:
                queue[:] = [it for it in queue if not (it["status"] == "done" and not os.path.exists(it.get("path", "")))
                            and not (it["status"] == "error" and it["pid"] in closed)]
            save_queue_soon()
            res["closed"] = closed
            res["storage"] = _storage_all()
            return self._json(res)

        if p == "/api/uninstall":
            exe = uninstaller_path()
            if not exe:
                return self._err("No uninstaller found. If you're using the portable version, just delete its folder.", 404)
            try:
                subprocess.Popen([exe], close_fds=True)
            except Exception as e:
                return self._err(f"Couldn't start the uninstaller: {e}", 500)
            self._json({"ok": True})
            threading.Timer(0.5, lambda: shutdown_hook[0]()).start()
            return

        if p == "/api/live/start":
            src = (body.get("source") or "").strip()
            on = live_list()
            if any(_same_source(sess.source, src) for sess in on):
                return self._err("That stream is already being clipped.", 409)
            limit = exports.load()["lives"]
            if len(on) >= limit:
                return self._err(f"Already clipping {len(on)} stream{'s' if len(on) != 1 else ''}. Stop one first, or allow more under "
                                 "Settings › Performance › Live streams at once.", 409)
            if not engine.which("ffmpeg"):
                return self._err("ffmpeg is missing. Download the tools first.")
            try:
                sess = start_live_session(src, dict(body))
            except ValueError as e:
                return self._err(str(e))
            return self._json(live_view(sess))

        m = re.fullmatch(r"/api/live/(\w+)/(stop|clip|settings)", p)
        if m:
            sess = live_sessions.get(m.group(1))
            if not sess:
                return self._err("That live session is gone.", 404)
            act = m.group(2)
            if act == "stop":
                sess.extra["user_stopped"] = True
                if sess.status == "waiting":
                    sess.status = "stopped"
                threading.Thread(target=sess.stop, daemon=True).start()
            elif act == "clip":
                if sess.status not in ("recording", "reconnecting"):
                    return self._err("Recording hasn't started yet." if sess.status == "starting" else "The stream isn't recording.")
                sess.clip_now(float(body.get("seconds", 30)))
            else:
                if "auto_render" in body:
                    sess.extra["auto_render"] = bool(body["auto_render"])
                if "opts" in body:
                    sess.extra["opts"] = dict(body["opts"] or {})
                level = body.get("frequency") or body.get("sensitivity")
                if level:
                    sess.set_frequency(level)
                if "min_quality" in body:
                    try:
                        sess.min_quality = int(min(max(int(body["min_quality"] or 0), 0), 95))
                    except (TypeError, ValueError):
                        pass
                for k in ("keep_watching",):
                    if k in body:
                        sess.extra[k] = bool(body[k])
                if "hands_free" in body:
                    sess.extra["auto_render"] = bool(body["hands_free"])
                for k in ("model", "language"):
                    if k in body:
                        sess.extra[k] = body[k]
            return self._json(live_view(sess))

        if p == "/api/handoff":
            # TikTok Studio: copy the caption (in the window), show the file, open the upload page
            with queue_lock:
                it = next((x for x in queue if x["id"] == body.get("item")), None)
            if not it or it["status"] != "done":
                return self._err("That clip isn't rendered yet.")
            try:
                r = studio.handoff(it.get("path", ""), it.get("title", ""), it.get("post", ""),
                                   reveal_file=body.get("reveal", True) is not False)
            except RuntimeError as e:
                return self._err(str(e))
            it["handoff"] = {"at": r["opened_at"], "url": r["url"]}
            save_queue_soon()
            try:
                record_feedback(it["pid"], it.get("clip_id"), "post")    # you chose to post it: a strong "keep"
            except Exception:
                pass
            return self._json(r)

        if p == "/api/open_url":
            url = str(body.get("url", ""))
            if re.match(r"^https://([a-z0-9-]+\.)*(tiktok\.com|github\.com)/", url):
                threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
                return self._json({"ok": True})
            return self._err("That link isn't allowed.", 400)

        if p == "/api/cancel":
            job = jobs.get(body.get("job", ""))
            if job and job["status"] == "running":
                job["cancel"] = True
                job["step"] = "Cancelling"
            return self._json({"ok": True})

        if p == "/api/repick":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            pr = projects[pid]
            an = pr["analysis"]
            det = pr.get("detect") or detect.settings({})
            if isinstance(body.get("detect"), dict):
                base = dict(det)
                if body["detect"].get("preset") and body["detect"]["preset"] != det.get("preset"):
                    base.pop("min_len", None)          # a new stream type brings its own clip lengths
                    base.pop("max_len", None)
                det = detect.settings(dict(base, **{k: v for k, v in body["detect"].items() if v is not None}))
            if body.get("dry_run"):
                # "Try": what these settings would suggest, compared with the list you have now
                return self._json(_compare_settings(pr, det, float(body.get("chat_weight", an.chat_weight_pct)),
                                                    int(body.get("count", 0) or 0), float(body.get("length", 0) or 0)))
            an.combine(float(body.get("chat_weight", an.chat_weight_pct)), detect.preset(det["preset"]))
            recut = det != pr.get("detect")          # new settings: clips you haven't touched get new cuts
            pr["detect"] = det
            length = float(body.get("length", 0) or 0)
            pr["length"] = length
            clips = _pick(pr, int(body.get("count", 0) or 0), length, det)
            # clips you kept, skipped, trimmed, retitled or added stay whatever the sliders say
            mine = [c for c in pr["clips"] if persist.touched(c)]
            cur = {c["id"]: c for c in pr["clips"]}
            taken = {c["id"] for c in mine}
            clips = [c for c in clips if not any(c.start < u["end"] - 2 and c.end > u["start"] + 2 for u in mine)]
            _number(pr, clips, "hype", taken)
            gone = _removed_nums(pr)                 # clips you removed don't come back
            clips = [c for c in clips if c.id not in gone]
            fresh, order = [], []
            for c in clips:
                o = cur.get(c.id)
                if o and not o.get("manual"):
                    # the same moment: it keeps its number, title and transcript
                    o.update(score=c.score, reason=c.reason, feat=c.feat, tuned=c.tuned)
                    if o.get("len_mode", 0) != length or recut:     # new length or settings: new cuts, then a fresh listen
                        o.update(start=c.start, end=c.end, peak=c.peak, len_mode=length, listened=False,
                                 orig=[c.start, c.end])
                    order.append(o)
                else:
                    fresh.append(c)
                    order.append(c)
            made = {d["id"]: d for d in clip_dicts(pid, fresh)}
            for d in made.values():
                d["len_mode"] = length
            new = [made[x.id] if isinstance(x, engine.Clip) else x for x in order]
            for u in mine:                     # yours slot in by score
                at = next((i for i, c in enumerate(new) if c.get("score", 0) < u.get("score", 0)), len(new))
                new.insert(at, u)
            pr["clips"] = new
            pr["clips_ver"] = pr.get("clips_ver", 0) + 1
            save_project_soon(pid)
            return self._json(project_view(pid))

        if p == "/api/add_clip":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            pr = projects[pid]
            an = pr["analysis"]
            t = float(body["t"])
            length = float(body.get("length", 0) or 0)
            if length > 0:
                length = min(length, an.duration)
                start = max(0.0, min(t - length * 0.5, an.duration - length))
                end = min(an.duration, start + length)
            else:
                start, end, _ = engine.smart_bounds(an, t)
            sc = an.score[int(min(max(t, 0), len(an.score) - 1))]
            clip = engine.Clip(0, round(start, 2), round(end, 2), t, round(float(sc), 2), "Added by you",
                               engine.clip_features(an, start, end, t, pr["chat"], engine.chat_times(pr["chat"]),
                                                    pr["chat_delay"]))
            _number(pr, [clip], "hype", {c["id"] for c in pr["clips"] + pr.get("gems", [])})
            d = clip_dicts(pid, [clip])[0]
            d["manual"] = True
            d["kind"] = "hype"
            d["quality"] = detect.quality(float(sc))
            d["parts"] = detect.breakdown(an, t)
            pr["clips"].append(d)
            learning.record_added(pr.get("streamer", ""))
            save_project_soon(pid)
            return self._json(d)

        if p == "/api/check":
            # Content Check review actions on one clip, or a few at once (leave, dismiss, exclude)
            pid = body.get("pid")
            pr = projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            cfg = safety.load_config()
            has_audio = pr["info"].get("has_audio", True)
            ids = body["ids"] if isinstance(body.get("ids"), list) else [body.get("id")]
            action = str(body.get("action") or "")
            if len(ids) > 1 and action not in ("leave", "dismiss", "exclude"):
                return self._err("That can't be done to several clips at once.")
            out, fix = [], None
            preview = action == "fix" and bool((body.get("args") or {}).get("preview"))
            for cid in ids:
                d = _find_clip(pr, cid)
                if not d:
                    continue
                if preview:          # what a fix would do, without doing it
                    d = copy.deepcopy(d)
                if len(ids) == 1 and any(k in body for k in ("start", "title", "state", "words_edit", "postText")):
                    _apply_clip_state(pr, d, body)       # the clip as it is in the app right now
                if d.get("state") == "kept" and action in ("fix", "undo"):
                    return self._err("This clip is already in the render queue. Remove it from the queue to change it.")
                if not d.get("safety") and action not in ("", "recheck", "exclude"):
                    checkflow.check(d, cfg, has_audio)
                try:
                    fix = checkflow.act(d, action, body.get("args") or {}, cfg, has_audio) if action else None
                except ValueError as e:
                    return self._err(str(e))
                if action in ("", "recheck") or (action in ("fix", "undo") and cfg.get("auto_recheck", True)):
                    checkflow.check(d, cfg, has_audio)
                else:
                    checkflow.refresh(d, cfg, has_audio)
                    if action in ("fix", "undo"):
                        checkflow.mark_changed(d)
                if action == "exclude" and (body.get("args") or {}).get("on", True):
                    if _drop_from_queue(pid, d["id"]) and d.get("state") == "kept":
                        d["state"] = "todo"
                out.append(_check_view(d))
            if not pr.get("live") and not preview:
                save_project_soon(pid, 0.2)
            return self._json({"clips": out, "fix": fix})

        if p in ("/api/clip_remove", "/api/clip_restore"):
            # F: take a clip out of the project (its source video and rendered files stay), or undo that
            pid = body.get("pid")
            pr = projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            cid = body.get("id")
            if p == "/api/clip_remove":
                d = _find_clip(pr, cid)
                if not d:
                    return self._err("That clip is gone.", 404)
                for key in ("clips", "gems"):
                    if d in pr.get(key, []):
                        pr[key].remove(d)
                pr.setdefault("removed", []).append(d)
                for m in (pr.get("book") or {}).get("moments", []):
                    if m["num"] == d["id"]:
                        m["removed"] = True
                _drop_from_queue(pid, d["id"])
                checkflow.log(d, "remove", "Removed from the project")
            else:
                d = next((x for x in pr.get("removed", []) if x.get("id") == cid), None)
                if not d:
                    return self._err("That clip can't be brought back.", 404)
                pr["removed"].remove(d)
                key = "gems" if d.get("kind") == "gem" else "clips"
                lst = pr.setdefault(key, [])
                at = next((i for i, c in enumerate(lst) if c.get("score", 0) < d.get("score", 0)), len(lst))
                lst.insert(at, d)
                for m in (pr.get("book") or {}).get("moments", []):
                    if m["num"] == d["id"]:
                        m.pop("removed", None)
                checkflow.log(d, "restore", "Put back in the project")
            pr["clips_ver"] = pr.get("clips_ver", 0) + 1
            if not pr.get("live"):
                save_project_soon(pid, 0.2)
            return self._json({"ok": True, "clip": d if p == "/api/clip_restore" else None})

        if p == "/api/preview_render":
            # a few seconds rendered exactly like the export, to check an edit without exporting
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                job = start_preview_render(pid, body.get("clip") or {}, body.get("opts") or {}, float(body.get("at", 0)))
            except RuntimeError as e:
                return self._err(str(e))
            return self._json({"job": job["id"]})

        if p == "/api/titles":
            return self._json(set_title_prefs(body))
        if p == "/api/cleanup":
            cfg = cleanup.save(body.get("config") or {})
            if body.get("run"):
                run_cleanup(wait=True)
            return self._json({"config": cfg, "last": cleanup.last})
        if p == "/api/hashtags":
            hashtags.save({k: v for k, v in body.items() if k in ("file", "post")})
            return self._json(hashtags_view(str(body.get("streamer") or "")))

        if p == "/api/framing/track":
            # where the crop will be over a clip (Layout › Follow face), for the preview
            pr = projects.get(body.get("pid"))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                a, b = float(body["start"]), float(body["end"])
            except (KeyError, TypeError, ValueError):
                return self._err("Bad clip.")
            ro = _ropts(dict(body.get("opts") or {}, track="auto"))
            fr = _framing_for(pr, a, b, ro) if b - a >= 1 else None
            if fr is None:
                return self._json({"path": [], "reason": "Follow face works with the Whole frame layout." if engine.effective_layout(ro) != "full"
                                   else "Face finding isn't available in this copy.", "confidence": 0})
            return self._json(fr)

        if p == "/api/export":
            prefs = exports.save(body)
            apply_perf(prefs)
            return self._json(export_view(body.get("sample")))

        if p == "/api/streamer":
            # the streamer's name as it should appear in titles (Title B) and descriptions
            pid = body.get("pid")
            pr = projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:40]
            if name and not re.fullmatch(r"[\w.\- ]{2,40}", name):
                return self._err("Use letters, numbers, spaces, dots, dashes or underscores.")
            pr["streamer_display"] = name
            if not pr.get("live"):
                engine._save_meta(pr["workdir"], streamer_display=name)
            used: set = set()
            for d in pr.get("clips", []) + pr.get("gems", []):
                _title_clip(pr, d, d.get("words") or [], used)
            if not pr.get("live"):
                save_project_soon(pid)
            return self._json({"name": name, "clips": [{k: d.get(k) for k in ("id", "title", "title_a", "title_b", "title_b_named",
                                                                                  "titles", "description", "hashtags", "post")}
                                                         for d in pr.get("clips", []) + pr.get("gems", [])]})

        if p == "/api/clip_state":
            # what you did to a clip in the app (keep, skip, trims, title, captions), saved with the VOD
            pr = projects.get(body.get("pid"))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            d = _find_clip(pr, body.get("id"))
            if not d:
                return self._err("That clip is gone.", 404)
            _apply_clip_state(pr, d, body)
            if not pr.get("live"):
                save_project_soon(body["pid"], float(body.get("delay", 0.5) or 0))
            return self._json({"ok": True})

        if p == "/api/listen":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            job = start_listening(pid, [int(i) for i in body.get("order", [])],
                                  body.get("model") or "small", body.get("language") or "")
            return self._json({"job": job["id"]})

        if p == "/api/queue/add":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                it = queue_add(pid, body.get("clip") or {}, body.get("opts") or {})
            except (KeyError, TypeError, ValueError):
                return self._err("Bad clip.")
            except RuntimeError as e:
                return self._err(str(e))
            return self._json(queue_public(it))

        if p == "/api/queue/remove":
            with queue_lock:
                it = next((x for x in queue if x["id"] == body.get("id")), None)
                if it and it["status"] == "rendering":
                    it["cancel"] = True          # the worker drops it once ffmpeg stops
                elif it:
                    queue.remove(it)
            save_queue_soon()
            return self._json({"ok": True})

        if p == "/api/queue/retry":
            n = 0
            with queue_lock:
                for it in queue:
                    if it["status"] == "error" and (body.get("id") in (it["id"], "all")):
                        # Drop stale render state so a retry starts a fresh attempt, not a
                        # half-finished UI lifecycle. Keep the chosen output name for replacement.
                        it.update(status="queued", progress=0.0, error="", detail="", step="Waiting",
                                  cancel=False, eta=None, attempts=it.get("attempts", 0))
                        n += 1
            queue_wake.set()
            save_queue_soon()
            if not n:
                return self._err("Nothing to retry.", 404)
            return self._json({"ok": True, "retried": n})

        if p.startswith("/api/test/") and os.environ.get("CLIPRADAR_TEST") == "1":
            act = p[len("/api/test/"):]
            if act in ("fail_next_render", "break_next_output"):
                TEST_HOOKS[act] = True
                return self._json({"ok": True})
            if act == "hotkey":
                return self._json({"ok": hotkey.simulate()})
            if act == "yt_hls":
                # stands in for YouTube: live links resolve to this playlist (a local test stream)
                os.environ["CLIPRADAR_TEST_YT_HLS"] = str(body.get("url") or "")
                return self._json({"ok": True})
            return self._err("not found", 404)

        if p == "/api/safety":
            safety.save_config(body or {})
            rescan_all()
            return self._json(safety.public_config())
        if p == "/api/safety/scan":
            ws = _clean_words(body.get("words")) or []
            return self._json(safety.scan(ws, str(body.get("title") or ""), str(body.get("post") or "")))
        if p == "/api/hotkey":
            hotkey.set_enabled(bool(body.get("enabled", True)))
            return self._json(hotkey.status())

        if p == "/api/deepscan":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            if body.get("cancel"):
                j = jobs.get(projects[pid].get("deep_job", ""))
                if j and j["status"] == "running":
                    j["cancel"] = True
                return self._json(deep_status(projects[pid]))
            try:
                start_deep_scan(pid, body.get("model") or "auto", body.get("language") or "")
            except RuntimeError as e:
                return self._err(str(e))
            return self._json(deep_status(projects[pid]))

        if p == "/api/feedback":
            action = str(body.get("action") or "")
            if action == "reason":
                pr = projects.get(body.get("pid")) or {}
                learning.record_reason(pr.get("streamer", ""), str(body.get("reason") or ""))
                return self._json({"ok": True})
            record_feedback(body.get("pid"), body.get("clip"), action)
            return self._json({"ok": True})
        if p == "/api/learning":
            if "enabled" in body:
                learning.set_enabled(bool(body["enabled"]))
            return self._json(learning.summary())
        if p == "/api/learning/forget":
            learning.forget(str(body.get("streamer") or ""))
            _refresh_live_learning()
            return self._json(learning.summary())

        if p == "/api/twitch/token":
            try:
                return self._json(twitchauth.set_token(str(body.get("token") or "")))
            except ValueError as e:
                return self._err(str(e))
            except RuntimeError as e:
                return self._err(str(e), 502)
        if p == "/api/twitch/logout":
            return self._json(twitchauth.set_token(""))
        if p == "/api/twitch/login":
            if not twitchauth.login_impl[0]:
                return self._err("Signing in through a window needs the Clip Radar app window. Paste your token instead.")
            job = new_job("twitchlogin")
            run_job(job, lambda: twitchauth.login_with_window())
            return self._json({"job": job["id"]})

        if p == "/api/open_folder":
            folder = body.get("folder", "")
            if folder == "root":
                folder = WORKROOT
            elif folder in locations.KEYS:
                folder = locations.path(folder) or WORKROOT
            if folder and os.path.isdir(folder) and any(os.path.realpath(folder) == r_ or os.path.realpath(folder).startswith(r_ + os.sep)
                                                        for r_ in _allowed_roots()):
                try:
                    if sys.platform == "win32":
                        os.startfile(folder)  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
                except Exception as e:
                    return self._err(str(e), 500)
                return self._json({"ok": True})
            return self._err("Folder not found.", 404)

        return self._err("not found", 404)


def _say(msg: str):
    print(msg, flush=True)


_update_running = threading.Lock()


def _install_update():
    """Download the new installer if needed, start it, then close so it can replace the app."""
    try:
        f = updater._file[0]
        path = f if f and os.path.isfile(f) and f.endswith(f"-{updater.state['latest']}.exe") \
            else updater.download(log=_say)
        if busy():
            raise RuntimeError("Something started while the update downloaded. Update again when it's finished.")
        updater.launch_installer(path, log=_say)
    except Exception as e:
        with updater._lock:
            updater.state.update(state="error", error=str(e))
        _update_running.release()
        return
    time.sleep(1.5)            # let the window show "Installing…"
    shutdown_hook[0]()


def uninstaller_path() -> str:
    """The Inno Setup uninstaller sits next to ClipRadar.exe when it was installed with Setup."""
    if not paths.FROZEN:
        return ""
    for name in ("unins000.exe", "unins001.exe"):
        p = paths.APP_DIR / name
        if p.is_file():
            return str(p)
    return ""


_pick_lock = threading.Lock()
picker_impl = [None]   # app.py swaps in the app window's native dialog when available


def pick_file(kind: str) -> str:
    """Native Windows file dialog. Returns '' if cancelled or unavailable."""
    if picker_impl[0]:
        try:
            return picker_impl[0](kind) or ""
        except Exception:
            pass
    with _pick_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return ""
        types = ([("Chat logs", "*.json *.json.gz"), ("All files", "*.*")] if kind == "chat" else
                 [("Videos", "*.mp4 *.mkv *.mov *.flv *.ts *.webm *.m4v *.avi"), ("All files", "*.*")])
        try:
            root = tk.Tk()
        except Exception:
            return ""
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            if kind == "folder":
                return filedialog.askdirectory(parent=root, title="Choose a folder") or ""
            return filedialog.askopenfilename(parent=root, title="Choose a chat log" if kind == "chat"
                                              else "Choose a video", filetypes=types) or ""
        except Exception:
            return ""
        finally:
            root.destroy()


def _has(mod: str, load: bool = True) -> bool:
    """Is a package there? load=False only looks for it: importing faster_whisper, yt_dlp or
    onnxruntime just to say "installed" costs ~50 MB and a few hundred ms, and the window
    asks every few seconds from the moment it opens."""
    if not load:
        if mod in sys.modules:
            return True
        try:
            import importlib.util
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False
    try:
        __import__(mod)
        return True
    except Exception:
        return False


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let two copies share a port, so only use it elsewhere.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def handle_error(self, request, client_address):
        # the video player drops connections all the time while seeking; that's not an error
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _bind(start: int):
    for port in range(start, start + 20):
        try:
            return _Server(("127.0.0.1", port), Handler), port
        except OSError:
            continue
    raise SystemExit(f"No free port between {start} and {start + 19}.")


def start(port: int = PORT):
    """Start the server on a background thread. Returns (server, url)."""
    srv, port = _bind(port)
    SERVER_PORT[0] = port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not _worker_started[0]:
        _worker_started[0] = True
        _forget_tiktok_api()
        _clean_leftovers()
        load_queue()
        run_cleanup()
        apply_perf()
        updater.start_background(log=_say)
        hotkey.start(on_hotkey)
    return srv, f"http://127.0.0.1:{port}"


def load_queue():
    """Bring back Exports and any renders that hadn't finished when Clip Radar last closed."""
    items = persist.restore_queue(persist.read_json(QUEUE_FILE))
    for it in items:
        if it["status"] == "done":
            it["url"] = f"/export/{it['id']}?v={int(time.time() * 1000)}"
    with queue_lock:
        known = {x["id"] for x in queue}
        queue[:0] = [it for it in items if it["id"] not in known]
    left = sum(1 for it in items if it["status"] == "queued")
    if items:
        print(f"  Render queue restored: {len(items)} clip(s)" + (f", {left} to render" if left else ""), flush=True)
    if left:
        queue_wake.set()


def _forget_tiktok_api():
    """4.4 moved TikTok posting to TikTok Studio: the old developer-app key, secret and sign-in
    (tiktok.dat, encrypted) aren't used by anything any more, so they don't stay on the PC."""
    f = paths.DATA_DIR / "tiktok.dat"
    try:
        if f.exists():
            f.unlink()
            print("  Removed the old TikTok sign-in (posting now goes through TikTok Studio).", flush=True)
    except OSError as e:
        print(f"  Couldn't remove the old TikTok sign-in file ({e}); it isn't used.", flush=True)


def _clean_leftovers():
    """Temporary files a render leaves behind if Clip Radar is closed or crashes mid-render."""
    pats = ("vc_*.part.mp4", "vc_*.ass", "*.mp4.*.tmp", "*.txt.*.tmp")
    folders = [f for root in (locations.project_roots() or [WORKROOT]) for f in glob.glob(os.path.join(root, "*", "tiktok"))]
    if locations.path("clips"):
        folders += glob.glob(os.path.join(locations.path("clips"), "*"))
    for folder in folders:
        for pat in pats:
            for f in glob.glob(os.path.join(folder, pat)):
                try:
                    os.remove(f)
                except OSError:
                    pass
        for d in glob.glob(os.path.join(folder, "vc_fonts_*")):
            shutil.rmtree(d, ignore_errors=True)


def busy() -> bool:
    """A download, tool setup or render is in progress (background listening doesn't count)."""
    return (queue_busy() or live_active() is not None
            or any(j["status"] == "running" and j["kind"] not in ("listen", "deepscan", "twitchlogin")
                   for j in jobs.values()))


_worker_started = [False]


def main():
    srv, url = start()
    print(f"\n  Clip Radar is running at {url}\n"
          f"  Clips are saved under {WORKROOT}\n"
          f"  Keep this window open while you use it. Close it to quit.\n", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
