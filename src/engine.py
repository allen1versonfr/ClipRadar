"""
Clip Radar engine: find the best moments in a Twitch VOD and render them as
vertical (9:16) TikTok clips with optional auto-captions.

Pipeline (each step in its own module since hardening phase 4; engine re-exports their names)
  1. acquire()       download the VOD (yt-dlp) + chat replay, or use a local file       acquire.py
  2. analyze()       per-second scores from chat activity and audio loudness            analysis.py
  3. pick_clips()    choose the top non-overlapping peaks (instant, re-runnable)        analysis.py
  4. render_clip()   cut, reframe to 1080x1920, burn captions/title, export mp4        render.py
     transcribe()    the words heard, for captions and titles                           whisper.py
  storage_summary(), delete_vods() …  what's on disk                                    storage.py

What stays here: the helpers every step shares (which, ffmpeg, ffprobe, run, probe, Cancelled…). The step modules
import them from here, so engine imports each step module once they're defined.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

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
# storage: what's on disk, and cleaning it up: storage.py (re-exported here, so engine.<name> still works)
# --------------------------------------------------------------------------
from storage import (  # noqa: E402,F401
    _clip_files, _dir_size, _has_vod, _project_dirs, _prune, _remove, _size, _vod_files,
    delete_clips, delete_vods, recent_projects, storage_summary,
)


# --------------------------------------------------------------------------
# 1. acquire: acquire.py (re-exported here, so engine.<name> still works)
# --------------------------------------------------------------------------
from acquire import (  # noqa: E402,F401
    acquire, _chat_games, chat_times, _check_download_space, _clean_ytdlp_error, _container, _download_bytes,
    _download_chat, DOWNLOAD_STALL_S, _download_video, _early_sound, _finish_download, FRAGMENTS, game_at,
    _hand_over, _join_progress, kill_tree, load_chat, local_workdir, LOW_HEIGHT, low_quality, _meta, NO_FASTSTART,
    probe_url, _reusable, _sidecar_chat, _size_of, _Soon, SOUND_GRACE_S, _sound_part, Source, _Stepper, TDL_PART,
    _video_format, _watch_stall, _with_info, _write_info, _youtube_chat, _yt_chat, _yt_emoji, _ytdlp_cli,
    _ytdlp_module,
)


# --------------------------------------------------------------------------
# 2. analyze and 3. pick clips: analysis.py (re-exported here, so engine.<name> still works; callers use analysis.<name>)
# --------------------------------------------------------------------------
import analysis  # noqa: E402,F401
from analysis import (  # noqa: E402,F401
    AGREE_WEIGHT, Analysis, _analysis_key, ANALYSIS_VERSION, analyze, analyze_cached, _audio_z, _awkward_points,
    _awkward_word, chat_mood, chat_rate, chat_weight, Clip, clip_features, CONFUSED, _CONFUSED_ASK,
    CONFUSED_PHRASES, CRINGE, CRINGE_PHRASES, _EMOTE_TOK, _ends_sentence, estimate, EVENT_WEIGHTS, FINE_HZ,
    _finish, _fit, fixed_bounds, grades, _HMM, HYPE, is_reaction, _KIND_OF, KINDS, LAUGH_TEXT, _load_analysis,
    make_thumb, MAX_AUTO_CLIPS, measure_audio, MIN_AUTO_CLIPS, moment_scores, MOOD_LABEL, MOOD_MIN, NO_CHAT_JOIN,
    NO_CHAT_SCALE, pick_clips, REACT, REACT_PHRASES, _react_points, reaction_kind, _reason, refine_with_words,
    _robust_z, _rolling, _save_analysis, score_signals, smart_bounds, _snap, standout_bar, STANDOUT_GAP, strength,
    _STRETCH, STRETCHED, _TOKEN, TOKEN_REPEAT, _UHH, WEAK_QUALITY,
    MIN_LEN, MAX_LEN, STRONG, GOOD,
)


# --------------------------------------------------------------------------
# 4. render: render.py (re-exported here, so engine.<name> still works; callers use render.<name>)
# --------------------------------------------------------------------------
# whisper: whisper.py (re-exported here, so engine.<name> still works; callers use whisper.<name>)
from whisper import (  # noqa: E402,F401
    _busy_lock, _CARD_ERROR, CARD_GAIN, CARD_REST, _card_rest, extract_audio, FEW_WORDS, _HALLUCINATION, _hear,
    _load_whisper, REHEAR_FROM, _release_later, _release_timer, release_whisper, transcribe, _transcribe,
    whisper_available, _whisper_busy, whisper_cpu_threads, whisper_device, WHISPER_IDLE, WHISPER_KEEP,
    _whisper_lock, _whisper_used, _whisper_cache, _whisper_device, _whisper_said,
)
import render  # noqa: E402,F401
from render import (  # noqa: E402,F401
    _ass_color, _ass_escape, _ass_time, audio_filter, audio_peak, _balance, black_start, BLUR_SHARP, build_ass,
    build_filter, CAPTION_MOTIONS, CAPTION_STYLES, caption_words, _chunks, CPU_ENCODERS, _crop_failed, crop_filter,
    DARK_LUMA, effective_layout, EMPH_WORDS, _emphasised, _encoder_cache, _even, _ffmpeg_reason, FILLER_WORDS,
    final_size, _first_reason, FIT_MIN, fit_text, _font, FONT_GLYPH, FONT_SCALE, FONTS, fonts_dir, fonts_for,
    _frame_luma, _graph_failed, _HW, _HW_BUSY, HW_DECODE, hw_failed, _hw_fails, _HW_HEVC, HW_SLOTS, _hw_slots,
    hw_worked, _join_lines, LAYOUT_NAMES, _line_cost, LINE_LEADS, LINE_LEADS_SOFT, mark_anchor, _mark_dialogue,
    _mark_fs, mark_window, marks_of, MAX_CAMS, MAX_FPS, MAX_TEXT_SIZE, MAX_ZOOM, MIN_CAM_PX, motion_of, _mpos,
    mute_commands, mute_graph, _num, out_size, output_fps, picture_check, _probe_hw, punch_beats, PUNCH_EDGE,
    punch_filter, PUNCH_GAP, PUNCH_MAX, PUNCH_ZOOM, _rate_args, read_through, render_clip, RENDER_PRIORITY, ENDING_MAX, STALL_MAX,
    RENDER_THREADS, RenderOpts, SEEN_LUMA, SEEN_SHARE, SHAPES, _shifted, SPANS_INLINE, SPEEDS, _start_lumas,
    text_width, title_border, TITLE_DEFAULT_Y, TITLE_GROW, TITLE_STYLES, TITLE_TOP, title_top, TITLE_YELLOW,
    video_encoder, watermark_anchor, watermark_window, _with_rate, _wm_alpha, WM_POSITIONS, write_srt,
    W, H, PUNCH_IN, PUNCH_HOLD, PUNCH_OUT, WM_TOP, WM_BOTTOM,
)
