Warning: truncated output (original token count: 38360)
Total output lines: 3298

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import paths
import procjob

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


class RenderInputError(ValueError):
    """A render that can't work with what it was given (a clip outside the video, a file with no
    picture). Retrying won't change the outcome, so it isn't retried automatically. kind names the cause."""
    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


def _noop(*_a, **_k):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def which(name: str) -> Optional[str]:
    """Find a tool in Clip Radar's tools folders first, then on PATH."""
    suffixes = (".exe", "") if paths.IS_WIN else ("",)
    for d in paths.tools_dirs():
        for suffix in suffixes:
            cand = d / (name + suffix)
            if cand.is_file() and (paths.IS_WIN or os.access(cand, os.X_OK)):
                return str(cand)
    return shutil.which(name)


def ffmpeg() -> str:
    exe = which("ffmpeg")
    if not exe:
        hint = "Run install.bat, or put ffmpeg.exe in the tools folder." if paths.IS_WIN else "Install FFmpeg and make it available on PATH."
        raise RuntimeError("ffmpeg isn't installed. " + hint)
    return exe


def ffprobe() -> str:
    exe = which("ffprobe")
    if not exe:
        hint = "Run install.bat, or put ffprobe.exe in the tools folder." if paths.IS_WIN else "Install FFmpeg and make ffprobe available on PATH."
        raise RuntimeError("ffprobe isn't installed. " + hint)
    return exe


def make_preview(src: str, out: str, duration: float, progress: Callable = lambda x: None,
                 check: Callable = lambda: None) -> str:
    """A copy of a video that the app window can always play, for the Review preview only: H.264 and
    AAC in an MP4, 480p, at most 30 fps, encoded fast. Made when the window can't play the original
    (HEVC or AV1 from Twitch's enhanced streams or from OBS, or a container it can't read). Clips still
    render from the original. Written next to it, then moved into place, so a half-made copy never plays."""
    tmp = out + ".part.mp4"
    cmd = [ffmpeg(), "-v", "error", "-y", "-nostdin", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
           "-vf", "scale=-2:'min(480,ih)':flags=bilinear",
           "-r", "30", *(["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "600k",
                          "-c:a", "libopus"] if os.environ.get("CLIPRADAR_PREVIEW_VP9") == "1" else   # tests' browsers lack H.264
                         ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "fastdecode", "-crf", "30", "-c:a", "aac"]),
           "-g", "60", "-pix_fmt", "yuv420p", "-b:a", "96k", "-ac", "2",
           "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    procjob.adopt(p)
    err: list = []
    threading.Thread(target=lambda: err.extend(p.stderr), daemon=True).start()
    try:
        for line in p.stdout:
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    progress(min(1.0, max(0.0, int(line.split("=")[1]) / 1e6 / duration)))
                except ValueError:
                    pass
            try:
                check()
            except Cancelled:
                p.kill()
                raise
    finally:
        p.wait()
    if p.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 1024:
        try:
            os.remove(tmp)
        except OSError:
            pass
        tail = "".join(err).strip().splitlines()[-4:]
        raise RuntimeError("Couldn't make a preview copy of this video" + (f": {' '.join(tail)[-300:]}" if tail else "."))
    os.replace(tmp, out)
    return out


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
            except OSError as e:
                if attempt == 2:
                    failed.append(os.path.basename(p))
                    # name who has it open (Windows), so a file that stays busy can be traced
                    import filelocks
                    who = filelocks.who_has(p)
                    print(f"  Couldn't delete {os.path.basename(p)}: {e.strerror or e}"
                          + (f"; open in {', '.join(who)}" if who else ""), flush=True)
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
            raise RuntimeError(f"That's a {desc['label'].lower()} happening now. Use L…28360 tokens truncated…= {"split": "Cam + game", "full": "Whole frame", "cam": "Facecam only", "blur": "Blur-fit",
                "square": "Square blur"}


def _first_reason(err: str) -> str:
    """The line that says why FFmpeg failed: the first from a filter, else the last line."""
    lines = [ln.strip() for ln in err.strip().splitlines() if ln.strip()]
    why = [ln for ln in lines if "[Parsed_" in ln]
    return (why or lines or ["no error output"])[0][:200]


def _frame_luma(path: str, at: float = 0.0, ignore_editlist: bool = False) -> Optional[float]:
    """Average brightness (0-255) of the frame at `at` seconds, or None if it can't be read.
    ignore_editlist reads the file the way tools that skip MP4 edit lists do (TikTok's cover picker)."""
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if ignore_editlist:
        cmd += ["-ignore_editlist", "1"]
    if at > 0:
        cmd += ["-ss", f"{at:.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or len(p.stdout) < 1024:
        return None
    return sum(p.stdout[:1024]) / 1024


def black_start(path: str, length: float) -> bool:
    """The clip opens on a black frame although the picture right after it isn't black: what TikTok
    shows as a black cover. Checked both as players read the file and with its edit list ignored."""
    ref = _frame_luma(path, min(0.4, max(0.1, length / 4)))
    if ref is None or ref < 40:
        return False                    # the clip really starts dark (or can't be read): nothing to fix
    firsts = [_frame_luma(path)]
    if path.lower().endswith((".mp4", ".mov", ".m4v")):
        firsts.append(_frame_luma(path, ignore_editlist=True))
    first = min((f for f in firsts if f is not None), default=None)
    return first is not None and first < 24 and ref - first > 25


def _ffmpeg_reason(err: str) -> str:
    """The end of FFmpeg's errors, plus the filter lines that say why (they come before the
    generic 'Error reinitializing filters' and encoder lines, so the last few alone lose them)."""
    lines = err.strip().splitlines()
    why = [ln for ln in lines[:-10] if "[Parsed_" in ln][:3]
    return "\n".join(why + lines[-10:])


def render_clip(video: str, clip_start: float, clip_end: float, out_path: str, o: RenderOpts,
                title: str = "", log: Log = _noop, progress: Progress = _noop,
                check: Check = _noop, words: Optional[list] = None,
                censor: Optional[list] = None, censor_mode: str = "off", framing: Optional[dict] = None,
                warn: Log = _noop, diag: Optional[dict] = None) -> str:
    """words: transcript (times relative to clip_start) to caption with; transcribed here if None.
    censor: [(start, end)] spans (relative) to mute ("mute") or bleep ("bleep").
    warn: told when the clip came out differently from what was asked (a layout that had to fall back).
    diag: filled in with what FFmpeg was asked and said (cmd, encoder, exit, stderr, fallback), for the
    render's attempt history, whether it worked or not."""
    diag = diag if diag is not None else {}
    info = probe(video)
    clip_start = max(0.0, clip_start)
    if not clip_end > clip_start:
        raise RenderInputError("source_range", "This clip ends before it starts.")
    if info["duration"] and clip_start > info["duration"] - 0.5:
        # FFmpeg would "succeed" here with an empty file
        raise RenderInputError("source_range", f"This clip starts at {fmt_ts(clip_start)}, after the end of the video "
                                               f"({fmt_ts(info['duration'])}). The recording may have been replaced "
                                               "by a shorter one.")
    clip_end = min(info["duration"] or clip_end, clip_end)
    length = max(1.0, clip_end - clip_start)
    fr = framing or {}
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

    use_ass = bool(words) or bool(title.strip()) or any(m.get("kind") != "image" and str(m.get("text") or "").strip()
                                                        for m in marks_of(o))
    sub = ""
    if use_ass:
        # bare filenames + cwd=work sidestep Windows drive-letter escaping in the subtitles filter
        sub = f"subtitles=filename={ass_name}"
        need = fonts_for(o)
        if need:
            os.makedirs(font_dir, exist_ok=True)
            for fp in need:
                shutil.copy2(fp, font_dir)
            sub += f":fontsdir={font_dir_name}"

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

    wm_imgs = [m for m in marks_of(o) if m.get("kind") == "image" and m.get("image") and os.path.isfile(m["image"])]

    def filters(lo: RenderOpts, path: Optional[list]) -> str:
        """The whole filter_complex for layout lo (captions, censoring, watermark, export size)."""
        vf, cap_y = build_filter(info, lo, path, fr)
        if use_ass:
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(build_ass(words, o, cap_y, length, title))
            vf = vf.replace("format=yuv420p[v]", f"format=yuv420p,{sub}[v]")
        vf = vf + af
        if wm_imgs:
        # image watermark layers: overlaid on the finished frame (after captions), scaled with the frame,
        # at the same anchor as a text layer; the source video isn't changed
            W_, H_ = out_size(o)
            i = vf.rfind("[v]")
            chain, prev = vf[:i] + "[vm0];", "vm0"
            for k, m in enumerate(wm_imgs):
                mw = _even(W_ * 0.2 * max(20, min(400, int(m.get("size", 100)))) / 100)
                a, b = mark_window(m, length)
                op = max(5, min(100, int(m.get("opacity", 70)))) / 100
                rot = int(m.get("rotation") or 0)
                turn = (f",rotate=a={rot * math.pi / 180:.5f}:c=none:ow='rotw({rot * math.pi / 180:.5f})'"
                        f":oh='roth({rot * math.pi / 180:.5f})'") if rot else ""
                # x/y from the anchor for an unknown size: ffmpeg's overlay evaluates W/H/w/h itself, so
                # pass expressions that keep the layer inside the frame
                pos = _mpos(m)
                mg = int(max(0, min(200, float(m.get("margin", 40)))) * W_ / 1080)
                if pos == "free":
                    xe, ye = f"{float(m.get('x', .5)) * W_:.1f}-w/2", f"{float(m.get('y', .5)) * H_:.1f}-h/2"
                elif pos == "c":
                    xe, ye = "(W-w)/2", "(H-h)/2"
                else:
                    xe = str(mg) if pos[1] == "l" else f"W-w-{mg}"
                    ye = str(int(H_ * WM_TOP)) if pos[0] == "t" else f"{int(H_ * WM_BOTTOM)}-h"
                last = k == len(wm_imgs) - 1
                out = "v" if last else f"vm{k + 1}"
                chain += (f"[{k + 1}:v]scale={mw}:-2:flags=lanczos,format=rgba,colorchannelmixer=aa={op:.2f}{turn}[wm{k}];"
                          f"[{prev}][wm{k}]overlay=x='max(0,min({xe},W-w))':y='max(0,min({ye},H-h))':"
                          f"enable='between(t,{a:.3f},{b:.3f})'" + (",format=yuv420p" if last else "") + f"[{out}]"
                          + ("" if last else ";"))
                prev = out
            vf = chain + vf[i + 3:]
        # export size below the shape's full size: scale the finished frame (captions included)
        fw, fh = final_size(o)
        if (fw, fh) != out_size(o):
            i = vf.rfind("[v]")
            vf = vf[:i] + f",scale={fw}:{fh}:flags=lanczos[v]" + vf[i + 3:]
        return vf

    try:
        vf = filters(o, fr.get("path") or None)
    except Exception:
        shutil.rmtree(font_dir, ignore_errors=True)
        try:
            os.remove(ass_path)
        except OSError:
            pass
        raise
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
        for m in wm_imgs:
            cmd += ["-i", m["image"]]
        cmd += ["-filter_complex", vf, "-map", "[v]"]
        if info["has_audio"] and af:
            cmd += ["-map", "[a]", *acodec, "-ar", "48000"]
        elif info["has_audio"]:
            cmd += ["-map", "0:a:0", *acodec, "-af", audio_filter(o), "-ar", "48000"]
        cmd += [*enc_args, "-pix_fmt", "yuv420p", "-r", str(output_fps(info, o))]
        if cont != "webm":
            # negative_cts_offsets: the first frame shows at exactly 0:00 even where the edit list is ignored
            # (TikTok's cover picker does; without it the first frame sits one frame in and the cover is black)
            cmd += ["-movflags", "+faststart+negative_cts_offsets"]
        cmd += [*RENDER_THREADS, tmp_name]
        diag["cmd"] = subprocess.list2cmdline(cmd)
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW | RENDER_PRIORITY[0])
        procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
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
            err = "".join(err_lines) + "\nFFmpeg made no progress for 120 seconds and was stopped."
            code = p.returncode or -1
        else:
            code, err = p.returncode, "".join(err_lines)
        diag.update(exit=code, stderr=err[-2000:], stalled=stalled[0])
        return code, err

    enc_name, enc_args = video_encoder(o)
    if paths.IS_MAC and o.hw_encode and enc_name == "libx264":
        problem = _encoder_cache.get("hw_error") or "this FFmpeg build has no compatible hardware encoder"
        log(f"VideoToolbox isn't available ({problem}); rendering with software H.264.")
    layout = effective_layout(o)
    following = layout == "full" and "'clip(" in vf          # a crop that moves (framing.x_expr)
    chosen = LAYOUT_NAMES.get(layout, layout) + (", following the face" if following else "")
    diag.update(encoder=enc_name, fallback="", layout_used=chosen)
    try:
        code, err = encode(enc_args)
        if code != 0 and enc_name != "libx264" and not _graph_failed(err):
            log(f"Hardware encoder ({enc_name}) failed, re-encoding on the CPU.")
            _encoder_cache["hw"] = None
            enc_args = video_encoder(RenderOpts(hw_encode=False, crf=o.crf, vbitrate=getattr(o, "vbitrate", 0)))[1]
            diag.update(encoder="libx264", fallback=f"{enc_name} failed, used the CPU")
            code, err = encode(enc_args)
        if code != 0 and _crop_failed(err) and (layout != "blur" or _num(o.game_zoom, 1.0, 1.0, MAX_ZOOM) > 1):
            # Every crop is checked before FFmpeg runs (crop_filter), so this is FFmpeg refusing a crop that
            # fits: a source it can't handle the way it's set up. The first failure is kept as the attempt's
            # (graph, exit code, FFmpeg's words); then the same layout without following the face, and
            # last the whole picture on a blurred background, which has no crop of the source.
            first = {"layout": chosen, "exit": code, "reason": _ffmpeg_reason(err)[-1500:], "filter": vf[:4000]}
            diag["first_failure"] = first
            kept = {k: diag.get(k) for k in ("cmd", "exit", "stderr")}
            tries = []
            if following:
                tries.append((LAYOUT_NAMES["full"] + ", not following the face", lambda: filters(o, None),
                              "This clip couldn't follow the face, so it shows the middle of the picture (or your Focus "
                              "point). Exports › the clip › details has FFmpeg's reason."))
            tries.append((LAYOUT_NAMES["blur"], lambda: filters(replace(o, layout="blur", game_zoom=1.0), None),
                          f"This clip's layout ({LAYOUT_NAMES.get(layout, layout)}) couldn't be built from the video, so "
                          "it shows the whole picture on a blurred background. Exports › the clip › details has "
                          "FFmpeg's reason; please send it to us so this can be fixed."))
            used, failed_vf = "", vf
            for used, graph, msg in tries:
                vf = graph()          # (this also writes the captions for that layout)
                if vf == failed_vf:
                    continue          # the same graph would fail the same way
                log(f"The layout couldn't be built ({_first_reason(err)}); trying {used}.")
                code, err = encode(enc_args)
                if code == 0 or not _crop_failed(err):
                    break
            note = f"{chosen} failed (exit {first['exit']}: {_first_reason(first['reason'])})"
            diag["fallback"] = (diag["fallback"] + "; " if diag.get("fallback") else "") + (
                f"{note}, used {used}" if code == 0 else f"{note}, and {used} failed too (exit {code})")
            if code == 0:
                diag["layout_used"] = used
                warn(msg)
            else:
                diag["fallback_stderr"] = err[-1500:]
                diag.update(kept)               # the attempt shows the first failure, not the fallback's
                raise RuntimeError(f"ffmpeg render failed: the layout ({chosen}) couldn't be built, and {used} failed "
                                   f"too.\n{first['reason']}\n\n{used}:\n{_ffmpeg_reason(err)}")
        if code == 0 and black_start(tmp_path, length):
            # TikTok takes the frame at 0:00 as the cover: a black one there is a black cover
            used = diag.get("encoder", enc_name)
            if used != "libx264" and o.vcodec in ("h264", "") and cont != "webm":
                log(f"The hardware encoder ({used}) made a black first frame; rendering on the CPU instead.")
                _encoder_cache["hw"] = None                 # and for the rest of this session
                _encoder_cache["black_start"] = used
                enc_args = video_encoder(RenderOpts(hw_encode=False, crf=o.crf, vbitrate=getattr(o, "vbitrate", 0),
                                                    speed=getattr(o, "speed", "balanced")))[1]
                diag.update(encoder="libx264", fallback=(diag.get("fallback", "") + "; " if diag.get("fallback") else "")
                            + f"{used} made a black first frame, used the CPU")
                code, err = encode(enc_args)
            diag["black_start"] = code == 0 and black_start(tmp_path, length)
            if diag["black_start"]:
                warn("This clip's first frame came out black, so TikTok may show a black cover. Pick a cover "
                     "when you upload, and tell us: Exports › the clip › details has what's needed to fix it.")
        if code != 0:
            raise RuntimeError("ffmpeg render failed:\n" + _ffmpeg_reason(err))
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
