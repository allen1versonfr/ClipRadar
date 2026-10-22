"""
YouTube videos without chat (4.9): what YouTube itself says about a video, used next to the
sound when ranking moments, and to title clips.

  Most replayed  YouTube's graph of where viewers rewatch (yt-dlp's "heatmap": about a hundred
                 segments, 0-1). The bumps are moments people went back for.
  Comments       timestamps in the top comments ("3:45 when he fell"): each commenter counts once
                 per moment and likes count as agreement, so one comment can't make a clip but a
                 moment many people point at stands out. Their words can title the clip, the same
                 way chat phrases do (chattitles), shown as Comments: "...".
  Captions       YouTube's own captions (uploaded or automatic), with word timing when YouTube
                 has it. Titles and cut points use them straight away, without Whisper; burned-in
                 captions still use Whisper, which punctuates.
  Chapters       kept for the reason text ("in chapter 'Boss fight'").

Everything here is optional: a video without these, a network error or a format change gives
nothing, and ranking falls back to the sound as before. Saved as youtube.json in the project.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from typing import Optional

import numpy as np

FILE = "youtube.json"
MAX_COMMENTS = 400                     # top comments read (enough for the timestamps people agree on)
REPLAY_GAIN = 3.0                      # how much a "most replayed" peak can add (audio z-scores are 0-8)
COMMENT_GAIN = 3.0
LIKES_PER_VOTE = 25                    # a liked comment counts as a few people pointing at the moment
MAX_VOTES = 8
_TS = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?![\d:])")


def path(workdir: str) -> str:
    return os.path.join(workdir, FILE)


def load(workdir: str) -> dict:
    try:
        with open(path(workdir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(workdir: str, patch: dict) -> dict:
    d = load(workdir)
    d.update(patch)
    tmp = path(workdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, path(workdir))
    return d


# ---------------------------------------------------------------- reading yt-dlp's info

def from_info(info: dict) -> dict:
    """Most replayed and chapters from yt-dlp's metadata (no extra download)."""
    out = {}
    hm = []
    for seg in info.get("heatmap") or []:
        try:
            a, b, v = float(seg["start_time"]), float(seg["end_time"]), float(seg["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a and 0 <= v <= 1.0001:
            hm.append([round(a, 2), round(b, 2), round(v, 4)])
    if len(hm) >= 10:
        out["heatmap"] = hm
    ch = []
    for c in info.get("chapters") or []:
        try:
            ch.append([round(float(c["start_time"]), 2), round(float(c["end_time"]), 2), str(c.get("title") or "")[:80]])
        except (KeyError, TypeError, ValueError):
            continue
    if ch:
        out["chapters"] = ch
    out["duration"] = float(info.get("duration") or 0)
    out["language"] = str(info.get("language") or "")
    return out


def timestamps(text: str, duration: float = 0) -> list:
    """Times (seconds) written in a comment: 3:45, 1:02:03. Ones past the end of the video are dropped."""
    out = []
    for h, m, s in _TS.findall(text or ""):
        t = int(h or 0) * 3600 + int(m) * 60 + int(s)
        if duration <= 0 or t <= duration:
            out.append(float(t))
    return out


def comments_from_info(info: dict, duration: float) -> list:
    """[(time, text without the timestamp, author, likes)] for each timestamp in the comments."""
    out = []
    for c in info.get("comments") or []:
        if not isinstance(c, dict) or c.get("parent") not in (None, "root"):
            continue                                  # replies quote the parent's time: count the parent once
        text = str(c.get("text") or "")
        ts = timestamps(text, duration)
        if not ts or len(ts) > 4:                     # a list of chapters someone typed isn't a reaction
            continue
        rest = re.sub(r"\s+", " ", _TS.sub(" ", text)).strip(" -:|")[:200]
        try:
            likes = int(c.get("like_count") or 0)
        except (TypeError, ValueError):
            likes = 0
        author = str(c.get("author_id") or c.get("author") or "")
        for t in ts:
            out.append([t, rest, author, likes])
    return out


def caption_track(info: dict) -> Optional[tuple]:
    """(url, automatic) of the best caption track to read, json3 preferred: uploaded captions in
    the video's language, then automatic ones."""
    lang = (info.get("language") or "").split("-")[0].lower()

    def pick(tracks: dict, keys: list) -> Optional[str]:
        for k in keys:
            for f in tracks.get(k) or []:
                if f.get("ext") == "json3" and f.get("url"):
                    return f["url"]
        return None
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    langs = [lang] if lang else []
    langs += ["en"] if "en" not in langs else []
    for keys, is_auto in (([k for k in subs for l in langs if k == l or k.startswith(l + "-")], False),
                          ([f"{l}-orig" for l in langs] + langs, True)):
        u = pick(subs if not is_auto else auto, keys)
        if u:
            return u, is_auto
    return None


def caption_words(json3: dict) -> list:
    """Words with times from a json3 caption file. Automatic captions time each word; uploaded
    ones time each line, so its words are spread across the line."""
    words = []
    for ev in json3.get("events") or []:
        segs = ev.get("segs") or []
        try:
            t0 = float(ev.get("tStartMs") or 0) / 1000
            dur = float(ev.get("dDurationMs") or 0) / 1000
        except (TypeError, ValueError):
            continue
        parts = []
        for s in segs:
            txt = str(s.get("utf8") or "")
            if not txt.strip() or txt.strip() == "\n":
                continue
            off = s.get("tOffsetMs")
            parts.append((None if off is None else t0 + float(off) / 1000, txt.strip()))
        if not parts:
            continue
        timed = any(p[0] is not None for p in parts[1:])
        if timed:
            for k, (t, txt) in enumerate(parts):
                for w in txt.split():
                    words.append({"start": round(t if t is not None else t0, 3), "word": w})
        else:
            line = " ".join(p[1] for p in parts).split()
            step = (dur or 0.4 * len(line)) / max(1, len(line))
            for k, w in enumerate(line):
                words.append({"start": round(t0 + k * step, 3), "word": w})
    words = [w for w in words if not re.fullmatch(r"\[[^\]]*\]", w["word"])]      # [Music], [Applause]
    words.sort(key=lambda w: w["start"])
    for k, w in enumerate(words):
        nxt = words[k + 1]["start"] if k + 1 < len(words) else w["start"] + 0.5
        w["end"] = round(min(nxt, w["start"] + 1.2), 3)
    return words


# ---------------------------------------------------------------- fetching

def fetch_comments(url: str, duration: float, log=lambda m: None, cookie_args: list = ()) -> list:
    """Timestamps from the top comments, through yt-dlp (one extra request; nothing on failure)."""
    import subprocess
    import engine
    exe = engine.which("yt-dlp")
    if not exe:
        return []
    cmd = [exe, "-J", "--skip-download", "--write-comments", "--no-warnings", "--ignore-config", "--no-playlist",
           "--extractor-args", f"youtube:max_comments={MAX_COMMENTS},{MAX_COMMENTS},0,0;comment_sort=top", *cookie_args]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
        info = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip().startswith("{") else {}
    except Exception:
        return []
    out = comments_from_info(info, duration)
    if out:
        log(f"{len({c[2] for c in out}):,} commenters pointed at moments in this video.")
    return out


def fetch_captions(info: dict, log=lambda m: None) -> list:
    tr = caption_track(info)
    if not tr:
        return []
    try:
        req = urllib.request.Request(tr[0], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            words = caption_words(json.loads(r.read().decode("utf-8", "replace")))
    except Exception:
        return []
    if words:
        log(f"Using YouTube's {'automatic ' if tr[1] else ''}captions for titles ({len(words):,} words).")
    return words


def gather(url: str, workdir: str, info: dict, log=lambda m: None, cookie_args: list = ()) -> dict:
    """Everything above for one YouTube video, saved in the project. Never raises."""
    try:
        d = from_info(info)
        d["comments"] = fetch_comments(url, d["duration"], log, cookie_args)
        d["words"] = fetch_captions(info, log)
        if d.get("heatmap"):
            log("Using YouTube's Most replayed graph.")
        return save(workdir, d)
    except Exception as e:
        log(f"Couldn't read YouTube's extra information ({str(e)[:80]}).")
        return {}


# ---------------------------------------------------------------- signals

def replay_signal(d: dict, n: int) -> Optional[np.ndarray]:
    """0..1 per second: how far above its usual level the Most replayed graph is."""
    hm = d.get("heatmap") or []
    if len(hm) < 10 or n <= 0:
        return None
    x = np.zeros(n, dtype=np.float32)
    for a, b, v in hm:
        x[int(max(0, a)):int(min(n, math.ceil(b)))] = v
    base = float(np.median(x))
    top = float(x.max())
    if top - base < 0.05:
        return None                                    # flat graph: nothing stands out
    return np.clip((x - base) / (top - base), 0, 1).astype(np.float32)


def comment_signal(d: dict, n: int) -> tuple:
    """(0..1 per second, commenters per second): moments the comments point at. A timestamp marks
    where a moment starts, so each one counts over the next few seconds."""
    cs = d.get("comments") or []
    if not cs or n <= 0:
        return None, None
    votes = np.zeros(n, dtype=np.float32)
    people = np.zeros(n, dtype=np.float32)
    seen = set()
    for t, _text, author, likes in cs:
        s = int(t)
        if not 0 <= s < n or (author, s // 5) in seen:
            continue
        seen.add((author, s // 5))
        v = min(MAX_VOTES, 1 + int(likes) // LIKES_PER_VOTE)
        votes[s:min(n, s + 10)] += v
        people[s:min(n, s + 10)] += 1
    if votes.max() <= 0:
        return None, None
    # one commenter alone is weak evidence; agreement grows the signal up to full at 8 votes
    sig = np.clip(votes / 8.0, 0, 1)
    return sig.astype(np.float32), people


def apply(an, d: dict):
    """Attach the YouTube signals to an Analysis (engine.Analysis.combine mixes them in)."""
    n = len(an.audio_z)
    an.replay = replay_signal(d, n)
    an.comments, an.comment_people = comment_signal(d, n)
    an.chapters = d.get("chapters") or []
    return an


def chapter_at(d_or_chapters, t: float) -> str:
    chs = d_or_chapters.get("chapters") if isinstance(d_or_chapters, dict) else d_or_chapters
    for a, b, title in chs or []:
        if a <= t < b:
            return title
    return ""


def words_in(d: dict, a: float, b: float) -> list:
    return [w for w in d.get("words") or [] if a <= w["start"] < b]


def comment_title(d: dict, start: float, end: float, streamer: str = "") -> str:
    """Comments: "..." from a phrase several commenters used about this stretch ('' if none)."""
    msgs = []
    for t, text, author, likes in d.get("comments") or []:
        if start - 3 <= t <= end:
            # likes are people agreeing with the comment: each LIKES_PER_VOTE likes count as one more voice
            for k in range(min(MAX_VOTES, 1 + int(likes) // LIKES_PER_VOTE)):
                msgs.append((text, f"{author}#{k}"))
    if not msgs:
        return ""
    import chattitles
    found = chattitles.find(msgs, set(), streamer)
    return f'Comments: "{found["phrase"]}"' if found.get("phrase") else ""
