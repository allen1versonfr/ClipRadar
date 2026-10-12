"""
VodClipper local server. Run:  python server.py
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

import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np

import engine
import live
import paths
import tiktok
import titles
import hotkey
import safety
import tools_setup
import updater

from version import VERSION
HERE = paths.RES_DIR
WORKROOT = str(paths.WORKROOT)
PORT = int(os.environ.get("VODCLIPPER_PORT", "7860"))
paths.ensure_dirs()
os.makedirs(WORKROOT, exist_ok=True)

MODE = {"window": False}          # True when shown in VodClipper's own app window
last_ping = [time.time()]


def shutdown_all(wait: float = 6.0):
    """Cancel running jobs (stops ffmpeg/downloads cleanly), then exit the process."""
    for j in list(jobs.values()):
        if j["status"] == "running":
            j["cancel"] = True
    for sess in list(live_sessions.values()):
        threading.Thread(target=sess.stop, daemon=True).start()
    for it in list(queue):
        it["cancel"] = True
        if it["status"] == "queued":
            it["status"] = "cancelled"
    end = time.time() + wait
    while time.time() < end and (any(j["status"] == "running" for j in jobs.values())
                                 or any(it["status"] == "rendering" for it in queue)):
        time.sleep(0.2)
    os._exit(0)


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
        job["progress"] = round(lo + (hi - lo) * max(0.0, min(1.0, x)), 4)
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
            job["status"] = "cancelled"
            job["step"] = "Cancelled"
            job_logger(job)("Cancelled.")
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e) or type(e).__name__
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


TX_MARGIN = 5.0   # seconds transcribed around each clip, so -5s/+5s trims still have captions


def _chat_window(pr: dict, start: float, end: float) -> list:
    d = pr.get("chat_delay", 6.0)
    lo, hi = start + d - 2, end + d + 8
    return [m for t, m in pr["chat"] if lo <= t <= hi]


def _title_clip(pr: dict, d: dict, words_abs: list, used: set):
    rel = [{"start": w["start"] - d["start"], "end": w["end"] - d["start"], "word": w["word"]}
           for w in words_abs if d["start"] - 0.2 <= w["start"] <= d["end"]]
    info = titles.generate(rel, _chat_window(pr, d["start"], d["end"]),
                           peak_rel=d.get("peak", d["start"]) - d["start"], used=used)
    cfg = safety.load_config()
    if not d.get("user_title"):
        d["title"] = safety.mask_text(info["title"], cfg)
        d["post"] = safety.mask_text(info["post"], cfg)
    d["titles"] = [safety.mask_text(t, cfg) for t in info["titles"]]
    d["vibe"] = info["vibe"]
    d["emoji"] = info["emoji"]
    d["safety"] = safety.scan(rel, d["title"], d.get("post", ""), cfg)
    used.add(d["title"].lower())


def rescan_all():
    """Content-check settings changed: re-check every clip that has been listened to."""
    cfg = safety.load_config()
    for pr in list(projects.values()):
        for d in pr.get("clips", []):
            if d.get("listened"):
                rel = [{"start": w["start"] - d["start"], "end": w["end"] - d["start"], "word": w["word"]}
                       for w in d.get("words", []) if d["start"] - 0.2 <= w["start"] <= d["end"]]
                d["safety"] = safety.scan(rel, d.get("title", ""), d.get("post", ""), cfg)


def clip_dicts(pid: str, clips: list) -> list:
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
        # quick first title from chat alone; replaced once the clip has been listened to
        _title_clip(pr, d, [], used)
        out.append(d)
    return out


def project_view(pid: str) -> dict:
    pr = projects[pid]
    an = pr["analysis"]
    return {
        "pid": pid, "title": pr["title"], "source": pr["source"], "duration": an.duration,
        "width": pr["info"]["width"], "height": pr["info"]["height"],
        "has_chat": an.has_chat, "has_audio": an.has_audio, "chat_count": len(pr["chat"]),
        "chat_weight": an.chat_weight_pct,
        "curve": curve_preview(an.score), "clips": pr["clips"],
        "vod": f"/vod/{pid}", "vcodec": pr["info"].get("vcodec", ""),
        "ext": os.path.splitext(pr["video"])[1].lower(),
    }


def do_analyze(job, body: dict):
    log, step, check = job_logger(job), job_step(job), job_check(job)
    src = (body.get("source") or "").strip()
    if not src:
        raise RuntimeError("Paste a Twitch VOD link or the path to a video file.")
    step("Getting the VOD")
    s = engine.acquire(src, WORKROOT, chat_file=body.get("chat_file") or "",
                       max_height=int(body.get("max_height", 1080)),
                       cookies_browser=body.get("cookies") or "",
                       log=log, progress=job_progress(job, 0.0, 0.62), step=step, check=check)
    step("Finding the best moments")
    delay = float(body.get("chat_delay", 6))
    an = engine.analyze(s.video, s.chat, chat_delay=delay,
                        chat_weight_pct=float(body.get("chat_weight", 60)),
                        log=log, progress=job_progress(job, 0.62, 0.94), check=check)
    pid = uuid.uuid4().hex[:8]
    info = engine.probe(s.video)
    projects[pid] = {"video": s.video, "chat": s.chat, "title": s.title, "workdir": s.workdir,
                     "analysis": an, "info": info, "clips": [], "chat_delay": delay,
                     "source": src, "clips_ver": 0}
    engine._save_meta(s.workdir, duration=an.duration)
    clips = engine.pick_clips(an, count=int(body.get("count", 10)),
                              length=float(body.get("length", 30)), chat=s.chat, chat_delay=delay)
    step("Making thumbnails")
    projects[pid]["clips"] = clip_dicts(pid, clips)
    if clips:
        log(f"Found {len(clips)} clips worth a look.")
    else:
        log("Nothing stood out strongly. Click the timeline to add clips yourself.")
    return project_view(pid)


# ---- transcript cache: each stretch of the VOD is transcribed once, then reused
#      for titles, the live caption preview, and the final render

def _tx(pr: dict, model: str, lang: str) -> dict:
    return pr.setdefault("tx", {}).setdefault(f"{model}|{lang}", {"words": [], "covered": []})


def ensure_words(pr: dict, a: float, b: float, model: str, lang: str, check=lambda: None) -> list:
    """Transcript words (absolute times) for [a, b] of the VOD, transcribing any missing parts."""
    a, b = max(0.0, a), min(pr["analysis"].duration, b)
    tx = _tx(pr, model, lang)
    with pr.setdefault("tx_lock", threading.Lock()):
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
            new = []
            if audio is not None and audio.size >= 1600:
                for w in engine.transcribe(audio, model, lang, lambda m: None):
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
                try:
                    words = ensure_words(pr, d["start"] - TX_MARGIN, d["end"] + TX_MARGIN, model, lang, check)
                except engine.Cancelled:
                    raise
                except Exception as e:
                    job_logger(job)(f"Couldn't transcribe clip: {str(e)[:160]}")
            used = {c["title"].lower() for c in pr["clips"] if c is not d and c.get("listened")}
            sess = live_sessions.get(pr.get("live", ""))
            if sess:   # live moments are separate projects; don't repeat titles across the stream
                used |= {projects[m]["clips"][0]["title"].lower() for m in sess.moments
                         if m != pid and m in projects and projects[m]["clips"] and projects[m]["clips"][0].get("listened")}
            d["words"] = words
            _title_clip(pr, d, words, used)
            d["listened"] = True
            job["progress"] = (n + 1) / max(1, len(todo))
        if on_done:
            try:
                on_done(pid)
            except Exception:
                traceback.print_exc()
        return {"ok": True}
    run_job(job, go)
    return job


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
            out.append(item)
    return sorted(out, key=lambda w: w["start"])


def _ropts(o: dict) -> engine.RenderOpts:
    return engine.RenderOpts(
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
    )


def queue_public(it: dict) -> dict:
    d = {k: v for k, v in it.items() if k not in ("opts", "cancel", "path", "words")}
    d["duration"] = round(it["end"] - it["start"], 2)
    return d


def queue_add(pid: str, c: dict, opts: dict) -> dict:
    start_, end_ = float(c["start"]), float(c["end"])
    if end_ - start_ < 1:
        raise RuntimeError("That clip is too short.")
    it = {"id": uuid.uuid4().hex[:8], "pid": pid, "clip_id": c.get("id"), "rank": int(c.get("rank") or 0),
          "start": start_, "end": end_, "title": str(c.get("title") or "")[:120],
          "post": str(c.get("post") or "")[:2200], "thumb": c.get("thumb", ""),
          "source_title": projects[pid]["title"] if pid in projects else "",
          "opts": dict(opts or {}), "status": "queued", "progress": 0.0, "step": "Waiting",
          "error": "", "cancel": False, "created": time.time(), "tiktok": None,
          "words": _clean_words(c.get("words"))}
    with queue_lock:
        queue.append(it)
    queue_wake.set()
    return it


def queue_busy() -> bool:
    return any(it["status"] in ("queued", "rendering") for it in queue)


def _render_item(it: dict):
    pr = projects.get(it["pid"])
    if not pr or not os.path.exists(pr["video"]):
        raise RuntimeError("The VOD for this clip was deleted.")
    ro = _ropts(it["opts"])
    outdir = os.path.join(pr["workdir"], "tiktok")
    os.makedirs(outdir, exist_ok=True)
    base = _safe_name(it["title"]) if it["title"].strip() else f"clip {engine.fmt_ts(it['start']).replace(':', '-')}"
    name = f"{it['rank']:02d} - {base}.mp4"
    n = 2
    while os.path.exists(os.path.join(outdir, name)):
        name = f"{it['rank']:02d} - {base} ({n}).mp4"
        n += 1

    def check():
        if it["cancel"]:
            raise engine.Cancelled()

    def prog(x):
        it["progress"] = round(max(0.0, min(1.0, x)), 3)

    if TEST_HOOKS.get("fail_next_render"):
        TEST_HOOKS["fail_next_render"] = False
        raise OSError(28, "No space left on device")

    cfg = safety.load_config()
    censoring = cfg.get("enabled", True) and cfg.get("censor", "off") != "off"
    words = None
    it["source_title"] = pr["title"]
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
            words, spans, mode = safety.censored(words or [], res, cfg)
            title, post = safety.mask_text(title, cfg), safety.mask_text(post, cfg)
            it["censored"] = sum(1 for f in res["flags"] if f["cat"] in cfg.get("censor_categories", []))
    if not ro.captions:
        words = []
    it["step"] = "Rendering"
    path = os.path.join(outdir, name)
    engine.render_clip(pr["video"], it["start"], it["end"], path, ro, title=title,
                       progress=prog, check=check, words=words, censor=spans, censor_mode=mode,
                       log=lambda m: print("  " + m, flush=True))
    # caption to paste when posting, saved next to the video
    if post:
        it["post"] = post
        try:
            with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
                f.write(post + "\n")
        except OSError:
            pass
    it["name"] = name
    it["path"] = path
    it["folder"] = outdir
    it["url"] = f"/files/{it['pid']}/tiktok/{name}?v={int(time.time() * 1000)}"


TEST_HOOKS = {"fail_next_render": False}


def friendly_render_error(e: Exception) -> tuple:
    """(what went wrong and what to do, in plain words; technical details)."""
    raw = str(e) or type(e).__name__
    low = raw.lower()
    errno_ = getattr(e, "errno", None) or getattr(e, "winerror", None)
    rules = [
        (errno_ in (28, 112) or "no space left" in low or "not enough space" in low,
         "Your drive is full. Free up space (Settings › Storage › Delete VODs), then retry."),
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


def start_tiktok_post(it: dict, mode: str, body: dict) -> dict:
    """Upload a finished clip to TikTok (direct post or drafts) as a background job."""
    job = new_job("tiktok")
    it["tiktok"] = {"state": "posting", "mode": mode, "job": job["id"], "at": time.time(),
                    "auto": bool(body.get("auto"))}

    def go():
        try:
            r = tiktok.post_video(
                it["path"], mode=mode, title=body.get("title", ""), privacy=body.get("privacy", ""),
                allow_comment=bool(body.get("allow_comment")), allow_duet=bool(body.get("allow_duet")),
                allow_stitch=bool(body.get("allow_stitch")), brand_organic=bool(body.get("brand_organic")),
                brand_content=bool(body.get("brand_content")),
                progress=job_progress(job, 0, 1), step=job_step(job), check=job_check(job))
        except Exception as e:
            it["tiktok"] = {"state": "error", "mode": mode, "error": str(e), "at": time.time()}
            raise
        url = tiktok.post_url(r.get("post_id", ""))
        it["tiktok"] = {"state": "done", "mode": mode, "status": r["status"], "post_id": r.get("post_id", ""),
                        "url": url, "at": time.time(), "auto": bool(body.get("auto"))}
        return dict(r, url=url)
    run_job(job, go)
    return job


def _after_render(it: dict):
    """Hands-free extras once a clip is ready."""
    if it.get("auto_draft") and not it.get("tiktok"):
        st = tiktok.status()
        if st.get("connected"):
            try:
                start_tiktok_post(it, "draft", {"title": it.get("post", "") or it.get("title", ""), "auto": True})
            except Exception as e:
                it["tiktok"] = {"state": "error", "mode": "draft", "error": str(e), "at": time.time()}


def queue_worker():
    while True:
        queue_wake.wait(2.0)
        queue_wake.clear()
        while True:
            with queue_lock:
                it = next((x for x in queue if x["status"] == "queued"), None)
                if it:
                    it["status"] = "rendering"
                    it["progress"] = 0.0
            if not it:
                break
            t0 = time.time()
            try:
                _render_item(it)
                it["status"] = "done"
                it["progress"] = 1.0
                it["step"] = f"Done in {time.time() - t0:.0f}s"
            except engine.Cancelled:
                with queue_lock:
                    if it in queue:
                        queue.remove(it)
                continue
            except Exception as e:
                it["status"] = "error"
                it["error"], it["detail"] = friendly_render_error(e)
                it["attempts"] = it.get("attempts", 0) + 1
                print(f"  Render failed: {it['error']} ({it['detail'][:300]})", flush=True)
                if not isinstance(e, (RuntimeError, OSError)):
                    traceback.print_exc()
                continue
            try:
                _after_render(it)
            except Exception:
                traceback.print_exc()


# --------------------------------------------------------------------------
# live mode
# --------------------------------------------------------------------------

live_sessions: dict = {}
SERVER_PORT = [PORT]


def live_active():
    return next((s for s in live_sessions.values() if s.status in ("starting", "waiting", "recording", "reconnecting")), None)


def on_hotkey():
    """Ctrl+Alt+C anywhere in Windows: clip the last 30 seconds of the live stream."""
    sess = live_active()
    if sess and sess.status in ("recording", "reconnecting"):
        sess.clip_now(30)
        sess.hotkey_clips = getattr(sess, "hotkey_clips", 0) + 1
        hotkey.beep(True)
    else:
        hotkey.beep(False)


def live_on_moment(sess, m: dict):
    """A live session found a moment: register it as a small project, title it, maybe render it."""
    mid = uuid.uuid4().hex[:8]
    pr = {"video": m["video"], "chat": m["chat"], "title": f"{sess.title}, moment {m['number']}",
          "workdir": sess.dir, "analysis": m["analysis"], "info": engine.probe(m["video"]), "clips": [],
          "chat_delay": sess.chat_delay, "source": sess.source, "clips_ver": 0, "live": sess.id,
          "thumb_prefix": f"m{m['number']}_"}
    projects[mid] = pr
    clip = m["clip"]
    clip.id = m["number"]
    pr["clips"] = clip_dicts(mid, [clip])
    d = pr["clips"][0]
    d.update(moment=m["number"], manual=m["manual"], live=sess.id, created=time.time(),
             at=round(m["offset"] + clip.peak, 1), abs_start=round(m["offset"] + clip.start, 1),
             abs_end=round(m["offset"] + clip.end, 1))
    sess.moments.append(mid)
    extra = sess.extra

    def done(_pid):
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
        c = dict(d, rank=m["number"])
        c["post"] = d.get("post", "")
        it = queue_add(mid, c, extra.get("opts") or {})
        it["auto_draft"] = bool(extra.get("auto_draft"))
        d["auto_queued"] = True
    start_listening(mid, [d["id"]], extra.get("model") or "small", extra.get("language") or "", on_done=done)


def start_live_session(src: str, body: dict):
    """Create and start a live session. body holds every setting so 'keep watching' can start the next one."""
    sid = uuid.uuid4().hex[:8]
    sess = live.LiveSession(
        sid, src, WORKROOT, length=float(body.get("length", 30)),
        sensitivity=body.get("frequency") or body.get("sensitivity", "normal"),
        chat_delay=float(body.get("chat_delay", 6)),
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
    hands_free = bool(body.get("hands_free", body.get("auto_render")))
    sess.extra = {"auto_render": hands_free, "opts": dict(body.get("opts") or {}),
                  "model": body.get("model") or "small", "language": body.get("language") or "",
                  "auto_draft": bool(body.get("auto_draft")), "keep_watching": bool(body.get("keep_watching")),
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
    params.update(wait_for_live=True, opts=sess.extra.get("opts"), auto_draft=sess.extra.get("auto_draft"),
                  hands_free=sess.extra.get("auto_render"), frequency=sess.frequency,
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
    v["auto_draft"] = bool(sess.extra.get("auto_draft"))
    v["keep_watching"] = bool(sess.extra.get("keep_watching"))
    v["held"] = sum(1 for c in v["clips"] if c.get("held"))
    v["hotkey_clips"] = getattr(sess, "hotkey_clips", 0)
    v["next"] = sess.extra.get("next", "")
    v["listening"] = any(j["status"] == "running" and j["kind"] == "listen" for j in
                         (jobs.get(projects[mid].get("listen_job", "")) or {"status": "", "kind": ""}
                          for mid in sess.moments if mid in projects))
    return v


def _callback_page(ok: bool, msg: str) -> bytes:
    color = "#3ec28f" if ok else "#f0565a"
    head = "TikTok connected" if ok else "TikTok sign-in didn't finish"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>VodClipper</title>
<style>body{{margin:0;height:100vh;display:grid;place-items:center;background:#111214;color:#ececee;
font:15px "Segoe UI",system-ui,sans-serif}}div{{max-width:420px;padding:28px;border:1px solid #2a2b31;border-radius:8px;
background:#17181b}}h1{{font-size:18px;margin:0 0 8px;display:flex;gap:10px;align-items:center}}
h1 i{{width:10px;height:10px;border-radius:50%;background:{color};display:inline-block}}p{{color:#a4a7b0;margin:0}}</style>
</head><body><div><h1><i></i>{head}</h1><p>{msg}</p></div></body></html>""".encode("utf-8")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "VodClipper"
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
                "ytdlp": ts["ytdlp_exe"] or _has("yt_dlp"),
                "whisper": engine.whisper_available(),
                "twitchdownloader": ts["twitchdownloader"],
                "can_download_tools": ts["can_download"],
                "workroot": WORKROOT,
                "window": MODE["window"],
                "frozen": paths.FROZEN,
                "version": VERSION,
                "running": busy(),
                "live": (live_active().id if live_active() else ""),
                "hotkey": hotkey.status(),
            })
        if p == "/api/update":
            return self._json(updater.view())
        if p == "/api/safety":
            return self._json(safety.public_config())
        if p == "/api/hotkey":
            return self._json(hotkey.status())
        if p == "/api/ping":
            last_ping[0] = time.time()
            return self._json({"ok": True})
        if p == "/api/recent":
            return self._json(engine.recent_projects(WORKROOT))
        m = re.fullmatch(r"/api/live/(\w+)", p)
        if m:
            sess = live_sessions.get(m.group(1))
            return self._json(live_view(sess)) if sess else self._err("That live session is gone.", 404)
        if p == "/api/tiktok":
            return self._json(tiktok.status())
        if p == "/callback/" or p == "/callback":
            params = {k: v[0] for k, v in q.items()}
            try:
                who = tiktok.handle_callback(params)
                page = _callback_page(True, f"Signed in{' as ' + who if who else ''}. You can close this tab and go back to VodClipper.")
            except tiktok.TikTokError as e:
                page = _callback_page(False, str(e))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if p == "/api/queue":
            with queue_lock:
                items = [queue_public(it) for it in queue]
            return self._json({"items": items})
        m = re.fullmatch(r"/api/clips/(\w+)", p)
        if m:
            pr = projects.get(m.group(1))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            lj = jobs.get(pr.get("listen_job", ""))
            return self._json({"clips": pr["clips"],
                               "listening": bool(lj and lj["status"] == "running"),
                               "step": lj["step"] if lj else ""})
        if p == "/api/storage":
            st = engine.storage_summary(WORKROOT)
            st["uninstaller"] = bool(uninstaller_path())
            return self._json(st)
        m = re.fullmatch(r"/api/job/(\w+)", p)
        if m:
            job = jobs.get(m.group(1))
            return self._json(job) if job else self._err("That job is gone. Start it again.", 404)
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
        if m and m.group(1) in projects:
            base = os.path.realpath(projects[m.group(1)]["workdir"])
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

        if p == "/api/analyze":
            job = new_job("analyze")
            run_job(job, lambda: do_analyze(job, body))
            return self._json({"job": job["id"]})

        if p == "/api/setup_tools":
            want = [w for w in body.get("want", []) if w in ("ffmpeg", "twitchdownloader", "ytdlp")]
            if not want:
                return self._err("Nothing to download.")
            job = new_job("setup")

            def go():
                log, step = job_logger(job), job_step(job)
                step("Downloading tools")
                return tools_setup.setup(want, log=log, progress=job_progress(job, 0, 1),
                                         check=job_check(job))
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
            if not updater.installed():
                return self._err("This copy of VodClipper wasn't installed with Setup, so it can't update itself. "
                                 "Download the new version from GitHub instead.", 400)
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
                if j["kind"] == "listen" and j["status"] == "running":
                    j["cancel"] = True
            end_wait = time.time() + 5
            while time.time() < end_wait and any(j["kind"] == "listen" and j["status"] == "running" for j in jobs.values()):
                time.sleep(0.1)
            fn = engine.delete_vods if what == "vods" else engine.delete_clips
            res = fn(WORKROOT)
            closed = []
            if what == "vods":
                # projects whose downloaded VOD is gone can't be previewed or rendered any more
                root = os.path.realpath(WORKROOT)
                for pid, pr in list(projects.items()):
                    if os.path.realpath(pr["video"]).startswith(root + os.sep) and not os.path.exists(pr["video"]):
                        projects.pop(pid, None)
                        closed.append(pid)
            with queue_lock:
                queue[:] = [it for it in queue if not (it["status"] == "done" and not os.path.exists(it.get("path", "")))
                            and not (it["status"] == "error" and it["pid"] in closed)]
            res["closed"] = closed
            res["storage"] = engine.storage_summary(WORKROOT)
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
            if live_active():
                return self._err("Live clipping is already running. Stop it first.", 409)
            if not engine.which("ffmpeg"):
                return self._err("ffmpeg is missing. Download the tools first.")
            src = (body.get("source") or "").strip()
            sid = uuid.uuid4().hex[:8]
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
                for k in ("auto_draft", "keep_watching"):
                    if k in body:
                        sess.extra[k] = bool(body[k])
                if "hands_free" in body:
                    sess.extra["auto_render"] = bool(body["hands_free"])
                for k in ("model", "language"):
                    if k in body:
                        sess.extra[k] = body[k]
            return self._json(live_view(sess))

        if p.startswith("/api/tiktok/"):
            act = p[len("/api/tiktok/"):]
            try:
                if act == "config":
                    tiktok.configure(body.get("client_key", ""), body.get("client_secret", ""))
                    return self._json(tiktok.status())
                if act == "connect":
                    url = tiktok.auth_url(SERVER_PORT[0])
                    if body.get("open", True):
                        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
                    return self._json({"url": url})
                if act == "disconnect":
                    tiktok.disconnect()
                    return self._json(tiktok.status())
                if act == "creator":
                    return self._json(tiktok.creator_info())
                if act == "post":
                    with queue_lock:
                        it = next((x for x in queue if x["id"] == body.get("item")), None)
                    if not it or it["status"] != "done" or not os.path.exists(it.get("path", "")):
                        return self._err("That clip isn't rendered (or its file was deleted).")
                    if (it.get("tiktok") or {}).get("state") == "posting":
                        return self._err("This clip is already being posted.")
                    mode = "draft" if body.get("mode") == "draft" else "direct"
                    job = start_tiktok_post(it, mode, body)
                    return self._json({"job": job["id"]})
            except tiktok.TikTokError as e:
                return self._err(str(e), 400)
            return self._err("not found", 404)

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
            an.combine(float(body.get("chat_weight", an.chat_weight_pct)))
            clips = engine.pick_clips(an, count=int(body.get("count", 10)),
                                      length=float(body.get("length", 30)),
                                      chat=pr["chat"], chat_delay=pr["chat_delay"])
            new = clip_dicts(pid, clips)
            for m in [c for c in pr["clips"] if c.get("manual")]:
                if not any(m["start"] < c["end"] and m["end"] > c["start"] for c in new):
                    m = dict(m, id=max([c["id"] for c in new] + [0]) + 1)
                    new.append(m)
            pr["clips"] = new
            pr["clips_ver"] = pr.get("clips_ver", 0) + 1
            return self._json(project_view(pid))

        if p == "/api/add_clip":
            pid = body.get("pid")
            if pid not in projects:
                return self._err("This session expired. Press Find clips again.", 404)
            pr = projects[pid]
            an = pr["analysis"]
            t = float(body["t"])
            length = min(float(body.get("length", 30)), an.duration)
            start = max(0.0, min(t - length * 0.5, an.duration - length))
            nid = max([c["id"] for c in pr["clips"]] + [0]) + 1
            sc = an.score[int(min(max(t, 0), len(an.score) - 1))]
            clip = engine.Clip(nid, round(start, 2), round(min(an.duration, start + length), 2),
                               t, round(float(sc), 2), "Added by you")
            d = clip_dicts(pid, [clip])[0]
            d["manual"] = True
            pr["clips"].append(d)
            return self._json(d)

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
                it["auto_draft"] = bool(body.get("auto_draft"))
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
            return self._json({"ok": True})

        if p == "/api/queue/retry":
            n = 0
            with queue_lock:
                for it in queue:
                    if it["status"] == "error" and (body.get("id") in (it["id"], "all")):
                        it.update(status="queued", progress=0.0, error="", detail="", step="Waiting", cancel=False)
                        n += 1
            queue_wake.set()
            if not n:
                return self._err("Nothing to retry.", 404)
            return self._json({"ok": True, "retried": n})

        if p.startswith("/api/test/") and os.environ.get("VODCLIPPER_TEST") == "1":
            act = p[len("/api/test/"):]
            if act == "fail_next_render":
                TEST_HOOKS["fail_next_render"] = True
                return self._json({"ok": True})
            if act == "hotkey":
                return self._json({"ok": hotkey.simulate()})
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

        if p == "/api/open_folder":
            folder = body.get("folder", "")
            root = os.path.realpath(WORKROOT)
            if folder == "root":
                folder = WORKROOT
            if folder and os.path.isdir(folder) and os.path.realpath(folder).startswith(root):
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
    """The Inno Setup uninstaller sits next to VodClipper.exe when it was installed with Setup."""
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
            return filedialog.askopenfilename(parent=root, title="Choose a chat log" if kind == "chat"
                                              else "Choose a video", filetypes=types) or ""
        except Exception:
            return ""
        finally:
            root.destroy()


def _has(mod: str) -> bool:
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
        threading.Thread(target=queue_worker, daemon=True).start()
        updater.start_background(log=_say)
        hotkey.start(on_hotkey)
    return srv, f"http://127.0.0.1:{port}"


def busy() -> bool:
    """A download, tool setup or render is in progress (background listening doesn't count)."""
    return (queue_busy() or live_active() is not None
            or any(j["status"] == "running" and j["kind"] != "listen" for j in jobs.values()))


_worker_started = [False]


def main():
    srv, url = start()
    print(f"\n  VodClipper is running at {url}\n"
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
