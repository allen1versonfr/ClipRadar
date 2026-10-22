"""
Clip Radar engine: find the best moments in a Twitch VOD and render them as
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
from dataclasses import dataclass, field
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
    """Find a tool in Clip Radar's tools folders first, then on PATH."""
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
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(cmd[0]).stem} timed out after {timeout:.0f}s (the file may be on a slow "
                           "or disconnected drive).")
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
             "-show_format", "-show_streams", path], timeout=120)
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
           "vcodec": v.get("codec_name", ""), "fps": _fps(v)}
    if len(_probe_cache) >= 64:
        # a live recording changes with every probe; don't let those pile up over a long stream
        _probe_cache.pop(next(iter(_probe_cache)))
    _probe_cache[key] = out
    return out


def _fps(v: dict) -> float:
    """Frames per second of a video stream (30 when it can't be read)."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, d = str(v.get(key) or "0/1").split("/")
            f = float(n) / float(d or 1)
            if 1 <= f <= 240:
                return round(f, 3)
        except (ValueError, ZeroDivisionError):
            continue
    return 30.0


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.ASCII).strip("_")
    return s[:60] or "vod"


def project_video(workdir: str) -> Optional[str]:
    """A project's downloaded video: in the Downloads location (Settings › Storage) or its own folder."""
    import locations
    return _video_in(locations.download_dir(workdir)) or _video_in(workdir)


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
    import persist
    persist.write_json(path, meta, indent=1)


# --------------------------------------------------------------------------
# storage: what's on disk, and cleaning it up
# --------------------------------------------------------------------------
# Only folders Clip Radar made (they contain meta.json) are ever touched, and a
# local video you pointed Clip Radar at is never deleted; it lives outside these folders.

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
    """Downloaded video in a project folder (or the Downloads location): the VOD (incl. half-finished
    downloads), or for a live session its recording buffer and the cut-out moments."""
    import locations
    out = []
    dl = locations.download_dir(pdir)
    for d in dict.fromkeys([pdir, dl]):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
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
    """Rendered clips for a project: its "tiktok" folder and, when set, the Clips location."""
    import locations
    out = []
    for tdir in dict.fromkeys([os.path.join(pdir, "tiktok"), locations.clips_dir(pdir)]):
        if os.path.isdir(tdir):
            out += [os.path.join(tdir, f) for f in os.listdir(tdir)
                    if os.path.isfile(os.path.join(tdir, f)) and f.lower().endswith((".mp4", ".mov", ".webm", ".srt", ".ass", ".wav", ".txt"))]
    return out


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
        import locations
        for tdir in dict.fromkeys([os.path.join(pdir, "tiktok"), locations.clips_dir(pdir), locations.download_dir(pdir)]):
            if tdir != pdir and os.path.isdir(tdir) and not os.listdir(tdir):
                try:
                    os.rmdir(tdir)
                except OSError:
                    pass


def delete_vods(workroot: str) -> dict:
    """Delete downloaded VODs (+ their chat and thumbnails). Rendered clips are kept."""
    files, count = [], 0
    for pdir in _project_dirs(workroot):
        v = _vod_files(pdir)
        count += 1 if _has_vod(pdir) else 0
        files += v
        for name in ("chat.json", "analysis.npz"):
            if os.path.isfile(os.path.join(pdir, name)):
                files.append(os.path.join(pdir, name))
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
        if not m.get("local") and not project_video(os.path.join(workroot, d)):
            continue
        out.append({"title": m.get("title", d), "source": src, "updated": m.get("updated", 0),
                    "duration": m.get("duration", 0), "review": m.get("review")})
    out.sort(key=lambda x: -x["updated"])
    return out[:limit]


# --------------------------------------------------------------------------
# 1. acquire
# --------------------------------------------------------------------------

@dataclass
class Source:
    video: str
    chat: list  # [(seconds, text, user, kind)] sorted
    title: str
    workdir: str
    streamer: str = ""      # channel name when known (for learning what you like per streamer)
    start: float = 0.0      # where the link said to start (YouTube ?t=, Twitch ?t=)
    info: dict = field(default_factory=dict)   # what the source is (sources.describe)


def _meta(workdir: str) -> dict:
    try:
        return json.load(open(os.path.join(workdir, "meta.json"), encoding="utf-8"))
    except Exception:
        return {}


def acquire(source: str, workroot: str, chat_file: str = "", max_height: int = 1080,
            cookies_browser: str = "", log: Log = _noop, progress: Progress = _noop,
            step: Log = _noop, check: Check = _noop, cookie_file: str = "") -> Source:
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
        info: dict = {}
        if chat_file:
            if os.path.abspath(chat_file) != os.path.abspath(os.path.join(workdir, "chat.json")):
                shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
            chat = load_chat(chat_file, info)
            log(f"Loaded {len(chat):,} chat messages.")
        else:
            chat = _sidecar_chat(video, workdir, log, info)
        streamer = info.get("streamer") or _meta(workdir).get("streamer", "")
        _save_meta(workdir, title=title, source=video, local=True, streamer=streamer,
                   **({"streamer_id": info["streamer_id"]} if info.get("streamer_id") else {}))
        progress(1.0)
        return Source(video, chat, title, workdir, streamer)

    import sources
    c = sources.classify(source)
    if c["platform"] == "local":
        if "\\" in source or "/" in source or re.match(r"^[a-zA-Z]:", source):
            raise RuntimeError(f"File not found: {source}")
        raise RuntimeError("Paste a Twitch, YouTube or X video link, or pick a video file on your PC.")
    if c["kind"] == "channel":
        raise RuntimeError({"twitch": "That's a channel link. Open the channel's Videos tab and paste the link to a past broadcast "
                                      "(twitch.tv/videos/…), or use Live clipping to clip it as it streams.",
                            "youtube": "That's a channel link. Paste the link to one of its videos, or use Live clipping for its "
                                       "livestream."}.get(c["platform"], "That's a channel link. Paste the link to a video."))

    vod_id = c["id"] if c["platform"] == "twitch" and c["kind"] == "vod" else None
    url = c["url"]
    workdir = os.path.join(workroot, sources.workdir_name(c, _slug(source)[:40]))
    os.makedirs(workdir, exist_ok=True)

    desc: dict = {}
    meta = None
    if not vod_id and not project_video(workdir):
        # YouTube, X and others: find out what it is first (a video, a finished stream, a live one...)
        step("Checking the link")
        meta, err = probe_url(url, cookies_browser, cookie_file)
        if meta is None and err:
            raise RuntimeError(sources.explain(err, c["platform"]))
        desc = sources.describe(meta or {}, c)
        if desc["live"]:
            raise RuntimeError(f"That's a {desc['label'].lower()} happening now. Use Live clipping for it, "
                               "or wait until it ends and paste the link again.")
        if desc["problem"]:
            raise RuntimeError(desc["problem"])
        need = float((meta or {}).get("filesize") or (meta or {}).get("filesize_approx") or 0)
        _check_download_space(workdir, need)
        log(f"{desc['label']}: {desc['title'] or url}" + (f" by {desc['uploader']}" if desc["uploader"] else "")
            + (f", {fmt_ts(desc['duration'])}" if desc["duration"] else ""))
        _save_meta(workdir, platform=c["platform"], kind=desc["kind"], label=desc["label"], uploader=desc["uploader"],
                   uploaded=desc["uploaded"], **({"streamer": desc["uploader_id"].lower()} if desc["uploader_id"] else {}))
    elif vod_id:
        _save_meta(workdir, platform="twitch", kind="vod", label="Twitch past broadcast")

    if vod_id and not project_video(workdir):
        _check_download_space(workdir, 0)
    step("Downloading the VOD" if c["platform"] == "twitch" else "Downloading the video")
    try:
        video, title = _download_video(url, vod_id, workdir, max_height, cookies_browser,
                                       log, progress, step, check, cookie_file)
    except Cancelled:
        raise
    except RuntimeError as e:
        if c["platform"] == "twitch":
            raise
        raise RuntimeError(sources.explain(str(e), c["platform"])) from None
    check()
    if c["platform"] == "youtube" and meta:
        # no chat on an ordinary video: what YouTube knows about it (Most replayed, comments, captions)
        import ytextra
        step("Reading YouTube's Most replayed graph, comments and captions")
        ytextra.gather(url, workdir, meta, log, ["--cookies", cookie_file] if cookie_file
                       else ["--cookies-from-browser", cookies_browser] if cookies_browser else [])
        check()
    step("Getting chat replay")
    info: dict = {}
    if chat_file:
        shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
        chat = load_chat(chat_file, info)
    elif c["platform"] == "youtube":
        chat = _youtube_chat(url, workdir, log, cookies_browser, cookie_file) if _meta(workdir).get("kind") == "vod" else []
        if not chat:
            log("No chat replay for this video, ranking by audio.")
    elif c["platform"] == "twitch":
        chat = _download_chat(url, vod_id, workdir, log, info)
    else:
        chat = []
        log("No chat for this video, ranking by audio.")
    streamer = _meta(workdir).get("streamer") or info.get("streamer", "")
    _save_meta(workdir, title=title, source=url, local=False, streamer=streamer, start=c["start"],
               **({"streamer_id": info["streamer_id"]} if info.get("streamer_id") else {}))
    m = _meta(workdir)
    return Source(video, chat, title, workdir, streamer, start=c["start"],
                  info={k: m.get(k, "") for k in ("platform", "kind", "label", "uploader", "uploaded")})


def _check_download_space(workdir: str, need: float):
    """Stop before a download that can't fit (Settings › Storage › Downloaded videos)."""
    import locations
    dl = locations.download_dir(workdir)
    free = locations.free_space(dl)
    if free is None:
        return
    need = need * 1.1 + 300e6 if need else 2e9          # unknown size: a stream download needs a few GB
    if free < need:
        raise RuntimeError(f"Not enough space to download this: {free / 1e9:.1f} GB free on that drive, about "
                           f"{need / 1e9:.1f} GB needed. Free up space (Settings › Storage) or choose another folder for "
                           "downloaded videos.")


def probe_url(url: str, cookies_browser: str = "", cookie_file: str = "") -> tuple:
    """yt-dlp's metadata for a link, without downloading: (info or None, error text)."""
    exe = which("yt-dlp")
    if exe:
        cmd = [exe, "-J", "--skip-download", "--no-warnings", "--ignore-config", "--no-playlist"]
        if cookie_file:
            cmd += ["--cookies", cookie_file]
        elif cookies_browser:
            cmd += ["--cookies-from-browser", cookies_browser]
        try:
            p = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            return None, str(e)
        if p.returncode == 0 and p.stdout.strip().startswith("{"):
            try:
                return json.loads(p.stdout), ""
            except ValueError:
                pass
        return None, (p.stderr or p.stdout or "").strip()
    try:
        import yt_dlp
    except ImportError:
        return None, "yt-dlp isn't installed"
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False) or {}, ""
    except Exception as e:
        return None, str(e)


def _youtube_chat(url: str, workdir: str, log: Log, cookies_browser: str = "", cookie_file: str = "") -> list:
    """A finished YouTube livestream's chat replay (yt-dlp's live_chat), when it has one."""
    out = os.path.join(workdir, "chat.json")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        try:
            chat = load_chat(out)
            log(f"Chat already downloaded ({len(chat):,} messages), reusing it.")
            return chat
        except Exception:
            pass
    exe = which("yt-dlp")
    if not exe:
        return []
    log("Downloading the chat replay (long streams take a while)...")
    cmd = [exe, "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-warnings", "--ignore-config",
           "-o", os.path.join(workdir, "chat_yt.%(ext)s")]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    try:
        subprocess.run(cmd + [url], capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL,
                       encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except Exception:
        return []
    for f in os.listdir(workdir):
        if f.startswith("chat_yt") and f.endswith(".json"):
            shutil.move(os.path.join(workdir, f), out)
            try:
                chat = load_chat(out)
            except Exception:
                return []
            log(f"Got {len(chat):,} chat messages.")
            return chat
    return []


def _download_video(url: str, vod_id: Optional[str], workdir: str, max_height: int,
                    cookies_browser: str, log: Log, progress: Progress, step: Log, check: Check,
                    cookie_file: str = ""):
    import locations
    dl = locations.download_dir(workdir)              # Settings › Storage › Downloaded videos
    os.makedirs(dl, exist_ok=True)
    existing = _video_in(dl) or _video_in(workdir)
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
            return _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file, out_dir=dl)
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
            return _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file, out_dir=dl)
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
        out = os.path.join(dl, "vod.mp4")
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
        video = _video_in(dl)
        if p.returncode == 0 and video:
            title = f"Twitch VOD {vod_id}"
            _save_meta(workdir, title=title)
            progress(1.0)
            return video, title
        err = (err or "") + "\nTwitchDownloader: " + " / ".join(t for t in tail if t)[-300:]

    if not vod_id and "twitch.tv" not in url:
        raise RuntimeError(err or "yt-dlp is not installed")     # explained by sources.explain in acquire
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
        self.frac = 0.0

    def update(self, done, total, frag_i, frag_n, speed, eta):
        frac = None
        if total:
            frac = done / total if done is not None else None
        elif frag_n:
            frac = (frag_i or 0) / frag_n
        if frac is not None:
            self.frac = frac
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


def _finish_download(workdir: str, title: str, duration, progress: Progress, log: Log, streamer: str = "", out_dir: str = ""):
    _save_meta(workdir, title=title, duration=duration or 0, **({"streamer": streamer} if streamer else {}))
    video = _video_in(out_dir or workdir)
    if not video:
        raise RuntimeError("the download finished but no video file was found")
    progress(1.0)
    log(f"Downloaded: {title}")
    return video, title


def _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file="", out_dir=""):
    out_dir = out_dir or workdir
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    cmd = [exe, "--newline", "--progress", "--no-colors", "--no-simulate", "--ignore-config",
           "--print", "before_dl:VCINFO %(.{title,is_live,duration,uploader_id,uploader})j",
           "--progress-template",
           "download:VCPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
           "%(progress.total_bytes_estimate)s %(progress.fragment_index)s "
           "%(progress.fragment_count)s %(progress.speed)s %(progress.eta)s",
           "-f", fmt, "-o", os.path.join(out_dir, "vod.%(ext)s"), "--merge-output-format", "mp4",
           "-N", "8", "--socket-timeout", "30", "--retries", "10", "--fragment-retries", "20",
           "--match-filters", "!is_live"]
    ff = which("ffmpeg")
    if ff:
        cmd += ["--ffmpeg-location", os.path.dirname(ff)]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd.append(url)
    log("Downloading the VOD. Long streams take a while.")
    st = _Stepper(progress, step, log)
    info = {}
    errors, tail = [], []
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
                         **({} if os.name == "nt" else {"start_new_session": True}))
    last_output = [time.monotonic()]
    last_line = [time.monotonic()]
    stalled = [False]

    def watch_download():
        # yt-dlp normally prints progress often, but after 100% it can go quiet for minutes while it
        # joins or remuxes a long VOD (an 18 GB, 6-hour stream: about 3 minutes). A download file that
        # is still growing counts as progress; only real silence is a stall. A stalled download is
        # stopped with its whole process tree: yt-dlp.exe is a launcher, and killing only it left the
        # real download running in the background.
        size, told = -1, False
        while p.poll() is None:
            now = _download_bytes(out_dir)
            if now != size:
                if size >= 0:
                    last_output[0] = time.monotonic()
                size = now
            if not told and st.frac >= 0.99 and time.monotonic() - last_line[0] > 5:
                told = True
                step("Putting the video together (a few minutes for a long stream)")
                log("Download finished; joining it into one video.")
            if time.monotonic() - last_output[0] > DOWNLOAD_STALL_S:
                stalled[0] = True
                kill_tree(p)
                return
            time.sleep(2)

    threading.Thread(target=watch_download, daemon=True).start()
    try:
        for line in p.stdout:
            last_output[0] = last_line[0] = time.monotonic()
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
                kill_tree(p)
                raise
    finally:
        p.wait()
    if stalled[0]:
        raise RuntimeError(f"The VOD download stopped for {DOWNLOAD_STALL_S // 60} minutes with nothing written. "
                           "Check your connection and retry.")
    if p.returncode != 0 or not _video_in(out_dir):
        if info.get("is_live") or any("is_live" in t for t in tail):
            raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
        raise RuntimeError(errors[-1] if errors else (tail[-1] if tail else f"yt-dlp exited with code {p.returncode}"))
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log,
                            str(info.get("uploader_id") or info.get("uploader") or "").lower(), out_dir)


DOWNLOAD_STALL_S = 180       # no output and no file growth for this long: the download is stuck


def _download_bytes(folder: str) -> int:
    """Bytes in the download's files so far (vod.*: parts, fragments, the file being joined)."""
    total = 0
    try:
        for f in os.listdir(folder):
            if f.startswith("vod.") or f.startswith("vod-"):
                try:
                    total += os.path.getsize(os.path.join(folder, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def kill_tree(p: subprocess.Popen):
    """Stop a process and everything it started (yt-dlp.exe is a launcher; ffmpeg runs under it)."""
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        p.kill()
    except OSError:
        pass


def _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file="", out_dir=""):
    out_dir = out_dir or workdir
    st = _Stepper(progress, step, log)

    def hook(d):
        check()
        if d.get("status") == "downloading":
            st.update(d.get("downloaded_bytes"), d.get("total_bytes") or d.get("total_bytes_estimate"),
                      d.get("fragment_index"), d.get("fragment_count"), d.get("speed"), d.get("eta"))

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(out_dir, "vod.%(ext)s"),
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 8,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 10,
        "extractor_retries": 3,
        "fragment_retries": 20,
        "file_access_retries": 3,
        "continuedl": True,
        "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
    }
    ff = which("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = os.path.dirname(ff)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    log("Downloading the VOD. Long streams take a while.")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    if info.get("is_live"):
        raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log,
                            str(info.get("uploader_id") or info.get("uploader") or "").lower(), out_dir)


def _clean_ytdlp_error(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = s.replace("ERROR: ", "").strip()
    return s.splitlines()[0][:300] if s else "unknown error"


def _download_chat(url: str, vod_id: Optional[str], workdir: str, log: Log, info: Optional[dict] = None) -> list:
    out = os.path.join(workdir, "chat.json")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        try:
            chat = load_chat(out, info)
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
            chat = load_chat(out, info)
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
                chat = load_chat(out, info)
                log(f"Got {len(chat):,} chat messages.")
                return chat
    except Exception:
        pass

    log("No chat replay available, ranking by audio only. "
        "(Run install.bat to add TwitchDownloaderCLI for chat.)")
    return []


def _sidecar_chat(video: str, workdir: str, log: Log, info: Optional[dict] = None) -> list:
    base = os.path.splitext(video)[0]
    for cand in (base + ".chat.json", base + "_chat.json", base + ".json",
                 os.path.join(os.path.dirname(video), "chat.json"),
                 os.path.join(workdir, "chat.json")):
        if os.path.exists(cand):
            try:
                chat = load_chat(cand, info)
            except Exception:
                continue
            log(f"Found chat file {os.path.basename(cand)} ({len(chat):,} messages).")
            return chat
    log("No chat file given, ranking by audio only.")
    return []


def load_chat(path: str, info: Optional[dict] = None) -> list:
    """Parse TwitchDownloader / yt-dlp rechat / generic chat JSON into [(sec, text, user, kind)].
    kind is "" for normal chat, or the Twitch notice type ("sub", "subgift", "raid", "bits"...).
    If info is given, it gets the streamer's channel name when the file has it."""
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
        kind = ""
        if isinstance(msg, dict):
            notice = msg.get("user_notice_params") or {}
            if isinstance(notice, dict) and notice.get("msg-id"):
                kind = str(notice.get("msg-id"))
            elif (msg.get("bits_spent") or 0) > 0:
                kind = "bits"
            msg = msg.get("body") or " ".join(
                fr.get("text", "") for fr in msg.get("fragments", []) or [] if isinstance(fr, dict))
        who = c.get("commenter") or c.get("author") or c.get("user") or c.get("name") or ""
        if isinstance(who, dict):
            who = who.get("name") or who.get("login") or who.get("display_name") or ""
        try:
            msgs.append((float(t), str(msg or ""), str(who).lower(), kind))
        except (TypeError, ValueError):
            pass

    try:
        data = json.loads(raw)
        items = data.get("comments", data) if isinstance(data, dict) else data
        if isinstance(data, dict) and info is not None:
            s = data.get("streamer") or {}
            if isinstance(s, dict) and s.get("name"):
                info["streamer"] = str(s["name"]).lower()
            if isinstance(s, dict) and s.get("id"):
                info["streamer_id"] = str(s["id"])
        for c in items or []:
            take(c)
    except json.JSONDecodeError:
        for line in raw.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and "replayChatItemAction" in obj:
                msgs.extend(_yt_chat(obj))          # YouTube chat replay (yt-dlp live_chat)
            else:
                take(obj)
    msgs.sort(key=lambda m: m[0])
    return msgs


def _yt_chat(obj: dict) -> list:
    """Messages from one line of a YouTube chat replay (yt-dlp's live_chat.json)."""
    r = obj.get("replayChatItemAction") or {}
    try:
        # a replay has the offset inside; yt-dlp's live-chat recording puts it next to it
        t = float(r.get("videoOffsetTimeMsec") or obj.get("videoOffsetTimeMsec") or 0) / 1000
    except (TypeError, ValueError):
        return []
    out = []
    for a in r.get("actions") or []:
        item = ((a or {}).get("addChatItemAction") or {}).get("item") or {}
        for key, kind in (("liveChatTextMessageRenderer", ""), ("liveChatPaidMessageRenderer", "superchat"),
                          ("liveChatMembershipItemRenderer", "sub"), ("liveChatSponsorshipsGiftPurchaseAnnouncementRenderer", "subgift")):
            m = item.get(key)
            if not isinstance(m, dict):
                continue
            runs = ((m.get("message") or m.get("headerSubtext") or {}).get("runs")) or []
            text = "".join(x.get("text") or _yt_emoji(x.get("emoji") or {}) for x in runs if isinstance(x, dict))
            who = ((m.get("authorName") or {}).get("simpleText")) or ""
            out.append((t, text, str(who).lower(), kind))
    return out


def _yt_emoji(e: dict) -> str:
    """A YouTube chat emoji as text: the real character for standard emoji (so 😂 counts as a laugh,
    like on Twitch), the name for a channel's own emoji (":_hahaLaugh:" -> "hahaLaugh")."""
    if not isinstance(e, dict):
        return ""
    if not e.get("isCustomEmoji") and e.get("emojiId") and len(str(e["emojiId"])) <= 8:
        return str(e["emojiId"])
    sc = (e.get("shortcuts") or [""])[0] or ""
    name = sc.strip(":").lstrip("_")
    return f" {name} " if name else ""


def chat_times(chat: list) -> list:
    return [m[0] for m in chat]


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
    "wideeyes": 1.2,
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


FINE_HZ = 10        # loudness is also kept every 0.1 s, to find pauses between sentences


def measure_audio(video: str, duration: float, events: bool = False, progress: Progress = _noop,
                  check: Check = _noop, log: Log = _noop) -> tuple:
    """One streamed pass over the audio. Returns (dB per second, dB per 0.1 s, sound events or None).
    Sound events (laughter, screams, cheering) need the bundled detector; without it they're skipped."""
    import audio_events
    use_ev = bool(events) and audio_events.available()
    sr = audio_events.SR if use_ev else 8000
    fmt, bps = ("f32le", 4) if use_ev else ("s16le", 2)
    n = int(math.ceil(duration)) + 1
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-i", video, "-map", "0:a:0", "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(sr), "-f", fmt, "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    blk = sr // FINE_HZ
    pows: list = []
    rest = np.zeros(0, dtype=np.float32)
    scanner = audio_events.Scanner() if use_ev else None
    got = 0
    ev_failed = False
    try:
        while True:
            chunk = p.stdout.read(sr * bps * 30)
            if not chunk:
                break
            check()
            chunk = chunk[: len(chunk) // bps * bps]
            if use_ev:
                arr = np.frombuffer(chunk, dtype=np.float32)
            else:
                arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            got += arr.size
            if scanner is not None and not ev_failed:
                try:
                    scanner.feed(arr)
                except Exception as e:          # never let the extra detector break the analysis
                    ev_failed = True
                    log(f"Laughter detection stopped: {str(e)[:120]}")
            buf = np.concatenate([rest, arr])
            whole = buf.size // blk
            if whole:
                b = buf[: whole * blk].reshape(whole, blk)
                pows.append(np.mean(b * b, axis=1))
            rest = buf[whole * blk:]
            if duration:
                progress(min(0.999, got / sr / duration))
        if scanner is not None and not ev_failed:
            try:
                scanner.feed(np.zeros(0, dtype=np.float32), final=True)
            except Exception as e:
                ev_failed = True
                log(f"Laughter detection stopped: {str(e)[:120]}")
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    pw = np.concatenate(pows) if pows else np.zeros(0, dtype=np.float32)
    fine = np.full(n * FINE_HZ, -100.0, dtype=np.float32)
    k = min(fine.size, pw.size)
    fine[:k] = 10 * np.log10(np.maximum(pw[:k], 1e-10))
    sec_pow = np.zeros(n, dtype=np.float64)
    full = pw.size // FINE_HZ
    if full:
        m = min(n, full)
        sec_pow[:m] = pw[: m * FINE_HZ].reshape(m, FINE_HZ).mean(axis=1)
    loud = np.where(sec_pow > 0, 10 * np.log10(np.maximum(sec_pow, 1e-10)), -90.0).astype(np.float32)
    progress(1.0)
    ev = scanner.result(n) if (scanner is not None and not ev_failed) else None
    return loud, fine, ev


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


def _fit(a, n: int, fill: float = 0.0) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)[:n]
    return np.pad(a, (0, max(0, n - len(a))), constant_values=fill)


# how much a clear laugh / scream / cheer adds to a second's score (scores are roughly z-scores)
EVENT_WEIGHTS = {"laugh": 4.0, "scream": 3.0, "cheer": 2.0}


@dataclass
class Analysis:
    duration: float
    chat_z: np.ndarray
    audio_z: np.ndarray
    has_chat: bool
    has_audio: bool
    score: Optional[np.ndarray] = None
    chat_weight_pct: float = 60.0
    events: Optional[dict] = None          # per-second probabilities: laugh, scream, cheer, speech, music
    loud: Optional[np.ndarray] = None      # dB per second
    fine: Optional[np.ndarray] = None      # dB per 0.1 s (pauses between sentences)
    chat_stats: Optional[dict] = None
    speech: Optional[np.ndarray] = None    # deep scan: how quotable each second is
    # 3.5: extra signals (detect.enrich) and the stream-type weights they're mixed with
    weights: Optional[dict] = None
    chat_delay: float = 6.0
    delay_sure: bool = False
    surge: Optional[np.ndarray] = None
    emote_z: Optional[np.ndarray] = None
    emote_rate: Optional[np.ndarray] = None
    react: Optional[np.ndarray] = None
    noise: Optional[np.ndarray] = None
    # 4.9: YouTube videos (ytextra): Most replayed and the moments comments point at, 0..1 per second
    replay: Optional[np.ndarray] = None
    comments: Optional[np.ndarray] = None
    comment_people: Optional[np.ndarray] = None
    chapters: Optional[list] = None

    @property
    def has_events(self) -> bool:
        return bool(self.events) and "laugh" in self.events

    def event_level(self, key: str) -> np.ndarray:
        """How much more than usual this stream is laughing (screaming, cheering) each second, 0..1."""
        n = len(self.audio_z)
        if not self.has_events or key not in self.events:
            return np.zeros(n, dtype=np.float32)
        cache = self.__dict__.setdefault("_levels", {})
        if key not in cache:
            x = _rolling(_fit(self.events[key], n), 3, "mean")
            base = float(np.median(x)) if len(x) else 0.0
            cache[key] = np.clip(x - base - 0.05, 0, 1).astype(np.float32)
        return cache[key]

    def combine(self, chat_weight_pct: float, weights: Optional[dict] = None) -> np.ndarray:
        """Mix chat, loudness, laughter/screams and the extra signals (detect.py) with the stream
        type's weights. Cheap, so the UI can re-rank instantly."""
        if weights is not None:
            self.weights = weights
        w = self.weights or {}
        wc = (chat_weight_pct / 100.0) if self.has_chat else 0.0
        if not self.has_audio:
            wc = 1.0 if self.has_chat else 0.0
        audio = self.audio_z
        if self.has_events and "music" in self.events:
            # a loud song isn't a moment: play down loudness while music is playing
            music = np.clip(_rolling(_fit(self.events["music"], len(audio)), 5, "mean") - 0.5, 0, 0.5) * 2
            audio = audio * (1 - 0.5 * music)
        parts = {"chat": wc * self.chat_z, "audio": (1 - wc) * audio}
        if self.has_events:
            for k in EVENT_WEIGHTS:
                parts[k] = float(w.get(k, EVENT_WEIGHTS[k])) * self.event_level(k)
        n = len(self.audio_z)
        for k, arr in (("emotes", self.emote_z), ("surge", self.surge), ("react", self.react)):
            if arr is not None and float(w.get(k, 0)) > 0:
                parts[k] = float(w[k]) * _fit(arr, n)
        import ytextra
        for k, arr, gain in (("replay", self.replay, ytextra.REPLAY_GAIN), ("comments", self.comments, ytextra.COMMENT_GAIN)):
            if arr is not None:
                parts[k] = float(w.get(k, 1.0)) * gain * _fit(arr, n)
        s = sum(parts.values())
        if self.noise is not None:
            s = np.where(s > 0, s * _fit(self.noise, n, 1.0), s)
        self.parts = parts
        self.score = _rolling(s, 5, "mean")
        self.chat_weight_pct = chat_weight_pct
        return self.score

    def part(self, a: int, b: int) -> "Analysis":
        """The same analysis for seconds a..b (live moments are small projects of their own)."""
        cut = lambda x: None if x is None else np.asarray(x)[a:b].copy()  # noqa: E731
        sub = Analysis(float(b - a), self.chat_z[a:b].copy(), self.audio_z[a:b].copy(), self.has_chat,
                       self.has_audio, events={k: cut(v) for k, v in self.events.items()} if self.events else None,
                       loud=cut(self.loud), fine=None if self.fine is None else self.fine[a * FINE_HZ:b * FINE_HZ].copy(),
                       chat_stats=self.chat_stats, weights=self.weights, chat_delay=self.chat_delay,
                       surge=cut(self.surge), emote_z=cut(self.emote_z), emote_rate=cut(self.emote_rate),
                       react=cut(self.react), noise=cut(self.noise), replay=cut(self.replay),
                       comments=cut(self.comments), comment_people=cut(self.comment_people), chapters=self.chapters)
        sub.combine(self.chat_weight_pct)
        return sub


def analyze(video: str, chat: list, chat_delay: float = 6.0, chat_weight_pct: float = 60,
            log: Log = _noop, progress: Progress = _noop, check: Check = _noop,
            events: bool = True) -> Analysis:
    info = probe(video)
    dur = info["duration"]
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length. The file may be incomplete or damaged.")
    n = int(math.ceil(dur)) + 1
    ev = fine = None
    if info["has_audio"]:
        import audio_events
        with_ev = events and audio_events.available()
        log(f"VOD length {fmt_ts(dur)}. Listening for loud moments" + (", laughter and screams..." if with_ev else "..."))
        loud, fine, ev = measure_audio(video, dur, with_ev, progress, check, log)
        loud = _fit(loud, n, -90.0)
        if loud is not None and (loud < -75).mean() > 0.2:
            log(f"{int((loud < -75).mean() * 100)}% of the audio is silent or muted.")
    else:
        log(f"VOD length {fmt_ts(dur)}. No audio track found, ranking by chat only.")
        loud = None
    if len(chat) > 20:
        log(f"Reading {len(chat):,} chat messages...")
    rate = stats = None
    used_delay = 6.0 if chat_delay == "auto" else float(chat_delay)
    sure = chat_delay != "auto"
    if chat_delay == "auto" and chat:
        # how late chat reacts: compare when chat surges with when the stream got loud or laughed
        import detect
        rate0, sc = chat_rate(chat, n, 0.0)
        stats = sc.summary()
        az = _audio_z(loud, n) if loud is not None else np.zeros(n, dtype=np.float32)
        used_delay, sure = detect.estimate_delay(rate0, az, ev)
        log(f"Chat reacts about {used_delay:.0f} s after the stream." if sure
            else "Couldn't tell how late chat reacts; assuming 6 s.")
        rate = detect.shift(rate0, used_delay)
    an = score_signals(loud, chat, n, dur, used_delay, chat_weight_pct, rate=rate, stats=stats, events=ev, fine=fine)
    an.chat_delay = used_delay
    an.delay_sure = bool(sure)
    st = an.chat_stats or {}
    if st.get("messages"):
        bits = [f"{st['chatters']:,} chatters"] if st.get("chatters") else []
        if st.get("ignored"):
            bits.append(f"{st['ignored']:,} bot, command and sub messages ignored")
        if st.get("raids"):
            bits.append(f"{st['raids']} raid{'s' if st['raids'] > 1 else ''} played down")
        if bits:
            log("Chat: " + ", ".join(bits) + ".")
    progress(1.0)
    return an


# Bump when anything that feeds the analysis changes (chat scoring, loudness, detector use),
# so a cached analysis.npz from an older version is thrown away instead of reused.
ANALYSIS_VERSION = 2          # 4.9: YouTube chat emoji are read as emoji


def _analysis_key(video: str, chat: list, chat_delay: float, events: bool) -> Optional[str]:
    try:
        st = os.stat(video)
        import audio_events
        with_ev = bool(events and audio_events.available())
        return json.dumps([ANALYSIS_VERSION, st.st_size, int(st.st_mtime), len(chat),
                           round(chat[-1][0], 2) if chat else 0,
                           chat_delay if chat_delay == "auto" else round(float(chat_delay), 2), with_ev,
                           audio_events.MODEL_SHA256 if with_ev else ""])
    except Exception:
        return None


def _save_analysis(path: str, key: str, an: Analysis):
    arrays = {"chat_z": np.asarray(an.chat_z, dtype=np.float32), "audio_z": np.asarray(an.audio_z, dtype=np.float32)}
    for name in ("loud", "fine"):
        v = getattr(an, name)
        if v is not None:
            arrays[name] = np.asarray(v, dtype=np.float32)
    for k, v in (an.events or {}).items():
        arrays["ev_" + k] = np.asarray(v, dtype=np.float32)
    meta = {"key": key, "duration": an.duration, "has_chat": an.has_chat, "has_audio": an.has_audio,
            "chat_stats": an.chat_stats, "events": sorted((an.events or {}).keys()), "chat_delay": an.chat_delay,
            "delay_sure": bool(getattr(an, "delay_sure", False))}
    arrays["meta"] = np.array(json.dumps(meta, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp = path[:-4] + f".{os.getpid()}.tmp.npz"
    np.savez(tmp, **arrays)
    os.replace(tmp, path)


def _load_analysis(path: str, key: str) -> Optional[Analysis]:
    with np.load(path, allow_pickle=False) as f:
        meta = json.loads(str(f["meta"]))
        if meta.get("key") != key:
            return None
        get = lambda n: f[n].copy() if n in f.files else None  # noqa: E731
        events = {k: f["ev_" + k].copy() for k in meta.get("events", [])} or None
        return Analysis(float(meta["duration"]), f["chat_z"].copy(), f["audio_z"].copy(), bool(meta["has_chat"]),
                        bool(meta["has_audio"]), events=events, loud=get("loud"), fine=get("fine"),
                        chat_stats=meta.get("chat_stats"), chat_delay=float(meta.get("chat_delay", 6.0)),
                        delay_sure=bool(meta.get("delay_sure")))


def analyze_cached(workdir: str, video: str, chat: list, chat_delay=6.0, chat_weight_pct: float = 60,
                   log: Log = _noop, progress: Progress = _noop, check: Check = _noop,
                   events: bool = True, emote_set=None, weights: Optional[dict] = None) -> Analysis:
    """analyze(), but a VOD you've opened before is ready instantly (cached in workdir/analysis.npz)."""
    path = os.path.join(workdir, "analysis.npz")
    key = _analysis_key(video, chat, chat_delay, events)
    if key and os.path.exists(path):
        try:
            an = _load_analysis(path, key)
        except Exception:
            an = None
        if an is not None:
            log("Opened the analysis from last time.")
            progress(1.0)
            return _finish(an, chat, emote_set, weights, chat_weight_pct, workdir)
    an = analyze(video, chat, chat_delay=chat_delay, chat_weight_pct=chat_weight_pct, log=log,
                 progress=progress, check=check, events=events)
    if key:
        try:
            _save_analysis(path, key, an)
        except Exception as e:
            log(f"Couldn't save the analysis for next time ({str(e)[:80]}).")
    return _finish(an, chat, emote_set, weights, chat_weight_pct, workdir)


def _finish(an: Analysis, chat: list, emote_set, weights: Optional[dict], chat_weight_pct: float,
            workdir: str = "") -> Analysis:
    import detect
    import ytextra
    detect.enrich(an, chat, an.chat_delay, emote_set)
    if workdir:
        ytextra.apply(an, ytextra.load(workdir))       # a YouTube video: Most replayed and comments
    an._emotes = emote_set
    an.combine(chat_weight_pct, weights or detect.preset("any"))
    return an


def chat_rate(chat: list, n: int, chat_delay: float, scorer=None) -> tuple:
    """(reaction rate per second, scorer). Chat reacts a few seconds late, so it's shifted back."""
    import chatsignal
    sc = scorer or chatsignal.ChatScorer(chat_weight)
    rate = np.zeros(n, dtype=np.float32)
    if chat:
        w = np.asarray(sc.feed(chat), dtype=np.float32)
        idx = (np.asarray([m[0] for m in chat], dtype=np.float64) - chat_delay).astype(np.int64)
        ok = (idx >= 0) & (idx < n) & (w > 0)
        np.add.at(rate, idx[ok], w[ok])
    return rate, sc


def _audio_z(loud: np.ndarray, n: int) -> np.ndarray:
    """How much louder each second is than the last few minutes (robust z-score)."""
    loud = _fit(loud, n, -90.0)
    # Twitch mutes DMCA'd parts: treat dead silence as "no info" rather than "boring"
    muted = loud < -75
    short = _rolling(loud, 4, "mean")
    base = _rolling(np.where(muted, np.median(loud[~muted]) if (~muted).any() else -60, loud), 180, "median")
    audio = np.clip(_robust_z(short - base), -3, 8)
    audio[muted] = np.minimum(audio[muted], 0)
    return audio


def score_signals(loud: Optional[np.ndarray], chat: list, n: int, duration: float,
                  chat_delay: float = 6.0, chat_weight_pct: float = 60, rate: Optional[np.ndarray] = None,
                  stats: Optional[dict] = None, events: Optional[dict] = None,
                  fine: Optional[np.ndarray] = None) -> "Analysis":
    """Per-second hype score from loudness (dB per second), chat and sound events. Shared by VODs and live.
    rate: chat reaction per second already weighed (live mode keeps it up to date as chat arrives)."""
    if loud is not None and len(loud):
        loud = _fit(loud, n, -90.0)
        audio = _audio_z(loud, n)
        has_audio = True
    else:
        audio = np.zeros(n, dtype=np.float32)
        has_audio = False

    if rate is None:
        rate, sc = chat_rate(chat, n, chat_delay)
        stats = sc.summary()
    rate = _fit(rate, n)
    has_chat = float((rate > 0).sum()) > 20
    if has_chat:
        short = _rolling(rate, 8, "mean")
        base = _rolling(rate, 300, "median")
        # relative surge matters more than raw volume (works for small and big streams)
        surge = (short - base) / (np.sqrt(np.maximum(base, 0)) + 1.0)
        chat_z = np.clip(_robust_z(surge), -3, 10)
    else:
        chat_z = np.zeros(n, dtype=np.float32)

    ev = {k: _fit(v, n) for k, v in events.items()} if events else None
    an = Analysis(duration, chat_z, audio, has_chat, has_audio, events=ev, loud=loud,
                  fine=None if fine is None else _fit(fine, n * FINE_HZ, -100.0), chat_stats=stats)
    an.combine(chat_weight_pct)
    return an


# --------------------------------------------------------------------------
# 3. pick clips
# --------------------------------------------------------------------------

MIN_LEN, MAX_LEN = 15.0, 90.0
STRONG, GOOD = 3.0, 2.0


@dataclass
class Clip:
    id: int
    start: float
    end: float
    peak: float
    score: float
    reason: str = ""
    feat: Optional[dict] = None       # what the signals looked like (for learning what you keep)
    tuned: float = 0.0                # how much learning moved this clip up or down
    quality: int = 0                  # 0-100 (detect.quality)
    parts: Optional[dict] = None      # points each signal added (detect.breakdown)
    trigger: Optional[float] = None   # what a late chat reaction was reacting to
    merged: int = 0                   # near-duplicate detections folded into this clip


def estimate(an: Analysis, sensitivity: str = "normal") -> dict:
    """How many distinct strong / decent moments the stream has (peaks at least 45 s apart),
    and how many clips to suggest for its length."""
    s = an.score
    n = len(s)
    hours = an.duration / 3600.0
    strong = good = 0
    if n >= 5:
        order = np.argsort(-s, kind="stable")
        blocked = np.zeros(n, dtype=bool)
        edge = min(60, int(n * 0.03))
        for i in order:
            v = float(s[i])
            if v < GOOD:
                break
            if blocked[i] or i < edge or i > n - 1 - edge:
                continue
            blocked[max(0, i - 45): i + 46] = True
            good += 1
            if v >= STRONG:
                strong += 1
    base = strong + (good - strong) // 2
    suggest = int(min(60, max(5, base, round(3 * hours))))
    import detect
    sens = detect.SENSITIVITY.get(sensitivity, detect.SENSITIVITY["normal"])
    suggest = int(min(100, max(3, round(suggest * sens["count"]))))
    return {"strong": strong, "good": good, "hours": round(hours, 2), "suggest": suggest}


def _snap(fine: Optional[np.ndarray], t: float, before: float, after: float) -> float:
    """Move a cut to the quietest moment nearby (a breath between sentences)."""
    if fine is None or not len(fine):
        return t
    a = max(0, int((t - before) * FINE_HZ))
    b = min(len(fine), int((t + after) * FINE_HZ) + 1)
    if b - a < 2:
        return t
    seg = fine[a:b].astype(np.float64)
    offs = np.abs(np.arange(a, b) / FINE_HZ - t)
    k = int(np.argmin(seg + 1.5 * offs))           # quiet, but don't wander far
    return (a + k) / FINE_HZ + 0.05


def smart_bounds(an: Analysis, peak: float, avail: Optional[int] = None,
                 min_len: float = MIN_LEN, max_len: float = MAX_LEN, final: bool = False,
                 trigger: Optional[float] = None) -> tuple:
    """Where a clip around a peak should start and end: back far enough to show what led up to it,
    on until the reaction has died down, 15 to 90 seconds. Returns (start, end, complete); complete is
    False while live mode hasn't seen the reaction end yet."""
    s = an.score
    n = len(s) if avail is None else min(len(s), int(avail))
    dur = min(an.duration, float(n))
    p = int(min(max(0, peak), n - 1))
    top = float(s[p])
    calm = max(0.8, 0.35 * top)            # the reaction is over below this
    calm_back = max(0.6, 0.25 * top)       # and it hadn't started yet below this
    # build-up: walk back to where things were calm for 3 seconds
    lim = max(0, p - 60)
    build = lim
    quiet = 0
    for i in range(p - 1, lim - 1, -1):
        if s[i] < calm_back:
            quiet += 1
            if quiet >= 3:
                build = i + 3
                break
        else:
            quiet = 0
    start = min(build - 7.0, p - 6.0)               # a little setup before it kicks off
    start = max(start, p - 55.0, 0.0)
    keep_from = p - 8.0
    if trigger is not None and trigger < p:
        # chat reacted late: start before what it reacted to
        start = max(0.0, min(start, trigger - 6.0))
        keep_from = min(keep_from, trigger - 3.0)
    # reaction: walk forward until it has been calm for 4 seconds
    end = None
    quiet = 0
    for j in range(p + 1, min(n, p + 46)):
        if s[j] < calm:
            quiet += 1
            if quiet >= 4:
                end = j - 4 + 2.5
                break
        else:
            quiet = 0
    complete = end is not None or avail is None or final or n >= p + 46
    if end is None:
        end = min(float(n), p + 45.0)
    end = max(end, p + 5.0)
    # length rules: longer clips trim the setup first, short ones grow mostly backwards
    L = end - start
    if L < min_len:
        need = min_len - L
        start -= need * 0.6
        end += need * 0.4
    elif L > max_len:
        cut = L - max_len
        room = max(0.0, keep_from - start)
        start += min(cut, room)
        end -= max(0.0, cut - room)
    if start < 0:
        end -= start
        start = 0.0
    if end > dur:
        start = max(0.0, start - (end - dur))
        end = dur
    s2, e2 = _snap(an.fine, start, 1.5, 0.8), _snap(an.fine, end, 0.8, 1.5)
    if min_len - 0.05 <= e2 - s2 <= max_len + 0.05 or not (min_len - 0.05 <= end - start <= max_len + 0.05):
        start, end = s2, e2                      # moving to a pause mustn't break the clip lengths you set
    if avail is not None and not final and end > n - 0.5:
        complete = False                         # live: the end hasn't been recorded yet
    start, end = max(0.0, start), min(dur, end)
    return round(start, 2), round(end, 2), bool(complete)


def fixed_bounds(an: Analysis, peak: float, length: float, lead: float = 0.6) -> tuple:
    length = min(length, max(5.0, an.duration))
    start = max(0.0, peak - length * lead)
    end = min(an.duration, start + length)
    start = max(0.0, end - length)
    if an.fine is not None:
        s2, e2 = _snap(an.fine, start, 1.0, 1.0), _snap(an.fine, end, 1.0, 1.0)
        start, end = max(0.0, s2), min(an.duration, e2)
    return round(start, 2), round(end, 2)


def _ends_sentence(w: dict) -> bool:
    return w["word"][-1:] in ".?!"


def refine_with_words(start: float, end: float, peak: float, words: list,
                      min_len: float = MIN_LEN, max_len: float = MAX_LEN, max_move: float = 8.0) -> tuple:
    """Never start or stop in the middle of a sentence. words: transcript with absolute times."""
    ws = [w for w in words if w.get("word")]
    if not ws:
        return start, end
    gap = 0.6
    # ---- start
    k = next((i for i, w in enumerate(ws) if w["end"] > start + 0.05), None)
    if k is not None and ws[k]["start"] < end:
        prev = ws[k - 1] if k > 0 else None
        mid_word = ws[k]["start"] < start - 0.05
        joined = prev is not None and ws[k]["start"] - prev["end"] < gap and not _ends_sentence(prev)
        if mid_word or joined:
            j = k
            while j > 0 and ws[j]["start"] - ws[j - 1]["end"] < gap and not _ends_sentence(ws[j - 1]):
                j -= 1
            back = ws[j]["start"] - 0.2
            if start - back <= max_move and end - back <= max_len + 5:
                start = back
            else:
                # too far back: start at the next sentence instead
                j = k
                while j + 1 < len(ws) and not (ws[j + 1]["start"] - ws[j]["end"] >= gap or _ends_sentence(ws[j])):
                    j += 1
                if j + 1 < len(ws) and ws[j + 1]["start"] - 0.2 < peak - 3:
                    start = ws[j + 1]["start"] - 0.2
        elif ws[k]["start"] - start > 1.2:
            start = max(start, ws[k]["start"] - 0.6)      # don't open on a long silence
    # ---- end
    inside = [i for i, w in enumerate(ws) if w["start"] < end - 0.05]
    if inside:
        k = inside[-1]
        nxt = ws[k + 1] if k + 1 < len(ws) else None
        mid_word = ws[k]["end"] > end + 0.05
        joined = nxt is not None and nxt["start"] - ws[k]["end"] < gap and not _ends_sentence(ws[k])
        if mid_word or joined:
            j = k
            while j + 1 < len(ws) and ws[j + 1]["start"] - ws[j]["end"] < gap and not _ends_sentence(ws[j]):
                j += 1
            fwd = ws[j]["end"] + 0.35
            if fwd - end <= max_move * 0.75 and fwd - start <= max_len + 5:
                end = fwd
            else:
                j = k
                while j > 0 and not (_ends_sentence(ws[j - 1]) or ws[j]["start"] - ws[j - 1]["end"] >= gap):
                    j -= 1
                if j > 0 and ws[j - 1]["end"] + 0.35 > peak + 2:
                    end = ws[j - 1]["end"] + 0.35
    if end - start < min(min_len, 5.0):
        return start, end
    return round(max(0.0, start), 2), round(end, 2)


def clip_features(an: Analysis, start: float, end: float, peak: float, chat: Optional[list] = None,
                  chat_t: Optional[list] = None, delay: float = 6.0) -> dict:
    """What the signals looked like for a clip. Used to learn what you keep and skip."""
    n = len(an.score)
    i = int(min(max(0, peak), n - 1))
    a, b = max(0, int(start)), max(int(start) + 1, min(n, int(math.ceil(end))))
    w0, w1 = max(0, i - 3), min(n, i + 4)
    f = {
        "chat": float(an.chat_z[w0:w1].max()) if an.has_chat else 0.0,
        "audio": float(an.audio_z[w0:w1].max()) if an.has_audio else 0.0,
        "laugh": float(np.max(an.event_level("laugh")[a:b])) if an.has_events and b > a else 0.0,
        "scream": float(np.max(an.event_level("scream")[a:b])) if an.has_events and b > a else 0.0,
        "cheer": float(np.max(an.event_level("cheer")[a:b])) if an.has_events and b > a else 0.0,
        "speech": float(np.max(an.speech[a:b])) if an.speech is not None and len(an.speech) > a else 0.0,
        "emotes": float(an.emote_z[w0:w1].max()) if an.emote_z is not None else 0.0,
        "length": (end - start) / 60.0,
        "clipreq": 0.0,
        "pos": peak / max(1.0, an.duration),
    }
    if chat and chat_t is not None:
        lo = bisect.bisect_left(chat_t, peak + delay - 5)
        hi = bisect.bisect_right(chat_t, peak + delay + 15)
        f["clipreq"] = min(2.0, sum(1 for m in chat[lo:hi] if "clip" in m[1].lower()) / 5.0)
    return {k: round(v, 3) for k, v in f.items()}


def pick_clips(an: Analysis, count: int = 10, length: float = 0, lead: float = 0.6,
               chat: Optional[list] = None, chat_delay: float = 6.0, adjust=None,
               min_len: float = MIN_LEN, max_len: float = MAX_LEN, sensitivity: str = "normal",
               back: float = 25.0, offsets: tuple = (0.0, 0.0)) -> list:
    """The best non-overlapping moments. count 0 = as many as the stream deserves (see estimate);
    length 0 = each clip gets its own length (smart_bounds). adjust(features) -> score changes
    learned from what you kept and skipped before."""
    s = an.score
    n = len(s)
    if n < 5:
        return []
    import detect
    auto = count <= 0
    if auto:
        count = estimate(an, sensitivity)["suggest"]
    order = np.argsort(-s, kind="stable")
    top = float(s[order[0]])
    # automatic: only moments that really stand out; a number you chose gets filled with weaker ones
    # too, down to anything above the stream's normal level (fewer only when there aren't that many
    # separate moments; the caller says so)
    sens = detect.SENSITIVITY.get(sensitivity, detect.SENSITIVITY["normal"])
    floor = max(sens["floor"], 0.15 * top) if auto else 0.0
    skip_edge = min(60, int(n * 0.03))  # ignore "starting soon"/outro edges a bit
    chat_t = chat_times(chat) if chat else []
    # candidate peaks, a little apart from each other
    blocked = np.zeros(n, dtype=bool)
    cands = []
    want = max(count * 4, count + 10)
    for idx in order:
        if len(cands) >= want or s[idx] <= floor:
            break
        if idx < skip_edge or idx > n - 1 - skip_edge or blocked[idx]:
            continue
        cands.append(int(idx))
        blocked[max(0, idx - 12): idx + 13] = True
    items = []
    ds, de = offsets
    for idx in cands:
        trig = None
        if length and length > 0:
            st, en = fixed_bounds(an, idx, length, lead)
        else:
            trig = detect.find_trigger(an, idx, back)
            st, en, _ = smart_bounds(an, idx, None, min_len, max_len, trigger=trig)
            if ds or de:      # learned from how you trim this streamer's clips
                st2, en2 = max(0.0, st + ds), min(an.duration, en + de)
                if min_len <= en2 - st2 <= max_len and st2 < idx < en2:   # your lengths still apply
                    st, en = round(st2, 2), round(en2, 2)
        items.append({"peak": idx, "start": st, "end": en, "base": float(s[idx]), "trigger": trig,
                      "feat": clip_features(an, st, en, idx, chat, chat_t, chat_delay)})
    adj = [0.0] * len(items)
    if adjust is not None and items:
        try:
            adj = list(adjust([it["feat"] for it in items]))
        except Exception:
            adj = [0.0] * len(items)
    for it, a in zip(items, adj):
        it["tuned"] = float(a)
        it["rank"] = it["base"] + float(a)
    items.sort(key=lambda it: -it["rank"])
    clips: list = []
    smart = not (length and length > 0)
    for it in items:
        if len(clips) >= count:
            break
        st, en = it["start"], it["end"]
        near = next((c for c in clips if st < c.end + 4 and en > c.start - 4), None)
        if near is not None:
            # the same event seen twice (setup and reaction, or two waves of chat): one clip, not two
            if smart and max(en, near.end) - min(st, near.start) <= max_len:
                near.start, near.end = min(st, near.start), max(en, near.end)
                near.merged += 1
            continue
        clips.append(Clip(0, st, en, float(it["peak"]), round(it["rank"], 2),
                          _reason(an, it["peak"], chat, chat_t, chat_delay, it["trigger"]), it["feat"],
                          round(it["tuned"], 2), detect.quality(it["rank"]),
                          detect.breakdown(an, it["peak"], it["tuned"]), it["trigger"]))
    for i, c in enumerate(clips, 1):
        c.id = i
    return clips


def _reason(an: Analysis, i: int, chat: Optional[list], chat_t: list, delay: float,
            trigger: Optional[float] = None) -> str:
    i = int(min(max(0, i), len(an.audio_z) - 1))
    c = float(an.chat_z[i]) if an.has_chat else 0.0
    a = float(an.audio_z[i])
    parts = []
    if an.has_events:
        w0, w1 = max(0, i - 6), min(len(an.audio_z), i + 6)
        for key, label in (("laugh", "laughter"), ("scream", "screaming"), ("cheer", "cheering")):
            if float(np.max(an.event_level(key)[w0:w1])) > 0.3:
                parts.append(label)
                break
    if an.has_chat and c > 2:
        parts.append("chat explodes" if c > 5 else "chat spike")
    if a > 2 and not parts[:1] == ["screaming"]:
        parts.append("loud moment" if a > 4 else "volume jump")
    if an.react is not None and float(an.react[max(0, i - 3):i + 3].max()) > 0.8:
        parts.append("right after a quiet moment")
    if trigger is not None:
        parts.append(f"chat reacting to {max(1, int(round(i - trigger)))} s earlier")
    w0, w1 = max(0, i - 8), i + 8
    if an.replay is not None and float(np.max(an.replay[w0:w1], initial=0)) > 0.5:
        parts.append("most replayed" if float(np.max(an.replay[w0:w1])) > 0.85 else "often replayed")
    if an.comment_people is not None:
        k = int(np.max(an.comment_people[max(0, i - 15):i + 4], initial=0))
        if k >= 2:
            parts.append(f"{k} comments point here")
    if an.chapters:
        import ytextra
        ch = ytextra.chapter_at(an.chapters, float(i))
        if ch:
            parts.append(f"in chapter \u201c{ch[:40]}\u201d")
    es = getattr(an, "_emotes", None)
    if chat:
        import chatsignal
        lo = bisect.bisect_left(chat_t, i + delay - 4)
        hi = bisect.bisect_right(chat_t, i + delay + 14)
        words: dict = {}
        emoji = {"😂", "🤣", "💀", "🔥", "😱"}
        for m in chatsignal.human(chat[lo:hi]):
            for tok in set(re.findall(r"\w+|[😂🤣💀🔥😱]", m[1])):
                e = es.find(tok) if es else None
                if e:
                    words[e] = words.get(e, 0) + 1
                elif (len(tok) > 1 and tok.lower() in HYPE) or tok in emoji:
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
    layout: str = "split"           # split (cam + game) | full (whole frame) | blur | cam (facecam only)
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
    caption_anim: bool = True       # words pop in (kept for saved styles; caption_motion says how)
    caption_motion: str = ""        # none | pop | word | bounce | fade | slide | reveal ("" = pop if caption_anim)
    caption_speed: int = 100        # animation speed, percent (higher = quicker)
    caption_emph: bool = False      # strong words ("no way", "insane", shouting) in the highlight colour
    caption_filler: bool = True     # keep "um", "uh" in captions
    caption_punct: bool = True      # keep punctuation in captions
    caption_spacing: int = 100      # gap between a caption's two lines, percent of normal (100-200)
    caption_offset: int = 0         # move captions earlier (-) or later (+), milliseconds
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
    fps: int = 0                    # 0 = the source's own frame rate (up to 60); never raised above the source
    speed: str = "balanced"         # fast | balanced | high: encoder effort (quality_args)
    aspect: str = "9:16"            # 9:16 vertical | 1:1 square | 16:9 wide
    # 3.7 export settings (presets in exports.py)
    scale: float = 1.0              # output size relative to the shape's full size (0.6667 = 720p)
    vcodec: str = "h264"            # h264 | hevc | vp9
    vbitrate: int = 0               # kbps; 0 = constant quality (crf)
    acodec: str = "aac"             # aac | opus
    abitrate: int = 192             # kbps
    container: str = "mp4"          # mp4 | mov | webm
    loudness: float = -14.0         # LUFS target; 0 = leave the volume as it is (peaks are still limited)
    gain_db: float = 0.0            # this clip louder or quieter (Clip tab › Volume), -12..+12 dB
    denoise: bool = False           # reduce steady background noise
    voice: bool = False             # make speech clearer (gentle compression) without burying game audio
    cap_outline: int = 100          # caption outline, percent of normal
    cap_shadow: int = 100           # caption shadow, percent of normal
    # 4.5 watermark (export only; the source is never changed)
    wm_text: str = ""               # text mark, or "" for none
    wm_image: str = ""              # path to a PNG/JPG mark, or "" (used instead of the text when set)
    wm_pos: str = "tl"              # tl | tr | bl | br | c
    wm_size: int = 100              # percent of the normal size
    wm_opacity: int = 70            # percent
    wm_margin: int = 40             # pixels from the side at 1080 wide
    wm_box: bool = False            # a dark box behind a text mark (easier to read on busy footage)
    wm_start: float = 0.0           # seconds into the clip it appears
    wm_end: float = 0.0             # seconds it disappears; 0 = until the end
    # 3.9 framing
    track: str = "auto"             # auto: whole-frame crops follow the streamer's face | off
    track_target: str = "auto"      # auto | largest | left | right | point (nearest to game_x, focus_y)
    focus_y: float = 0.4            # vertical focus point (for "point")


W, H = 1080, 1920
SHAPES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def out_size(o: "RenderOpts") -> tuple:
    return SHAPES.get(getattr(o, "aspect", "9:16"), SHAPES["9:16"])


def final_size(o: "RenderOpts") -> tuple:
    """The exported frame size: the shape's size times the preset's scale (even numbers)."""
    w, h = out_size(o)
    k = min(1.0, max(0.25, float(getattr(o, "scale", 1.0) or 1.0)))
    return (_even(w * k), _even(h * k)) if k < 0.999 else (w, h)


def audio_filter(o: "RenderOpts") -> str:
    """Audio chain for an export: optional noise reduction and voice clarity, then loudness to the
    preset's target (or just a peak limiter), at 48 kHz."""
    f = []
    if getattr(o, "denoise", False):
        f.append("afftdn=nr=12:nf=-40")
    if getattr(o, "voice", False):
        f.append("highpass=f=80,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200:makeup=2")
    lufs = float(getattr(o, "loudness", -14.0) or 0)
    gain = max(-12.0, min(12.0, float(getattr(o, "gain_db", 0) or 0)))
    if lufs < 0:
        f.append(f"loudnorm=I={lufs:g}:TP=-1.5:LRA=11")
        if gain:
            # after evening out (which would undo it if it came first), then peaks kept under -1 dB.
            # Raising loudnorm's own target instead measured *quieter* on peaky audio (true-peak bound).
            f += [f"volume={gain:g}dB", "alimiter=limit=0.89:level=false"]
    else:
        if gain:
            f.append(f"volume={gain:g}dB")
        f.append("alimiter=limit=0.89:level=false")
    f.append("aresample=48000")
    return ",".join(f)


def effective_layout(o: "RenderOpts") -> str:
    """Cam + game stacks the facecam over gameplay; in a wide frame that doesn't fit, so it uses the whole frame."""
    return "full" if o.layout == "split" and getattr(o, "aspect", "9:16") == "16:9" else o.layout


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


MAX_FPS = 60


def output_fps(info: dict, o: RenderOpts) -> float:
    """The frame rate a clip renders at: the source's own (o.fps 0), or the one chosen, but never
    above the source's. Turning 30 FPS into 60 only repeats frames; it can't add motion that wasn't
    recorded, so Clip Radar doesn't do it."""
    src = float(info.get("fps") or 0)
    if not (1 <= src <= 240):
        src = 30.0
    want = float(getattr(o, "fps", 0) or 0)
    out = min(src, MAX_FPS) if want <= 0 else min(want, src)
    return round(out, 3)


SPEEDS = {   # x264 preset, quality offset (lower crf = better), NVENC preset
    "fast": ("veryfast", 2, "p3"),
    "balanced": ("medium", 0, "p5"),
    "high": ("slow", -2, "p7"),
}


def build_filter(info: dict, o: RenderOpts, path: Optional[list] = None, face: Optional[dict] = None) -> tuple:
    """Return (filter_complex producing [v] in yuv420p, caption_center_y).
    path: [(t, centre x)] for a whole-frame crop that follows a face (3.9 framing).
    face: {"face_y", "face_h"} so automatic captions don't cover the face."""
    sw, sh = info["width"], info["height"]
    z = max(1.0, o.game_zoom)
    W, H = out_size(o)
    layout = effective_layout(o)
    fr = output_fps(info, o)

    def safe_cam_crop(rect: tuple) -> tuple:
        """Clamp user-edited facecam rectangles to a valid, even-sized source crop."""
        x, y, w, h = [min(max(float(v), 0.0), 1.0) for v in rect]
        max_w, max_h = max(2, sw - sw % 2), max(2, sh - sh % 2)
        cx = min(max(0, int(x * sw)), max_w - 2)
        cy = min(max(0, int(y * sh)), max_h - 2)
        cx -= cx % 2
        cy -= cy % 2
        cw = min(max(2, _even(w * sw)), max_w - cx)
        ch = min(max(2, _even(h * sh)), max_h - cy)
        cw -= cw % 2
        ch -= ch % 2
        return max(2, cw), max(2, ch), cx, cy

    def game_crop(out_w: int, out_h: int, follow: Optional[list] = None) -> str:
        aspect = out_w / out_h
        cwf = min(sw, sh * aspect) / z
        cw = min(_even(cwf), sw - sw % 2)
        ch = min(_even(cwf / aspect), sh - sh % 2)
        cx = int(min(max(0, o.game_x * sw - cw / 2), sw - cw))
        cx -= cx % 2
        if follow and cw < sw - 2:
            import framing
            cx = framing.x_expr(follow, sw, cw)      # moves with the face, smoothly
        cy = (sh - ch) // 2
        cy -= cy % 2
        return f"crop={cw}:{ch}:{cx}:{cy},scale={out_w}:{out_h}:flags=lanczos,setsar=1"

    if layout == "split":
        top_h = _even(H * min(max(o.cam_height, 0.2), 0.5))
        bot_h = H - top_h
        cw, ch, cx, cy = safe_cam_crop(o.cam)
        cam = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{top_h}:force_original_aspect_ratio=increase:"
               f"flags=lanczos,crop={W}:{top_h},setsar=1")
        f = (f"[0:v]split=2[a][b];[a]{cam}[cam];[b]{game_crop(W, bot_h)}[game];"
             f"[cam][game]vstack=inputs=2,fps={fr},format=yuv420p[v]")
        cap_y = top_h  # right on the seam reads best
    elif layout == "full":
        f = f"[0:v]{game_crop(W, H, path)},fps={fr},format=yuv420p[v]"
        cap_y = H * 0.70
        if face and path:
            import framing
            cap_y = framing.caption_y(face.get("face_y"), face.get("face_h"), H, cap_y)
    elif layout == "cam":
        # the facecam by itself, filling the frame
        cw, ch, cx, cy = safe_cam_crop(o.cam)
        f = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={W}:{H},setsar=1,fps={fr},format=yuv420p[v]")
        cap_y = H * 0.78
    elif layout == "blur":
        # the whole picture, fitted inside the frame (never cropped or stretched), on a blurred,
        # darkened copy of itself that fills the rest. Works for wide, square and tall sources.
        fg_w, fg_h = W, _even(W * sh / sw)
        if fg_h > H:                                   # a tall source: fit the height instead
            fg_w, fg_h = min(W, _even(H * sw / sh)), H
        fg = game_crop(fg_w, fg_h) if z > 1 else f"scale={fg_w}:{fg_h}:flags=lanczos,setsar=1"
        bw, bh = max(2, _even(W / 4)), max(2, _even(H / 4))
        f = (f"[0:v]split=2[a][b];"
             f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=12:2,"
             f"eq=brightness=-0.12,scale={W}:{H},setsar=1[bg];"
             f"[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fr},format=yuv420p[v]")
        cap_y = min(H / 2 + fg_h / 2 + 170, H * 0.8)
    else:
        raise ValueError(f"unknown layout {o.layout}")

    if o.caption_pos == "custom":
        return f, min(max(o.cap_y * H, 120), H - 120)
    pos = {"top": H * 0.22, "middle": H * 0.5, "bottom": H * 0.78}.get(o.caption_pos, cap_y)
    return f, min(max(pos, 260), H - 220)


# ---- captions --------------------------------------------------------------

_whisper_cache: dict = {}
_whisper_lock = threading.RLock()   # one transcription at a time (titles + renders share the model)
_whisper_used = [0.0]               # when the model last finished a job
_whisper_busy = [0]                 # transcriptions running or waiting for the model
_busy_lock = threading.Lock()
WHISPER_IDLE = 600                  # seconds unused before models are let go (each holds 0.3-3 GB)
WHISPER_KEEP = 2                    # models kept loaded at most


def release_whisper(idle: float = 0.0) -> bool:
    """Let go of the loaded Whisper model if it hasn't been used for `idle` seconds.
    Returns True when one was released. The next transcription loads it again (a few seconds)."""
    with _whisper_lock:
        if not _whisper_cache or _whisper_busy[0] or time.time() - _whisper_used[0] < idle:
            return False
        _whisper_cache.clear()
    import gc
    gc.collect()
    return True


_release_timer = [None]


def _release_later():
    """One timer at a time: it lets the model go once it's been idle long enough, or checks again."""
    if _release_timer[0] is not None:
        return

    def tick():
        _release_timer[0] = None
        if not release_whisper(WHISPER_IDLE) and _whisper_cache:
            _release_later()
    t = threading.Timer(WHISPER_IDLE / 4, tick)
    t.daemon = True
    _release_timer[0] = t
    t.start()


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _load_whisper(model_size: str, log: Log):
    if model_size in _whisper_cache:
        _whisper_cache[model_size] = _whisper_cache.pop(model_size)     # most recently used last
        return _whisper_cache[model_size]
    while len(_whisper_cache) >= WHISPER_KEEP:
        # Deep Scan and title listening may use two sizes side by side; a third (switching
        # small -> medium in Settings) pushes out the one used longest ago
        _whisper_cache.pop(next(iter(_whisper_cache)))
        import gc
        gc.collect()
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


_HALLUCINATION = re.compile(r"thank(s| you) (so much )?for watching|please subscribe|like and subscribe|"
                            r"subtitles by|amara\.org|♪|\[music\]|\(music\)", re.I)


def transcribe(audio, model_size: str, language: str, log: Log, prompt: str = "",
               progress: Progress = _noop) -> list:
    """audio: path or 16 kHz mono float32 numpy array. prompt: names and words to expect (the
    streamer, emotes, your own vocabulary), so Whisper spells them right. progress gets 0-1 as
    the transcript reaches further into the audio."""
    if isinstance(audio, (str, os.PathLike)):
        # decode with the bundled FFmpeg: the packaged app has no PyAV (stubs/av)
        audio = extract_audio(str(audio), 0.0, probe(str(audio))["duration"] or 0.1)
        if audio is None:
            raise RuntimeError("Couldn't read the sound in this file.")
    with _busy_lock:
        _whisper_busy[0] += 1
    try:
        return _transcribe(audio, model_size, language, log, prompt, progress)
    finally:
        with _busy_lock:
            _whisper_used[0] = time.time()
            _whisper_busy[0] -= 1
        _release_later()


def _transcribe(audio, model_size: str, language: str, log: Log, prompt: str, progress: Progress = _noop) -> list:
    with _whisper_lock:
        model = _load_whisper(model_size, log)
        kw = dict(language=language or None, word_timestamps=True, vad_filter=True, beam_size=5,
                  vad_parameters={"min_silence_duration_ms": 400}, condition_on_previous_text=False)
        if prompt:
            kw["initial_prompt"] = prompt[:400]
        try:
            segs, _ = model.transcribe(audio, **kw)
        except TypeError:                      # an older faster-whisper without some of these options
            kw.pop("condition_on_previous_text", None)
            segs, _ = model.transcribe(audio, **kw)
        words = []
        total = max(0.1, len(audio) / 16000)
        for seg in segs:
            progress(min(1.0, float(getattr(seg, "end", 0) or 0) / total))
            # Whisper's own check for text made up during silence or music
            if getattr(seg, "no_speech_prob", 0) > 0.6 and getattr(seg, "avg_logprob", 0) < -1.0:
                continue
            if _HALLUCINATION.search(getattr(seg, "text", "") or ""):
                continue
            for w in seg.words or []:
                txt = w.word.strip()
                if txt:
                    words.append({"start": float(w.start), "end": float(w.end), "word": txt})
    return words


def extract_audio(video: str, start: float, length: float) -> Optional[np.ndarray]:
    """16 kHz mono float32 samples of a stretch of the video's audio (None if unreadable)."""
    try:
        # a damaged file or a stuck network drive mustn't hold Deep Scan or a render forever
        p = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}",
                            "-t", f"{max(0.1, length):.3f}", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1",
                            "-ar", "16000", "-f", "f32le", "-"], capture_output=True,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                           timeout=max(60.0, length * 2))
    except (subprocess.TimeoutExpired, OSError):
        return None
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


CAPTION_MOTIONS = ("none", "pop", "word", "bounce", "fade", "slide", "reveal")
FILLER_WORDS = {"um", "uh", "uhh", "umm", "erm", "hmm", "mm", "uhm"}
EMPH_WORDS = {"no", "way", "what", "insane", "crazy", "wait", "yes", "never", "impossible", "clutch", "holy",
              "omg", "wtf", "bro", "stop", "run", "dead", "help", "why", "how"}


def caption_words(words: list, o: "RenderOpts") -> list:
    """The words captions show: filler words and punctuation dropped when you asked (timing unchanged)."""
    out = []
    for w in words:
        core = re.sub(r"[^\w']", "", w["word"]).lower()
        if not o.caption_filler and core in FILLER_WORDS:
            continue
        if not o.caption_punct:
            txt = re.sub(r"[.,!?;:…\"“”]+", "", w["word"]).strip()
            if not txt:
                continue
            w = dict(w, word=txt)
        out.append(w)
    return out


def motion_of(o: "RenderOpts") -> str:
    m = getattr(o, "caption_motion", "") or ""
    if m in CAPTION_MOTIONS:
        return m
    return "pop" if o.caption_anim else "none"


def _emphasised(word: str) -> bool:
    core = re.sub(r"[^\w']", "", word)
    return core.lower() in EMPH_WORDS or word.endswith("!") or (len(core) > 2 and core.isupper())


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


WM_POSITIONS = ("tl", "tr", "bl", "br", "c")
WM_TOP, WM_BOTTOM = 0.13, 0.80   # top marks sit below TikTok's top tabs; bottom ones above its caption area


def watermark_anchor(o: RenderOpts, W: int, H: int, w: int = 0, h: int = 0) -> tuple:
    """Top-left corner (x, y) for a mark of size w×h, kept inside the frame. Shared by the text
    and image marks and by the preview, so all three land in the same place."""
    pos = o.wm_pos if o.wm_pos in WM_POSITIONS else "tl"
    m = int(max(0, min(200, o.wm_margin)) * W / 1080)
    if pos == "c":
        x, y = (W - w) // 2, (H - h) // 2
    else:
        x = m if pos[1] == "l" else W - w - m
        y = int(H * WM_TOP) if pos[0] == "t" else int(H * WM_BOTTOM) - h
    return max(0, min(x, W - w)), max(0, min(y, H - h))


def watermark_window(o: RenderOpts, clip_len: float) -> tuple:
    a = max(0.0, min(float(o.wm_start or 0), clip_len))
    b = float(o.wm_end or 0)
    b = clip_len if b <= a else min(b, clip_len)
    return a, b


def _wm_alpha(opacity: int) -> str:
    return f"&H{int(round(255 * (1 - max(5, min(100, opacity)) / 100))):02X}&"


def _shifted(words: list, o: "RenderOpts") -> list:
    """Caption words moved by Text › Timing (burned-in captions and the .srt alike)."""
    off = max(-1000, min(1000, int(getattr(o, "caption_offset", 0) or 0))) / 1000
    if not off:
        return words
    return [dict(w, start=max(0.0, w["start"] + off), end=max(0.05, w["end"] + off)) for w in words]


def _join_lines(parts: list, texts: list, fs: int, spacing: int, width: int, upper: bool) -> str:
    """The words of one caption. At normal line spacing libass wraps them itself; with more
    spacing, a caption too wide for one line is split into two balanced lines here, with a
    spacer line (its height is the extra space) between them."""
    if spacing <= 100 or len(parts) < 2:
        return " ".join(parts)
    glyph = 0.62 if upper else 0.54                    # average character width, share of the font size
    chars = sum(len(t) for t in texts) + len(texts) - 1
    if chars * fs * glyph <= width:
        return " ".join(parts)
    left = [sum(len(t) + 1 for t in texts[:k]) for k in range(1, len(texts))]
    k = 1 + min(range(len(left)), key=lambda i: abs(left[i] - chars / 2))
    gap = max(1, int(fs * (spacing - 100) / 100))
    return " ".join(parts[:k]) + f"\\N{{\\fs{gap}}}\\h{{\\fs{fs}}}\\N" + " ".join(parts[k:])


def build_ass(words: list, o: RenderOpts, center_y: float, clip_len: float, title: str = "") -> str:
    W, H = out_size(o)
    hi = _ass_color(o.caption_color)
    cfont, cbold, _ = _font(o.caption_font)
    tfont, tbold, _ = _font(o.title_font)
    style = o.caption_style if o.caption_style in CAPTION_STYLES else "pop"
    tstyle = o.title_style if o.title_style in TITLE_STYLES else "box"
    base_fs = int(86 * max(50, min(160, o.caption_size)) / 100 * FONT_SCALE.get(o.caption_font, 1.0))
    title_fs = int(64 * max(50, min(160, o.title_size)) / 100 * FONT_SCALE.get(o.title_font, 1.0))
    ob = lambda x: max(0, int(round(x * max(0, min(300, getattr(o, "cap_outline", 100))) / 100)))  # noqa: E731
    shd = lambda x: max(0, int(round(x * max(0, min(300, getattr(o, "cap_shadow", 100))) / 100)))  # noqa: E731
    bord = ob(max(4, base_fs // 12))
    white, black, dark = "&H00FFFFFF", "&H00000000", "&H00141414"
    if style == "clean":
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H64000000,{cbold},0,0,0,100,100,1,0,1,0,{shd(4)},5,70,70,0,1"
    else:
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H96000000,{cbold},0,0,0,100,100,1,0,1,{bord},{shd(3)},5,70,70,0,1"
    tpad = max(8, title_fs // 4)
    if tstyle == "outline":
        t_style = f"Style: Title,{tfont},{title_fs},{white},{white},{black},&H96000000,{tbold},0,0,0,100,100,0,0,1,{max(4, title_fs // 10)},3,8,90,90,0,1"
    else:
        fill, boxc = {"box": (dark, white), "dark": (white, "&H1A141414"), "accent": (dark, hi.rstrip("&"))}[tstyle]
        t_style = f"Style: Title,{tfont},{title_fs},{fill},{fill},{boxc},{boxc},{tbold},0,0,0,100,100,0,0,3,{tpad},0,8,90,90,0,1"
    wm_fs = int(44 * max(40, min(250, o.wm_size)) / 100)
    alpha = _wm_alpha(o.wm_opacity)
    wa = alpha.strip("&H").strip("&")
    if o.wm_box:
        mark_style = (f"Style: Mark,{cfont},{wm_fs},&H{wa}FFFFFF,&H{wa}FFFFFF,&H{wa}141414,&H{wa}141414,{cbold},"
                      f"0,0,0,100,100,0,0,3,{max(6, wm_fs // 4)},0,7,0,0,0,1")
    else:
        mark_style = (f"Style: Mark,{cfont},{wm_fs},&H{wa}FFFFFF,&H{wa}FFFFFF,&H{wa}000000,&H{wa}000000,{cbold},"
                      f"0,0,0,100,100,0,0,1,{max(2, wm_fs // 14)},1,7,0,0,0,1")
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
{mark_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    wm = (o.wm_text or "").strip() if not o.wm_image else ""
    if wm:
        # a text mark: same anchor as an image mark; its size is estimated for the anchor, and \an
        # keeps it inside the frame on the side it's anchored to
        a, b = watermark_window(o, clip_len)
        an = {"tl": 7, "tr": 9, "bl": 1, "br": 3, "c": 5}.get(o.wm_pos if o.wm_pos in WM_POSITIONS else "tl")
        # with a zero-size box the anchor is the point the text hangs from (\an picks which corner)
        x, y = watermark_anchor(o, W, H)
        lines.append(f"Dialogue: 2,{_ass_time(a)},{_ass_time(b)},Mark,,0,0,0,,{{\\an{an}\\pos({x},{y})}}{_ass_escape(wm[:60])}")
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
    motion = motion_of(o)
    if style == "clean" and motion == "pop":
        motion = "none"                    # clean captions never popped
    dur = int(120 * 100 / max(50, min(200, getattr(o, "caption_speed", 100))))    # ms
    words = _shifted(words, o)
    spacing = max(100, min(200, int(getattr(o, "caption_spacing", 100) or 100)))
    chunks = _chunks(caption_words(words, o), o, clip_len)
    for ci, ch in enumerate(chunks):
        texts = [_ass_escape(w["word"].upper() if o.uppercase else w["word"]) for w in ch]
        emph = [o.caption_emph and _emphasised(w["word"]) for w in ch]
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
                if emph[k] and k != wi and style in ("pop", "glow", "box", "clean"):
                    t = f"{{\\c{hi}}}{t}{{\\c{white}&}}"          # a strong word stays in the highlight colour
                if motion == "reveal" and k > wi:
                    parts.append(f"{{\\alpha&HFF&}}{t}{{\\alpha&H00&}}")   # not said yet: room kept, nothing shown
                    continue
                if k == wi and motion in ("word", "bounce"):
                    end_sc = 112 if style == "glow" else 100
                    if motion == "word":
                        grow = f"\\fscx132\\fscy132\\t(0,{dur},\\fscx{end_sc}\\fscy{end_sc})"
                    else:
                        grow = (f"\\fscx70\\fscy70\\t(0,{dur // 2},\\fscx122\\fscy122)"
                                f"\\t({dur // 2},{dur},\\fscx{end_sc}\\fscy{end_sc})")
                    inner = t
                    if style == "pop":
                        inner = f"{{\\c{hi}}}{t}{{\\c{white}&}}"
                    elif style == "karaoke":
                        inner = f"{{\\c{hi}}}{t}{{\\c{white}&}}"
                    elif style == "box":
                        inner = (f"{{\\3c{hi}\\bord{max(8, int(fs * 0.22))}\\c{dark}&}}{t}"
                                 f"{{\\3c{black}&\\bord{ob(max(4, fs // 12))}\\c{white}&}}")
                    parts.append(f"{{{grow}}}{inner}{{\\fscx100\\fscy100}}")
                    continue
                if style == "pop":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k == wi else t)
                elif style == "glow":
                    parts.append(f"{{\\fscx112\\fscy112}}{t}{{\\fscx100\\fscy100}}" if k == wi else t)
                elif style == "karaoke":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k <= wi else t)
                elif style == "box":
                    parts.append(f"{{\\3c{hi}\\bord{max(8, int(fs * 0.22))}\\c{dark}&}}{t}{{\\3c{black}&\\bord{ob(max(4, fs // 12))}\\c{white}&}}"
                                 if k == wi else t)
                else:
                    parts.append(t)
            lead = ""
            if style == "glow":
                lead = f"\\3c{hi}\\bord{ob(max(4, fs // 12))}\\blur{max(5, fs // 8)}"
            if style == "box":
                lead = f"\\bord{ob(max(4, fs // 12))}"
            pop = ""
            place = f"\\pos({x},{y})"
            if wi == 0:                    # how the chunk comes in
                if motion == "pop":
                    pop = f"{{\\fscx114\\fscy114\\t(0,{dur},\\fscx100\\fscy100)}}"
                elif motion == "fade":
                    lead += f"\\fad({dur * 2},0)"
                elif motion == "slide":
                    place = f"\\move({x},{y + int(fs * 0.6)},{x},{y},0,{dur * 2})"
                    lead += f"\\fad({dur * 2},0)"
            lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Cap,,{ml},{mr},0,,"
                         f"{{\\an5{place}\\fs{fs}{lead}}}{pop}{_join_lines(parts, texts, fs, spacing, 2 * half, o.uppercase)}")
    return header + "\n".join(lines) + "\n"


# ---- encoders --------------------------------------------------------------

_encoder_cache: dict = {}

RENDER_THREADS: list = []         # ["-threads", "N"] when Settings › Performance asks to keep the PC responsive
RENDER_PRIORITY = [0]             # Windows priority class for ffmpeg (BELOW_NORMAL when keeping the PC responsive)
_HW = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                    "-cq", "21", "-b:v", "0", "-profile:v", "high"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                  "-qp_i", "20", "-qp_p", "22", "-qp_b", "24"]),
]


def _rate_args(o: RenderOpts) -> list:
    kb = int(getattr(o, "vbitrate", 0) or 0)
    return ["-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k"] if kb > 0 else []


def video_encoder(o: RenderOpts) -> tuple:
    """(name, args). H.264 tries GPU encoders once (fast) and falls back to x264. H.265 and VP9
    (for .webm) use the CPU encoders. A bitrate from the preset replaces constant quality."""
    vc = getattr(o, "vcodec", "h264")
    if vc == "vp9" or getattr(o, "container", "mp4") == "webm":
        kb = int(getattr(o, "vbitrate", 0) or 0)
        q = ["-b:v", f"{kb}k"] if kb else ["-crf", str(min(63, o.crf + 12)), "-b:v", "0"]
        return "libvpx-vp9", ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", *q]
    if vc == "hevc":
        x_pre, dq, _nv = SPEEDS.get(getattr(o, "speed", "balanced"), SPEEDS["balanced"])
        q = _rate_args(o) or ["-crf", str(o.crf + 4 + dq)]
        return "libx265", ["-c:v", "libx265", "-preset", x_pre, *q, "-tag:v", "hvc1"]
    rate = _rate_args(o)
    x_pre, dq, _nv = SPEEDS.get(getattr(o, "speed", "balanced"), SPEEDS["balanced"])
    crf = min(max(o.crf + dq, 12), 36)
    x264 = ("libx264", ["-c:v", "libx264", "-preset", x_pre, *(rate or ["-crf", str(crf)]),
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
    hw = _encoder_cache["hw"]
    if hw and rate:          # a target bitrate: swap the GPU encoder's quality setting for it
        name, args = hw
        keep, skip = [], {"-cq", "-global_quality", "-qp_i", "-qp_p", "-qp_b", "-b:v", "-rc"}
        i = 0
        while i < len(args):
            if args[i] in skip:
                i += 2
                continue
            keep.append(args[i])
            i += 1
        return name, keep + rate
    if hw and hw[0] == "h264_nvenc":       # the effort setting picks NVENC's preset too (p3 / p5 / p7)
        return hw[0], [_nv if a == "p5" else a for a in hw[1]]
    return hw or x264


def write_srt(words: list, o: "RenderOpts", path: str, clip_len: float):
    """Captions as a subtitle file next to the video (times relative to the clip)."""
    def ts(t):
        t = max(0.0, t)
        h, m, sec = int(t // 3600), int(t % 3600 // 60), t % 60
        return f"{h:02d}:{m:02d}:{int(sec):02d},{int(round((sec - int(sec)) * 1000)) % 1000:03d}"
    lines = []
    chunks = _chunks(caption_words(_shifted(words, o), o), o, clip_len)
    for i, ch in enumerate(chunks, 1):
        end = min(clip_len, chunks[i][0]["start"] if i < len(chunks) else ch[-1]["end"] + 0.4)
        text = " ".join(w["word"] for w in ch)
        lines.append(f"{i}\n{ts(ch[0]['start'])} --> {ts(max(end, ch[0]['start'] + 0.3))}\n{text.upper() if o.uppercase else text}\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def audio_peak(path: str) -> Optional[float]:
    """Loudest point of a file's audio in dB (None if it has no audio track)."""
    try:
        if not probe(path)["has_audio"]:
            return None
        out = subprocess.run([ffmpeg(), "-v", "info", "-nostdin", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW).stderr     # ffmpeg echoes the file name in UTF-8
        m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", out)
        return -200.0 if m and m.group(1) == "-inf" else float(m.group(1)) if m else None
    except Exception:
        return None


def render_clip(video: str, clip_start: float, clip_end: float, out_path: str, o: RenderOpts,
                title: str = "", log: Log = _noop, progress: Progress = _noop,
                check: Check = _noop, words: Optional[list] = None,
                censor: Optional[list] = None, censor_mode: str = "off", framing: Optional[dict] = None) -> str:
    """words: transcript (times relative to clip_start) to caption with; transcribed here if None.
    censor: [(start, end)] spans (relative) to mute ("mute") or bleep ("bleep")."""
    info = probe(video)
    clip_start = max(0.0, clip_start)
    clip_end = min(info["duration"] or clip_end, clip_end)
    length = max(1.0, clip_end - clip_start)
    fr = framing or {}
    vf, cap_y = build_filter(info, o, fr.get("path") or None, fr)
    work = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(work, exist_ok=True)
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
                        # Whisper can take a while on a long clip: move the bar as it goes
                        words = transcribe(audio, o.whisper_model, o.language, log,
                                           progress=lambda f: progress(0.08 + 0.22 * f))
                    except Exception as e:
                        log(f"Captions skipped: {str(e)[:200]}")
                        words = []
                if not words:
                    log("No speech found in this clip.")
    progress(0.3)
    check()

    use_ass = bool(words) or bool(title.strip()) or bool((o.wm_text or "").strip() and not o.wm_image)
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
    # spans are (start, end) using censor_mode, or (start, end, mode) for a fix made on one clip
    af = ""
    spans = []
    for sp in censor or []:
        a, b = float(sp[0]), float(sp[1])
        m = sp[2] if len(sp) > 2 else censor_mode
        if m in ("mute", "bleep") and b > a and a < length:
            spans.append((max(0.0, a), min(length, b), m))
    if spans and info["has_audio"]:
        on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b, _ in spans)
        beeps = [(a, b) for a, b, m in spans if m == "bleep"]
        tail = audio_filter(o)
        if beeps:
            on_b = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in beeps)
            af = (f";[0:a:0]volume=0:enable='{on}'[am];"
                  f"sine=frequency=1000:sample_rate=48000:duration={length:.3f},volume=0.6,"
                  f"volume=0:enable='not({on_b})'[bp];"
                  f"[am][bp]amix=inputs=2:duration=first:normalize=0,{tail}[a]")
        else:
            af = f";[0:a:0]volume=0:enable='{on}',{tail}[a]"
        vf = vf + af

    # an image watermark: overlaid on the finished frame (after captions), scaled with the frame,
    # at the same anchor as a text mark; the source video isn't changed
    wm_img = o.wm_image if o.wm_image and os.path.isfile(o.wm_image) else ""
    if wm_img:
        W_, H_ = out_size(o)
        mw = _even(W_ * 0.2 * max(40, min(250, o.wm_size)) / 100)
        a, b = watermark_window(o, length)
        op = max(5, min(100, o.wm_opacity)) / 100
        i = vf.rfind("[v]")
        # x/y from the anchor for an unknown height: ffmpeg's overlay evaluates W/H/w/h itself, so
        # pass expressions that keep the mark inside the frame
        pos = o.wm_pos if o.wm_pos in WM_POSITIONS else "tl"
        m = int(max(0, min(200, o.wm_margin)) * W_ / 1080)
        xs = {"l": str(m), "r": f"W-w-{m}"}
        xe = "(W-w)/2" if pos == "c" else xs[pos[1]]
        ye = "(H-h)/2" if pos == "c" else (str(int(H_ * WM_TOP)) if pos[0] == "t" else f"{int(H_ * WM_BOTTOM)}-h")
        vf = (vf[:i] + "[vpre];"
              f"[1:v]scale={mw}:-2:flags=lanczos,format=rgba,colorchannelmixer=aa={op:.2f}[wm];"
              f"[vpre][wm]overlay=x='max(0,min({xe},W-w))':y='max(0,min({ye},H-h))':"
              f"enable='between(t,{a:.3f},{b:.3f})',format=yuv420p[v]" + vf[i + 3:])

    # export size below the shape's full size: scale the finished frame (captions included)
    fw, fh = final_size(o)
    if (fw, fh) != out_size(o):
        i = vf.rfind("[v]")
        vf = vf[:i] + f",scale={fw}:{fh}:flags=lanczos[v]" + vf[i + 3:]
    cont = getattr(o, "container", "mp4") if getattr(o, "container", "mp4") in ("mp4", "mov", "webm") else "mp4"
    if cont == "webm":
        acodec = ["-c:a", "libopus", "-b:a", f"{int(getattr(o, 'abitrate', 128))}k"]
    else:
        acodec = ["-c:a", "aac", "-b:a", f"{int(getattr(o, 'abitrate', 192))}k"]
    tmp_name = f"vc_{tag}.part.{cont}"
    tmp_path = os.path.join(work, tmp_name)

    def encode(enc_args: list):
        cmd = [ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
               "-ss", f"{clip_start:.3f}", "-t", f"{length:.3f}", "-i", video]
        if wm_img:
            cmd += ["-i", wm_img]
        cmd += ["-filter_complex", vf, "-map", "[v]"]
        if info["has_audio"] and af:
            cmd += ["-map", "[a]", *acodec, "-ar", "48000"]
        elif info["has_audio"]:
            cmd += ["-map", "0:a:0", *acodec, "-af", audio_filter(o), "-ar", "48000"]
        cmd += [*enc_args, "-pix_fmt", "yuv420p", "-r", str(output_fps(info, o))]
        if cont != "webm":
            cmd += ["-movflags", "+faststart"]
        cmd += [*RENDER_THREADS, tmp_name]
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW | RENDER_PRIORITY[0])
        # read errors on the side: a full stderr pipe would otherwise freeze ffmpeg mid-render
        err_lines: list = []
        last_output = [time.monotonic()]
        stalled = [False]

        def drain():
            for ln in p.stderr:
                err_lines.append(ln)
                del err_lines[:-60]

        def watch_render():
            # FFmpeg's -progress stream normally updates several times per second.
            # A long silence indicates a hung filter/encoder or a wedged input.
            while p.poll() is None:
                if time.monotonic() - last_output[0] > 120:
                    stalled[0] = True
                    try:
                        p.kill()
                    except OSError:
                        pass
                    return
                time.sleep(2)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        threading.Thread(target=watch_render, daemon=True).start()
        try:
            for line in p.stdout:
                last_output[0] = time.monotonic()
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
        finally:
            p.wait()
            reader.join(timeout=5)
        if stalled[0]:
            return p.returncode or -1, "".join(err_lines) + "\nFFmpeg made no progress for 120 seconds and was stopped."
        return p.returncode, "".join(err_lines)

    enc_name, enc_args = video_encoder(o)
    try:
        code, err = encode(enc_args)
        if code != 0 and enc_name != "libx264":
            log(f"GPU encoder ({enc_name}) failed, re-encoding on CPU.")
            _encoder_cache["hw"] = None
            code, err = encode(video_encoder(RenderOpts(hw_encode=False, crf=o.crf, vbitrate=getattr(o, "vbitrate", 0)))[1])
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
