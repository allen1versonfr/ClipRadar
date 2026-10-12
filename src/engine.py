"""
VodClipper engine: find the best moments in a Twitch VOD and render them as
vertical (9:16) TikTok clips with optional auto-captions.

Pipeline
  1. acquire()       download the VOD (yt-dlp) + chat replay, or use a local file
  2. analyze()       per-second scores from chat activity and audio loudness
  3. pick_clips()    choose the top non-overlapping peaks (instant, re-runnable)
  4. render_clip()   cut, reframe to 1080x1920, burn captions/title, export mp4
"""

from __future__ import annotations

import bisect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import paths

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

Log = Callable[[str], None]
Progress = Callable[[float], None]
Check = Callable[[], None]   # raises Cancelled when the user hits Cancel

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".m4v", ".avi"}


class Cancelled(Exception):
    def __init__(self, msg: str = "Cancelled"):
        super().__init__(msg)


def _noop(*_a, **_k):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def which(name: str) -> Optional[str]:
    """Find a tool in VodClipper's tools folders first, then on PATH."""
    for d in paths.tools_dirs():
        for cand in (d / (name + ".exe"), d / name):
            if cand.is_file():
                return str(cand)
    return shutil.which(name)


def ffmpeg() -> str:
    exe = which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg isn't installed. Run install.bat, or put ffmpeg.exe in the tools folder.")
    return exe


def ffprobe() -> str:
    exe = which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe isn't installed. Run install.bat, or put ffprobe.exe in the tools folder.")
    return exe


def run(cmd: list, cwd: Optional[str] = None, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL,
                       encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-12:])
        raise RuntimeError(f"{Path(cmd[0]).stem} failed:\n{tail}")
    return p


_probe_cache: dict = {}


def probe(path: str) -> dict:
    key = (path, os.path.getmtime(path) if os.path.exists(path) else 0)
    if key in _probe_cache:
        return _probe_cache[key]
    p = run([ffprobe(), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path])
    info = json.loads(p.stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    if not v:
        raise RuntimeError("That file has no video track.")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"])) % 180
    if rot == 90:
        w, h = h, w
    out = {"duration": dur, "width": w, "height": h, "has_audio": a is not None,
           "vcodec": v.get("codec_name", "")}
    _probe_cache[key] = out
    return out


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def twitch_vod_id(url: str) -> Optional[str]:
    m = re.search(r"twitch\.tv/(?:videos/|\w+/v/|.*[?&]video=v?)(\d+)", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"v?\d{6,}", url.strip()):
        return url.strip().lstrip("v")
    return None


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.ASCII).strip("_")
    return s[:60] or "vod"


def _video_in(folder: str) -> Optional[str]:
    """A finished download in folder (ignores yt-dlp .part/.ytdl/fragment leftovers)."""
    if not os.path.isdir(folder):
        return None
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        stem, ext = os.path.splitext(f)
        if stem == "vod" and ext.lower() in VIDEO_EXTS and os.path.getsize(p) > 1024:
            return p
    return None


def _save_meta(workdir: str, **kv):
    path = os.path.join(workdir, "meta.json")
    meta = {}
    if os.path.exists(path):
        try:
            meta = json.load(open(path, encoding="utf-8"))
        except Exception:
            meta = {}
    meta.update(kv)
    meta["updated"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------
# storage: what's on disk, and cleaning it up
# --------------------------------------------------------------------------
# Only folders VodClipper made (they contain meta.json) are ever touched, and a
# local video you pointed VodClipper at is never deleted; it lives outside these folders.

def _project_dirs(workroot: str) -> list:
    if not os.path.isdir(workroot):
        return []
    out = []
    for d in os.listdir(workroot):
        p = os.path.join(workroot, d)
        if os.path.isdir(p) and not os.path.islink(p) and os.path.isfile(os.path.join(p, "meta.json")):
            out.append(p)
    return out


def _vod_files(pdir: str) -> list:
    """Downloaded video in a project folder: the VOD (incl. half-finished downloads), or for a live
    session its recording buffer and the cut-out moments."""
    out = []
    for f in os.listdir(pdir):
        p = os.path.join(pdir, f)
        if os.path.isfile(p) and (f.startswith("vod.") or f.startswith("chat_raw")):
            out.append(p)
    for sub in ("segments", "moments"):
        d = os.path.join(pdir, sub)
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
    return out


def _has_vod(pdir: str) -> bool:
    return any(os.path.splitext(f)[1].lower() in VIDEO_EXTS | {".ts"} for f in _vod_files(pdir))


def _clip_files(pdir: str) -> list:
    tdir = os.path.join(pdir, "tiktok")
    if not os.path.isdir(tdir):
        return []
    return [os.path.join(tdir, f) for f in os.listdir(tdir)
            if os.path.isfile(os.path.join(tdir, f)) and f.lower().endswith((".mp4", ".ass", ".wav", ".txt"))]


def _size(files: list) -> int:
    total = 0
    for f in files:
        try:
            total += os.path.getsize(f)
        except OSError:
            pass
    return total


def _dir_size(d: str) -> int:
    total = 0
    for root, _, files in os.walk(d):
        total += _size([os.path.join(root, f) for f in files])
    return total


def storage_summary(workroot: str) -> dict:
    vods, clips = [], []
    other, vod_count = 0, 0
    for pdir in _project_dirs(workroot):
        v = [f for f in _vod_files(pdir) if not f.endswith(".json")]
        vods += v
        vod_count += 1 if _has_vod(pdir) else 0
        clips += [f for f in _clip_files(pdir) if f.lower().endswith(".mp4")]
        other += _dir_size(os.path.join(pdir, "thumbs"))
    return {
        "vods": {"count": vod_count, "bytes": _size(vods)},
        "clips": {"count": len(clips), "bytes": _size(clips)},
        "other_bytes": other,
        "workroot": workroot,
    }


def _remove(paths_: list) -> tuple:
    freed, failed = 0, []
    for p in paths_:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        for attempt in range(3):  # Windows can briefly lock a file that was just being read
            try:
                os.remove(p)
                freed += size
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == 2:
                    failed.append(os.path.basename(p))
                else:
                    time.sleep(0.4)
    return freed, failed


def _prune(pdir: str):
    """Remove a project folder once it holds neither a VOD nor any clips."""
    if not _has_vod(pdir) and not _clip_files(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
    else:
        tdir = os.path.join(pdir, "tiktok")
        if os.path.isdir(tdir) and not os.listdir(tdir):
            os.rmdir(tdir)


def delete_vods(workroot: str) -> dict:
    """Delete downloaded VODs (+ their chat and thumbnails). Rendered clips are kept."""
    files, count = [], 0
    for pdir in _project_dirs(workroot):
        v = _vod_files(pdir)
        count += 1 if _has_vod(pdir) else 0
        files += v
        chat = os.path.join(pdir, "chat.json")
        if os.path.isfile(chat):
            files.append(chat)
        tdir = os.path.join(pdir, "thumbs")
        if os.path.isdir(tdir):
            files += [os.path.join(tdir, f) for f in os.listdir(tdir)]
    freed, failed = _remove(files)
    for pdir in _project_dirs(workroot):
        for sub in ("thumbs", "segments", "moments"):
            d = os.path.join(pdir, sub)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        _prune(pdir)
    return {"deleted": count, "freed": freed, "failed": failed}


def delete_clips(workroot: str) -> dict:
    """Delete every rendered TikTok clip. Downloaded VODs are kept."""
    files = []
    for pdir in _project_dirs(workroot):
        files += _clip_files(pdir)
    count = len([f for f in files if f.lower().endswith(".mp4")])
    freed, failed = _remove(files)
    for pdir in _project_dirs(workroot):
        _prune(pdir)
    return {"deleted": count, "freed": freed, "failed": failed}


def recent_projects(workroot: str, limit: int = 8) -> list:
    out = []
    if not os.path.isdir(workroot):
        return out
    for d in os.listdir(workroot):
        meta_p = os.path.join(workroot, d, "meta.json")
        if not os.path.exists(meta_p):
            continue
        try:
            m = json.load(open(meta_p, encoding="utf-8"))
        except Exception:
            continue
        src = m.get("source", "")
        if m.get("live"):
            continue
        if not src or (m.get("local") and not os.path.exists(src)):
            continue
        if not m.get("local") and not _video_in(os.path.join(workroot, d)):
            continue
        out.append({"title": m.get("title", d), "source": src, "updated": m.get("updated", 0),
                    "duration": m.get("duration", 0)})
    out.sort(key=lambda x: -x["updated"])
    return out[:limit]


# --------------------------------------------------------------------------
# 1. acquire
# --------------------------------------------------------------------------

@dataclass
class Source:
    video: str
    chat: list  # [(seconds, text)] sorted
    title: str
    workdir: str


def acquire(source: str, workroot: str, chat_file: str = "", max_height: int = 1080,
            cookies_browser: str = "", log: Log = _noop, progress: Progress = _noop,
            step: Log = _noop, check: Check = _noop) -> Source:
    source = source.strip().strip('"').strip("'")
    chat_file = (chat_file or "").strip().strip('"').strip("'")
    if chat_file and not os.path.isfile(chat_file):
        raise RuntimeError(f"Chat file not found: {chat_file}")

    # ---- local file
    if os.path.isfile(source):
        video = os.path.abspath(source)
        if os.path.splitext(video)[1].lower() not in VIDEO_EXTS:
            log("That file type isn't a usual video format, trying anyway.")
        title = Path(video).stem
        workdir = os.path.join(workroot, _slug(title))
        os.makedirs(workdir, exist_ok=True)
        log(f"Using local file: {video}")
        if chat_file:
            shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
            chat = load_chat(chat_file)
            log(f"Loaded {len(chat):,} chat messages.")
        else:
            chat = _sidecar_chat(video, workdir, log)
        _save_meta(workdir, title=title, source=video, local=True)
        progress(1.0)
        return Source(video, chat, title, workdir)

    if not re.match(r"^https?://", source, re.I) and (
            "\\" in source or "/" in source or re.match(r"^[a-zA-Z]:", source)) and "twitch.tv" not in source:
        raise RuntimeError(f"File not found: {source}")

    # ---- online
    if not re.match(r"^https?://", source) and not twitch_vod_id(source):
        raise RuntimeError("Paste a Twitch VOD link like https://www.twitch.tv/videos/123456789, "
                           "or the full path to a video file on your PC.")
    if re.search(r"twitch\.tv/(?!videos/)[\w]+/?(\?.*)?$", source) and "/clip/" not in source:
        raise RuntimeError("That's a channel link. Open the stream's Videos tab and paste the link "
                           "to a past broadcast (twitch.tv/videos/…).")

    vod_id = twitch_vod_id(source)
    url = source if re.match(r"^https?://", source) else f"https://www.twitch.tv/videos/{vod_id}"
    workdir = os.path.join(workroot, f"vod_{vod_id}" if vod_id else _slug(source)[:40])
    os.makedirs(workdir, exist_ok=True)

    step("Downloading the VOD")
    video, title = _download_video(url, vod_id, workdir, max_height, cookies_browser,
                                   log, progress, step, check)
    check()
    step("Getting chat replay")
    if chat_file:
        shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
        chat = load_chat(chat_file)
    else:
        chat = _download_chat(url, vod_id, workdir, log)
    _save_meta(workdir, title=title, source=url, local=False)
    return Source(video, chat, title, workdir)


def _download_video(url: str, vod_id: Optional[str], workdir: str, max_height: int,
                    cookies_browser: str, log: Log, progress: Progress, step: Log, check: Check):
    existing = _video_in(workdir)
    meta_p = os.path.join(workdir, "meta.json")
    if existing:
        log("VOD already downloaded, reusing it.")
        progress(1.0)
        title = "vod"
        if os.path.exists(meta_p):
            try:
                title = json.load(open(meta_p, encoding="utf-8")).get("title", "vod")
            except Exception:
                pass
        return existing, title

    fmt = (f"bv*[height<={max_height}][vcodec^=avc1]+ba/"
           f"b[height<={max_height}][vcodec^=avc1]/"
           f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b")
    errs = []

    # 1) standalone yt-dlp.exe (kept up to date by the app, so Twitch changes don't need a rebuild)
    exe = which("yt-dlp")
    if exe:
        try:
            return _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check)
        except Cancelled:
            raise
        except Exception as e:
            errs.append(str(e))
            log(f"yt-dlp couldn't download it: {e}")

    # 2) yt-dlp Python module
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None
    if yt_dlp:
        try:
            return _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check)
        except Cancelled:
            raise
        except Exception as e:
            if "Cancelled" in type(e).__name__ or "Cancelled" in str(e):
                raise Cancelled()
            msg = _clean_ytdlp_error(str(e))
            errs.append(msg)
            log(f"yt-dlp couldn't download it: {msg}")
    elif not exe:
        errs.append("yt-dlp isn't installed")

    err = "\n".join(dict.fromkeys(errs)) if errs else None

    # fallback: TwitchDownloaderCLI
    tdl = which("TwitchDownloaderCLI")
    if tdl and vod_id:
        step("Downloading the VOD (TwitchDownloader)")
        log("Trying TwitchDownloaderCLI instead...")
        out = os.path.join(workdir, "vod.mp4")
        cmd = [tdl, "videodownload", "--id", vod_id, "-o", out, "-t", "8"]
        if which("ffmpeg"):
            cmd += ["--ffmpeg-path", ffmpeg()]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        tail = []
        try:
            for line in p.stdout:
                tail = (tail + [line.strip()])[-8:]
                m = re.search(r"(\d{1,3})%", line)
                if m:
                    progress(min(0.999, int(m.group(1)) / 100))
                try:
                    check()
                except Cancelled:
                    p.kill()
                    raise
        finally:
            p.wait()
        video = _video_in(workdir)
        if p.returncode == 0 and video:
            title = f"Twitch VOD {vod_id}"
            _save_meta(workdir, title=title)
            progress(1.0)
            return video, title
        err = (err or "") + "\nTwitchDownloader: " + " / ".join(t for t in tail if t)[-300:]

    raise RuntimeError(
        "Couldn't download that VOD.\n"
        f"{err or 'yt-dlp is not installed'}\n"
        "If it's subscriber-only, set 'Sign in with' under More options to the browser where you're "
        "logged in to Twitch. If Twitch changed something, press 'Update downloader' under More options.")



class _Stepper:
    """Turns raw download numbers into progress + a friendly step line."""

    def __init__(self, progress: Progress, step: Log, log: Log):
        self.progress, self.step, self.log = progress, step, log
        self.t = 0.0
        self.pct = -1

    def update(self, done, total, frag_i, frag_n, speed, eta):
        frac = None
        if total:
            frac = done / total if done is not None else None
        elif frag_n:
            frac = (frag_i or 0) / frag_n
        if frac is not None:
            self.progress(min(0.999, max(0.0, frac)))
        now = time.time()
        if now - self.t > 1.0:
            self.t = now
            bits = ["Downloading the VOD"]
            if speed:
                bits.append(f"{speed / 1e6:.1f} MB/s")
            if eta:
                bits.append(f"about {fmt_ts(eta)} left")
            self.step(", ".join(bits))
            if frac is not None and int(frac * 10) > self.pct:
                self.pct = int(frac * 10)
                self.log(f"Download {int(frac * 100)}%")


def _finish_download(workdir: str, title: str, duration, progress: Progress, log: Log):
    _save_meta(workdir, title=title, duration=duration or 0)
    video = _video_in(workdir)
    if not video:
        raise RuntimeError("the download finished but no video file was found")
    progress(1.0)
    log(f"Downloaded: {title}")
    return video, title


def _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check):
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    cmd = [exe, "--newline", "--progress", "--no-colors", "--no-simulate", "--ignore-config",
           "--print", "before_dl:VCINFO %(.{title,is_live,duration})j",
           "--progress-template",
           "download:VCPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
           "%(progress.total_bytes_estimate)s %(progress.fragment_index)s "
           "%(progress.fragment_count)s %(progress.speed)s %(progress.eta)s",
           "-f", fmt, "-o", os.path.join(workdir, "vod.%(ext)s"), "--merge-output-format", "mp4",
           "-N", "8", "--retries", "10", "--fragment-retries", "20", "--match-filters", "!is_live"]
    ff = which("ffmpeg")
    if ff:
        cmd += ["--ffmpeg-location", os.path.dirname(ff)]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd.append(url)
    log("Downloading the VOD. Long streams take a while.")
    st = _Stepper(progress, step, log)
    info = {}
    errors, tail = [], []
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    try:
        for line in p.stdout:
            line = line.strip()
            if line.startswith("VCPROG "):
                v = [num(x) for x in line.split()[1:8]]
                v += [None] * (7 - len(v))
                st.update(v[0], v[1] or v[2], v[3], v[4], v[5], v[6])
            elif line.startswith("VCINFO "):
                try:
                    info = json.loads(line[7:])
                except Exception:
                    pass
            elif line:
                tail = (tail + [line])[-6:]
                if line.startswith("ERROR"):
                    errors.append(_clean_ytdlp_error(line))
            try:
                check()
            except Cancelled:
                p.kill()
                raise
    finally:
        p.wait()
    if p.returncode != 0 or not _video_in(workdir):
        if info.get("is_live") or any("is_live" in t for t in tail):
            raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
        raise RuntimeError(errors[-1] if errors else (tail[-1] if tail else f"yt-dlp exited with code {p.returncode}"))
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log)


def _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check):
    st = _Stepper(progress, step, log)

    def hook(d):
        check()
        if d.get("status") == "downloading":
            st.update(d.get("downloaded_bytes"), d.get("total_bytes") or d.get("total_bytes_estimate"),
                      d.get("fragment_index"), d.get("fragment_count"), d.get("speed"), d.get("eta"))

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(workdir, "vod.%(ext)s"),
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 8,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 10,
        "fragment_retries": 20,
        "continuedl": True,
        "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
    }
    ff = which("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = os.path.dirname(ff)
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    log("Downloading the VOD. Long streams take a while.")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    if info.get("is_live"):
        raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log)


def _clean_ytdlp_error(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = s.replace("ERROR: ", "").strip()
    return s.splitlines()[0][:300] if s else "unknown error"


def _download_chat(url: str, vod_id: Optional[str], workdir: str, log: Log) -> list:
    out = os.path.join(workdir, "chat.json")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        try:
            chat = load_chat(out)
            log(f"Chat already downloaded ({len(chat):,} messages), reusing it.")
            return chat
        except Exception:
            pass

    tdl = which("TwitchDownloaderCLI")
    if tdl and vod_id:
        log("Downloading chat replay with TwitchDownloaderCLI...")
        try:
            if os.path.exists(out):
                os.remove(out)
            run([tdl, "chatdownload", "--id", vod_id, "-o", out, "-t", "8"], timeout=3600)
            chat = load_chat(out)
            log(f"Got {len(chat):,} chat messages.")
            return chat
        except Exception as e:
            log(f"Chat download failed: {str(e).splitlines()[-1][:160]}")

    try:
        import yt_dlp
        opts = {"skip_download": True, "writesubtitles": True, "subtitleslangs": ["rechat"],
                "outtmpl": os.path.join(workdir, "chat_raw"), "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for f in os.listdir(workdir):
            if f.startswith("chat_raw") and f.endswith(".json"):
                shutil.move(os.path.join(workdir, f), out)
                chat = load_chat(out)
                log(f"Got {len(chat):,} chat messages.")
                return chat
    except Exception:
        pass

    log("No chat replay available, ranking by audio only. "
        "(Run install.bat to add TwitchDownloaderCLI for chat.)")
    return []


def _sidecar_chat(video: str, workdir: str, log: Log) -> list:
    base = os.path.splitext(video)[0]
    for cand in (base + ".chat.json", base + "_chat.json", base + ".json",
                 os.path.join(os.path.dirname(video), "chat.json"),
                 os.path.join(workdir, "chat.json")):
        if os.path.exists(cand):
            try:
                chat = load_chat(cand)
            except Exception:
                continue
            log(f"Found chat file {os.path.basename(cand)} ({len(chat):,} messages).")
            return chat
    log("No chat file given, ranking by audio only.")
    return []


def load_chat(path: str) -> list:
    """Parse TwitchDownloader / yt-dlp rechat / generic chat JSON into [(sec, text)]."""
    import gzip
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()
    msgs = []

    def take(c):
        if not isinstance(c, dict):
            return
        t = c.get("content_offset_seconds", c.get("offset", c.get("time", c.get("t"))))
        msg = c.get("message", c.get("text", c.get("body", "")))
        if isinstance(msg, dict):
            msg = msg.get("body") or " ".join(
                fr.get("text", "") for fr in msg.get("fragments", []) or [] if isinstance(fr, dict))
        try:
            msgs.append((float(t), str(msg or "")))
        except (TypeError, ValueError):
            pass

    try:
        data = json.loads(raw)
        items = data.get("comments", data) if isinstance(data, dict) else data
        for c in items or []:
            take(c)
    except json.JSONDecodeError:
        for line in raw.splitlines():
            try:
                take(json.loads(line))
            except Exception:
                continue
    msgs.sort(key=lambda m: m[0])
    return msgs


# --------------------------------------------------------------------------
# 2. analyze
# --------------------------------------------------------------------------

HYPE = {
    # laughter
    "kekw": 2.0, "lul": 1.5, "lol": 1.2, "lmao": 1.5, "lmfao": 1.8, "omegalul": 2.0,
    "icant": 1.8, "😂": 1.5, "🤣": 1.5, "xd": 1.0, "pepelaugh": 1.8, "kekl": 1.8, "lulw": 1.8,
    "kek": 1.5, "aware": 1.0, "dead": 1.2, "💀": 1.5,
    # hype
    "pog": 1.8, "pogchamp": 1.8, "poggers": 1.8, "pogu": 1.8, "w": 1.0, "ez": 1.0,
    "letsgo": 1.5, "hypers": 1.5, "gg": 0.8, "goat": 1.2, "insane": 1.5, "🔥": 1.0,
    # shock
    "monkas": 1.5, "wtf": 1.5, "omg": 1.3, "😱": 1.3, "d:": 1.2, "nooo": 1.5, "noway": 1.5,
    "omegalul": 2.0, "wideeyes": 1.2,
    # clip requests (strong signal)
    "clip": 3.0, "clipit": 3.0, "clipped": 2.5,
}
_TOKEN = re.compile(r"[\w:]+|[^\w\s]")


def chat_weight(text: str) -> float:
    t = text.lower()
    w = 1.0
    if "clip it" in t or "clip that" in t or "clip this" in t:
        w += 3.0
    if "no way" in t or "lets go" in t or "let's go" in t:
        w += 1.5
    for tok in _TOKEN.findall(t):
        if tok in HYPE:
            w += HYPE[tok]
    if "???" in t:
        w += 1.0
    if re.search(r"(\w)\1{4,}", t):  # "LETSGOOOOO", "AAAAAA"
        w += 0.8
    return min(w, 8.0)


def audio_loudness(video: str, duration: float, progress: Progress = _noop, check: Check = _noop) -> np.ndarray:
    """Per-second RMS loudness in dB, streamed so multi-hour VODs stay light on RAM."""
    sr = 8000
    n = int(math.ceil(duration)) + 1
    out = np.full(n, -90.0, dtype=np.float32)
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-i", video, "-map", "0:a:0", "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    block = sr * 2  # 1 second of 16-bit mono samples
    i = 0
    buf = b""
    try:
        while True:
            chunk = p.stdout.read(block * 60)
            if not chunk:
                break
            check()
            buf += chunk
            whole = len(buf) // block
            if whole and i < n:
                arr = np.frombuffer(buf[: whole * block], dtype=np.int16).astype(np.float32)
                arr = arr.reshape(whole, sr)
                rms = np.sqrt(np.mean(arr * arr, axis=1)) / 32768.0
                db = 20 * np.log10(np.maximum(rms, 1e-5))
                end = min(n, i + whole)
                out[i:end] = db[: end - i]
                i += whole
                buf = buf[whole * block:]
                if duration:
                    progress(min(0.999, i / duration))
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    progress(1.0)
    return out


def _rolling(x: np.ndarray, win: int, fn: str = "mean") -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if win <= 1 or len(x) == 0:
        return x.copy()
    if fn == "mean":
        c = np.cumsum(np.pad(x, (win // 2 + 1, win - 1 - win // 2), mode="edge"), dtype=np.float64)
        return ((c[win:] - c[:-win]) / win).astype(np.float32)
    if fn == "median":
        # rolling median on a decimated grid, interpolated back (fast for 10h+ VODs)
        step = max(1, win // 10)
        idx = np.arange(0, len(x), step)
        half = win // 2
        med = np.array([np.median(x[max(0, j - half): j + half + 1]) for j in idx], dtype=np.float32)
        return np.interp(np.arange(len(x)), idx, med).astype(np.float32)
    raise ValueError(fn)


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    if mad < 1e-6:
        sd = float(np.std(x))
        mad = sd if sd > 1e-6 else 1.0
    return ((x - med) / mad).astype(np.float32)


@dataclass
class Analysis:
    duration: float
    chat_z: np.ndarray
    audio_z: np.ndarray
    has_chat: bool
    has_audio: bool
    score: Optional[np.ndarray] = None
    chat_weight_pct: float = 60.0

    def combine(self, chat_weight_pct: float) -> np.ndarray:
        """Mix chat and audio signals. Cheap, so the UI can re-rank instantly."""
        wc = (chat_weight_pct / 100.0) if self.has_chat else 0.0
        if not self.has_audio:
            wc = 1.0 if self.has_chat else 0.0
        s = wc * self.chat_z + (1 - wc) * self.audio_z
        self.score = _rolling(s, 5, "mean")
        self.chat_weight_pct = chat_weight_pct
        return self.score


def analyze(video: str, chat: list, chat_delay: float = 6.0, chat_weight_pct: float = 60,
            log: Log = _noop, progress: Progress = _noop, check: Check = _noop) -> Analysis:
    info = probe(video)
    dur = info["duration"]
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length. The file may be incomplete or damaged.")
    n = int(math.ceil(dur)) + 1
    log(f"VOD length {fmt_ts(dur)}. Measuring audio...")

    if info["has_audio"]:
        loud = audio_loudness(video, dur, progress, check)[:n]
        loud = np.pad(loud, (0, max(0, n - len(loud))), constant_values=-90)
    else:
        log("No audio track found, ranking by chat only.")
        loud = None
    if len(chat) > 20:
        log(f"Scoring {len(chat):,} chat messages...")
    an = score_signals(loud, chat, n, dur, chat_delay, chat_weight_pct)
    if loud is not None and (loud < -75).mean() > 0.2:
        log(f"{int((loud < -75).mean() * 100)}% of the audio is silent or muted.")
    progress(1.0)
    return an


def score_signals(loud: Optional[np.ndarray], chat: list, n: int, duration: float,
                  chat_delay: float = 6.0, chat_weight_pct: float = 60) -> "Analysis":
    """Per-second hype score from loudness (dB per second) and chat. Shared by VODs and live mode."""
    if loud is not None and len(loud):
        loud = np.asarray(loud, dtype=np.float32)[:n]
        loud = np.pad(loud, (0, max(0, n - len(loud))), constant_values=-90)
        # Twitch mutes DMCA'd parts: treat dead silence as "no info" rather than "boring"
        muted = loud < -75
        short = _rolling(loud, 4, "mean")
        base = _rolling(np.where(muted, np.median(loud[~muted]) if (~muted).any() else -60, loud), 180, "median")
        audio = np.clip(_robust_z(short - base), -3, 8)
        audio[muted] = np.minimum(audio[muted], 0)
        has_audio = True
    else:
        audio = np.zeros(n, dtype=np.float32)
        has_audio = False

    has_chat = len(chat) > 20
    if has_chat:
        rate = np.zeros(n, dtype=np.float32)
        for t, msg in chat:
            s = int(t - chat_delay)  # chat reacts a few seconds late; shift it back
            if 0 <= s < n:
                rate[s] += chat_weight(msg)
        short = _rolling(rate, 8, "mean")
        base = _rolling(rate, 300, "median")
        # relative surge matters more than raw volume (works for small and big streams)
        surge = (short - base) / (np.sqrt(np.maximum(base, 0)) + 1.0)
        chat_z = np.clip(_robust_z(surge), -3, 10)
    else:
        chat_z = np.zeros(n, dtype=np.float32)

    an = Analysis(duration, chat_z, audio, has_chat, has_audio)
    an.combine(chat_weight_pct)
    return an


# --------------------------------------------------------------------------
# 3. pick clips
# --------------------------------------------------------------------------

@dataclass
class Clip:
    id: int
    start: float
    end: float
    peak: float
    score: float
    reason: str = ""


def pick_clips(an: Analysis, count: int = 10, length: float = 30, lead: float = 0.6,
               chat: Optional[list] = None, chat_delay: float = 6.0) -> list:
    s = an.score
    n = len(s)
    length = min(length, max(5.0, an.duration))
    if n < 5:
        return []
    order = np.argsort(-s, kind="stable")
    top = float(s[order[0]])
    floor = max(1.0, 0.15 * top)
    taken = np.zeros(n, dtype=bool)
    clips: list = []
    skip_edge = min(60, int(n * 0.03))  # ignore "starting soon"/outro edges a bit
    chat_t = [t for t, _ in chat] if chat else []
    for idx in order:
        if len(clips) >= count or s[idx] <= floor:
            break
        if idx < skip_edge or idx > n - 1 - skip_edge or taken[idx]:
            continue
        start = max(0.0, idx - length * lead)
        end = min(an.duration, start + length)
        start = max(0.0, end - length)
        taken[int(start):int(end) + 1] = True
        if any(start < c.end - 2 and end > c.start + 2 for c in clips):
            continue  # would overlap a better clip
        clips.append(Clip(0, round(start, 2), round(end, 2), float(idx),
                          round(float(s[idx]), 2), _reason(an, int(idx), chat, chat_t, chat_delay)))
    for i, c in enumerate(clips, 1):
        c.id = i
    return clips


def _reason(an: Analysis, i: int, chat: Optional[list], chat_t: list, delay: float) -> str:
    c = float(an.chat_z[i]) if an.has_chat else 0.0
    a = float(an.audio_z[i])
    parts = []
    if an.has_chat and c > 2:
        parts.append("Chat explodes" if c > 5 else "Chat spike")
    if a > 2:
        parts.append("loud moment" if a > 4 else "volume jump")
    if chat:
        lo = bisect.bisect_left(chat_t, i + delay - 4)
        hi = bisect.bisect_right(chat_t, i + delay + 14)
        words: dict = {}
        emoji = {"😂", "🤣", "💀", "🔥", "😱"}
        for _, msg in chat[lo:hi]:
            for tok in set(re.findall(r"\w+|[😂🤣💀🔥😱]", msg)):
                if (len(tok) > 1 and tok.lower() in HYPE) or tok in emoji:
                    words[tok] = words.get(tok, 0) + 1
        top = sorted(words.items(), key=lambda kv: -kv[1])[:3]
        if top:
            parts.append(", ".join(f"{w} ×{k}" for w, k in top))
    if not parts:
        return "Activity peak"
    text = "; ".join(parts)
    return text[0].upper() + text[1:]


def make_thumb(video: str, t: float, out: str, width: int = 480) -> str:
    tmp = out + ".tmp.jpg"
    run([ffmpeg(), "-v", "error", "-y", "-nostdin", "-ss", f"{max(0, t):.2f}", "-i", video,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", tmp], timeout=60)
    os.replace(tmp, out)
    return out


# --------------------------------------------------------------------------
# 4. render
# --------------------------------------------------------------------------

@dataclass
class RenderOpts:
    layout: str = "split"           # split | full | blur
    cam: tuple = (0.0, 0.0, 0.25, 0.25)   # facecam crop x, y, w, h as fractions of source
    cam_height: float = 0.33        # fraction of output height used by facecam in split
    game_zoom: float = 1.0          # >1 zooms gameplay in
    game_x: float = 0.5             # horizontal focus of gameplay crop 0..1
    captions: bool = True
    caption_pos: str = "auto"       # auto | top | middle | bottom | custom (cap_x, cap_y)
    caption_color: str = "#FFE600"  # highlight color for the spoken word
    caption_size: int = 100         # percent
    words_per_line: int = 3
    uppercase: bool = True
    caption_style: str = "pop"      # pop | box | karaoke | glow | clean
    caption_font: str = "classic"   # key of FONTS
    caption_anim: bool = True       # words pop in
    cap_x: float = 0.5              # custom caption centre, fractions of the frame
    cap_y: float = 0.7
    title_style: str = "box"        # box | dark | accent | outline
    title_font: str = "classic"
    title_size: int = 100           # percent
    title_x: float = 0.5            # title centre x and top y, fractions of the frame
    title_y: float = 0.05           # top of the title box
    whisper_model: str = "small"
    language: str = ""              # "" = auto-detect
    hw_encode: bool = True
    crf: int = 20
    fps: int = 30


W, H = 1080, 1920


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def build_filter(info: dict, o: RenderOpts) -> tuple:
    """Return (filter_complex producing [v] in yuv420p, caption_center_y)."""
    sw, sh = info["width"], info["height"]
    z = max(1.0, o.game_zoom)

    def game_crop(out_w: int, out_h: int) -> str:
        aspect = out_w / out_h
        cwf = min(sw, sh * aspect) / z
        cw = min(_even(cwf), sw - sw % 2)
        ch = min(_even(cwf / aspect), sh - sh % 2)
        cx = int(min(max(0, o.game_x * sw - cw / 2), sw - cw))
        cy = (sh - ch) // 2
        return f"crop={cw}:{ch}:{cx}:{cy},scale={out_w}:{out_h}:flags=lanczos,setsar=1"

    if o.layout == "split":
        top_h = _even(H * min(max(o.cam_height, 0.2), 0.5))
        bot_h = H - top_h
        x, y, w, h = [min(max(float(v), 0.0), 1.0) for v in o.cam]
        cx, cy = int(x * sw), int(y * sh)
        cw, ch = max(16, int(w * sw)), max(16, int(h * sh))
        cw, ch = min(cw, sw - cx), min(ch, sh - cy)
        cw, ch = cw - cw % 2, ch - ch % 2
        cam = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{top_h}:force_original_aspect_ratio=increase:"
               f"flags=lanczos,crop={W}:{top_h},setsar=1")
        f = (f"[0:v]split=2[a][b];[a]{cam}[cam];[b]{game_crop(W, bot_h)}[game];"
             f"[cam][game]vstack=inputs=2,fps={o.fps},format=yuv420p[v]")
        cap_y = top_h  # right on the seam reads best
    elif o.layout == "full":
        f = f"[0:v]{game_crop(W, H)},fps={o.fps},format=yuv420p[v]"
        cap_y = H * 0.70
    elif o.layout == "blur":
        fg_h = _even(W * sh / sw)
        fg = game_crop(W, fg_h) if z > 1 else f"scale={W}:{fg_h}:flags=lanczos,setsar=1"
        f = (f"[0:v]split=2[a][b];"
             f"[a]scale=-2:{H // 4},crop={W // 4}:{H // 4},boxblur=12:2,eq=brightness=-0.12,"
             f"scale={W}:{H},setsar=1[bg];"
             f"[b]{fg}[fg];[bg][fg]overlay=0:(H-h)/2,fps={o.fps},format=yuv420p[v]")
        cap_y = H / 2 + fg_h / 2 + 170
    else:
        raise ValueError(f"unknown layout {o.layout}")

    if o.caption_pos == "custom":
        return f, min(max(o.cap_y * H, 120), H - 120)
    pos = {"top": H * 0.22, "middle": H * 0.5, "bottom": H * 0.78}.get(o.caption_pos, cap_y)
    return f, min(max(pos, 260), H - 220)


# ---- captions --------------------------------------------------------------

_whisper_cache: dict = {}
_whisper_lock = threading.RLock()   # one transcription at a time (titles + renders share the model)


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _load_whisper(model_size: str, log: Log):
    if model_size in _whisper_cache:
        return _whisper_cache[model_size]
    from faster_whisper import WhisperModel
    silence = np.zeros(16000, dtype=np.float32)
    errors = []
    for device, ctype in (("cuda", "float16"), ("cpu", "int8")):
        try:
            log(f"Loading Whisper '{model_size}' on {'GPU' if device == 'cuda' else 'CPU'} "
                f"(the first time downloads the model)...")
            model = WhisperModel(model_size, device=device, compute_type=ctype)
            # tiny dry run so missing CUDA libraries fail here, not mid-render
            list(model.transcribe(silence, beam_size=1, language="en", without_timestamps=True)[0])
            _whisper_cache[model_size] = model
            return model
        except Exception as e:
            msg = str(e).splitlines()[0][:140] if str(e) else type(e).__name__
            errors.append(f"{device}: {msg}")
            if device == "cuda":
                log("  GPU not usable for Whisper, using CPU.")
    raise RuntimeError("Couldn't load Whisper. " + " | ".join(errors))


def transcribe(audio, model_size: str, language: str, log: Log) -> list:
    """audio: path or 16 kHz mono float32 numpy array."""
    with _whisper_lock:
        model = _load_whisper(model_size, log)
        segs, _ = model.transcribe(audio, language=language or None, word_timestamps=True,
                                   vad_filter=True, beam_size=5,
                                   vad_parameters={"min_silence_duration_ms": 400})
        words = []
        for seg in segs:
            for w in seg.words or []:
                txt = w.word.strip()
                if txt:
                    words.append({"start": float(w.start), "end": float(w.end), "word": txt})
    return words


def extract_audio(video: str, start: float, length: float) -> Optional[np.ndarray]:
    """16 kHz mono float32 samples of a stretch of the video's audio (None if unreadable)."""
    p = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}",
                        "-t", f"{max(0.1, length):.3f}", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1",
                        "-ar", "16000", "-f", "f32le", "-"], capture_output=True,
                       stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    if p.returncode != 0:
        return None
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def _ass_color(hex_rgb: str) -> str:
    h = (hex_rgb or "").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFE600"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " "))


# Caption and title fonts. "classic" is the system's heaviest sans; the rest ship in ui/fonts/captions
# (OFL / Apache licensed) and are handed to libass with fontsdir. Values: (file, family name in the file).
FONTS = {
    "classic": ("", ""),
    "montserrat": ("Montserrat-Black.ttf", "Montserrat Black"),
    "poppins": ("Poppins-Black.ttf", "Poppins Black"),
    "inter": ("Inter-Black.otf", "Inter Black"),
    "anton": ("Anton-Regular.ttf", "Anton"),
    "bebas": ("BebasNeue-Regular.ttf", "Bebas Neue"),
    "bangers": ("Bangers-Regular.ttf", "Bangers"),
    "luckiest": ("LuckiestGuy-Regular.ttf", "Luckiest Guy"),
}
# condensed faces look small at the same point size; scale them up to a similar visual size
FONT_SCALE = {"inter": 1.05, "anton": 1.3, "bebas": 1.4, "bangers": 1.25}
CAPTION_STYLES = ("pop", "box", "karaoke", "glow", "clean")
TITLE_STYLES = ("box", "dark", "accent", "outline")


def fonts_dir() -> Path:
    return Path(paths.RES_DIR) / "ui" / "fonts" / "captions"


def _font(key: str = "classic") -> tuple:
    """(family name for ASS, bold flag, font file or '')"""
    file, family = FONTS.get(key, FONTS["classic"])
    if file and (fonts_dir() / file).is_file():
        return family, 0, file          # already a heavy cut: no fake bold
    return ("Arial Black" if sys.platform == "win32" else "DejaVu Sans"), -1, ""


def fonts_for(o: "RenderOpts") -> list:
    """Font files the ASS needs from the bundled set."""
    out = []
    for key in ({o.caption_font, o.title_font}):
        f = _font(key)[2]
        if f:
            out.append(str(fonts_dir() / f))
    return out


def _chunks(words: list, o: "RenderOpts", clip_len: float) -> list:
    chunks: list = []
    cur: list = []
    for w in words:
        if w["start"] >= clip_len:
            break
        prev = cur[-1] if cur else None
        if prev and (w.get("g") or prev.get("g")):
            brk = w.get("g") != prev.get("g")      # a line typed in the app stays one caption
        else:
            brk = bool(cur) and (len(cur) >= max(1, o.words_per_line)
                                 or w["start"] - prev["end"] > 0.6
                                 or prev["word"][-1:] in ".?!")
        if brk:
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list, o: RenderOpts, center_y: float, clip_len: float, title: str = "") -> str:
    hi = _ass_color(o.caption_color)
    cfont, cbold, _ = _font(o.caption_font)
    tfont, tbold, _ = _font(o.title_font)
    style = o.caption_style if o.caption_style in CAPTION_STYLES else "pop"
    tstyle = o.title_style if o.title_style in TITLE_STYLES else "box"
    base_fs = int(86 * max(50, min(160, o.caption_size)) / 100 * FONT_SCALE.get(o.caption_font, 1.0))
    title_fs = int(64 * max(50, min(160, o.title_size)) / 100 * FONT_SCALE.get(o.title_font, 1.0))
    bord = max(4, base_fs // 12)
    white, black, dark = "&H00FFFFFF", "&H00000000", "&H00141414"
    if style == "clean":
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H64000000,{cbold},0,0,0,100,100,1,0,1,0,4,5,70,70,0,1"
    else:
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H96000000,{cbold},0,0,0,100,100,1,0,1,{bord},3,5,70,70,0,1"
    tpad = max(8, title_fs // 4)
    if tstyle == "outline":
        t_style = f"Style: Title,{tfont},{title_fs},{white},{white},{black},&H96000000,{tbold},0,0,0,100,100,0,0,1,{max(4, title_fs // 10)},3,8,90,90,0,1"
    else:
        fill, boxc = {"box": (dark, white), "dark": (white, "&H1A141414"), "accent": (dark, hi.rstrip("&"))}[tstyle]
        t_style = f"Style: Title,{tfont},{title_fs},{fill},{fill},{boxc},{boxc},{tbold},0,0,0,100,100,0,0,3,{tpad},0,8,90,90,0,1"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cap_style}
{t_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title.strip():
        tx = int(min(max(o.title_x, 0.3), 0.7) * W)
        th = min(tx, W - tx) - 90           # wrap the title inside the frame around its own centre
        ty = int(min(max(o.title_y, 0.0), 0.92) * H) + (tpad if tstyle != "outline" else 0)
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(clip_len)},Title,,{tx - th},{W - tx - th},0,,"
                     f"{{\\an8\\pos({tx},{ty})}}{_ass_escape(title.strip())}")

    x = int(min(max(o.cap_x if o.caption_pos == "custom" else 0.5, 0.3), 0.7) * W)
    half = min(x, W - x) - 70               # wrap captions inside the frame around their own centre
    ml, mr = x - half, W - x - half
    y = int(center_y)
    chunks = _chunks(words, o, clip_len)
    for ci, ch in enumerate(chunks):
        texts = [_ass_escape(w["word"].upper() if o.uppercase else w["word"]) for w in ch]
        chars = sum(len(t) for t in texts) + len(texts) - 1
        # shrink long chunks so they stay on one or two lines
        fs = base_fs if chars <= 16 else max(int(base_fs * 0.75), int(base_fs * 18 / chars))
        chunk_end = ch[-1]["end"]
        if ci + 1 < len(chunks):
            chunk_end = min(max(chunk_end, ch[-1]["end"] + 0.3), chunks[ci + 1][0]["start"])
        else:
            chunk_end = ch[-1]["end"] + 0.4
        chunk_end = min(chunk_end, clip_len)
        for wi, w in enumerate(ch):
            st = max(0.0, w["start"])
            en = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
            if en <= st:
                en = st + 0.08
            parts = []
            for k, t in enumerate(texts):
                if style == "pop":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k == wi else t)
                elif style == "glow":
                    parts.append(f"{{\\fscx112\\fscy112}}{t}{{\\fscx100\\fscy100}}" if k == wi else t)
                elif style == "karaoke":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k <= wi else t)
                elif style == "box":
                    parts.append(f"{{\\3c{hi}\\bord{max(8, int(fs * 0.22))}\\c{dark}&}}{t}{{\\3c{black}&\\bord{max(4, fs // 12)}\\c{white}&}}"
                                 if k == wi else t)
                else:
                    parts.append(t)
            lead = ""
            if style == "glow":
                lead = f"\\3c{hi}\\bord{max(4, fs // 12)}\\blur{max(5, fs // 8)}"
            if style == "box":
                lead = f"\\bord{max(4, fs // 12)}"
            pop = r"{\fscx114\fscy114\t(0,100,\fscx100\fscy100)}" if (wi == 0 and o.caption_anim and style != "clean") else ""
            lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Cap,,{ml},{mr},0,,"
                         f"{{\\an5\\pos({x},{y})\\fs{fs}{lead}}}{pop}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


# ---- encoders --------------------------------------------------------------

_encoder_cache: dict = {}

_HW = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                    "-cq", "21", "-b:v", "0", "-profile:v", "high"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                  "-qp_i", "20", "-qp_p", "22", "-qp_b", "24"]),
]


def video_encoder(o: RenderOpts) -> tuple:
    """(name, args). Tries GPU encoders once (fast), falls back to x264."""
    x264 = ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", str(o.crf),
                        "-profile:v", "high"])
    if not o.hw_encode:
        return x264
    if "hw" not in _encoder_cache:
        _encoder_cache["hw"] = None
        try:
            listing = run([ffmpeg(), "-hide_banner", "-encoders"], timeout=20).stdout
        except Exception:
            listing = ""
        for name, args in _HW:
            if name not in listing:
                continue
            try:
                run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=1080x1920:d=0.2",
                     "-pix_fmt", "yuv420p", *args, "-f", "null", "-"], timeout=30)
                _encoder_cache["hw"] = (name, args)
                break
            except Exception:
                continue
    return _encoder_cache["hw"] or x264


def render_clip(video: str, clip_start: float, clip_end: float, out_path: str, o: RenderOpts,
                title: str = "", log: Log = _noop, progress: Progress = _noop,
                check: Check = _noop, words: Optional[list] = None,
                censor: Optional[list] = None, censor_mode: str = "off") -> str:
    """words: transcript (times relative to clip_start) to caption with; transcribed here if None.
    censor: [(start, end)] spans (relative) to mute ("mute") or bleep ("bleep")."""
    info = probe(video)
    clip_start = max(0.0, clip_start)
    clip_end = min(info["duration"] or clip_end, clip_end)
    length = max(1.0, clip_end - clip_start)
    vf, cap_y = build_filter(info, o)
    work = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(work, exist_ok=True)
    stem = Path(out_path).stem
    tag = uuid.uuid4().hex[:8]
    # ASCII names: the subtitles filter can't take quotes or other specials from a clip title
    ass_name = f"vc_{tag}.ass"
    ass_path = os.path.join(work, ass_name)
    font_dir_name = f"vc_fonts_{tag}"
    font_dir = os.path.join(work, font_dir_name)

    if words is not None:
        words = list(words) if o.captions else []
    else:
        words = []
        if o.captions and info["has_audio"]:
            if not whisper_available():
                log("Captions skipped: faster-whisper isn't installed (run install.bat).")
            else:
                # decode with our ffmpeg straight to 16 kHz mono float samples; Whisper takes the array
                # directly, so captions don't depend on PyAV's file-reading API
                audio = extract_audio(video, clip_start, length)
                progress(0.08)
                check()
                if audio is None or audio.size < 1600:
                    log("Captions skipped: couldn't read this clip's audio.")
                else:
                    log("Transcribing speech for captions...")
                    try:
                        words = transcribe(audio, o.whisper_model, o.language, log)
                    except Exception as e:
                        log(f"Captions skipped: {str(e)[:200]}")
                        words = []
                if not words:
                    log("No speech found in this clip.")
    progress(0.3)
    check()

    use_ass = bool(words) or bool(title.strip())
    if use_ass:
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(words, o, cap_y, length, title))
        # bare filenames + cwd=work sidestep Windows drive-letter escaping in the subtitles filter
        sub = f"subtitles=filename={ass_name}"
        need = fonts_for(o)
        if need:
            os.makedirs(font_dir, exist_ok=True)
            for fp in need:
                shutil.copy2(fp, font_dir)
            sub += f":fontsdir={font_dir_name}"
        vf = vf.replace("format=yuv420p[v]", f"format=yuv420p,{sub}[v]")

    # censoring: silence flagged words, and for "bleep" lay a 1 kHz tone over them
    af = ""
    spans = [(max(0.0, a), min(length, b)) for a, b in (censor or []) if b > a and a < length]
    if spans and info["has_audio"] and censor_mode in ("mute", "bleep"):
        on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
        tail = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        if censor_mode == "bleep":
            af = (f";[0:a:0]volume=0:enable='{on}'[am];"
                  f"sine=frequency=1000:sample_rate=48000:duration={length:.3f},volume=0.6,"
                  f"volume=0:enable='not({on})'[bp];"
                  f"[am][bp]amix=inputs=2:duration=first:normalize=0,{tail}[a]")
        else:
            af = f";[0:a:0]volume=0:enable='{on}',{tail}[a]"
        vf = vf + af

    tmp_name = f"vc_{tag}.part.mp4"
    tmp_path = os.path.join(work, tmp_name)

    def encode(enc_args: list):
        cmd = [ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
               "-ss", f"{clip_start:.3f}", "-t", f"{length:.3f}", "-i", video,
               "-filter_complex", vf, "-map", "[v]"]
        if info["has_audio"] and af:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        elif info["has_audio"]:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000", "-ar", "48000"]
        cmd += [*enc_args, "-pix_fmt", "yuv420p", "-r", str(o.fps), "-movflags", "+faststart",
                tmp_name]
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        try:
            for line in p.stdout:
                if line.startswith("out_time_us="):
                    try:
                        t = int(line.split("=")[1]) / 1e6
                        progress(0.3 + 0.7 * min(1.0, max(0.0, t) / length))
                    except ValueError:
                        pass
                try:
                    check()
                except Cancelled:
                    p.kill()
                    raise
            err = p.stderr.read()
        finally:
            p.wait()
        return p.returncode, err

    enc_name, enc_args = video_encoder(o)
    try:
        code, err = encode(enc_args)
        if code != 0 and enc_name != "libx264":
            log(f"GPU encoder ({enc_name}) failed, re-encoding on CPU.")
            _encoder_cache["hw"] = None
            code, err = encode(video_encoder(RenderOpts(hw_encode=False, crf=o.crf))[1])
        if code != 0:
            raise RuntimeError("ffmpeg render failed:\n" + "\n".join(err.strip().splitlines()[-10:]))
        os.replace(tmp_path, out_path)
    finally:
        shutil.rmtree(font_dir, ignore_errors=True)
        for p in (tmp_path, ass_path):
            try:
                os.remove(p)
            except OSError:
                pass
    progress(1.0)
    return out_path
