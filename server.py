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
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np

import engine
import paths
import titles
import tools_setup

VERSION = "1.4"
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
    if not d.get("user_title"):
        d["title"] = info["title"]
        d["post"] = info["post"]
    d["titles"] = info["titles"]
    d["vibe"] = info["vibe"]
    d["emoji"] = info["emoji"]
    used.add(d["title"].lower())


def clip_dicts(pid: str, clips: list) -> list:
    pr = projects[pid]
    tdir = os.path.join(pr["workdir"], "thumbs")
    os.makedirs(tdir, exist_ok=True)
    out, used = [], set()
    for c in clips:
        d = asdict(c) if not isinstance(c, dict) else dict(c)
        t = d["start"] + (d["end"] - d["start"]) * 0.6
        name = f"t_{int(t)}.jpg"
        path = os.path.join(tdir, name)
        if not os.path.exists(path):
            try:
                engine.make_thumb(pr["video"], t, path)
            except Exception:
                pass
        d["thumb"] = f"/files/{pid}/thumbs/{name}"
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


def start_listening(pid: str, order: list, model: str, lang: str):
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
            d["words"] = words
            _title_clip(pr, d, words, used)
            d["listened"] = True
            job["progress"] = (n + 1) / max(1, len(todo))
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
        whisper_model=o.get("whisper_model", "small"),
        language=o.get("language", ""),
        hw_encode=bool(o.get("hw_encode", True)),
    )


def queue_public(it: dict) -> dict:
    return {k: v for k, v in it.items() if k not in ("opts", "cancel", "path")}


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

    words = None
    if ro.captions and pr["info"]["has_audio"] and engine.whisper_available():
        it["step"] = "Listening for captions"
        abs_words = ensure_words(pr, it["start"], it["end"], ro.whisper_model, ro.language, check)
        words = [{"start": w["start"] - it["start"], "end": w["end"] - it["start"], "word": w["word"]}
                 for w in abs_words if w["start"] < it["end"]]
    it["step"] = "Rendering"
    path = os.path.join(outdir, name)
    engine.render_clip(pr["video"], it["start"], it["end"], path, ro, title=it["title"],
                       progress=prog, check=check, words=words,
                       log=lambda m: print("  " + m, flush=True))
    # caption to paste when posting, saved next to the video
    if it.get("post"):
        try:
            with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
                f.write(it["post"] + "\n")
        except OSError:
            pass
    it["name"] = name
    it["path"] = path
    it["folder"] = outdir
    it["url"] = f"/files/{it['pid']}/tiktok/{name}?v={int(time.time() * 1000)}"


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
            except Exception as e:
                it["status"] = "error"
                it["error"] = (str(e) or type(e).__name__)[:400]
                if not isinstance(e, RuntimeError):
                    traceback.print_exc()


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
                return self._send_file(path, cache=True)
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
            })
        if p == "/api/ping":
            last_ping[0] = time.time()
            return self._json({"ok": True})
        if p == "/api/recent":
            return self._json(engine.recent_projects(WORKROOT))
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
            out = os.path.join(pr["workdir"], "thumbs", f"frame_{int(t)}.jpg")
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

        if p == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: shutdown_hook[0]()).start()
            return

        if p == "/api/delete":
            what = body.get("what")
            if what not in ("vods", "clips"):
                return self._err("Unknown delete request.")
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
            c = body.get("clip") or {}
            try:
                start_, end_ = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                return self._err("Bad clip.")
            if end_ - start_ < 1:
                return self._err("That clip is too short.")
            it = {"id": uuid.uuid4().hex[:8], "pid": pid, "clip_id": c.get("id"), "rank": int(c.get("rank") or 0),
                  "start": start_, "end": end_, "title": str(c.get("title") or "")[:120],
                  "post": str(c.get("post") or "")[:2000], "thumb": c.get("thumb", ""),
                  "opts": dict(body.get("opts") or {}), "status": "queued", "progress": 0.0, "step": "Waiting",
                  "error": "", "cancel": False, "created": time.time()}
            with queue_lock:
                queue.append(it)
            queue_wake.set()
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
            with queue_lock:
                it = next((x for x in queue if x["id"] == body.get("id")), None)
                if it and it["status"] == "error":
                    it.update(status="queued", progress=0.0, error="", step="Waiting", cancel=False)
            queue_wake.set()
            return self._json({"ok": True})

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
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not _worker_started[0]:
        _worker_started[0] = True
        threading.Thread(target=queue_worker, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def busy() -> bool:
    """A download, tool setup or render is in progress (background listening doesn't count)."""
    return queue_busy() or any(j["status"] == "running" and j["kind"] != "listen" for j in jobs.values())


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
