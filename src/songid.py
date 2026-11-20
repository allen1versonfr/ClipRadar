"""
Which song is it (5.3): Erase song the way YouTube Studio does it, without being told the song.

YouTube knows the song in a stretch because Content ID recognised it. Clip Radar does the same for each stretch the
copyright scan found:

  1. Recognise it: 12-second samples of the stretch are sent to Shazam (through the shazamio library), as many as it
     takes, from different places in the stretch. Where the song eraser model is there, the music is pulled out of
     the talking first, so a song under talking is recognised too.
  2. Find the recording: the song's official audio on YouTube (an "Artist - Topic" upload first), fetched with
     yt-dlp, and checked: the recording must really be the one in the stream (songref's correlation spike).
  3. Export VOD and Segments then take that exact recording out (songref.py). A stretch that isn't recognised, or
     whose recording isn't found, is erased by the model as before.

Only those 12-second samples leave the PC, and only as Shazam's fingerprint of them, not the sound itself: the same as
pressing Shazam on a phone held up to the speakers. It runs with the copyright scan (or Recognise the songs on Export
VOD), never on its own.

  identify(sound [2, n]) -> {"title", "artist", "key"} or None
  find_recording(song, sound, folder) -> {"url", "name"} or None
  name_spans(video, spans, folder) -> spans with "song" filled in where recognised
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import wave
from typing import Callable, Optional

import numpy as np

import songref

SR = songref.SR
WINDOW = 12.0          # seconds Shazam hears at a time (shorter and it never answers)
TRIES = 4              # samples tried per stretch before giving up
CANDIDATES = 4         # YouTube results tried for the recording
MAX_SKEW = 2e-3        # Shazam's answer skewed more than this in time or pitch: a wrong answer, most likely
GAP = 0.4              # seconds between Shazam requests, so it's never asked too fast

_noop = lambda *a, **k: None  # noqa: E731


def available() -> bool:
    try:
        import shazamio  # noqa: F401
        return True
    except Exception:       # noqa: BLE001  not installed, or a broken install: recognition is simply off
        return False


def _wav(x: np.ndarray) -> bytes:
    """Mono 16-bit WAV, peak at -1 dB (Shazam's fingerprint works on the loudest parts)."""
    y = x.mean(0) if x.ndim == 2 else x
    y = y / (float(np.abs(y).max()) + 1e-9) * 0.9
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    return b.getvalue()


def _ask(sound: np.ndarray) -> Optional[dict]:
    """One Shazam request: {"title", "artist", "key"} or None."""
    from shazamio import Shazam

    async def go():
        s = Shazam()
        try:
            return await s.recognize(_wav(sound), max_skew=MAX_SKEW)
        finally:
            close = getattr(getattr(s, "http_client", None), "close", None)
            if close:
                try:
                    r = close()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:   # noqa: BLE001
                    pass
    try:
        r = asyncio.run(asyncio.wait_for(go(), 30))
    except Exception:           # noqa: BLE001  no network, Shazam down or changed: not recognised
        return None
    tr = (r or {}).get("track") or {}
    if not tr.get("title"):
        return None
    return {"title": str(tr["title"])[:200], "artist": str(tr.get("subtitle") or "")[:200], "key": str(tr.get("key") or "")}


def pull_music(x: np.ndarray) -> np.ndarray:
    """The music in x with the talking taken out (what the song eraser removes), when the eraser model is there."""
    import songerase
    if not songerase.available():
        return x
    xp = np.pad(x, ((0, 0), (songerase.CTX, songerase.SEG)))
    out = []
    for p in range(0, x.shape[1], songerase.GEN):
        kept = songerase.erase_block(xp[:, p:p + songerase.SEG], 1.0)
        out.append(xp[:, p + songerase.CTX:p + songerase.CTX + songerase.GEN] - kept)
    return np.concatenate(out, axis=1)[:, :x.shape[1]].astype(np.float32)


def windows(a: float, b: float, n: int = TRIES) -> list:
    """Where to take the samples in a stretch: spread over it, the middle first (a song is surest there)."""
    if b - a <= WINDOW:
        return [a]
    span = b - a - WINDOW
    order = [0.5, 0.2, 0.8, 0.35, 0.65, 0.05, 0.95][:n]
    return [round(a + f * span, 2) for f in order]


def identify(get: Callable, a: float, b: float, check: Callable = _noop, separate: bool = True) -> Optional[dict]:
    """The song in a stretch, or None. get(t, seconds) -> sound [2, n] of the video from t."""
    for t in windows(a, b):
        check()
        x = get(t, min(WINDOW + 0.5, max(WINDOW, b - t)))
        if x.shape[1] < WINDOW * SR * 0.95:
            continue
        tries = [x]
        if separate:
            try:
                tries.append(pull_music(x))
            except Exception:       # noqa: BLE001  the model failed: the plain sound is still tried
                pass
        for y in tries:
            got = _ask(y)
            time.sleep(GAP)
            if got:
                return got
    return None


# ---------------------------------------------------------------- the recording

_JUNK = re.compile(r"\b(live|cover|remix|sped ?up|slowed|nightcore|karaoke|instrumental|8d|reverb|lyrics? video)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


def candidates(song: dict, search: Optional[Callable] = None) -> list:
    """YouTube videos that could be the song's recording, best first: [{"url", "name", "channel"}].
    The artist's own "Topic" upload (YouTube's official audio) first, then uploads named after the song;
    covers, live versions and remixes left out unless the song itself is one."""
    q = f"{song.get('artist', '')} - {song.get('title', '')}".strip(" -")
    rows = (search or _search)(q)
    title = _norm(song.get("title", ""))
    artist = _norm(song.get("artist", ""))
    first_artist = artist.split(" feat ")[0].split(" ft ")[0].split(" x ")[0].strip()
    out = []
    for r in rows:
        name, ch = r.get("title") or "", r.get("channel") or ""
        n = _norm(name)
        if title and title not in n:
            continue
        if _JUNK.search(name) and not _JUNK.search(song.get("title", "")):
            continue
        topic = ch.endswith(" - Topic") and first_artist and first_artist in _norm(ch)
        by_artist = first_artist and (first_artist in _norm(ch) or first_artist in n)
        dur = float(r.get("duration") or 0)
        if dur and not (45 <= dur <= 15 * 60):
            continue
        rank = (0 if topic else 1 if by_artist else 2, rows.index(r))
        out.append((rank, {"url": r.get("url") or "", "name": name, "channel": ch}))
    return [x for _, x in sorted(out, key=lambda t: t[0]) if x["url"]][:CANDIDATES]


def _search(q: str) -> list:
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}) as y:
            r = y.extract_info("ytsearch8:" + q, download=False) or {}
        return [e for e in (r.get("entries") or []) if e]
    except Exception:           # noqa: BLE001  no network, YouTube changed: nothing found
        return []


def matches(sound: np.ndarray, ref_path: str) -> bool:
    """Whether the recording is the one in the stream: its spike in the correlation stands out (songref._z)."""
    ref = songref.read(ref_path)
    if ref.shape[1] < SR:
        return False
    _, z = songref.locate(sound, ref)
    return z >= songref.MIN_Z


def find_recording(song: dict, sound: np.ndarray, folder: str, check: Callable = _noop, log: Callable = _noop,
                   search: Optional[Callable] = None) -> Optional[dict]:
    """The recording of song on YouTube that's the one in sound (20 s or so of the stretch): {"url", "name"} or None."""
    for c in candidates(song, search):
        check()
        try:
            path = songref.fetch(c["url"], folder)
        except RuntimeError as e:
            log(f"  {c['name']}: {e}")
            continue
        if matches(sound, path):
            return {"url": c["url"], "name": c["name"]}
    return None


def name_spans(video: str, spans: list, folder: str, progress: Callable = _noop, check: Callable = _noop,
               log: Callable = _noop) -> int:
    """Fill in spans[i]["song"] = {"title", "artist", "url"} for each stretch whose song is recognised and whose
    recording is found ("url" "" when only the name is known). Returns how many were named."""
    named = 0
    cache: dict = {}
    for i, s in enumerate(spans):
        check()
        a, b = float(s["start"]), float(s["end"])
        if b - a < 8:
            progress((i + 1) / len(spans))
            continue
        got = identify(lambda t, n: songref.read(video, t, n), a, b, check=check)
        if got:
            k = (got["title"].lower(), got["artist"].lower())
            if k not in cache:
                mid = max(a, (a + b) / 2 - 10)
                cache[k] = find_recording(got, songref.read(video, mid, min(20.0, b - mid)), folder, check, log)
            rec = cache[k]
            s["song"] = dict(got, url=rec["url"] if rec else "")
            named += 1
            log(f"  {_ts(a)}: {got['artist']} - {got['title']}" + ("" if rec else " (its recording wasn't found on YouTube)"))
        progress((i + 1) / len(spans))
    return named


def _ts(t: float) -> str:
    import engine
    return engine.fmt_ts(t)


# ---------------------------------------------------------------- songs the music scan didn't hear (5.7)

EVERY = 30.0               # seconds between the samples Look for songs everywhere sends


def scan_everywhere(video: str, duration: float, skip: list, folder: str, progress: Callable = _noop,
                    check: Callable = _noop, log: Callable = _noop, every: float = EVERY) -> dict:
    """Songs anywhere in the video, music scan or not: a sample every `every` seconds (outside the stretches in skip,
    already known) sent to Shazam. Returns {recording url: {"title", "artist", "url", "at": [seconds heard]}} for each
    song whose recording was found; the caller looks for each everywhere (songref.sweep) to find where it plays."""
    times = [t for t in np.arange(0.0, max(0.0, duration - WINDOW), every)
             if not any(a - WINDOW <= t <= b for a, b in skip)]
    heard: dict = {}
    for i, t in enumerate(times):
        check()
        x = songref.read(video, float(t), WINDOW + 0.5)
        if x.shape[1] >= WINDOW * SR * 0.95 and float(np.abs(x).max()) > 1e-3:
            got = _ask(x)
            time.sleep(GAP)
            if got:
                k = (got["title"].lower(), got["artist"].lower())
                heard.setdefault(k, dict(got, at=[]))["at"].append(round(float(t), 1))
        progress((i + 1) / max(1, len(times)) * 0.8)
    out: dict = {}
    for j, song in enumerate(heard.values()):
        check()
        t = song["at"][0]
        rec = find_recording(song, songref.read(video, max(0.0, t - 4), 20.0), folder, check, log)
        if rec:
            out[rec["url"]] = dict(song, url=rec["url"])
            log(f"  Heard {song['artist']} - {song['title']} at {_ts(t)}" + (f" and {len(song['at']) - 1} more place{'s' if len(song['at']) != 2 else ''}" if len(song["at"]) > 1 else "") + ".")
        else:
            log(f"  Heard {song['artist']} - {song['title']} at {_ts(t)}, but its recording isn't on YouTube: mark that stretch by hand to erase it.")
        progress(0.8 + 0.2 * (j + 1) / max(1, len(heard)))
    return out


# ---------------------------------------------------------------- would YouTube still catch it? (5.7)

def still_heard(get: Callable, stretches: list, check: Callable = _noop, progress: Callable = _noop,
                separate: bool = True) -> list:
    """The parts of the erased stretches a song is still recognised in: [(start, end, "Artist - Title")].
    get(t, seconds) -> the erased sound from t. Each stretch heard in back-to-back WINDOW-second samples, as it is and,
    when the song eraser model is there, with what's left of the music pulled out from under the talking (YouTube's
    Content ID hears music under talking better than Shazam does as it is). Shazam not answering counts as not heard."""
    wins = []
    for a, b in stretches:
        t = a
        while t < b - 3:
            wins.append((t, min(b, t + WINDOW)))
            t += WINDOW
    out = []
    for i, (a, b) in enumerate(wins):
        check()
        x = get(a, max(WINDOW, b - a))
        tries = [x]
        if separate:
            try:
                tries.append(pull_music(x))
            except Exception:       # noqa: BLE001
                pass
        for y in tries:
            got = _ask(y)
            time.sleep(GAP)
            if got:
                out.append((round(a, 2), round(b, 2), f"{got['artist']} - {got['title']}".strip(" -")))
                break
        progress((i + 1) / max(1, len(wins)))
    return out
