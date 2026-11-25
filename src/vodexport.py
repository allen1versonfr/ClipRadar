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
    for laughter. Each stretch can have its song erased (songerase.py, 5.0: the song goes, singing and all, and
    talking and game sound stay; voicekeep.py's older "keep the voices" when only that model is installed), be
    muted whole, or be left alone. Stretches can be moved and added by hand on the screen. It hears *music*,
    not which song: a safety net, not a guarantee.
  - Speed: when the picture isn't changed (no burned-in captions, the video's own size) it's copied as
    it is and only the sound is made again. Otherwise it's render.render_clip in a wide frame (16:9).
"""

from __future__ import annotations

import json
import math
import os
import re
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
import exporteta
import locations
import outcome
import procjob
import safety
import studio
import studio_auto
import youtube_auto
import ytextra
import voicekeep
import session
import songerase
import listening
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


def can_erase() -> bool:
    """Whether "Erase song" can run: the song eraser, or the older music remover that keeps only voices."""
    return songerase.available() or voicekeep.available()


def suggest_action(confidence: float, speech: float, can_remove: bool, keeps_rest: bool = False) -> str:
    """What a music stretch starts as on the screen, before you change it: "voice" (take the music out,
    keep the voices), "mute" (silence the whole stretch) or "keep" (leave it alone).

    confidence: how sure the scan is that it's music (0..1). speech: the part of the stretch where someone
    is talking (0..1). can_remove: whether a remover is installed ("voice" needs it). keeps_rest: it's the song
    eraser, which keeps the game's sound too, so it's better than muting even where nobody talks (5.0).
    """
    talking = speech >= 0.2           # someone talks over at least a fifth of it
    if talking:
        if can_remove:
            return "voice"            # the music goes, the talking stays: what the stretch is for
        # without the remover, muting would silence the talking too: only when there's little of it
        return "keep" if speech >= SPEECH_ON else "mute"
    # nobody talking: muting does what the voice-only remover would, in no time; the song eraser keeps the game's
    # sound, which muting would lose. A weak detection is left alone, so game sound half-taken for music is kept
    if can_remove and keeps_rest:
        # 5.7.3: erased even when the scan is unsure. The song eraser keeps the talking and the game's sound, so a
        # stretch that wasn't music loses nothing; a song left as "keep" because the scan was unsure is what YouTube
        # caught. (Without it, a weak detection is still left alone: muting would lose the game's sound.)
        return "voice"
    if confidence < 0.5:
        return "keep"
    return "mute"


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
            pr["copyright"] = {"spans": data["spans"], "at": data.get("at", 0), "how": data.get("how", ""),
                               "curve": data.get("curve") or [], "named": data.get("named", 0), "everywhere": data.get("everywhere", 0)}
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
    # how much it sounded like music, second by second: the timeline draws it, so music the scan didn't list
    # (too short, too quiet under talking) can be found and marked by hand (5.0)
    curve = [round(float(x), 2) for x in np.nan_to_num(np.asarray(ev.get("music") if ev.get("music") is not None else [], dtype=np.float32))]
    pr["copyright"] = {"spans": spans, "at": time.time(), "how": how, "curve": curve}
    _write_json(_scan_file(pr), dict(pr["copyright"], video=_sig(pr)))
    total = sum(s["end"] - s["start"] for s in spans)
    log(f"Found {len(spans)} stretch{'es' if len(spans) != 1 else ''} with music ({engine.fmt_ts(total)} in all).")
    return pr["copyright"]


def music_curve(pr: dict, a: float, b: float) -> list:
    """How much each second from a to b sounded like music in the last copyright scan (0..1), for the timeline."""
    curve = (load_scan(pr) or {}).get("curve") or []
    return curve[max(0, int(a)):max(0, int(math.ceil(b)))]


def _music_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "music.json")


def save_music(pr: dict, body: dict) -> dict:
    """The music as you left it on Export VOD (5.0): each stretch's edges and what to do with it, and the ones
    you marked yourself (mine), made on the scan of `at`. Kept with the video, so Segments' "Deal with music"
    and the screen next time (in any browser) use it."""
    dur = _duration(pr) or math.inf
    songs = []
    for s in (body.get("songs") or [])[:500]:
        if not isinstance(s, dict):
            continue
        try:
            a, b = max(0.0, float(s.get("start"))), min(dur, float(s.get("end")))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)) or b - a < 0.5:
            continue
        songs.append({"start": round(a, 2), "end": round(b, 2),
                      "act": s.get("act") if s.get("act") in MUSIC_ACTIONS else "mute", "mine": bool(s.get("mine")),
                      "song": song_source(s.get("song")), "found": _found(s.get("found"))})
    try:
        at = float(body.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    pr["music_mine"] = {"at": at, "songs": sorted(songs, key=lambda s: s["start"])}
    _write_json(_music_file(pr), dict(pr["music_mine"], video=_sig(pr)))
    return {"ok": True, "songs": len(songs)}


def load_music(pr: dict) -> Optional[dict]:
    """The music as you last left it on Export VOD ({"at", "songs"}), if the video hasn't changed since."""
    if "music_mine" not in pr:
        pr["music_mine"] = None
        try:
            with open(_music_file(pr), encoding="utf-8") as f:
                data = json.load(f)
            if data.get("video") == _sig(pr) and isinstance(data.get("songs"), list):
                pr["music_mine"] = {"at": float(data.get("at") or 0), "songs": data["songs"]}
        except (OSError, ValueError, TypeError):
            pass
    return pr["music_mine"]


def _scan_view(scan: Optional[dict]) -> Optional[dict]:
    """The scan for the screen: each stretch with what Clip Radar would do with it to start with."""
    if not scan:
        return None
    scan = {k: v for k, v in scan.items() if k != "curve"}      # the timeline asks for it a stretch at a time
    can, rest = can_erase(), songerase.available()
    spans = []
    for s in scan["spans"]:
        known = bool((s.get("song") or {}).get("url"))
        act = "voice" if known else suggest_action(float(s.get("confidence", 0)), float(s.get("speech", 0)), can, rest)
        spans.append(dict(s, speech=s.get("speech", 0), suggest=act if act in MUSIC_ACTIONS and (can or known or act != "voice") else "mute"))
    return dict(scan, spans=spans, recognise=_can_recognise())


def _can_recognise() -> bool:
    import songid
    return songid.available()


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
    return open_known(S, wd, video, title)


def open_known(S, wd: str, video: str, title: str, remember: bool = True) -> str:
    """Open a video for export in its own project folder (its scan, flagged words and music are kept there).
    Used by open_video, and when Clip Radar opens again where it left off (session.py)."""
    pid = S._pid_for(wd)
    if usable(S.projects.get(pid)):
        if remember:
            session.remember_light(wd, video, title)
        return pid
    try:
        info = engine.probe(video)
    except Exception as e:
        raise RuntimeError("That video couldn't be read: " + (str(e).strip().splitlines() or ["unknown error"])[-1][:200]) from None
    os.makedirs(wd, exist_ok=True)
    pr = {"video": video, "workdir": wd, "title": title, "info": info, "duration": info["duration"],
          "tx_lock": threading.Lock(), "live": "", "light": True, "clips": [], "gems": []}
    listening._load_transcripts(pr)
    S.projects[pid] = pr
    if not engine._meta(wd) and os.path.normcase(os.path.abspath(wd)) == os.path.normcase(os.path.abspath(
            engine.local_workdir(S.WORKROOT, video))):
        try:          # remembered like any opened file, so it's listed here (and on Home) next time
            engine._save_meta(wd, source=video, local=True, title=title, duration=info["duration"])
        except OSError:
            pass
    if remember:
        session.remember_light(wd, video, title)      # open again next time Clip Radar starts (5.0.2)
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


def song_source(v) -> str:
    """Which song a stretch is (5.3): a YouTube (or other web) link, or a sound or video file on this PC. "" if neither."""
    v = str(v or "").strip()[:2000]
    if not v:
        return ""
    if re.match(r"^https?://", v, re.I):
        return v
    return v if os.path.isabs(v) else ""


def _found(f) -> Optional[dict]:
    """A recognised song as the screen sends it back: {"title", "artist", "url"}, checked."""
    if not isinstance(f, dict) or not f.get("title"):
        return None
    return {"title": str(f["title"])[:200], "artist": str(f.get("artist") or "")[:200], "url": song_source(f.get("url"))}


def _songs(rows, duration: float) -> list:
    """[(start, end, source)] for the "Erase song" stretches you said the song of."""
    out = []
    for a, b, (m, src) in _pairs(rows, duration, lambda sp: (sp[2] if len(sp) > 2 else "", song_source(sp[3] if len(sp) > 3 else ""))):
        if m == "voice" and src:
            out.append((a, b, src))
    return out


def clean_options(body: dict, duration: float) -> dict:
    """What the Export VOD screen asked for, checked: unknown values fall back to the defaults, and
    spans are numbers inside the video, in order."""
    style = {k: v for k, v in (body.get("style") or {}).items() if k in STYLE_KEYS} if isinstance(body.get("style"), dict) else {}
    return {
        # 5.1.2: no captions on Export VOD and Segments: YouTube makes its own, and nothing is listened to for them
        "captions": "off",
        "words": body.get("words") if body.get("words") in WORD_MODES else "mute",
        # the flagged words ticked in the queue: [(start, end)]
        "word_spans": _pairs(body.get("word_spans"), duration),
        # music stretches: [(start, end, "voice" | "mute")]
        "music": _pairs(body.get("music"), duration,
                        lambda sp: sp[2] if len(sp) > 2 and sp[2] in ("voice", "mute") else "mute"),
        # 5.3: the song in an "Erase song" stretch, when you said which (a YouTube link or a file): [(start, end, source)]
        "songs": _songs(body.get("music"), duration),
        "quality": body.get("quality") if body.get("quality") in QUALITY else "balanced",
        "size": str(body.get("size")) if str(body.get("size")) in SIZES else "source",
        "whisper_model": str(body.get("whisper_model") or "small")[:40],
        "language": str(body.get("language") or "")[:10],
        "style": dict(VOD_STYLE, **style),
        "hw_encode": body.get("hw_encode", True) is not False,
        # 5.7: after erasing, what Shazam still recognises is muted, so YouTube doesn't catch it either
        "claim_check": body.get("claim_check", True) is not False,
    }


def render_opts(S, o: dict, info: dict) -> render.RenderOpts:
    """A wide frame showing the whole picture: as it is for a 16:9 video, on a blurred background otherwise,
    with the captions styled as chosen on the screen."""
    sw, sh = info.get("width") or 1920, info.get("height") or 1080
    wide = abs(sw / max(1, sh) - 16 / 9) < 0.03
    scale = SIZES.get(o["size"]) or min(1.0, max(0.25, sh / 1080))
    ro = S._ropts(dict(o["style"], whisper_model=o["whisper_model"], language=o["language"]))
    return replace(ro, layout="full" if wide else "blur", aspect="16:9", track="off", punch="off",
                   captions=o["captions"] in ("burn", "both"), hw_encode=o["hw_encode"], speed="wide_" + o["quality"], scale=scale,
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
        words += listening.ensure_words(pr, x, y, model, lang, check)
        progress((k + 1) / n)
    listening._save_transcripts(pr)
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

FASTSTART_MAX = 600.0     # seconds: a longer export isn't written a second time to put its index at the front


def movflags(length: float) -> list:
    """+faststart (the file's index at the front) costs FFmpeg a second pass over the whole file once it's made. For a
    clip that's a moment; for a VOD it was minutes of the disk flat out, with the bar at 100% and the PC crawling
    (5.4). YouTube and players take the file either way, so a long export goes without."""
    return ["-movflags", "+faststart"] if length <= FASTSTART_MAX else []


SILENT_MAX = 90.0         # seconds FFmpeg may say nothing at all before it counts as hung (5.7.1)
FROZEN_MAX = 120.0        # seconds its position may stand still while it keeps reporting (5.7.1)
ENDING_MAX = 600.0        # seconds it may take to finish the file once it has written all of it (the faststart pass)


def _run_ffmpeg(cmd: list, cwd: str, length: float, progress, check) -> None:
    """Run FFmpeg (asked for -progress pipe:1), moving the bar and stopping when the job is cancelled. At the render
    priority (Settings › Performance: below normal, so the PC stays usable).

    5.7.1: watched from the side, the way a clip's render is (render.py, 5.4.3). Before, Stop and the stall check only
    ran when FFmpeg printed a line: an FFmpeg that hung (a wedged disk, a network drive gone, a source it choked on)
    kept an Export VOD or a Segments join at the same percent for good, and Stop did nothing. Now Stop kills it at
    once, and one that says nothing for SILENT_MAX seconds, or whose position doesn't move for FROZEN_MAX, is stopped
    and fails with that said (the caller then makes it another way where it can)."""
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=engine.CREATE_NO_WINDOW | render.RENDER_PRIORITY[0])
    procjob.adopt(p)
    err: list = []

    def drain():
        for ln in p.stderr:
            err.append(ln)
            del err[:-80]
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    now = time.monotonic
    seen = {"line": now(), "moved": now(), "pos": -1.0, "ended": 0.0}
    why = {"stuck": "", "cancelled": False}

    def watch():
        while p.poll() is None:
            try:
                check()
            except engine.Cancelled:
                why["cancelled"] = True
            except Exception:       # noqa: BLE001  a check that fails isn't a reason to stop the export
                pass
            t = now()
            if not why["cancelled"]:
                if seen["ended"] and t - seen["ended"] > ENDING_MAX:
                    why["stuck"] = f"FFmpeg wrote the whole file but didn't finish it within {int(ENDING_MAX)} seconds"
                elif not seen["ended"] and t - seen["line"] > SILENT_MAX:
                    why["stuck"] = f"FFmpeg said nothing for {int(SILENT_MAX)} seconds"
                elif not seen["ended"] and t - seen["moved"] > FROZEN_MAX:
                    why["stuck"] = (f"FFmpeg made no progress for {int(FROZEN_MAX)} seconds (stuck at "
                                    f"{engine.fmt_ts(max(0.0, seen['pos']))} of {engine.fmt_ts(length)})")
            if why["cancelled"] or why["stuck"]:
                try:
                    p.kill()
                except OSError:
                    pass
                return
            time.sleep(1.0)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        for line in p.stdout:
            seen["line"] = now()
            if line.startswith("out_time_us="):
                try:
                    pos = max(0.0, int(line.split("=")[1]) / 1e6)
                except ValueError:
                    continue
                if pos > seen["pos"] + 1e-3:
                    seen["pos"], seen["moved"] = pos, now()
                if pos >= length - 0.5 and not seen["ended"]:
                    seen["ended"] = now()
                progress(min(0.999, pos / max(1.0, length)))
            elif line.startswith("progress=end"):
                seen["ended"] = seen["ended"] or now()
            try:
                check()
            except engine.Cancelled:
                why["cancelled"] = True
                p.kill()
                raise
    finally:
        p.wait()
        reader.join(timeout=5)
        watcher.join(timeout=3)
    if why["cancelled"]:
        raise engine.Cancelled()
    if why["stuck"]:
        raise RuntimeError("ffmpeg failed:\n" + render._ffmpeg_reason("".join(err[-60:])) + "\n" + why["stuck"] + ", and was stopped.")
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + render._ffmpeg_reason("".join(err[-60:])))


def keyframes(video: str, around: list, reach: float = 12.0) -> list:
    """The times of the picture's keyframes near each time in `around` (within `reach` seconds), sorted.
    Only those stretches of the file are read, so a six-hour VOD takes a moment, not a full pass.
    Times are the video's own (0 = where it starts), as FFmpeg's -ss takes them.

    5.7.1: ffprobe reads and prints the file's timestamps as they are, and a recording's often don't start at 0 (an
    OBS .ts or .flv, a stream recorded from the middle: 1.4 s, 10 s, hours). Those were taken as the video's own
    times, so a copied segment started on a frame that wasn't a keyframe, a second or more from where its sound did.
    Each time is also rounded up to the millisecond, never down: -ss a hair before a keyframe copies from the one
    before it, two seconds early, with the sound starting where it should (out of step by that much). Asked in
    batches, so hundreds of segments don't make a command line Windows refuses, and a probe that fails or takes too
    long is no keyframes (the segments are made the slow way), never a failed export."""
    if not around:
        return []
    zero = _start_time(video)
    times = sorted(around)
    out = set()
    for k in range(0, len(times), 150):
        iv = ",".join(f"{max(0.0, zero + t - reach):.3f}%{zero + t + reach:.3f}" for t in times[k:k + 150])
        try:
            r = subprocess.run([engine.ffprobe(), "-v", "error", "-select_streams", "v:0", "-read_intervals", iv,
                                "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", video],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                               creationflags=engine.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in r.stdout.splitlines():
            bits = line.strip().split(",")
            if len(bits) >= 2 and "K" in bits[1]:
                try:
                    t = float(bits[0]) - zero
                except ValueError:
                    continue
                if math.isfinite(t) and t >= -0.0005:
                    out.add(max(0.0, math.ceil(round(t * 1000, 6)) / 1000))
    return sorted(out)


def _start_time(video: str) -> float:
    """Where the file's timestamps start (seconds; 0 when they start at 0 or it can't be read)."""
    try:
        r = subprocess.run([engine.ffprobe(), "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", video],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           creationflags=engine.CREATE_NO_WINDOW)
        t = float((r.stdout or "0").strip().split(",")[0] or 0)
        return t if math.isfinite(t) and t > 0 else 0.0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


SNAP_MAX = 2.5            # seconds a copied segment's edge may move to a keyframe (Twitch and YouTube: one every 2 s)


def snap_to_keyframes(stretches: list, kf: list, first_back: bool = True, most: float = SNAP_MAX) -> list:
    """Stretches [(a, b)] moved so each starts on a keyframe, for a copied picture (which can only start on one):
    the first one back to the keyframe before it (a few seconds more at the start, nothing lost), the ones after a
    cut forward to the keyframe after (a little more cut, nothing that was cut shown). An edge that would move more
    than `most` seconds is None (the caller makes that segment the slow way instead, exactly where you set it)."""
    out = []
    for i, (a, b) in enumerate(stretches):
        before = [k for k in kf if a - most - 0.001 <= k <= a + 0.001]
        after = [k for k in kf if a - 0.001 <= k <= min(b - 0.5, a + most)]
        k = (before[-1] if before else None) if (i == 0 and first_back) else (after[0] if after else None)
        if k is not None and b - k >= 0.5:
            out.append((k, b))
        else:
            out.append(None)
    return out


def copy_video(video: str, out_path: str, info: dict, ro: render.RenderOpts, spans: list, audio_src: str,
               progress, check, start: float = 0.0, length: float = 0.0, audio_start: float = 0.0,
               pieces: Optional[list] = None) -> None:
    """The export with the picture copied as it is and only the sound made again (muted spans, the sound with
    its music removed). Many times faster than encoding the picture: nothing is decoded but the sound.
    start, length: only that stretch (a segment, 5.1.1; start must be a keyframe). spans are times in the stretch,
    and audio_src (when there is one) starts where the stretch does.
    5.7.3: audio_start: where in audio_src the sound starts (the video's own sound under a picture made apart).
    pieces: the picture is these files one after another (made side by side), joined as they are; `video` is then
    only where the sound comes from when there's no audio_src."""
    work = os.path.dirname(os.path.abspath(out_path))
    tag = uuid.uuid4().hex[:8]
    tmp, cmd_name = f"vc_{tag}.part.mp4", f"vc_{tag}.cmd"
    length = float(length or (float(info["duration"]) - start))
    cmd = [engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats"]
    lst = ""
    if pieces:
        lst = os.path.join(work, f"vc_{tag}.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p_ in pieces:
                f.write("file '" + os.path.abspath(p_).replace("\\", "/").replace("'", r"'\''") + "'\n")
        cmd += ["-f", "concat", "-safe", "0", "-t", f"{length:.3f}", "-i", lst]
        if not audio_src and info.get("has_audio"):
            audio_src = video             # the sound from the video itself, from audio_start
    else:
        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-t", f"{length:.3f}", "-i", video]
    if audio_src:
        cmd += [*(["-ss", f"{audio_start:.3f}"] if audio_start > 0 else []), "-t", f"{length:.3f}", "-i", audio_src]
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
        cmd += [*movflags(length), "-avoid_negative_ts", "make_zero", tmp]
        _run_ffmpeg(cmd, work, length, progress, check)
        os.replace(os.path.join(work, tmp), out_path)
    finally:
        for name in (tmp, cmd_name, lst):
            if name:
                try:
                    os.remove(os.path.join(work, name))
                except OSError:
                    pass
    progress(1.0)


# ---------------------------------------------------------------- the picture, made apart (5.7.3)

PIECE_MIN = 90.0          # seconds: a picture shorter than twice this is made in one piece


def picture_pieces(length: float) -> int:
    """How many pieces a picture of `length` seconds is made in, side by side. One FFmpeg can't keep every core busy
    (its decoding and filters run on few of them): measured on 4 cores, two halves side by side took 12.5 s where one
    FFmpeg took 15.0 s. More pieces on a PC with more cores; each at least PIECE_MIN seconds."""
    cores = os.cpu_count() or 2
    most = 1 if cores < 4 else 2 if cores < 8 else 3 if cores < 16 else 4
    return max(1, min(most, int(length // PIECE_MIN)))


class _Picture:
    """The picture from a for length seconds (no sound), made in n pieces side by side, each an exact number of frames,
    so they add up to the whole to the frame and the sound put on after stays in step. Started at once and left to run
    while the sound is worked on; wait() returns the pieces in order, or raises what went wrong."""

    def __init__(self, video: str, a: float, length: float, ro, info: dict, folder: str, n: int, check, log, warn):
        self.fps = render.output_fps(info, ro)
        total = max(1, int(round(length * self.fps)))
        n = max(1, min(n, total))
        self.cuts = [int(round(k * total / n)) for k in range(n + 1)]
        tag = uuid.uuid4().hex[:8]
        self.paths = [os.path.join(folder, f"pic_{tag}_{k}.mp4") for k in range(n)]
        self.done = [0.0] * n
        self.errors: list = []
        self.stopped = False
        self.on_progress = lambda: None
        self._threads = []

        def stop_check():
            if self.stopped:
                raise engine.Cancelled()
            check()

        def one(k):
            f0, f1 = self.cuts[k], self.cuts[k + 1]
            t0 = a + f0 / self.fps
            t1 = t0 + (f1 - f0) / self.fps + 2.0 / self.fps        # a frame or two over: -frames:v cuts it exact
            try:
                render.render_clip(video, t0, t1, self.paths[k], ro, title="", log=log, check=stop_check,
                                   progress=lambda f, k=k: self._moved(k, f), words=[], censor=[], audio_src="",
                                   warn=warn, faststart=False, picture_only=True, frames=f1 - f0, threads=[])
            except BaseException as e:      # noqa: BLE001  said by wait()
                self.errors.append(e)
                self.stopped = True         # the others stop too: the picture is made the usual way instead
        for k in range(n):
            th = threading.Thread(target=one, args=(k,), daemon=True)
            self._threads.append(th)
            th.start()

    def _moved(self, k: int, f: float):
        self.done[k] = max(self.done[k], min(1.0, f))
        self.on_progress()

    def fraction(self) -> float:
        return sum(self.done) / len(self.done)

    def wait(self, check) -> list:
        for th in self._threads:
            while th.is_alive():
                check()
                th.join(0.5)
        if self.errors:
            raise self.errors[0]
        return self.paths

    def stop(self):
        self.stopped = True
        for th in self._threads:
            th.join(30)
        for p_ in self.paths:
            _remove(p_)


def check_made(path: str, expect: float, want_audio: bool) -> str:
    """What's wrong with a video just made, or "" if nothing: it must be there, readable, about as long as asked
    (1% or 3 s either way), and have sound when the source does (5.7.1). Read from the file's header, so it takes a
    moment even for a six-hour export: a copy cut short (a source whose timestamps jump) or written without its
    sound is caught here, not on YouTube."""
    try:
        if os.path.getsize(path) < 1000:
            return "the file came out empty"
        info = engine.probe(path)
    except Exception as e:      # noqa: BLE001
        return "the file it made can't be read (" + ((str(e).strip().splitlines() or [type(e).__name__])[-1][:160]) + ")"
    got = float(info.get("duration") or 0)
    if expect > 0 and abs(got - expect) > max(3.0, expect * 0.01):
        return f"it came out {engine.fmt_ts(got)} long instead of {engine.fmt_ts(expect)}"
    if want_audio and not info.get("has_audio"):
        return "it came out without sound"
    return ""


def _temp_room(folder: str, seconds: float, files: int) -> None:
    """Room in the temp folder for the sound made again (FLAC, about 0.12 MB a second each), checked before an hour of
    erasing rather than failing at the end of it (5.7.1)."""
    if seconds <= 0 or files <= 0:
        return
    need = seconds * 0.13e6 * files + 300e6
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < need:
        raise OSError(28, f"No space left on device for the sound being made again in {folder} "
                          f"({free // 1_000_000} MB free, about {int(need // 1_000_000)} MB needed). "
                          "Free some space, or move the temp folder in Settings › Storage")


def plan_range(info: dict, o: dict, ro, length: float, voice: list, known: list, claim: bool, copying: bool,
               n_pieces: int, apart: bool) -> dict:
    """How long making `length` seconds with these choices should take on this PC, step by step (exporteta.plan),
    with "key": the render speed it was looked up by."""
    enc = render.video_encoder(ro)[0] if not copying else ""
    fps = render.output_fps(info, ro)
    out_h = int(round((info.get("height") or 1080) * (ro.scale or 1.0)))
    erase_s = sum(y - x for x, y in voice)
    known_s = sum(y - x for x, y, *_ in known)
    p = exporteta.plan(length=length, info=info, copying=copying, size=o.get("size", "source"), quality=o.get("quality", "balanced"),
                       encoder=enc, out_h=out_h, fps=fps, erase_s=erase_s, known_s=known_s,
                       claim_s=(erase_s + known_s) if claim else 0.0, separate=claim and can_erase() and songerase.available(),
                       pieces=max(1, n_pieces), together=apart and bool(voice or known or claim),
                       copyable=info.get("vcodec") in COPY_CODECS)
    p["key"] = exporteta.render_key(enc or "cpu", out_h, fps, o.get("quality", "balanced"))
    return p


def _basis(plan: dict) -> str:
    return "Time left from " + plan["basis"][0] + ", then from how fast each step is going."


def _sound_parts(o: dict, a: float, b: float, has_audio: bool) -> tuple:
    """(voice, known) from a to b: the Erase song stretches the eraser model does, and those whose song is known."""
    voice = [(x, y) for x, y, m in o["music"] if m == "voice" and y > a and x < b] if has_audio else []
    known = [(x, y, src) for x, y, src in o.get("songs") or [] if y > a and x < b] if has_audio else []
    voice = [(x, y) for x, y in voice if not any(p <= x + 0.01 and y - 0.01 <= q for p, q, _ in known)]
    clip = lambda x, y: (max(x, a), min(y, b))          # noqa: E731
    return [clip(x, y) for x, y in voice], [(*clip(x, y), s) for x, y, s in known]


def estimate(S, pr: dict, o: dict, a: float, b: float, whole: bool = True, part: bool = False, copy: bool = False) -> dict:
    """The plan for one export (or one Segments stretch) before it starts, made the way render_range will make it."""
    info = engine.probe(pr["video"])
    ro = render_opts(S, o, info)
    voice, known = _sound_parts(o, a, b, bool(info.get("has_audio")))
    claim = bool((voice or known) and o.get("claim_check", True) and _can_recognise())
    copying = whole and can_copy(o, info) and (not part or copy)
    n = picture_pieces(b - a) if not copying else 0
    return plan_range(info, o, ro, b - a, voice, known, claim, copying, n, bool(n and (n > 1 or voice or known or claim)))


def render_range(S, job: dict, pr: dict, o: dict, a: float, b: float, path: str, whole: bool,
                 part: bool = False, band: tuple = (0.0, 1.0), label: str = "", words: Optional[list] = None,
                 copy: bool = False, keep_models: bool = False) -> dict:
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
    # 5.3: the stretches whose song is known (recognised, or named by you): that recording is taken out exactly, the way
    # YouTube Studio's Erase song does it; the eraser model does the rest, and any part where the recording wasn't heard
    known = [(x, y, src) for x, y, src in o.get("songs") or [] if y > a and x < b] if has_audio else []
    voice = [(x, y) for x, y in voice if not any(p <= x + 0.01 and y - 0.01 <= q for p, q, _ in known)]
    hush = [(x, y) for x, y, m in o["music"] if m == "mute" and y > a and x < b] if has_audio else []
    told = [(x, y) for x, y in o["word_spans"] if y > a and x < b] if has_audio else []
    if voice and not can_erase():
        raise RuntimeError("Erase song needs the song eraser. Get it in Settings › Downloads and tools, "
                           "or set those stretches to Mute all sound.")
    # the bar: listening (only when captions are on), taking the music out, then the picture
    given = words is not None
    need_words = o["captions"] != "off" and has_audio and not given
    # a Segments part is copied too when its start was moved onto a keyframe for it (copy=True, 5.1.1)
    copying = whole and can_copy(o, info) and (not part or copy)
    w_hear = 0.45 if need_words else 0.0
    claim = bool((voice or known) and (whole or part) and o.get("claim_check", True) and _can_recognise())
    words = [w for w in (words or []) if a <= w["start"] < b] if given and o["captions"] != "off" else []
    if need_words:
        if not whisper.whisper_available() and not (pr.get("yt") or {}).get("words"):
            log("Captions skipped: the speech model (Whisper) isn't installed.")
            _warn(job, "Exported without captions: the speech model (Whisper) isn't installed.")
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
    if voice or known:
        _temp_room(tmpdir, length, (1 if known else 0) + (1 if voice or can_erase() else 0))
    audio_src = ""
    known_src = ""
    voice_src = ""
    erased_voice = False
    # 5.7.3: a picture that's made again is made apart from the sound, in pieces side by side, starting now: while the
    # songs are erased, not after (the two took one after the other, the CPU half idle through each), and with every
    # core busy. The sound is put on it at the end, without making the picture again.
    sound_work = bool(voice or known or claim)
    n_pieces = picture_pieces(length) if (whole or part) and not copying else 0
    apart = bool(n_pieces and (n_pieces > 1 or sound_work))
    # 5.7.4: how long each step should take here, followed as it runs: the time left, and what's holding it back
    plan = plan_range(info, o, ro, length, voice, known, claim, copying, n_pieces, apart)
    tr = exporteta.Tracker(job, plan["steps"], plan["together"], S.job_progress(job, lo, hi), basis=_basis(plan),
                           held=plan["held"], set_eta=not part)
    if not part and plan["held"]:
        log("This export: about " + exporteta._fmt(plan["seconds"]) + ". " + " ".join(plan["held"]))
    pic = None
    if apart:
        pic = _Picture(video, a, length, ro, info, tmpdir, n_pieces, check, log, lambda m: _warn(job, m))
        to_pic = tr.step("picture")
        made = pic
        pic.on_progress = lambda: to_pic(made.fraction())
        log(f"The picture is made at the same time as the sound, in {n_pieces} piece{'s' if n_pieces != 1 else ''} side by side.")
    try:
        if known:
            import songref
            total = sum(min(y, b) - max(x, a) for x, y, _ in known)
            step(f"Taking the recognised songs out of {engine.fmt_ts(total)}")
            known_src = os.path.join(tmpdir, f"known_{uuid.uuid4().hex[:8]}.flac")
            t0 = time.time()
            try:
                got = songref.erase_known(video, known, known_src, a, length, locations.temp_dir("songs"),
                                          progress=tr.step("known"), check=check, log=log)
            except engine.Cancelled:
                raise
            except Exception as e:       # noqa: BLE001  5.7.1: the eraser model or muting does it instead, not a failed export
                if _disk_full(e):
                    raise
                log(f"Couldn't take the recognised songs out ({_reason(e)}).")
                _remove(known_src)
                known_src = ""
                got = {"missed": [(x, y) for x, y, _ in known]}
                known = []
            else:
                log(f"Known songs taken out in {engine.fmt_ts(time.time() - t0)}.")
                audio_src = known_src
                exporteta.learn("vodknown", "", total, time.time() - t0)
            tr.done("known")
            left = [(max(p, a), min(q, b)) for p, q in got["missed"] if min(q, b) - max(p, a) >= 0.5]
            if left and can_erase():        # where the recording wasn't heard, the eraser model does it
                voice = sorted(voice + left)
                log(f"The recording wasn't heard in {len(left)} part{'s' if len(left) != 1 else ''}: the eraser model does "
                    + ", ".join(f"{engine.fmt_ts(p)}-{engine.fmt_ts(q)}" for p, q in left[:6]) + ".")
            elif left:
                # 5.7.1: muted rather than left as they were: you asked for the song to go, and without the eraser model
                # muting is the only way it does (as Segments already did for Erase song without the model)
                spans += [(*rel(p, q), "mute") for p, q in left]
                _warn(job, "The song couldn't be taken out of " + ", ".join(f"{engine.fmt_ts(p)}-{engine.fmt_ts(q)}" for p, q in left[:4])
                      + (f" and {len(left) - 4} more" if len(left) > 4 else "")
                      + ", so those parts are muted. Get the song eraser in Settings › Downloads and tools to keep the talking there.")
        if voice:
            total = sum(y - x for x, y in voice)
            eraser = songerase.available()
            step(f"Erasing songs from {engine.fmt_ts(total)}" if eraser else f"Taking the music out of {engine.fmt_ts(total)} (voices stay)")
            voice_src = os.path.join(tmpdir, f"voice_{uuid.uuid4().hex[:8]}.flac")
            t0 = time.time()
            fn = songerase.erase if eraser else voicekeep.keep_voice
            # it may have grown: parts the recording wasn't heard in (5.7.8: grow, not set on the plan alone, which failed
            # the eraser's first move; and at the card's speed when it runs there)
            blk = exporteta._eraser_block() if eraser else None
            tr.grow("erase", total / (10.0 / blk if blk else exporteta.rate("voderase")[0]))
            to_erase = tr.step("erase")
            if eraser and songerase.needs_trial():
                # 5.7.6: said, since the bar stands still while it happens (it looked stuck)
                step("Trying the song eraser on the graphics card (once, up to a minute or two)")
                log("Trying the song eraser on the graphics card: it's used if it works and is quicker than the processor.")
            try:
                if known_src:       # on top of the sound the named songs were already taken out of
                    got = fn(known_src, [rel(x, y) for x, y in voice], voice_src, start=0.0, length=0.0,
                             progress=to_erase, check=check)
                else:
                    got = fn(video, [rel(x, y) for x, y in voice], voice_src, start=a, length=0.0 if whole and not part else length,
                             progress=to_erase, check=check)
                if not os.path.isfile(voice_src) or os.path.getsize(voice_src) < 100:
                    raise RuntimeError("it made no sound")
            except engine.Cancelled:
                raise
            except Exception as e:       # noqa: BLE001  5.7.1: muted instead, so the export still can't be claimed there
                if _disk_full(e):
                    raise
                _remove(voice_src)
                voice_src = ""
                spans += [(*rel(x, y), "mute") for x, y in voice]
                log(f"The song eraser failed ({_reason(e)}): {len(voice)} stretch{'es' if len(voice) != 1 else ''} muted instead.")
                _warn(job, f"The song eraser failed ({_reason(e)}), so the {len(voice)} Erase song stretch{'es' if len(voice) != 1 else ''} "
                           f"({engine.fmt_ts(total)}) {'are' if len(voice) != 1 else 'is'} muted instead: YouTube won't catch them, "
                           "but the talking there is muted too.")
            else:
                audio_src = voice_src
                erased_voice = True
                if eraser:
                    where = songerase.status()
                    if where.get("where") == "cpu":         # the graphics card's speed is kept by songerase itself
                        exporteta.learn("voderase", "", total, time.time() - t0)
                    log(f"The song eraser ran on the {'graphics card' if where.get('where') == 'gpu' else 'processor'}"
                        + (f" ({where['why']})" if where.get("why") else "") + ".")
                    if where.get("note"):
                        _warn(job, where["note"])
                log(f"{'Songs erased' if eraser else 'Music removed, voices kept,'} in {len(voice)} stretch{'es' if len(voice) != 1 else ''} "
                    f"({engine.fmt_ts(total)}) in {engine.fmt_ts(time.time() - t0)}, {got['blocks']} blocks through the model.")
            tr.done("erase")
        if claim and audio_src:
            # 5.7: would YouTube still catch it? The erased sound heard again, as Content ID would hear it; what's still
            # recognised is muted in those seconds, and the export says where
            import songid
            import songref as _sr
            step("Checking YouTube won't still hear the songs")
            erased = sorted(([rel(x, y) for x, y in voice] if erased_voice else []) + [rel(x, y) for x, y, _ in known])
            src_ = audio_src
            t_claim = time.time()
            try:
                left = songid.still_heard(lambda t, n: _sr.read(src_, t, n), erased, check,
                                          tr.step("claim"), separate=can_erase() and songerase.available())
                exporteta.learn("vodclaim", "", sum(q - p for p, q in erased), time.time() - t_claim)
            except engine.Cancelled:
                raise
            except Exception as e:      # noqa: BLE001  5.7.1: the check is a safety net; it failing isn't a failed export
                left = []
                log(f"Couldn't check whether the songs are still recognisable ({_reason(e)}).")
                _warn(job, "The check that YouTube won't still hear the songs couldn't run this time "
                           f"({_reason(e)}); the songs were erased as usual.")
            else:
                if not left:
                    log("Checked: none of the erased songs is recognisable any more.")
            if left:
                spans += [(p, q, "mute") for p, q, _ in left]
                where = ", ".join(f"{engine.fmt_ts(a + p)}" for p, q, _ in left[:5]) + (f" and {len(left) - 5} more" if len(left) > 5 else "")
                log(f"Still recognisable after erasing ({left[0][2]}) at {where}: muted there, {engine.fmt_ts(sum(q - p for p, q, _ in left))} in all.")
                _warn(job, f"A song was still recognisable after erasing in {len(left)} place{'s' if len(left) != 1 else ''} "
                           f"({where}): those seconds are muted, so YouTube won't catch them.")
        tr.done("claim")
        prog = tr.step("picture")
        t_pic = time.time()
        if pic:
            step(("Rendering" if part else "Rendering the whole video") + " (the picture)")
            try:
                pieces = pic.wait(check)
                step("Putting the picture and the sound together")
                pinfo = dict(info, vcodec=engine.probe(pieces[0]).get("vcodec", "h264"))
                tr.done("picture")
                if not sound_work:
                    exporteta.learn("vodrender", plan["key"], length, time.time() - t_pic)
                copy_video(video, path, pinfo, ro, spans, audio_src, tr.step("join"), check,
                           length=length, audio_start=0.0 if audio_src else a, pieces=pieces)
                bad = check_made(path, length, has_audio)
                if bad:
                    _remove(path)
                    raise RuntimeError(bad)
                copying = False
            except engine.Cancelled:
                raise
            except Exception as e:      # noqa: BLE001  made the usual way instead
                if _disk_full(e):
                    raise
                _remove(path)
                log(f"Making the picture apart didn't work ({_reason(e)}): making the video the usual way.")
                pic.stop()
                pic = None
                tr.restart("picture", plan_range(info, o, ro, length, [], [], False, False, 1, False)["steps"]["picture"])
                prog = tr.step("picture")
            else:
                pic.stop()          # the pieces aren't needed any more
                pic = "made"
        if copying:
            step("Copying the picture, making the sound")
            log("The picture is copied as it is (no burned-in captions, the video's own size).")
            try:
                copy_video(video, path, info, ro, spans, audio_src, prog, check,
                           start=a if part else 0.0, length=length if part else 0.0)
                bad = check_made(path, length, has_audio)
                if bad:
                    _remove(path)
                    raise RuntimeError(bad)
                if not part:
                    exporteta.learn("vodcopy", "", length, time.time() - t_pic)
                tr.done("picture")
            except engine.Cancelled:
                raise
            except Exception as e:      # noqa: BLE001  5.7.1: made the slow way instead of failing the export
                if _disk_full(e):
                    raise
                _remove(path)
                copying = False
                log(f"Copying the picture didn't work ({_reason(e)}): making the video again instead.")
                tr.restart("picture", plan_range(info, o, ro, length, [], [], False, False, 1, False)["steps"]["picture"])
                prog = tr.step("picture")
                step(("Rendering" if part else "Rendering the whole video") + " (the picture couldn't be copied)")
        if not copying and pic != "made":
            if not (whole and can_copy(o, info) and (not part or copy)):
                step("Rendering" if part else "Rendering the whole video" if whole else "Rendering the preview")
            burn = [dict(w, start=w["start"] - a, end=w["end"] - a) for w in words] if ro.captions else []
            render.render_clip(video, a, b, path, ro, title="", log=log, progress=prog, check=check,
                               words=burn, censor=spans, censor_mode="mute", audio_src=audio_src,
                               warn=lambda m: _warn(job, m), faststart=length <= FASTSTART_MAX,
                               threads=[] if whole or part else None)       # 5.7.3: an export uses every core
            if whole or part:
                bad = check_made(path, length, has_audio)
                if bad:
                    log(f"Made, but {bad}.")
                    _warn(job, f"The video was made, but {bad}. Watch it through before you upload it.")
                if not sound_work:          # timed alone (alongside the songs being erased, it shares the processor)
                    exporteta.learn("vodrender", plan["key"], length, time.time() - t_pic)
            tr.done("picture")
        tr.done("join")
    finally:
        if isinstance(pic, _Picture):
            pic.stop()
        for f in {audio_src, known_src, voice_src} - {""}:
            _remove(f)
        if voice and not keep_models:      # Segments lets go of them once, when every stretch is made
            voicekeep.release()
            songerase.release()
    return {"words": words, "ro": ro, "copied": copying, "muted_words": len(told), "muted_music": len(hush),
            "voice_music": len(voice), "known_songs": len(known)}


def _remove(f: str) -> None:
    try:
        os.remove(f)
    except OSError:
        pass


def _disk_full(e: BaseException) -> bool:
    """A full disk: making it another way would fail the same, so the export fails and says so."""
    return getattr(e, "errno", None) in (28, 112) or "No space left" in str(e) or "There is not enough space" in str(e)


def _reason(e: BaseException) -> str:
    """An error's last line, short, for the log and the export's warning."""
    lines = [ln.strip() for ln in str(e).strip().splitlines() if ln.strip() and ln.strip() != "ffmpeg failed:"]
    return (lines[-1] if lines else type(e).__name__)[:200]


def _warn(job: dict, m: str) -> None:
    """Add to the export's warning (5.7.1: each warning used to replace the one before, so only the last was shown)."""
    m = (m or "").strip()
    if not m:
        return
    have = job.get("warning") or ""
    if m not in have:
        job["warning"] = (have + " " + m).strip() if have else m


def run_export(S, job: dict, pid: str, o: dict) -> dict:
    pr = S.projects[pid]
    video = pr["video"]
    if not os.path.exists(video):
        raise RuntimeError("This video's file was deleted, so it can't be exported.")
    info = engine.probe(video)
    dur = float(info.get("duration") or _duration(pr))
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length.")
    outdir = locations.vods_dir()          # Settings › Storage › Exported VODs, apart from the clips (5.0)
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
    title = youtube_auto.youtube_title(str(body.get("title") or pr.get("title") or ""))
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
            r = youtube_auto.upload_youtube(path, title, desc, YT_UPLOAD_URL, S.job_step(job), S.job_check(job))
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


def name_songs(pr: dict, progress=lambda x: None, check=lambda: None, log=lambda m: None) -> dict:
    """Which song each music stretch is (5.3, songid.py): recognised with Shazam, its recording found on YouTube, so
    Erase song takes that exact recording out, the way YouTube Studio's does. Kept with the scan."""
    import songid
    scan = load_scan(pr)
    if not scan or not scan.get("spans"):
        return scan or {}
    if not songid.available():
        log("Song recognition isn't installed (shazamio): Erase song uses the eraser model.")
        return scan
    n = songid.name_spans(pr["video"], scan["spans"], locations.temp_dir("songs"), lambda f: progress(f * 0.6), check, log)
    spread_songs(pr, scan, lambda f: progress(0.6 + f * 0.4), check, log)
    scan["named"] = time.time()
    _write_json(_scan_file(pr), dict(scan, video=_sig(pr)))
    found = sum(1 for x in scan["spans"] if (x.get("song") or {}).get("url"))
    log(f"Recognised {n} of {len(scan['spans'])} music stretch{'es' if len(scan['spans']) != 1 else ''}"
        f"{f', {found} with the recording found' if n else ''}.")
    return scan


def spread_songs(pr: dict, scan: dict, progress=lambda x: None, check=lambda: None, log=lambda m: None,
                 extra: Optional[dict] = None) -> int:
    """Everywhere each recognised song plays (5.7, songref.sweep): the whole video searched for its recording, so the
    same song under talking, past a stretch's edges or played again later is erased too, not only where the music scan
    heard music (what YouTube still caught). A stretch it plays in is given the song, and new ones are added. Returns
    how many seconds were added."""
    import songref
    dur = _duration(pr)
    songs = {}
    for x in scan.get("spans", []):
        so = x.get("song") or {}
        if so.get("url"):
            songs.setdefault(so["url"], so)
    for url, so in (extra or {}).items():              # songs heard where the scan heard no music (find_everywhere)
        songs.setdefault(url, {k: so[k] for k in ("title", "artist", "url") if k in so})
    if not songs or dur <= 0:
        return 0
    refs = {}
    for url in songs:
        try:
            refs[url] = songref.read(songref.fetch(url, locations.temp_dir("songs"), log))
        except RuntimeError as e:
            log(f"  Couldn't get {songs[url].get('title', 'a song')} to look for it everywhere: {e}")
    if not refs:
        return 0
    log(f"Looking for {len(refs)} recognised song{'s' if len(refs) != 1 else ''} everywhere in the video...")
    hits = songref.sweep(pr["video"], dur, refs, progress, check)
    added = 0.0
    spans = scan["spans"]
    for url, ranges in hits.items():
        so = songs[url]
        for a, b in ranges:
            over = [x for x in spans if x["end"] > a and x["start"] < b]
            for x in over:
                if not (x.get("song") or {}).get("url"):
                    x["song"] = dict(so)          # an unrecognised stretch the song plays in is that song
            # only the parts no stretch covers yet become new stretches
            free, at = [], a
            for x in sorted(over, key=lambda x: x["start"]):
                if x["start"] > at:
                    free.append((at, min(b, x["start"])))
                at = max(at, x["end"])
            if at < b:
                free.append((at, b))
            for p, q in free:
                if q - p >= 2:
                    spans.append({"start": round(p, 2), "end": round(q, 2), "confidence": 1.0, "speech": 0.0,
                                  "song": dict(so), "found": "everywhere"})
                    added += q - p
    spans.sort(key=lambda x: x["start"])
    if added:
        log(f"Found the recognised songs in {engine.fmt_ts(added)} more of the video than the music scan heard.")
    return int(added)


def find_everywhere(pr: dict, progress=lambda x: None, check=lambda: None, log=lambda m: None) -> dict:
    """Look for songs everywhere (5.7): the whole video, not only where the music scan heard music, a sample every
    30 s sent to Shazam; every song heard is then found everywhere it plays and added as an Erase song stretch.
    Songs quiet under talking are what YouTube still caught after an export."""
    import songid
    scan = load_scan(pr)
    if not scan:
        raise RuntimeError("Scan for music first.")
    if not songid.available():
        raise RuntimeError("Song recognition isn't installed (shazamio).")
    dur = _duration(pr)
    skip = [(float(x["start"]), float(x["end"])) for x in scan.get("spans", [])]
    log(f"Listening for songs everywhere in the video: a sample every {int(songid.EVERY)} s...")
    new = songid.scan_everywhere(pr["video"], dur, skip, locations.temp_dir("songs"), lambda f: progress(f * 0.7), check, log)
    added = spread_songs(pr, scan, lambda f: progress(0.7 + f * 0.3), check, log, extra=new)
    scan["everywhere"] = time.time()
    _write_json(_scan_file(pr), dict(scan, video=_sig(pr)))
    log(f"Heard {len(new)} song{'s' if len(new) != 1 else ''} the music scan missed"
        + (f"; {engine.fmt_ts(added)} more to erase." if added else "."))
    return scan


def start_scan(S, pid: str, recognise_only: bool = False, everywhere: bool = False) -> dict:
    """The copyright scan, then each stretch's song recognised. recognise_only: just the songs, on the last scan.
    everywhere: Look for songs everywhere (find_everywhere), on the last scan."""
    pr = S.projects[pid]

    def go(job):
        if everywhere:
            S.job_step(job)("Looking for songs everywhere")
            return find_everywhere(pr, S.job_progress(job, 0, 1), S.job_check(job), S.job_logger(job))
        step, check, log = S.job_step(job), S.job_check(job), S.job_logger(job)
        if not recognise_only:
            step("Looking for music")
            scan_copyright(pr, S.job_progress(job, 0, 0.3), check, log)
        step("Recognising the songs")
        whole_too = not recognise_only and _can_recognise()
        try:
            name_songs(pr, S.job_progress(job, 0 if recognise_only else 0.3, 0.6 if whole_too else 1), check, log)
        except engine.Cancelled:
            raise
        except Exception as e:          # noqa: BLE001  the scan itself is done: recognising is a bonus
            log(f"Couldn't recognise the songs: {e}")
        if whole_too:
            # 5.7.3: Look for songs everywhere with every scan, not only when its button is pressed: the songs the music
            # scan can't hear (quiet under talking) are most of what YouTube still caught
            step("Looking for songs everywhere")
            try:
                find_everywhere(pr, S.job_progress(job, 0.6, 1), check, log)
            except engine.Cancelled:
                raise
            except Exception as e:      # noqa: BLE001
                log(f"Couldn't look for songs everywhere: {e}")
        return pr["copyright"]
    return _start(S, pid, "copyright_job", "copyright", go)


def _segfind_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "segfind.json")


def load_found(pr: dict) -> Optional[dict]:
    """The last Find segments of this video ({"parts", "at", "length", "heard"}), if the video hasn't changed since."""
    if "vod_found" not in pr:
        pr["vod_found"] = None
        try:
            with open(_segfind_file(pr), encoding="utf-8") as f:
                data = json.load(f)
            if data.get("video") == _sig(pr) and isinstance(data.get("parts"), list):
                pr["vod_found"] = {k: data.get(k) for k in ("parts", "at", "length", "heard")}
        except (OSError, ValueError, TypeError):
            pass
    return pr["vod_found"]


def find_segments(S, job: dict, pr: dict, body: dict) -> dict:
    """Find segments (5.0.3, segfind.py): split the video into its parts, by topic, pauses and games.
    The whole video is listened to first when it can be (the same transcript captions and flagged words use);
    without the speech model, what's been heard already is used, and pauses and games do the rest."""
    import segfind
    dur = _duration(pr)
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length.")
    model, lang = str(body.get("whisper_model") or "small")[:40], str(body.get("language") or "")[:10]
    length = body.get("length") if body.get("length") in segfind.LENGTHS else "medium"
    has_audio = (pr.get("info") or {}).get("has_audio", True)
    if has_audio and (whisper.whisper_available() or (pr.get("yt") or {}).get("words")):
        words = hear_all(S, pr, model, lang, 0.0, dur, S.job_progress(job, 0, 0.95), S.job_check(job), S.job_step(job), "the topics")
        got = "all"
    else:
        words = heard(pr, model, lang, 0.0, dur)["words"] if has_audio else []
        got = "some" if words else "none"
    S.job_step(job)("Finding where the topic changes")
    meta = engine._meta(pr["workdir"])
    games = [{"start": float(g.get("start", 0)), "game": str(g.get("game") or "")} for g in meta.get("games") or []
             if float(g.get("start", 0)) > 0]
    games += [{"start": float(a), "game": str(title)} for a, b, title in ((pr.get("yt") or {}).get("chapters") or []) if float(a) > 0]
    an = pr.get("analysis")
    parts = segfind.find(words, dur, length, score=getattr(an, "score", None), games=games)
    for i, p in enumerate(parts):            # a part that starts with a game or chapter takes its name
        g = next((x["game"] for x in games if abs(x["start"] - p["start"]) <= 60 and x["game"]), "")
        p["name"] = (f"{g}: {p['name']}" if g and p["name"] else g) or p["name"] or f"Part {i + 1}"
        p["on"] = not p["quiet"]
    pr["vod_found"] = {"parts": parts, "at": time.time(), "length": length, "heard": got}
    _write_json(_segfind_file(pr), dict(pr["vod_found"], video=_sig(pr)))
    S.job_logger(job)(f"Found {len(parts)} part{'s' if len(parts) != 1 else ''}"
                      + ("" if got == "all" else " (without the whole video heard: from pauses and games)") + ".")
    return {"parts": len(parts)}


def start_segfind(S, pid: str, body: dict) -> dict:
    pr = S.projects[pid]
    return _start(S, pid, "vod_found_job", "segfind", lambda job: find_segments(S, job, pr, body))


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


def estimate_export(S, pid: str, body: dict) -> dict:
    """How long the export would take with these choices, before it starts (5.7.4): {"seconds", "note", "held"}."""
    pr = S.projects[pid]
    dur = _duration(pr)
    if dur <= 0 or not os.path.isfile(pr["video"]):
        return {"seconds": None, "note": "", "held": []}
    return exporteta.summary(estimate(S, pr, clean_options(body, dur), 0.0, dur))


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
            "scan": _scan_view(load_scan(pr)), "scan_job": job("copyright_job"), "music": load_music(pr),
            "found": load_found(pr), "found_job": job("vod_found_job"),
            "flags": load_flags(pr), "words_job": job("vod_words_job"), "hear_job": job("vod_hear_job"),
            "preview_job": job("vod_preview_job"), "export_job": job("vodexport_job"),
            "exported": bool(out and os.path.isfile(out)), "youtube": pr.get("vod_yt"), "youtube_job": job("vod_yt_job"),
        })
    cfg = safety.load_config()
    return {"videos": vids, "library": library(S), "music_model": audio_events.available(),
            "voice_model": can_erase(), "voice_model_mb": songerase.MODEL_MB, "song_eraser": songerase.available(),
            "whisper": whisper.whisper_available(), "browser": studio_auto.available(),
            "censor": cfg.get("censor", "off"), "censor_categories": [safety.CATEGORIES[c]["label"] for c in
                                                                      cfg.get("censor_categories", []) if c in safety.CATEGORIES]}


def suggested_songs(pr: dict) -> list:
    """The songs you named on Export VOD's Erase song stretches (5.3), for Segments: [(start, end, source)]."""
    scan = load_scan(pr) or {}
    found = {(s["start"], s["end"]): (s.get("song") or {}).get("url", "") for s in scan.get("spans", [])}
    mine = load_music(pr)
    if not mine:                # the scan's stretches as they are: each recognised one with its recording
        return [(a, b, u) for (a, b), u in found.items() if song_source(u)]
    same = abs(float(mine["at"]) - float(scan.get("at") or 0)) < 1e-6
    out = []
    for s in mine["songs"]:
        if s.get("act") != "voice" or not (same or s.get("mine")):
            continue
        # the song you named, else the one recognised for the same stretch (edges you moved keep it)
        src = (song_source(s.get("song")) or song_source((s.get("found") or {}).get("url"))
               or (song_source(found.get((s["start"], s["end"]))) if same else ""))
        if src:
            out.append((s["start"], s["end"], src))
    return out


def covered(a: float, b: float, spans: list) -> bool:
    """Whether at least half of a to b is inside spans [(start, end)] (a stretch you moved the edges of still covers
    the one the scan found)."""
    if b <= a:
        return True
    inside = sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)
    return inside >= 0.5 * (b - a)


def suggested(pr: dict) -> tuple:
    """What Export VOD starts with for a video, for an export that has no screen of its own (Segments): each
    music stretch as you left it on Export VOD (5.0), or as the copyright scan suggests, and the flagged words in the categories Settings › Content
    check mutes. ([(start, end, "voice" | "mute")], [(start, end)])"""
    scan = _scan_view(load_scan(pr))
    music = [(s["start"], s["end"], s["suggest"]) for s in (scan or {}).get("spans", []) if s["suggest"] != "keep"]
    named = {(a, b) for a, b, _ in suggested_songs(pr)}
    mine = load_music(pr)
    if mine:          # what you set on Export VOD: all of it for this scan, only what you marked yourself for a newer one
        same = abs(float(mine["at"]) - float((scan or {}).get("at") or 0)) < 1e-6
        rows = [s for s in mine["songs"] if same or s.get("mine")]
        yours = [(s["start"], s["end"], s["act"]) for s in rows]
        # 5.7.3: with your list for this scan, the stretches the scan gained after it was made (Recognise the songs and
        # Look for songs everywhere add them to the same scan) are added too: they were left out, songs and all
        extra = [m for m in music if same and not covered(m[0], m[1], [(x, y) for x, y, _ in yours])]
        music = ([] if same else music) + extra + [(a, b, m) for a, b, m in yours if m != "keep"]
        # without the song eraser, an Erase song stretch is muted, unless you said which song it is (that needs no model)
        music = [(a, b, "mute" if m == "voice" and not can_erase() and (a, b) not in named else m) for a, b, m in sorted(music)]
    flags = (load_flags(pr) or {}).get("flags") or []
    return music, [(max(0.0, f["start"] - 0.05), f["end"] + 0.05) for f in flags if f.get("on")]
