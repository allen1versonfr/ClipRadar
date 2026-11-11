"""
Export VOD: a whole video saved as one file, with the muting system and captions (the Export VOD screen).

  - Captions: the whole video heard by Whisper in 10-minute stretches (each stretch once: the same
    transcript cache as clips, so stretches a clip already heard aren't heard again), burned in, saved
    as a subtitle file next to the video, or both.
  - Muting: words the content check flags are muted or bleeped, and masked in the captions, using
    the categories chosen in Settings › Content check.
  - Copyright scan: finds the stretches where music plays (the usual cause of copyright claims and
    muted VODs) with the sound model Clip Radar already uses for laughter, and lists them so each can
    be muted or kept. It hears *music*, not which song: it can't tell licensed music from a claimed
    track, so it's a safety net, not a guarantee.

The render itself is engine.render_clip over the whole video in a wide frame (16:9).
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Optional

import numpy as np

import audio_events
import engine
import exports
import locations
import safety
import ytextra

CHUNK = 600.0            # seconds of sound heard at a time
MUSIC_ON = 0.45          # music probability (smoothed) from which a second counts as music
MUSIC_MIN = 6.0          # shorter music than this isn't listed (a jingle, a sound alert)
MUSIC_GAP = 6.0          # music stretches this close are one stretch
MUSIC_PAD = 1.0          # muted this much before and after, so a song's edges aren't heard
CAPTION_MODES = ("off", "burn", "srt", "both")
WORD_MODES = ("off", "mute", "bleep")
QUALITY = ("fast", "balanced", "high")
SIZES = {"1080": 1.0, "720": 0.6667, "480": 0.4445}


# ---------------------------------------------------------------- copyright scan

def music_spans(music, duration: float, threshold: float = MUSIC_ON, min_len: float = MUSIC_MIN,
                gap: float = MUSIC_GAP, pad: float = MUSIC_PAD) -> list:
    """Stretches where music plays, from per-second music probabilities:
    [{"start", "end", "confidence"}] (seconds; confidence 0..1, the stretch's average)."""
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
                    "confidence": round(float(x[a:b].mean()), 2)})
    return out


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


def _save_scan(pr: dict):
    try:
        tmp = _scan_file(pr) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(pr["copyright"], video=_sig(pr)), f)
        os.replace(tmp, _scan_file(pr))
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
    return float(getattr(an, "duration", 0) or (pr.get("info") or {}).get("duration") or 0)


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
        music, how = ev["music"], "heard when the video was opened"
        progress(1.0)
    else:
        if not audio_events.available():
            raise RuntimeError("The copyright scan needs the laughter detector (the sound model that also hears "
                               "music). Get it in Settings › Downloads and tools, then scan again.")
        log("Listening to the whole video for music...")
        music, how = audio_events.scan_file(pr["video"], dur, progress=progress, check=check).get("music"), "heard now"
    spans = music_spans(music, dur)
    pr["copyright"] = {"spans": spans, "at": time.time(), "how": how}
    _save_scan(pr)
    total = sum(s["end"] - s["start"] for s in spans)
    log(f"Found {len(spans)} stretch{'es' if len(spans) != 1 else ''} with music ({engine.fmt_ts(total)} in all).")
    return pr["copyright"]


# ---------------------------------------------------------------- export settings

def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def clean_options(body: dict, duration: float) -> dict:
    """What the Export VOD screen asked for, checked: unknown values fall back to the defaults, and
    music spans are numbers inside the video, in order."""
    caps = body.get("captions") if body.get("captions") in CAPTION_MODES else "burn"
    words = body.get("words") if body.get("words") in WORD_MODES else "mute"
    spans = []
    for sp in body.get("music") or []:
        try:
            a, b = float(sp[0]), float(sp[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        a, b = max(0.0, a), min(duration or b, b)
        if b - a >= 0.1:
            spans.append((round(a, 3), round(b, 3)))
    music_mode = body.get("music_mode") if body.get("music_mode") in ("mute", "bleep") else "mute"
    return {
        "captions": caps, "words": words, "music": sorted(spans), "music_mode": music_mode,
        "quality": body.get("quality") if body.get("quality") in QUALITY else "balanced",
        "size": str(body.get("size")) if str(body.get("size")) in SIZES else "1080",
        "whisper_model": str(body.get("whisper_model") or "small")[:40],
        "language": str(body.get("language") or "")[:10],
        "caption_font": str(body.get("caption_font") or "classic")[:40],
        "caption_color": body.get("caption_color") if isinstance(body.get("caption_color"), str)
        and len(body["caption_color"]) == 7 and body["caption_color"].startswith("#") else "#FFFFFF",
        "caption_size": _int(body.get("caption_size"), 60, 40, 120),
        "hw_encode": body.get("hw_encode", True) is not False,
    }


def render_opts(o: dict, info: dict) -> engine.RenderOpts:
    """A wide frame showing the whole picture: as it is for a 16:9 video, on a blurred background otherwise.
    Captions sit low, a sentence at a time, without the clip-style word pops."""
    sw, sh = info.get("width") or 1920, info.get("height") or 1080
    wide = abs(sw / max(1, sh) - 16 / 9) < 0.03
    return engine.RenderOpts(
        layout="full" if wide else "blur", aspect="16:9", track="off", punch="off",
        captions=o["captions"] in ("burn", "both"), caption_pos="bottom", caption_style="clean",
        caption_font=o["caption_font"], caption_color=o["caption_color"], caption_size=o["caption_size"],
        words_per_line=7, uppercase=False, caption_anim=False, caption_motion="none", caption_emph=False,
        caption_fit=False, whisper_model=o["whisper_model"], language=o["language"],
        hw_encode=o["hw_encode"], speed=o["quality"], scale=SIZES[o["size"]],
        loudness=0.0,           # a whole stream keeps its own volume; only peaks are limited
    )


def output_name(pr: dict, outdir: str) -> str:
    base = exports.safe(pr.get("title") or os.path.splitext(os.path.basename(pr["video"]))[0] or "VOD")
    name, n = f"{base} (full VOD).mp4", 2
    while os.path.exists(os.path.join(outdir, name)):
        name = f"{base} (full VOD) ({n}).mp4"
        n += 1
    return name


# ---------------------------------------------------------------- the export

def hear_all(S, pr: dict, o: dict, duration: float, progress, check, step) -> list:
    """The whole video's words (absolute times), heard a stretch at a time. YouTube's own captions
    when the video has them."""
    yt = pr.get("yt") or {}
    if yt.get("words"):
        progress(1.0)
        return ytextra.words_in(yt, 0.0, duration)
    words: list = []
    n = max(1, int(math.ceil(duration / CHUNK)))
    for k in range(n):
        check()
        a, b = k * CHUNK, min(duration, (k + 1) * CHUNK)
        step(f"Listening for captions ({engine.fmt_ts(a)} to {engine.fmt_ts(b)} of {engine.fmt_ts(duration)})")
        words += S.ensure_words(pr, a, b, o["whisper_model"], o["language"], check)
        progress((k + 1) / n)
    S._save_transcripts(pr)
    return words


def word_spans(words: list, mode: str) -> tuple:
    """(words with flagged ones masked, [(start, end, mode)] to silence, how many were flagged), from
    the content check's categories with the muting mode chosen here."""
    if mode == "off" or not words:
        return words, [], 0
    cfg = safety.load_config()
    cfg = dict(cfg, enabled=True, censor=mode)
    res = safety.scan(words, "", "", cfg)
    out, spans, m = safety.censored(words, res, cfg)
    return out, [(a, b, m) for a, b in spans], len(spans)


def run_export(S, job: dict, pid: str, o: dict) -> dict:
    pr = S.projects[pid]
    log, step, check = S.job_logger(job), S.job_step(job), S.job_check(job)
    video = pr["video"]
    if not os.path.exists(video):
        raise RuntimeError("This video's file was deleted, so it can't be exported.")
    info = engine.probe(video)
    dur = float(info.get("duration") or _duration(pr))
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length.")
    outdir = locations.clips_dir(pr["workdir"])
    S._validate_render({"id": job["id"], "start": 0.0, "end": dur}, pr, outdir)
    ro = render_opts(o, info)
    S._check_space(outdir, dur * ro.scale * 0.5)        # a wide frame at stream bitrates, about half a vertical clip's
    need_words = (o["captions"] != "off" or o["words"] != "off") and info.get("has_audio")
    hear_share = 0.45 if need_words else 0.0
    words: list = []
    if need_words:
        if not engine.whisper_available() and not (pr.get("yt") or {}).get("words"):
            if o["words"] != "off":
                raise RuntimeError("Muting flagged words needs the words heard, and the speech model (Whisper) "
                                   "isn't installed. Get it in Settings › Downloads and tools, or turn word muting off.")
            log("Captions skipped: the speech model (Whisper) isn't installed.")
            job["warning"] = "Exported without captions: the speech model (Whisper) isn't installed."
        else:
            words = hear_all(S, pr, o, dur, S.job_progress(job, 0, hear_share), check, step)
            log(f"Heard {len(words):,} words.")
    words = exports.replace_words(words, exports.load().get("replace")) if words else words
    words, spans, flagged = word_spans(words, o["words"])
    if flagged:
        log(f"{'Bleeping' if o['words'] == 'bleep' else 'Muting'} {flagged} flagged word{'s' if flagged != 1 else ''}.")
    spans += [(a, b, o["music_mode"]) for a, b in o["music"]]
    if o["music"]:
        total = sum(b - a for a, b in o["music"])
        log(f"Muting {len(o['music'])} stretch{'es' if len(o['music']) != 1 else ''} of music ({engine.fmt_ts(total)}).")
    name = output_name(pr, outdir)
    path = os.path.join(outdir, name)
    step("Rendering the whole video")
    burn = words if ro.captions else []
    prog = S.job_progress(job, hear_share, 1.0)
    engine.render_clip(video, 0.0, dur, path, ro, title="", log=log, progress=prog, check=check,
                       words=burn, censor=spans, censor_mode="mute",
                       warn=lambda m: job.__setitem__("warning", m))
    srt = ""
    if o["captions"] in ("srt", "both") and words:
        srt = os.path.splitext(path)[0] + ".srt"
        engine.write_srt(words, ro, srt, dur)
    log(f"Saved {name}.")
    return {"path": path, "name": name, "folder": outdir, "srt": os.path.basename(srt) if srt else "",
            "muted_words": flagged, "muted_music": len(o["music"]), "captions": bool(words) and o["captions"] != "off",
            "warning": job.get("warning", "")}


# ---------------------------------------------------------------- jobs and the screen

def _busy(S, pr: dict, key: str) -> Optional[dict]:
    j = S.jobs.get(pr.get(key, ""))
    return j if j and j["status"] == "running" else None


def start_scan(S, pid: str) -> dict:
    pr = S.projects[pid]
    old = _busy(S, pr, "copyright_job")
    if old:
        return old
    job = S.new_job("copyright")
    job["pid"] = pid
    pr["copyright_job"] = job["id"]

    def go():
        S.job_step(job)("Looking for music")
        return scan_copyright(pr, S.job_progress(job, 0, 1), S.job_check(job), S.job_logger(job))
    S.run_job(job, go)
    return job


def start_export(S, pid: str, body: dict) -> dict:
    pr = S.projects[pid]
    old = _busy(S, pr, "vodexport_job")
    if old:
        return old
    o = clean_options(body, _duration(pr))
    job = S.new_job("vodexport")
    job["pid"] = pid
    pr["vodexport_job"] = job["id"]
    S.run_job(job, lambda: run_export(S, job, pid, o))
    return job


def _job_view(S, job: Optional[dict]) -> Optional[dict]:
    return dict(S.job_public(job), warning=job.get("warning", "")) if job else None


def view(S) -> dict:
    """The Export VOD screen: each open video (not live sessions or their moments), its last copyright
    scan, and the scan or export running or last run on it."""
    vids = []
    for pid, pr in list(S.projects.items()):
        if pr.get("live") or "analysis" not in pr or not pr.get("video"):
            continue
        scan = load_scan(pr)
        vids.append({
            "pid": pid, "title": pr.get("title") or os.path.basename(pr["video"]),
            "duration": round(_duration(pr), 1), "exists": os.path.exists(pr["video"]),
            "has_audio": bool((pr.get("info") or {}).get("has_audio", True)),
            "has_yt_words": bool((pr.get("yt") or {}).get("words")),
            "scan": scan, "scan_job": _job_view(S, S.jobs.get(pr.get("copyright_job", ""))),
            "export_job": _job_view(S, S.jobs.get(pr.get("vodexport_job", ""))),
        })
    cfg = safety.load_config()
    return {"videos": vids, "music_model": audio_events.available(), "whisper": engine.whisper_available(),
            "censor": cfg.get("censor", "off"), "censor_categories": [safety.CATEGORIES[c]["label"] for c in
                                                                      cfg.get("censor_categories", []) if c in safety.CATEGORIES]}
