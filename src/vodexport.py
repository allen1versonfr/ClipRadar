"""
Export VOD: a whole video saved as one file, with the muting system and captions (the Export VOD screen).

  - Videos: the ones open now, the VODs Clip Radar downloaded before (still on disk), and any video file
    on this PC. A video that's only being exported isn't scored for clips: it's probed and that's all.
  - Captions: the whole video heard by Whisper in 10-minute stretches (each stretch once: the same
    transcript cache as clips), burned in with the caption style chosen on the screen, saved as a
    subtitle file next to the video, or both. With captions off, nothing is listened to.
  - Flagged words: "Check for flagged words" hears the video and lists what the content check flags.
    Each one is ticked or left, and the ticked ones are muted or bleeped (and masked in the captions).
  - Copyright scan: finds the stretches where music plays with the sound model Clip Radar already uses
    for laughter. Each stretch can have its music removed with the voices kept (voicekeep.py), be muted
    whole, or be left alone. It hears *music*, not which song: a safety net, not a guarantee.
  - Speed: when the picture isn't changed (no burned-in captions, the video's own size) it's copied as
    it is and only the sound is made again. Otherwise it's render.render_clip in a wide frame (16:9).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from typing import Optional

import numpy as np

import audio_events
import engine
import render
import whisper
import exports
import locations
import outcome
import procjob
import safety
import studio
import studio_auto
import ytextra
import voicekeep
import render_queue

CHUNK = 600.0            # seconds of sound heard at a time
MUSIC_ON = 0.45          # music probability (smoothed) from which a second counts as music
MUSIC_MIN = 6.0          # shorter music than this isn't listed (a jingle, a sound alert)
MUSIC_GAP = 6.0          # music stretches this close are one stretch
MUSIC_PAD = 1.0          # treated this much before and after, so a song's edges aren't heard
SPEECH_ON = 0.5          # speech probability from which a second counts as someone talking
CAPTION_MODES = ("off", "burn", "srt", "both")
WORD_MODES = ("mute", "bleep")
MUSIC_ACTIONS = ("voice", "mute", "keep")     # remove the music and keep voices | silence the stretch | leave it
QUALITY = ("fast", "balanced", "high")
SIZES = {"1080": 1.0, "720": 0.6667, "480": 0.4445}       # "source" is the video's own size
COPY_CODECS = ("h264", "hevc")                # pictures an .mp4 can take as they are
PREVIEW_LEN = 10.0       # seconds the exact preview renders
LIBRARY_MAX = 40
YT_UPLOAD_URL = os.environ.get("CLIPRADAR_YT_URL", "https://www.youtube.com/upload")

# How captions look on a wide video until you change them (the screen keeps its own copy: vodx_style).
VOD_STYLE = {"caption_style": "clean", "caption_font": "classic", "caption_color": "#FFFFFF", "caption_size": 60,
             "words_per_line": 7, "uppercase": False, "caption_pos": "bottom", "caption_anim": False,
             "caption_motion": "none", "caption_emph": False, "caption_fit": False}
STYLE_KEYS = ("caption_style", "caption_font", "caption_color", "caption_size", "words_per_line", "uppercase",
              "caption_pos", "cap_x", "cap_y", "caption_anim", "caption_motion", "caption_speed", "caption_emph",
              "caption_filler", "caption_punct", "caption_spacing", "caption_offset", "caption_fit", "cap_outline",
              "cap_shadow")


# ---------------------------------------------------------------- copyright scan

def _share(x, a: float, b: float, threshold: float) -> float:
    """The part of seconds [a, b) where per-second values x are at or over threshold (0..1)."""
    if x is None or not len(x):
        return 0.0
    part = np.nan_to_num(np.asarray(x, dtype=np.float32)[max(0, int(a)):max(0, int(math.ceil(b)))], nan=0.0)
    return round(float((part >= threshold).mean()), 2) if part.size else 0.0


def music_spans(music, duration: float, threshold: float = MUSIC_ON, min_len: float = MUSIC_MIN,
                gap: float = MUSIC_GAP, pad: float = MUSIC_PAD, speech=None) -> list:
    """Stretches where music plays, from per-second music probabilities:
    [{"start", "end", "confidence", "speech"}] (seconds; confidence 0..1, the stretch's average; speech 0..1,
    the part of it where someone is talking, from per-second speech probabilities when given)."""
    x = np.nan_to_num(np.asarray(music if music is not None else [], dtype=np.float32), nan=0.0)
    if not x.size:
        return []
    k = 3
    sm = np.convolve(np.pad(x, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")[:x.size]
    on = sm >= threshold
    runs: list = []
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if runs and i - runs[-1][1] <= gap:
                runs[-1][1] = j
            else:
                runs.append([i, j])
            i = j
        else:
            i += 1
    out = []
    end = float(duration) if duration else float(n)
    for a, b in runs:
        if b - a < min_len:
            continue
        out.append({"start": round(max(0.0, a - pad), 2), "end": round(min(end, b + pad), 2),
                    "confidence": round(float(x[a:b].mean()), 2), "speech": _share(speech, a, b, SPEECH_ON)})
    return out


def suggest_action(confidence: float, speech: float, can_remove: bool) -> str:
    """What a music stretch starts as on the screen, before you change it: "voice" (take the music out,
    keep the voices), "mute" (silence the whole stretch) or "keep" (leave it alone).

    confidence: how sure the scan is that it's music (0..1). speech: the part of the stretch where someone
    is talking (0..1). can_remove: whether the music remover is installed ("voice" needs it).
    """
    talking = speech >= 0.2           # someone talks over at least a fifth of it
    if talking:
        if can_remove:
            return "voice"            # the music goes, the talking stays: what the stretch is for
        # without the remover, muting would silence the talking too: only when there's little of it
        return "keep" if speech >= SPEECH_ON else "mute"
    # nobody talking: muting does what removal would, in no time. A weak detection is left alone, so game
    # sound the scan half-took for music isn't silenced
    return "mute" if confidence >= 0.5 else "keep"


def _scan_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "copyright.json")


def load_scan(pr: dict) -> Optional[dict]:
    """The last copyright scan of this video, if the video hasn't changed since."""
    if "copyright" in pr:
        return pr["copyright"]
    try:
        with open(_scan_file(pr), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("video") == _sig(pr):
            pr["copyright"] = {"spans": data["spans"], "at": data.get("at", 0), "how": data.get("how", "")}
            return pr["copyright"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_json(path: str, data: dict):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _sig(pr: dict) -> str:
    try:
        st = os.stat(pr["video"])
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _duration(pr: dict) -> float:
    an = pr.get("analysis")
    return float(getattr(an, "duration", 0) or (pr.get("info") or {}).get("duration") or pr.get("duration") or 0)


def scan_copyright(pr: dict, progress=lambda x: None, check=lambda: None, log=lambda m: None) -> dict:
    """Finds the music in a video. Uses what opening the video already heard when it can (the sound
    model ran then), otherwise listens to the whole video now."""
    if not (pr.get("info") or {}).get("has_audio", True):
        pr["copyright"] = {"spans": [], "at": time.time(), "how": "no sound"}
        return pr["copyright"]
    dur = _duration(pr)
    an = pr.get("analysis")
    ev = getattr(an, "events", None) or {}
    if "music" in ev and len(ev["music"]):
        how = "heard when the video was opened"
        progress(1.0)
    else:
        if not audio_events.available():
            raise RuntimeError("The copyright scan needs the laughter detector (the sound model that also hears "
                               "music). Get it in Settings › Downloads and tools, then scan again.")
        log("Listening to the whole video for music...")
        ev, how = audio_events.scan_file(pr["video"], dur, progress=progress, check=check), "heard now"
    spans = music_spans(ev.get("music"), dur, speech=ev.get("speech"))
    pr["copyright"] = {"spans": spans, "at": time.time(), "how": how}
    _write_json(_scan_file(pr), dict(pr["copyright"], video=_sig(pr)))
    total = sum(s["end"] - s["start"] for s in spans)
    log(f"Found {len(spans)} stretch{'es' if len(spans) != 1 else ''} with music ({engine.fmt_ts(total)} in all).")
    return pr["copyright"]


def _scan_view(scan: Optional[dict]) -> Optional[dict]:
    """The scan for the screen: each stretch with what Clip Radar would do with it to start with."""
    if not scan:
        return None
    can = voicekeep.available()
    spans = []
    for s in scan["spans"]:
        act = suggest_action(float(s.get("confidence", 0)), float(s.get("speech", 0)), can)
        spans.append(dict(s, speech=s.get("speech", 0), suggest=act if act in MUSIC_ACTIONS and (can or act != "voice") else "mute"))
    return dict(scan, spans=spans)


# ---------------------------------------------------------------- which videos

def usable(pr: Optional[dict]) -> bool:
    """A project Export VOD can work on: an opened video, or one opened here only to export."""
    return bool(pr and not pr.get("live") and pr.get("video") and ("analysis" in pr or pr.get("light")))


_library: dict = {}       # pid -> (workdir, video, title): what library() last listed, so a video is opened by its id


def library(S) -> list:
    """VODs Clip Radar downloaded before, and files opened before, that aren't open now and are still on disk."""
    out = []
    for root in (locations.project_roots() or [S.WORKROOT]):
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for d in names:
            wd = os.path.join(root, d)
            meta = engine._meta(wd)
            if not meta or meta.get("live") or not meta.get("source"):
                continue
            video = str(meta["source"]) if meta.get("local") else engine.project_video(wd)
            if not video or not os.path.isfile(video):
                continue
            pid = S._pid_for(wd)
            if usable(S.projects.get(pid)):
                continue
            title = str(meta.get("title") or d)
            _library[pid] = (wd, video, title)
            out.append({"pid": pid, "title": title, "duration": round(float(meta.get("duration") or 0), 1),
                        "updated": float(meta.get("updated") or 0), "where": "file" if meta.get("local") else "downloaded"})
    out.sort(key=lambda x: -x["updated"])
    return out[:LIBRARY_MAX]


def open_video(S, pid: str = "", path: str = "") -> str:
    """Make a video ready to export without scoring it for clips: one from library() by its id, or a video file
    anywhere on this PC by its path. Returns its project id."""
    if path:
        video = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
        if not os.path.isfile(video):
            raise RuntimeError("That file isn't there. Check the path, or pick it again.")
        if os.path.splitext(video)[1].lower() not in engine.VIDEO_EXTS:
            raise RuntimeError("That isn't a video file Clip Radar can read (.mp4, .mkv, .mov, .webm, .flv, .ts, .m4v, .avi).")
        wd = engine.local_workdir(S.WORKROOT, video)
        title = os.path.splitext(os.path.basename(video))[0]
    else:
        if pid not in _library:
            library(S)
        if pid not in _library:
            raise RuntimeError("That video isn't on this PC any more.")
        wd, video, title = _library[pid]
        if not os.path.isfile(video):
            raise RuntimeError("That video's file was deleted.")
    pid = S._pid_for(wd)
    if usable(S.projects.get(pid)):
        return pid
    try:
        info = engine.probe(video)
    except Exception as e:
        raise RuntimeError("That video couldn't be read: " + (str(e).strip().splitlines() or ["unknown error"])[-1][:200]) from None
    os.makedirs(wd, exist_ok=True)
    pr = {"video": video, "workdir": wd, "title": title, "info": info, "duration": info["duration"],
          "tx_lock": threading.Lock(), "live": "", "light": True, "clips": [], "gems": []}
    S._load_transcripts(pr)
    S.projects[pid] = pr
    if path and not engine._meta(wd):
        try:          # remembered like any opened file, so it's listed here (and on Home) next time
            engine._save_meta(wd, source=video, local=True, title=title, duration=info["duration"])
        except OSError:
            pass
    return pid


# ---------------------------------------------------------------- export settings

def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _pairs(rows, duration: float, extra=None) -> list:
    """[(start, end)] or [(start, end, extra(row))] from what the screen sent: numbers, inside the video, in order."""
    out = []
    for sp in rows or []:
        try:
            a, b = float(sp[0]), float(sp[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        a, b = max(0.0, a), min(duration or b, b)
        if b - a >= 0.1:
            out.append((round(a, 3), round(b, 3)) + ((extra(sp),) if extra else ()))
    return sorted(out)


def clean_options(body: dict, duration: float) -> dict:
    """What the Export VOD screen asked for, checked: unknown values fall back to the defaults, and
    spans are numbers inside the video, in order."""
    style = {k: v for k, v in (body.get("style") or {}).items() if k in STYLE_KEYS} if isinstance(body.get("style"), dict) else {}
    return {
        "captions": body.get("captions") if body.get("captions") in CAPTION_MODES else "burn",
        "words": body.get("words") if body.get("words") in WORD_MODES else "mute",
        # the flagged words ticked in the queue: [(start, end)]
        "word_spans": _pairs(body.get("word_spans"), duration),
        # music stretches: [(start, end, "voice" | "mute")]
        "music": _pairs(body.get("music"), duration,
                        lambda sp: sp[2] if len(sp) > 2 and sp[2] in ("voice", "mute") else "mute"),
        "quality": body.get("quality") if body.get("quality") in QUALITY else "balanced",
        "size": str(body.get("size")) if str(body.get("size")) in SIZES else "source",
        "whisper_model": str(body.get("whisper_model") or "small")[:40],
        "language": str(body.get("language") or "")[:10],
        "style": dict(VOD_STYLE, **style),
        "hw_encode": body.get("hw_encode", True) is not False,
    }


def render_opts(S, o: dict, info: dict) -> render.RenderOpts:
    """A wide frame showing the whole picture: as it is for a 16:9 video, on a blurred background otherwise,
    with the captions styled as chosen on the screen."""
    sw, sh = info.get("width") or 1920, info.get("height") or 1080
    wide = abs(sw / max(1, sh) - 16 / 9) < 0.03
    scale = SIZES.get(o["size"]) or min(1.0, max(0.25, sh / 1080))
    ro = S._ropts(dict(o["style"], whisper_model=o["whisper_model"], language=o["language"]))
    return replace(ro, layout="full" if wide else "blur", aspect="16:9", track="off", punch="off",
                   captions=o["captions"] in ("burn", "both"), hw_encode=o["hw_encode"], speed=o["quality"], scale=scale,
                   loudness=0.0,           # a whole stream keeps its own volume; only peaks are limited
                   gain_db=float(o.get("gain_db") or 0))   # a segment made louder or quieter (Segments)


def can_copy(o: dict, info: dict) -> bool:
    """Whether the picture can go into the export untouched: nothing drawn on it, its own size, and a kind
    of video an .mp4 holds."""
    return o["size"] == "source" and o["captions"] not in ("burn", "both") and info.get("vcodec") in COPY_CODECS


def output_name(pr: dict, outdir: str) -> str:
    base = exports.safe(pr.get("title") or os.path.splitext(os.path.basename(pr["video"]))[0] or "VOD")
    name, n = f"{base} (full VOD).mp4", 2
    while os.path.exists(os.path.join(outdir, name)):
        name = f"{base} (full VOD) ({n}).mp4"
        n += 1
    return name


# ---------------------------------------------------------------- hearing, and the flagged words queue

def hear_all(S, pr: dict, model: str, lang: str, a: float, b: float, progress, check, step, what: str = "captions") -> list:
    """The words said from a to b (absolute times), heard a stretch at a time. YouTube's own captions
    when the video has them."""
    yt = pr.get("yt") or {}
    if yt.get("words"):
        progress(1.0)
        return ytextra.words_in(yt, a, b)
    words: list = []
    total = _duration(pr) or b
    n = max(1, int(math.ceil((b - a) / CHUNK)))
    for k in range(n):
        check()
        x, y = a + k * CHUNK, min(b, a + (k + 1) * CHUNK)
        step(f"Listening for {what} ({engine.fmt_ts(x)} to {engine.fmt_ts(y)} of {engine.fmt_ts(total)})")
        words += S.ensure_words(pr, x, y, model, lang, check)
        progress((k + 1) / n)
    S._save_transcripts(pr)
    return words


def heard(pr: dict, model: str, lang: str, a: float, b: float) -> dict:
    """The words already heard between a and b, without listening to anything: {"words", "covered"}."""
    yt = pr.get("yt") or {}
    if yt.get("words"):
        return {"words": ytextra.words_in(yt, a, b), "covered": [[a, b]]}
    lock = pr.get("tx_lock")
    if lock is None:
        return {"words": [], "covered": []}
    with lock:
        tx = (pr.get("tx") or {}).get(f"{model}|{lang}") or {}
        words = [w for w in tx.get("words", []) if a <= w["start"] < b]
        covered = [[max(a, ca), min(b, cb)] for ca, cb in tx.get("covered", []) if cb > a and ca < b]
    return {"words": words, "covered": covered}


def find_flags(words: list, cfg: Optional[dict] = None) -> list:
    """Everything the content check flags in what's said, for the queue: [{"id", "start", "end", "text", "label",
    "cat", "severity", "context", "on"}]. on: ticked to start with (its category is one Settings › Content check
    mutes). context: the words around it, the flagged ones masked."""
    cfg = dict(cfg or safety.load_config(), enabled=True)
    muted = set(cfg.get("censor_categories") or [])
    out = []
    found = [f for f in safety.scan(words, "", "", cfg)["flags"] if f["source"] == "speech"]
    # every flagged word is masked in every context line, not only the one the line is about
    hide = {i for f in found if f["cat"] not in ("personal", "custom") for i in range(f["first"], f["last"] + 1)}
    for f in found:
        a, b = f["first"], f["last"]
        around = [safety.mask(w["word"]) if i in hide else w["word"]
                  for i, w in enumerate(words[max(0, a - 5):b + 6], max(0, a - 5))]
        out.append({"id": f"{f['start']:.2f}-{f['end']:.2f}", "start": round(float(f["start"]), 3), "end": round(float(f["end"]), 3),
                    "text": f["text"], "label": f["label"], "cat": f["cat"], "severity": f["severity"],
                    "context": " ".join(around), "on": f["cat"] in muted})
    return out


def _flags_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "vod_flags.json")


def load_flags(pr: dict) -> Optional[dict]:
    """The last flagged-words check of this video ({"flags", "at", "rules", "model", "language"}), if the video
    hasn't changed since. stale: what counts as a flag has changed in Settings since it was made."""
    if "vod_flags" not in pr:
        pr["vod_flags"] = None
        try:
            with open(_flags_file(pr), encoding="utf-8") as f:
                data = json.load(f)
            if data.get("video") == _sig(pr) and isinstance(data.get("flags"), list):
                pr["vod_flags"] = {k: data.get(k) for k in ("flags", "at", "rules", "model", "language")}
        except (OSError, ValueError, TypeError):
            pass
    fl = pr["vod_flags"]
    return dict(fl, stale=fl.get("rules") != safety.rules_sig()) if fl else None


def check_words(S, job: dict, pr: dict, model: str, lang: str) -> dict:
    """Hear the whole video and list what the content check flags in it."""
    if not (pr.get("info") or {}).get("has_audio", True):
        raise RuntimeError("This video has no sound.")
    if not whisper.whisper_available() and not (pr.get("yt") or {}).get("words"):
        raise RuntimeError("Finding flagged words needs the speech model (Whisper). Get it in Settings › Downloads and tools.")
    dur = _duration(pr)
    words = hear_all(S, pr, model, lang, 0.0, dur, S.job_progress(job, 0, 0.98), S.job_check(job), S.job_step(job), "flagged words")
    flags = find_flags(words)
    pr["vod_flags"] = {"flags": flags, "at": time.time(), "rules": safety.rules_sig(), "model": model, "language": lang}
    _write_json(_flags_file(pr), dict(pr["vod_flags"], video=_sig(pr)))
    S.job_logger(job)(f"Heard {len(words):,} words, {len(flags)} flagged.")
    return {"flags": len(flags)}


def mask_spoken(words: list, spans: list) -> list:
    """words with the ones said inside a muted span masked, so the captions don't spell out what was silenced."""
    if not spans:
        return words
    out = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        out.append(dict(w, word=safety.mask(w["word"])) if any(a <= mid <= b for a, b in spans) else w)
    return out


# ---------------------------------------------------------------- the export

def _run_ffmpeg(cmd: list, cwd: str, length: float, progress, check) -> None:
    """Run FFmpeg (asked for -progress pipe:1), moving the bar and stopping when the job is cancelled."""
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
    procjob.adopt(p)
    err: list = []
    reader = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
    reader.start()
    try:
        for line in p.stdout:
            if line.startswith("out_time_us="):
                try:
                    progress(min(0.999, max(0.0, int(line.split("=")[1]) / 1e6) / max(1.0, length)))
                except ValueError:
                    pass
            try:
                check()
            except engine.Cancelled:
                p.kill()
                raise
    finally:
        p.wait()
        reader.join(timeout=5)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + render._ffmpeg_reason("".join(err[-60:])))


def copy_video(video: str, out_path: str, info: dict, ro: render.RenderOpts, spans: list, audio_src: str,
               progress, check) -> None:
    """The export with the picture copied as it is and only the sound made again (muted spans, the sound with
    its music removed). Many times faster than encoding the picture: nothing is decoded but the sound."""
    work = os.path.dirname(os.path.abspath(out_path))
    tag = uuid.uuid4().hex[:8]
    tmp, cmd_name = f"vc_{tag}.part.mp4", f"vc_{tag}.cmd"
    length = float(info["duration"])
    cmd = [engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", video]
    if audio_src:
        cmd += ["-i", audio_src]
    ain = "1:a:0" if audio_src else "0:a:0"
    cmd += ["-map", "0:v:0", "-c:v", "copy"]
    if info.get("vcodec") == "hevc":
        cmd += ["-tag:v", "hvc1"]
    try:
        if info.get("has_audio"):
            tail = render.audio_filter(ro)
            if spans:
                graph, commands = render.mute_graph(spans, length, tail, ain, cmd_name)
                if commands:
                    with open(os.path.join(work, cmd_name), "w", encoding="utf-8") as f:
                        f.write(commands)
                cmd += ["-filter_complex", graph, "-map", "[a]"]
            else:
                cmd += ["-map", ain, "-af", tail]
            cmd += ["-c:a", "aac", "-b:a", f"{int(ro.abitrate)}k", "-ar", "48000"]
        cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero", tmp]
        _run_ffmpeg(cmd, work, length, progress, check)
        os.replace(os.path.join(work, tmp), out_path)
    finally:
        for name in (tmp, cmd_name):
            try:
                os.remove(os.path.join(work, name))
            except OSError:
                pass
    progress(1.0)


def render_range(S, job: dict, pr: dict, o: dict, a: float, b: float, path: str, whole: bool,
                 part: bool = False, band: tuple = (0.0, 1.0), label: str = "", words: Optional[list] = None) -> dict:
    """Make the video from a to b with everything the screen chose. whole: the real export (the picture is
    copied when it can be, a subtitle file is written); otherwise the exact preview of a few seconds.
    part: one stretch of a Segments export, at full quality but never copied (a copied picture can only
    start on a keyframe, so the stretches wouldn't meet where they should). band: the share of the job's bar
    this takes, label: put before each step ("Part 2 of 5: "). words: the captions to use (absolute times)
    instead of hearing them, for captions edited by hand."""
    log, check = S.job_logger(job), S.job_check(job)
    step = (lambda m: S.job_step(job)(label + m)) if label else S.job_step(job)
    lo, hi = band
    bar = lambda x, y: S.job_progress(job, lo + x * (hi - lo), lo + y * (hi - lo))      # noqa: E731
    video = pr["video"]
    info = engine.probe(video)
    length = b - a
    ro = render_opts(S, o, info)
    if not whole and not part:
        ro = replace(ro, speed="fast", crf=28, scale=min(ro.scale, SIZES["720"]))
    has_audio = bool(info.get("has_audio"))
    voice = [(x, y) for x, y, m in o["music"] if m == "voice" and y > a and x < b] if has_audio else []
    hush = [(x, y) for x, y, m in o["music"] if m == "mute" and y > a and x < b] if has_audio else []
    told = [(x, y) for x, y in o["word_spans"] if y > a and x < b] if has_audio else []
    if voice and not voicekeep.available():
        raise RuntimeError("Removing music needs the music remover. Get it in Settings › Downloads and tools, "
                           "or set those stretches to Mute everything.")
    # the bar: listening (only when captions are on), taking the music out, then the picture
    given = words is not None
    need_words = o["captions"] != "off" and has_audio and not given
    copying = whole and not part and can_copy(o, info)
    w_hear = 0.45 if need_words else 0.0
    w_voice = (0.6 if copying else 0.25) * (1 - w_hear) if voice else 0.0
    words = [w for w in (words or []) if a <= w["start"] < b] if given and o["captions"] != "off" else []
    if need_words:
        if not whisper.whisper_available() and not (pr.get("yt") or {}).get("words"):
            log("Captions skipped: the speech model (Whisper) isn't installed.")
            job["warning"] = "Exported without captions: the speech model (Whisper) isn't installed."
        else:
            words = hear_all(S, pr, o["whisper_model"], o["language"], a, b, bar(0, w_hear), check, step)
            log(f"Heard {len(words):,} words.")
    words = exports.replace_words(words, exports.load().get("replace")) if words else words
    words = mask_spoken(words, told)
    if told:
        log(f"{'Bleeping' if o['words'] == 'bleep' else 'Muting'} {len(told)} flagged word{'s' if len(told) != 1 else ''}.")
    rel = lambda x, y: (max(0.0, x - a), min(length, y - a))          # noqa: E731  the video's time -> this file's
    spans = [(*rel(x, y), o["words"]) for x, y in told] + [(*rel(x, y), "mute") for x, y in hush]
    if hush:
        log(f"Muting {len(hush)} stretch{'es' if len(hush) != 1 else ''} of music ({engine.fmt_ts(sum(y - x for x, y in hush))}).")
    tmpdir = locations.temp_dir(os.path.basename(pr["workdir"]), "vodexport")
    os.makedirs(tmpdir, exist_ok=True)
    audio_src = ""
    try:
        if voice:
            total = sum(y - x for x, y in voice)
            step(f"Taking the music out of {engine.fmt_ts(total)} (voices stay)")
            audio_src = os.path.join(tmpdir, f"voice_{uuid.uuid4().hex[:8]}.flac")
            t0 = time.time()
            got = voicekeep.keep_voice(video, [rel(x, y) for x, y in voice], audio_src, start=a,
                                       length=0.0 if whole and not part else length,
                                       progress=bar(w_hear, w_hear + w_voice), check=check)
            log(f"Music removed from {len(voice)} stretch{'es' if len(voice) != 1 else ''} ({engine.fmt_ts(total)}) in "
                f"{engine.fmt_ts(time.time() - t0)}, {got['blocks']} blocks through the model.")
        prog = bar(w_hear + w_voice, 1.0)
        if copying:
            step("Copying the picture, making the sound")
            log("The picture is copied as it is (no burned-in captions, the video's own size).")
            copy_video(video, path, info, ro, spans, audio_src, prog, check)
        else:
            step("Rendering" if part else "Rendering the whole video" if whole else "Rendering the preview")
            burn = [dict(w, start=w["start"] - a, end=w["end"] - a) for w in words] if ro.captions else []
            render.render_clip(video, a, b, path, ro, title="", log=log, progress=prog, check=check,
                               words=burn, censor=spans, censor_mode="mute", audio_src=audio_src,
                               warn=lambda m: job.__setitem__("warning", m))
    finally:
        if audio_src:
            try:
                os.remove(audio_src)
            except OSError:
                pass
        if voice:
            voicekeep.release()
    return {"words": words, "ro": ro, "copied": copying, "muted_words": len(told), "muted_music": len(hush),
            "voice_music": len(voice)}


def run_export(S, job: dict, pid: str, o: dict) -> dict:
    pr = S.projects[pid]
    video = pr["video"]
    if not os.path.exists(video):
        raise RuntimeError("This video's file was deleted, so it can't be exported.")
    info = engine.probe(video)
    dur = float(info.get("duration") or _duration(pr))
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length.")
    outdir = locations.clips_dir(pr["workdir"])
    render_queue._validate_render({"id": job["id"], "start": 0.0, "end": dur}, pr, outdir)
    if can_copy(o, info):
        try:
            need = os.path.getsize(video) + 200e6
            if shutil.disk_usage(outdir).free < need:
                raise OSError(28, f"No space left on device (about {int(need // 1_000_000)} MB needed)")
        except FileNotFoundError:
            pass
    else:
        render_queue._check_space(outdir, dur * render_opts(S, o, info).scale * 0.5)   # a wide frame at stream bitrates
    name = output_name(pr, outdir)
    path = os.path.join(outdir, name)
    t0 = time.time()
    r = render_range(S, job, pr, o, 0.0, dur, path, whole=True)
    srt = ""
    if o["captions"] in ("srt", "both") and r["words"]:
        srt = os.path.splitext(path)[0] + ".srt"
        render.write_srt(r["words"], r["ro"], srt, dur)
    took = time.time() - t0
    S.job_logger(job)(f"Saved {name} in {engine.fmt_ts(took)}.")
    pr["vod_out"] = path
    pr.pop("vod_yt", None)
    return {"path": path, "name": name, "folder": outdir, "srt": os.path.basename(srt) if srt else "",
            "muted_words": r["muted_words"], "muted_music": r["muted_music"], "voice_music": r["voice_music"],
            "copied": r["copied"], "took": round(took, 1), "captions": bool(r["words"]) and o["captions"] != "off",
            "warning": job.get("warning", "")}


def run_preview(S, job: dict, pid: str, o: dict, at: float) -> dict:
    """A few seconds from `at`, made exactly like the export (music removed, words muted, captions burned in)."""
    pr = S.projects[pid]
    dur = _duration(pr)
    a = max(0.0, min(at, max(0.0, dur - PREVIEW_LEN)))
    b = min(dur, a + PREVIEW_LEN)
    if b - a < 1:
        raise RuntimeError("This video is too short to preview.")
    outdir = locations.temp_dir(os.path.basename(pr["workdir"]), "preview")
    os.makedirs(outdir, exist_ok=True)
    old = sorted(f for f in os.listdir(outdir) if f.startswith("vod_") and f.endswith(".mp4"))
    for f in old[:-2]:
        try:
            os.remove(os.path.join(outdir, f))
        except OSError:
            pass
    name = f"vod_{int(time.time() * 1000)}.mp4"
    render_range(S, job, pr, o, a, b, os.path.join(outdir, name), whole=False)
    return {"url": f"/files/{pid}/preview/{name}", "start": round(a, 2), "end": round(b, 2)}


# ---------------------------------------------------------------- YouTube Studio

def to_youtube(S, pid: str, body: dict) -> dict:
    """The exported file handed to YouTube Studio. auto: Clip Radar's own browser window puts the video in and
    types the title and description; the rest (audience, visibility, Publish) is always yours. Otherwise the file
    is shown in File Explorer and YouTube's upload page opens in your browser."""
    pr = S.projects[pid]
    path = pr.get("vod_out") or ""
    if not path or not os.path.isfile(path):
        raise RuntimeError("Export the video first: there's no exported file to upload yet.")
    title = studio_auto.youtube_title(str(body.get("title") or pr.get("title") or ""))
    desc = str(body.get("description") or "")[:5000]
    if not body.get("auto"):
        shown = studio.reveal(path)
        studio.open_url(YT_UPLOAD_URL)
        pr["vod_yt"] = {"at": time.time(), "state": "opened"}
        return {"opened": True, "revealed": shown}
    if not studio_auto.available():
        raise RuntimeError("Clip Radar needs Microsoft Edge or Google Chrome to upload for you.")
    old = _busy(S, pr, "vod_yt_job")
    if old:
        return {"job": old["id"]}
    job = S.new_job("youtube")
    job["pid"] = pid
    pr["vod_yt_job"] = job["id"]
    pr["vod_yt"] = {"at": time.time(), "state": "working"}

    def go():
        try:
            r = studio_auto.upload_youtube(path, title, desc, YT_UPLOAD_URL, S.job_step(job), S.job_check(job))
        except studio_auto.StudioError as e:
            outcome.log("upload", "failed", site="youtube", step=e.step)
            studio.reveal(path)                 # finish it by hand: the file, shown
            where = "the YouTube window"
            if e.step == "browser":
                studio.open_url(YT_UPLOAD_URL)
                where = "YouTube Studio, opening in your browser now"
            pr["vod_yt"] = {"at": time.time(), "state": "failed", "error": str(e)}
            raise RuntimeError(f"{e} The video is selected in File Explorer, so you can finish in {where}.") from None
        except Exception as e:
            if isinstance(e, engine.Cancelled):
                outcome.log("upload", "cancelled", site="youtube")
            else:
                outcome.log("upload", "failed", site="youtube", step="error")
            pr["vod_yt"] = {"at": time.time(), "state": "failed", "error": str(e)[:300] or type(e).__name__}
            raise
        outcome.log("upload", "ok", site="youtube", title=r.get("title"), description=r.get("description"))
        pr["vod_yt"] = {"at": time.time(), "state": "ready"}
        return r
    S.run_job(job, go)
    return {"job": job["id"]}


# ---------------------------------------------------------------- jobs and the screen

def _busy(S, pr: dict, key: str) -> Optional[dict]:
    j = S.jobs.get(pr.get(key, ""))
    return j if j and j["status"] == "running" else None


def _start(S, pid: str, key: str, kind: str, fn) -> dict:
    """One job of a kind per video: the one running, or a new one."""
    pr = S.projects[pid]
    old = _busy(S, pr, key)
    if old:
        return old
    job = S.new_job(kind)
    job["pid"] = pid
    pr[key] = job["id"]
    S.run_job(job, lambda: fn(job))
    return job


def start_scan(S, pid: str) -> dict:
    pr = S.projects[pid]

    def go(job):
        S.job_step(job)("Looking for music")
        return scan_copyright(pr, S.job_progress(job, 0, 1), S.job_check(job), S.job_logger(job))
    return _start(S, pid, "copyright_job", "copyright", go)


def start_words(S, pid: str, body: dict) -> dict:
    pr = S.projects[pid]
    model, lang = str(body.get("whisper_model") or "small")[:40], str(body.get("language") or "")[:10]
    return _start(S, pid, "vod_words_job", "vodwords", lambda job: check_words(S, job, pr, model, lang))


def start_hear(S, pid: str, body: dict) -> dict:
    """Hear half a minute around `at`, so the preview has captions there."""
    pr = S.projects[pid]
    model, lang = str(body.get("whisper_model") or "small")[:40], str(body.get("language") or "")[:10]
    try:
        at = float(body.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    a = max(0.0, at - 2.0)
    b = min(_duration(pr) or a + 30.0, a + 30.0)

    def go(job):
        if not whisper.whisper_available():
            raise RuntimeError("Captions need the speech model (Whisper). Get it in Settings › Downloads and tools.")
        words = hear_all(S, pr, model, lang, a, b, S.job_progress(job, 0, 1), S.job_check(job), S.job_step(job))
        return {"start": a, "end": b, "words": len(words)}
    return _start(S, pid, "vod_hear_job", "vodhear", go)


def start_export(S, pid: str, body: dict) -> dict:
    o = clean_options(body, _duration(S.projects[pid]))
    return _start(S, pid, "vodexport_job", "vodexport", lambda job: run_export(S, job, pid, o))


def start_preview(S, pid: str, body: dict) -> dict:
    pr = S.projects[pid]
    o = clean_options(body, _duration(pr))
    try:
        at = float(body.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    old = _busy(S, pr, "vod_preview_job")
    if old:
        old["cancel"] = True          # a newer preview replaces the one being made
        pr.pop("vod_preview_job", None)
    return _start(S, pid, "vod_preview_job", "vodpreview", lambda job: run_preview(S, job, pid, o, at))


def _job_view(S, job: Optional[dict]) -> Optional[dict]:
    return dict(S.job_public(job), warning=job.get("warning", "")) if job else None


def view(S) -> dict:
    """The Export VOD screen: each video that can be exported (not live sessions or their moments), its
    copyright scan and flagged words, the jobs running or last run on it, and the videos on disk that could
    be opened."""
    vids = []
    for pid, pr in list(S.projects.items()):
        if not usable(pr):
            continue
        info = pr.get("info") or {}
        job = lambda key: _job_view(S, S.jobs.get(pr.get(key, "")))      # noqa: E731
        out = pr.get("vod_out") or ""
        vids.append({
            "pid": pid, "title": pr.get("title") or os.path.basename(pr["video"]),
            "duration": round(_duration(pr), 1), "exists": os.path.exists(pr["video"]),
            "has_audio": bool(info.get("has_audio", True)),
            "has_yt_words": bool((pr.get("yt") or {}).get("words")),
            "width": int(info.get("width") or 0), "height": int(info.get("height") or 0),
            "copyable": info.get("vcodec") in COPY_CODECS, "light": bool(pr.get("light")),
            "scan": _scan_view(load_scan(pr)), "scan_job": job("copyright_job"),
            "flags": load_flags(pr), "words_job": job("vod_words_job"), "hear_job": job("vod_hear_job"),
            "preview_job": job("vod_preview_job"), "export_job": job("vodexport_job"),
            "exported": bool(out and os.path.isfile(out)), "youtube": pr.get("vod_yt"), "youtube_job": job("vod_yt_job"),
        })
    cfg = safety.load_config()
    return {"videos": vids, "library": library(S), "music_model": audio_events.available(),
            "voice_model": voicekeep.available(), "voice_model_mb": voicekeep.MODEL_MB,
            "whisper": whisper.whisper_available(), "browser": studio_auto.available(),
            "censor": cfg.get("censor", "off"), "censor_categories": [safety.CATEGORIES[c]["label"] for c in
                                                                      cfg.get("censor_categories", []) if c in safety.CATEGORIES]}


def suggested(pr: dict) -> tuple:
    """What Export VOD starts with for a video, for an export that has no screen of its own (Segments): each
    music stretch as the copyright scan suggests, and the flagged words in the categories Settings › Content
    check mutes. ([(start, end, "voice" | "mute")], [(start, end)])"""
    scan = _scan_view(load_scan(pr))
    music = [(s["start"], s["end"], s["suggest"]) for s in (scan or {}).get("spans", []) if s["suggest"] != "keep"]
    flags = (load_flags(pr) or {}).get("flags") or []
    return music, [(max(0.0, f["start"] - 0.05), f["end"] + 0.05) for f in flags if f.get("on")]
