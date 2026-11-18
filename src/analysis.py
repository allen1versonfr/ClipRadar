"""
2. analyze and 3. pick clips: what in a video is worth clipping (moved from engine.py, hardening phase 4e; no changes).

  analyze(video, chat, …), analyze_cached(workdir, …)   per-second scores from chat, loudness, laughter and the rest
  Analysis                                              those scores, and what they're made of
  pick_clips(an, …), smart_bounds(…)                    the best non-overlapping moments, with good start and end
  measure_audio, make_thumb, chat_weight, reaction_kind …

engine.py re-exports every name here, so engine.analyze and the rest still work. Code that calls these, and a test
that replaces one (analysis.make_thumb = fake), uses this module: a fake put on engine wouldn't reach it. The shared
helpers (run, ffmpeg, probe…) stay in engine.py and are imported from it (imported on its own, this module has
engine import it first).
"""

from __future__ import annotations

import bisect
import functools
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

import emotes
import procjob

if "engine" not in sys.modules:
    # imported before engine: engine imports this module itself once the helpers below are defined, so engine is
    # imported first and this copy steps aside (the import then gives back the copy engine loaded in full)
    del sys.modules[__name__]
    import engine  # noqa: E402,F401
from engine import (  # noqa: E402  engine imports this module after these are defined
    CREATE_NO_WINDOW, Check, Log, Progress, _noop, chat_times, ffmpeg, fmt_ts, probe, run,
)


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
# 4.32: more of how chat reacts. Kept apart from HYPE, which chat titles also use as words to leave out.
REACT = {
    "😭": 1.5, "😹": 1.2, "🤯": 1.3, "😳": 1.0, "omfg": 1.5, "rofl": 1.5, "sheesh": 1.3, "holy": 1.2,
    "bruh": 0.8, "wow": 1.0, "ww": 1.0, "l": 0.8,
}
# ordinary words that react only when stretched: "noooo", "whaaat", "yesss", "gooo", "brooo", "ohhh"
STRETCHED = {"no": 1.5, "what": 1.2, "yes": 1.0, "go": 1.0, "bro": 0.8, "oh": 0.8}
REACT_PHRASES = (("no shot", 1.5), ("im dead", 1.2), ("i'm dead", 1.2), ("i cant", 1.0), ("i can't", 1.0),
                 ("holy shit", 0.8), ("oh my god", 1.3))
# laughter typed out, in the languages Twitch and YouTube chat is in: hahaha, hehehe, LMAOOO, LOOOL, XDDD,
# jajaja (Spanish), kkkkk / kakaka (Portuguese), wwww / 草 (Japanese), ㅋㅋㅋ (Korean), хахаха (Russian),
# mdr / ptdr (French), jsjsjs, 哈哈哈 (Chinese)
LAUGH_TEXT = re.compile(
    r"^(?:a?(?:ha){2,}h?|a?(?:ah){2,}a?|(?:he){3,}h?|(?:hi){3,}|(?:ja){2,}j?|(?:je){3,}|(?:ka){3,}|k{4,}|w{4,}|"
    r"l+m+f*a+o+|lo{2,}l|lu{2,}l|x+d{2,}|ro+fl|ptdr|mdr|(?:js){3,}|(?:sk){3,}|[хx]?(?:[хx]а){2,}|(?:ㅋ){3,}|(?:ㅎ){3,}|"
    r"草+|(?:哈){2,}|(?:笑){2,})$")
_STRETCH = re.compile(r"(\w)\1{2,}")
TOKEN_REPEAT = 0.3          # the same reaction again in one message ("KEKW KEKW KEKW"): each one after the first


@functools.lru_cache(maxsize=1 << 16)          # chat repeats the same few thousand tokens all stream
def _react_points(tok: str) -> float:
    """What one token says about how chat is reacting (0 for ordinary words)."""
    v = HYPE.get(tok) or REACT.get(tok)
    if v:
        return v
    if LAUGH_TEXT.match(tok):
        return 1.5
    if _STRETCH.search(tok):
        short = _STRETCH.sub(r"\1", tok)       # "lmaoooo" -> "lmao", "poggg" -> "pog", "noooo" -> "no"
        v = HYPE.get(short) or REACT.get(short) or STRETCHED.get(short)
        if v:
            return v
        if LAUGH_TEXT.match(short):
            return 1.5
    return 0.0


# 4.34: two reactions told apart from each other (and from shock). Confused: chat doesn't get what just
# happened ("???", "huh", "wdym", 🤔). Cringe: chat is uncomfortable with it, an awkward moment ("uhhh",
# "ummm", "erm", 😬, "yikes"). Both are reactions worth a clip; reaction_kind says which one a message is.
CONFUSED = {"huh": 1.0, "wdym": 1.0, "wym": 0.8, "confused": 1.0, "🤔": 0.8, "❓": 1.0, "😕": 0.8, "🧐": 0.8,
            "monkahmm": 1.0, "hmm": 0.6}
CRINGE = {"yikes": 1.2, "cringe": 1.2, "awkward": 1.2, "erm": 1.0, "ick": 1.0, "oof": 0.8, "ayo": 1.0,
          "weirdchamp": 1.2, "weird": 0.6, "😬": 1.2, "😐": 1.0, "😑": 1.0, "🫠": 1.0, "🙃": 0.8, "🦗": 1.0}
CONFUSED_PHRASES = ("wait what", "what just happened", "what is happening", "what is going on", "im lost",
                    "i'm lost", "what did he", "what did she", "what did i just")
CRINGE_PHRASES = ("bro what", "why would you", "this is awkward", "so awkward", "second hand embarrassment",
                  "secondhand embarrassment", "im uncomfortable", "i'm uncomfortable")
_UHH = re.compile(r"^(?:u+h+|u+m{2,}|e+r+m+|e+h{2,})$")         # uh, uhh, uuuhhh, umm, errm, ehhh
_HMM = re.compile(r"^h+m{2,}$")                                  # hmm, hmmmm: confused, thinking
_CONFUSED_ASK = re.compile(r"^\W*(?:(?:what|huh|who|how|why|wait|bro|sorry|pardon|excuse me|what the|u+h+|u+m+|e+r+m+)\W*)?\?+[\s?!.]*$", re.I)
KINDS = ("laugh", "hype", "shock", "confused", "cringe", "fail")
_EMOTE_TOK = re.compile(r"[\w:]+")      # emote names keep their case: "CAUGHT" is the emote, "caught" a word
_KIND_OF = {}
for _k in ("kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "😂", "🤣", "xd", "pepelaugh", "kekl", "lulw",
           "kek", "dead", "💀", "😭", "😹", "rofl", "omfg"):
    _KIND_OF[_k] = "laugh"
for _k in ("pog", "pogchamp", "poggers", "pogu", "w", "ez", "letsgo", "hypers", "gg", "goat", "insane", "🔥", "clip",
           "clipit", "clipped", "ww", "sheesh", "yes", "go"):
    _KIND_OF[_k] = "hype"
for _k in ("monkas", "wtf", "omg", "😱", "d:", "nooo", "noway", "wideeyes", "holy", "🤯", "😳", "wow", "no", "what", "oh"):
    _KIND_OF[_k] = "shock"
_KIND_OF.update({k: "confused" for k in CONFUSED})
_KIND_OF.update({k: "cringe" for k in CRINGE})


def _awkward_points(tok: str, alone: bool) -> tuple:
    """(points, kind) for the confused and cringe words. alone: the token is the whole message, so a
    plain "uh" or "hmm" counts (inside a sentence they're just filler)."""
    if tok in CRINGE:
        return CRINGE[tok], "cringe"
    if tok in CONFUSED:
        return CONFUSED[tok], "confused"
    if _UHH.match(tok) and (alone or len(tok) >= 3):
        return 1.2, "cringe"
    if _HMM.match(tok) and (alone or len(tok) >= 4):
        return 0.6, "confused"
    return 0.0, ""


def reaction_kind(text: str) -> str:
    """What kind of reaction a chat message is: laugh, hype, shock, confused, cringe, or '' (talk). Used to
    tell a confused chat ("???", "huh", "wdym") from an awkward, cringe moment ("uhhh", "😬", "yikes")."""
    t = (text or "").lower().strip()
    if not t:
        return ""
    votes = {k: 0.0 for k in KINDS}
    if _CONFUSED_ASK.match(t) or "???" in t:
        votes["confused"] += 1.5
    for p in CONFUSED_PHRASES:
        if p in t:
            votes["confused"] += 1.5
    for p in CRINGE_PHRASES:
        if p in t:
            votes["cringe"] += 1.5
    for tok in _EMOTE_TOK.findall(text):
        pe = emotes.POPULAR.get(tok)
        if pe and pe[0] in votes:
            votes[pe[0]] += 1.0
    toks = _TOKEN.findall(t)
    alone = len([x for x in toks if x.isalnum()]) <= 1
    for tok in toks:
        v, kind = _awkward_points(tok, alone)
        if kind:
            votes[kind] += 1.0
            continue
        short = _STRETCH.sub(r"\1", tok)
        kind = _KIND_OF.get(tok) or (_KIND_OF.get(short) if short != tok else None)
        if kind in ("hype", "shock") and tok in ("yes", "go", "no", "what", "oh"):
            kind = None                     # these react only when stretched ("noooo", "whaaat")
        if not kind and (LAUGH_TEXT.match(tok) or LAUGH_TEXT.match(short)):
            kind = "laugh"
        if kind:
            votes[kind] += 1.0
    best = max(KINDS, key=lambda k: votes[k])      # ties: the order of KINDS
    return best if votes[best] > 0 else ""


MOOD_LABEL = {"confused": "chat confused", "cringe": "awkward moment", "both": "awkward and confusing"}
MOOD_MIN = 5            # chatters reacting the same way before a moment is called confused or awkward


def chat_mood(msgs: list) -> tuple:
    """How chat reacted around a moment: (mood, {kind: chatters}). msgs: (seconds, text, user, kind) without
    the noise. Each chatter counts once, for what they reacted with most. mood is 'confused' or 'cringe'
    when that's clearly how chat took it: at least MOOD_MIN people, a third of everyone reacting, and well
    ahead of the other one ("???" and "huh" against "uhhh" and "😬"); 'both' when chat was split between
    them. Otherwise '', and laughs, hype and shock speak for themselves in the reason."""
    per: dict = {}
    for m in msgs:
        k = reaction_kind(m[1])
        if k:
            d = per.setdefault((m[2] if len(m) > 2 else "") or id(m), {})
            d[k] = d.get(k, 0) + 1
    counts = {k: 0 for k in KINDS}
    for d in per.values():
        counts[max(d, key=lambda k: (d[k], -KINDS.index(k)))] += 1
    total = sum(counts.values())
    a, b = counts["confused"], counts["cringe"]
    mood = ""
    for k, n_, other in (("confused", a, b), ("cringe", b, a)):
        if n_ >= MOOD_MIN and n_ >= total / 3 and n_ >= 1.5 * other and n_ >= max(counts.values()):
            mood = k
    if not mood and min(a, b) >= MOOD_MIN and a + b >= total / 3 and a + b >= max(counts.values()):
        mood = "both"
    return mood, counts


def _awkward_word(tok: str) -> str:
    """One spelling for the reason: "uuhhhh" -> "uhh", "ummmm" -> "umm", "hmmmm" -> "hmm"."""
    for rx, word in ((re.compile(r"^u+h+$"), "uhh"), (re.compile(r"^u+m+$"), "umm"), (re.compile(r"^e+r+m+$"), "erm"),
                     (re.compile(r"^e+h+$"), "ehh"), (_HMM, "hmm")):
        if rx.match(tok):
            return word
    return tok


def chat_weight(text: str) -> float:
    t = text.lower()
    w = 1.0
    if "clip it" in t or "clip that" in t or "clip this" in t:
        w += 3.0
    if "no way" in t or "lets go" in t or "let's go" in t:
        w += 1.5
    for p, v in REACT_PHRASES:
        if p in t:
            w += v
            break
    if _CONFUSED_ASK.match(t) and "???" not in t:
        w += 0.8                          # "?", "huh?", "what??": chat didn't get it
    if any(p in t for p in CONFUSED_PHRASES + CRINGE_PHRASES):
        w += 1.0
    seen: dict = {}
    toks = _TOKEN.findall(t)
    alone = len([x for x in toks if x.isalnum()]) <= 1
    popular: set = set()
    for tok in _EMOTE_TOK.findall(text):
        # the 7TV emotes chat uses most, by their exact name ("CAUGHT", not "caught")
        pe = emotes.POPULAR.get(tok)
        if pe and tok.lower() not in HYPE:
            popular.add(tok.lower())
            k = seen[tok.lower()] = seen.get(tok.lower(), 0) + 1
            w += pe[1] if k == 1 else pe[1] * TOKEN_REPEAT if k <= 3 else 0.0
    for tok in toks:
        if tok in popular:
            continue                      # counted above by its exact name
        v = _react_points(tok) or _awkward_points(tok, alone)[0]
        if v <= 0:
            continue
        key = _STRETCH.sub(r"\1", tok)
        k = seen[key] = seen.get(key, 0) + 1
        w += v if k == 1 else v * TOKEN_REPEAT if k <= 3 else 0.0
    if "???" in t:
        w += 1.0
    if re.search(r"(\w)\1{4,}", t):  # "LETSGOOOOO", "AAAAAA"
        w += 0.8
    return min(w, 8.0)


def is_reaction(text: str) -> bool:
    """A message that reacts (a laugh, hype, shock, a clip request) rather than talks."""
    return chat_weight(text or "") > 1.0


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
    procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
    blk = sr // FINE_HZ
    pows: list = []
    rest = np.zeros(0, dtype=np.float32)
    scanner = audio_events.Scanner() if use_ev else None
    got = 0
    ev_failed = False
    # FFmpeg decodes the next stretch while this thread listens to the last one. Read straight from the
    # pipe, FFmpeg sat idle whenever the laughter model ran (measured on a 9-hour VOD: 42 s of decoding
    # plus about 140 s of listening, one after the other). A few chunks ahead keeps memory small.
    chunks: queue.Queue = queue.Queue(maxsize=4)

    def read_ahead():
        try:
            while True:
                c = p.stdout.read(sr * bps * 30)
                chunks.put(c)
                if not c:
                    return
        except (OSError, ValueError):
            chunks.put(b"")

    reader = threading.Thread(target=read_ahead, daemon=True)
    reader.start()
    try:
        while True:
            chunk = chunks.get()
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
        while reader.is_alive():               # cancelled with the queue full: let the reader finish
            try:
                chunks.get(timeout=0.5)
            except queue.Empty:
                pass
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
AGREE_WEIGHT = 0.5      # chat and the stream's sound both reacting (detect.agreement), when a preset doesn't say


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
    captions: Optional[np.ndarray] = None  # 4.39: lines in YouTube's captions that land (no chat only), 0..1
    chapters: Optional[list] = None
    # 4.45: Twitch VODs (twitchclips): how much viewers clipped each second 0..1, clips and their views there
    vclips: Optional[np.ndarray] = None
    # 4.45.1: chat's reaction per second (chat_rate, at chat_delay), kept so detect.enrich doesn't weigh every
    # message again: it did, on every open and every reopen, about 3 s a pass for 300,000 messages
    chat_rate: Optional[np.ndarray] = None
    vclip_people: Optional[np.ndarray] = None
    vclip_views: Optional[np.ndarray] = None
    # 4.32: messages per second and those that were plain talk (shifted by the delay), and how much chat
    # counts where it's just talking (detect.talk_mult)
    said_rate: Optional[np.ndarray] = None
    talk_rate: Optional[np.ndarray] = None
    talk: Optional[np.ndarray] = None

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
        n = len(self.audio_z)
        parts = {"chat": wc * self.chat_z, "audio": (1 - wc) * audio}
        if self.has_events:
            for k in EVENT_WEIGHTS:
                parts[k] = float(w.get(k, EVENT_WEIGHTS[k])) * self.event_level(k)
        for k, arr in (("emotes", self.emote_z), ("surge", self.surge), ("react", self.react)):
            if arr is not None and float(w.get(k, 0)) > 0:
                parts[k] = float(w[k]) * _fit(arr, n)
        if self.talk is not None and wc > 0:
            # chat surging with plain talk (everyone answering a question) isn't chat reacting: what chat
            # and its speeding up added counts for less, shown as a part of its own
            chatty = np.clip(parts["chat"], 0, None) + parts.get("surge", 0)
            parts["talk"] = (chatty * (_fit(self.talk, n, 1.0) - 1)).astype(np.float32)
        import ytextra
        for k, arr, gain in (("replay", self.replay, ytextra.REPLAY_GAIN), ("comments", self.comments, ytextra.COMMENT_GAIN),
                             ("captions", self.captions, ytextra.CAPTION_GAIN)):
            if arr is not None:
                parts[k] = float(w.get(k, 1.0)) * gain * _fit(arr, n)
        if self.vclips is not None:
            import twitchclips
            parts["viewers"] = float(w.get("viewers", 1.0)) * twitchclips.GAIN * _fit(self.vclips, n)
        ag = float(w.get("agree", AGREE_WEIGHT))
        if ag > 0 and self.has_chat and self.has_audio and 0 < wc < 1:
            import detect
            parts["agree"] = ag * detect.agreement(self)
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
                       comments=cut(self.comments), comment_people=cut(self.comment_people), captions=cut(self.captions),
                       chapters=self.chapters, vclips=cut(self.vclips), vclip_people=cut(self.vclip_people),
                       vclip_views=cut(self.vclip_views), chat_rate=cut(self.chat_rate),
                       said_rate=cut(self.said_rate), talk_rate=cut(self.talk_rate), talk=cut(self.talk))
        sub.combine(self.chat_weight_pct)
        return sub


def analyze(video: str, chat: list, chat_delay: float = 6.0, chat_weight_pct: float = 60,
            log: Log = _noop, progress: Progress = _noop, check: Check = _noop,
            events: bool = True, sound: Optional[Callable] = None) -> Analysis:
    """sound: Source.sound, the sound already listened to while the video downloaded (same settings)."""
    info = probe(video)
    dur = info["duration"]
    if dur <= 0:
        raise RuntimeError("Couldn't read the video's length. The file may be incomplete or damaged.")
    n = int(math.ceil(dur)) + 1
    ev = fine = None
    # the bar: listening to the sound, then reading chat (about 15 s for 67,000 messages, against minutes of
    # listening), so a big chat isn't the bar sitting at 100% while it's read
    chat_share = 0.0 if len(chat) < 1000 else min(0.25, 0.04 + len(chat) / 600_000)
    sound_share = 1.0 - chat_share
    if info["has_audio"]:
        import audio_events
        with_ev = events and audio_events.available()
        early = None
        if sound is not None:
            log(f"VOD length {fmt_ts(dur)}. Finishing listening to its sound...")
            early = sound()
            check()
        if early is not None:
            loud, fine, ev = early
        else:
            log(f"VOD length {fmt_ts(dur)}. Listening for loud moments" + (", laughter and screams..." if with_ev else "..."))
            loud, fine, ev = measure_audio(video, dur, with_ev, lambda f: progress(f * sound_share), check, log)
        loud = _fit(loud, n, -90.0)
        if loud is not None and (loud < -75).mean() > 0.2:
            log(f"{int((loud < -75).mean() * 100)}% of the audio is silent or muted.")
    else:
        log(f"VOD length {fmt_ts(dur)}. No audio track found, ranking by chat only.")
        loud = None
    if len(chat) > 20:
        log(f"Reading {len(chat):,} chat messages...")
    rate = stats = said = talk = None
    used_delay = 6.0 if chat_delay == "auto" else float(chat_delay)
    sure = chat_delay != "auto"
    if chat_delay == "auto" and chat:
        # how late chat reacts: compare when chat surges with when the stream got loud or laughed
        import detect
        progress(sound_share)
        rate0, sc = chat_rate(chat, n, 0.0)
        said, talk = sc.said_rate, sc.talk_rate
        progress(sound_share + chat_share * 0.5)
        stats = sc.summary()
        az = _audio_z(loud, n) if loud is not None else np.zeros(n, dtype=np.float32)
        used_delay, sure = detect.estimate_delay(rate0, az, ev)
        log(f"Chat reacts about {used_delay:.0f} s after the stream." if sure
            else "Couldn't tell how late chat reacts; assuming 6 s.")
        rate = detect.shift(rate0, used_delay)
        said, talk = detect.shift(said, used_delay), detect.shift(talk, used_delay)
        progress(sound_share + chat_share * 0.7)
    an = score_signals(loud, chat, n, dur, used_delay, chat_weight_pct, rate=rate, stats=stats, events=ev, fine=fine,
                       said=said, talk=talk)
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
ANALYSIS_VERSION = 6          # 4.9: YouTube chat emoji; 4.10: chat delay found on long streams too; 4.32: chat reading, talk;
#                               4.34: confused and awkward reactions; 4.35: popular 7TV emotes


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
    for name in ("loud", "fine", "said_rate", "talk_rate", "chat_rate"):
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
        # an analysis saved before 4.45.1 has no chat_rate: enrich weighs the chat once, as before
        return Analysis(float(meta["duration"]), f["chat_z"].copy(), f["audio_z"].copy(), bool(meta["has_chat"]),
                        bool(meta["has_audio"]), events=events, loud=get("loud"), fine=get("fine"),
                        said_rate=get("said_rate"), talk_rate=get("talk_rate"), chat_rate=get("chat_rate"),
                        chat_stats=meta.get("chat_stats"), chat_delay=float(meta.get("chat_delay", 6.0)),
                        delay_sure=bool(meta.get("delay_sure")))


def analyze_cached(workdir: str, video: str, chat: list, chat_delay=6.0, chat_weight_pct: float = 60,
                   log: Log = _noop, progress: Progress = _noop, check: Check = _noop,
                   events: bool = True, emote_set=None, weights: Optional[dict] = None,
                   sound: Optional[Callable] = None) -> Analysis:
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
                 progress=progress, check=check, events=events, sound=sound)
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
    # the chat rate analyze already worked out (or saved): not every message weighed a second time
    rate = an.chat_rate if chat and an.chat_rate is not None and len(an.chat_rate) == len(an.audio_z) else None
    detect.enrich(an, chat, an.chat_delay, emote_set, rate=rate)
    if workdir:
        ytextra.apply(an, ytextra.load(workdir))       # a YouTube video: Most replayed and comments
        import twitchclips
        twitchclips.apply(an, twitchclips.load(workdir))   # a Twitch VOD: the clips viewers made from it
    an._emotes = emote_set
    an.combine(chat_weight_pct, weights or detect.preset("any"))
    return an


def chat_rate(chat: list, n: int, chat_delay: float, scorer=None) -> tuple:
    """(reaction rate per second, scorer). Chat reacts a few seconds late, so it's shifted back.
    scorer.said_rate, scorer.talk_rate: messages per second, and those that were plain talk rather than
    reactions (detect.talk_mult)."""
    import chatsignal
    sc = scorer or chatsignal.ChatScorer(chat_weight)
    rate = np.zeros(n, dtype=np.float32)
    said_rate = np.zeros(n, dtype=np.float32)
    talk_rate = np.zeros(n, dtype=np.float32)
    if chat:
        talk: list = []
        w = np.asarray(sc.feed(chat, talk), dtype=np.float32)
        tw = np.asarray(talk, dtype=np.float32)
        idx = (np.asarray([m[0] for m in chat], dtype=np.float64) - chat_delay).astype(np.int64)
        ok = (idx >= 0) & (idx < n) & (w > 0)
        np.add.at(rate, idx[ok], w[ok])
        np.add.at(said_rate, idx[ok], np.minimum(1.0, w[ok]))
        np.add.at(talk_rate, idx[ok], tw[ok])
    sc.said_rate, sc.talk_rate = said_rate, talk_rate
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
                  fine: Optional[np.ndarray] = None, said: Optional[np.ndarray] = None,
                  talk: Optional[np.ndarray] = None) -> "Analysis":
    """Per-second hype score from loudness (dB per second), chat and sound events. Shared by VODs and live.
    rate: chat reaction per second already weighed (live mode keeps it up to date as chat arrives).
    said, talk: messages per second, and those that were plain talk (chat_rate), same timing."""
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
        said, talk = sc.said_rate, sc.talk_rate
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
                  fine=None if fine is None else _fit(fine, n * FINE_HZ, -100.0), chat_stats=stats,
                  said_rate=_fit(said, n) if has_chat and said is not None else None,
                  talk_rate=_fit(talk, n) if has_chat and talk is not None else None)
    an.chat_rate = rate.astype(np.float32) if chat else None
    an.combine(chat_weight_pct)
    return an


# --------------------------------------------------------------------------
# 3. pick clips
# --------------------------------------------------------------------------

MIN_LEN, MAX_LEN = 15.0, 120.0          # 4.33: up to 2 minutes (was 90 s)
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
    # quality first (4.33): suggest the moments that stand out against this stream's best, however long it is.
    # Before, at least 3 an hour were suggested (5 at least), so a 4-hour stream got 13+ clips even with a few
    # good moments, and a busy one 60 (real-world test: a 4.4-hour IShowSpeed stream, 60 clips, many of them
    # chat begging or answering)
    bar = standout_bar(an, sensitivity)
    # counted as far apart as two clips can be (STANDOUT_GAP), not the 45 s scores are graded at: a short
    # video with four loud moments 25 s apart has four clips in it, not two
    peaks = moment_scores(an, edge=min(60, int(n * 0.03)), gap=STANDOUT_GAP)
    import detect
    share = detect.SENSITIVITY.get(sensitivity, detect.SENSITIVITY["normal"]).get("count", 1.0)
    suggest = int(min(MAX_AUTO_CLIPS, max(MIN_AUTO_CLIPS, round(int((peaks >= bar).sum()) * share))))
    return {"strong": strong, "good": good, "hours": round(hours, 2), "suggest": suggest}


MAX_AUTO_CLIPS = 40        # an automatic list never goes past this (pick a number to get more)
STANDOUT_GAP = 20          # seconds between two moments that would still be clips of their own


def standout_bar(an: Analysis, sensitivity: str = "normal") -> float:
    """The weakest moment an automatic list takes: above the sensitivity's floor, and a share of how strong
    the stream's best moments are (its third best, so one freak moment doesn't set the bar)."""
    import detect
    sens = detect.SENSITIVITY.get(sensitivity, detect.SENSITIVITY["normal"])
    peaks = moment_scores(an)
    ref = float(peaks[min(2, len(peaks) - 1)]) if len(peaks) else 0.0
    return max(sens["floor"], sens["rel"] * ref)


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
    on until the reaction has died down, 15 to 120 seconds. Returns (start, end, complete); complete is
    False while live mode hasn't seen the reaction end yet."""
    s = an.score
    n = len(s) if avail is None else min(len(s), int(avail))
    dur = min(an.duration, float(n))
    p = int(min(max(0, peak), n - 1))
    top = float(s[p])
    calm = max(0.8, 0.35 * top)            # the reaction is over below this
    calm_back = max(0.6, 0.25 * top)       # and it hadn't started yet below this
    # how far the setup and the reaction may reach: further for longer clips, so a 2-minute clip can keep a
    # long reaction (90 s clips used to stop 45 s after the peak, whatever was still going on)
    back_room, on_room = max(55, int(0.5 * max_len)), max(45, int(0.75 * max_len))
    # build-up: walk back to where things were calm for 3 seconds
    lim = max(0, p - back_room - 5)
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
    start = max(start, p - float(back_room), 0.0)
    keep_from = p - 8.0
    if trigger is not None and trigger < p:
        # chat reacted late: start before what it reacted to
        start = max(0.0, min(start, trigger - 6.0))
        keep_from = min(keep_from, trigger - 3.0)
    # reaction: walk forward until it has been calm for 4 seconds
    end = None
    quiet = 0
    for j in range(p + 1, min(n, p + on_room + 1)):
        if s[j] < calm:
            quiet += 1
            if quiet >= 4:
                end = j - 4 + 2.5
                break
        else:
            quiet = 0
    complete = end is not None or avail is None or final or n >= p + on_room + 1
    if end is None:
        end = min(float(n), p + float(on_room))
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

    def lead_in(i: int) -> float:
        # a little before word i, but never into the word before it: YouTube's captions have no gaps between
        # words, and 0.2 s early opened clips on the last sound of the sentence before (4.39)
        return ws[i]["start"] - (0.2 if i == 0 else min(0.2, max(0.0, ws[i]["start"] - ws[i - 1]["end"])))

    def tail(i: int) -> float:
        # a little after word i, never into the next one (0.35 s ran into the next sentence's first word)
        return ws[i]["end"] + (0.35 if i + 1 >= len(ws) else min(0.35, max(0.0, ws[i + 1]["start"] - ws[i]["end"])))

    def ends(i: int) -> bool:
        # a sentence ends after word i, or someone else speaks next (YouTube's captions mark it with >>)
        return _ends_sentence(ws[i]) or (i + 1 < len(ws) and bool(ws[i + 1].get("turn")))

    # ---- start
    k = next((i for i, w in enumerate(ws) if w["end"] > start + 0.05), None)
    if k is not None and ws[k]["start"] < end:
        prev = ws[k - 1] if k > 0 else None
        mid_word = ws[k]["start"] < start - 0.02       # even 50 ms in clips the word's first sound
        joined = prev is not None and ws[k]["start"] - prev["end"] < gap and not ends(k - 1)
        if mid_word or joined:
            j = k
            while j > 0 and ws[j]["start"] - ws[j - 1]["end"] < gap and not ends(j - 1):
                j -= 1
            back = lead_in(j)
            if start - back <= max_move and end - back <= max_len + 5:
                start = back
            else:
                # too far back: start at the next sentence instead
                j = k
                while j + 1 < len(ws) and not (ws[j + 1]["start"] - ws[j]["end"] >= gap or ends(j)):
                    j += 1
                if j + 1 < len(ws) and lead_in(j + 1) < peak - 3:
                    start = lead_in(j + 1)
        elif ws[k]["start"] - start > 1.2:
            start = max(start, ws[k]["start"] - 0.6)      # don't open on a long silence
    # ---- end
    inside = [i for i, w in enumerate(ws) if w["start"] < end - 0.05]
    if inside:
        k = inside[-1]
        nxt = ws[k + 1] if k + 1 < len(ws) else None
        mid_word = ws[k]["end"] > end + 0.02
        joined = nxt is not None and nxt["start"] - ws[k]["end"] < gap and not ends(k)
        if mid_word or joined:
            j = k
            while j + 1 < len(ws) and ws[j + 1]["start"] - ws[j]["end"] < gap and not ends(j):
                j += 1
            fwd = tail(j)
            if fwd - end <= max_move * 0.75 and fwd - start <= max_len + 5:
                end = fwd
            else:
                j = k
                while j > 0 and not (ends(j - 1) or ws[j]["start"] - ws[j - 1]["end"] >= gap):
                    j -= 1
                if j > 0 and tail(j - 1) > peak + 2:
                    end = tail(j - 1)
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


def moment_scores(an: Analysis, edge: int = 0, gap: int = 45) -> np.ndarray:
    """Scores of the stream's separate moments (peaks at least gap s apart), best first. What a clip's
    score is graded against (grades). edge: leave out moments this close to the start or end."""
    s = an.score
    if s is None or not len(s):
        return np.zeros(0)
    cached = getattr(an, "_moments", None)
    if cached is not None and cached[0] is s and cached[2] == (edge, gap):
        return cached[1]
    order = np.argsort(-s, kind="stable")
    blocked = np.zeros(len(s), dtype=bool)
    out = []
    for i in order:
        v = float(s[i])
        if v <= 0.5:
            break
        if blocked[i]:
            continue
        blocked[max(0, i - gap): i + gap + 1] = True
        if edge and (i < edge or i > len(s) - 1 - edge):
            continue
        out.append(v)
    arr = np.array(out)
    an._moments = (s, arr, (edge, gap))
    return arr


NO_CHAT_JOIN = 10          # seconds between two moments of a video without chat that make them one clip
NO_CHAT_SCALE = 1.35       # without chat a score of 2 is as clear as 2.7 with it (real-world test, 4.39)


def strength(an: Analysis, rank: float) -> int:
    """detect.quality, judged on the scale this video can reach. A score is a sum of signals, and chat is
    the biggest (up to 6 points by itself): a video without chat (a YouTube upload, a file) adds up sound,
    laughter, captions and YouTube's own signals only, so its clearest moments score lower than a stream's."""
    import detect
    return detect.quality(rank if an.has_chat else rank * NO_CHAT_SCALE)


def grades(an: Analysis, ranks: list) -> list:
    """0-100 scores for a stream's clips: each one's strength (detect.quality), capped by where it stands
    among the stream's moments and among these clips (detect.ceiling), so a long stream can't have dozens
    of 90+ clips. Same order as ranks."""
    import detect
    peaks = moment_scores(an)
    total = max(len(peaks), len(ranks))
    out = [0] * len(ranks)
    order = sorted(range(len(ranks)), key=lambda i: -float(ranks[i]))
    for k, i in enumerate(order):
        r = float(ranks[i])
        better = int(np.searchsorted(-peaks, -r - 1e-6, side="left")) if len(peaks) else 0
        out[i] = min(strength(an, r), detect.ceiling(max(k, better), total))
    return out


WEAK_QUALITY = 50          # a 0-100 score under this isn't "a clear moment" (detect.quality)
MIN_AUTO_CLIPS = 3         # automatic picks never drop below this many when the video has them


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
    floor = max(standout_bar(an, sensitivity), 0.15 * top) if auto else 0.0
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
    # automatic, no chat (an ordinary YouTube video, a file): after the first few, weak moments aren't
    # suggested just to reach the usual number (real-world test: 2 strong moments in a 28-min upload,
    # padded with clips scoring 45). With chat, the floor above already does this.
    weak = WEAK_QUALITY if auto and not an.has_chat else 0
    # two moments this close are one: with chat, two waves of it; in a talked-through video without chat (its
    # captions rank), one scene told across a pause (4.39: a vlog's "before the massage" and "after it", 9 s
    # apart, were two clips). Loud moments with nothing said stay apart
    join = NO_CHAT_JOIN if smart and an.captions is not None else 4
    for it in items:
        if len(clips) >= count:
            break
        if weak and len(clips) >= MIN_AUTO_CLIPS and strength(an, it["rank"]) < weak:
            continue
        st, en = it["start"], it["end"]
        near = next((c for c in clips if st < c.end + join and en > c.start - join), None)
        if near is not None:
            # the same event seen twice (setup and reaction, or two waves of chat): one clip, not two
            if smart and max(en, near.end) - min(st, near.start) <= max_len:
                near.start, near.end = min(st, near.start), max(en, near.end)
                near.merged += 1
                continue
            if st < near.end + 4 and en > near.start - 4:
                continue
            # too long together, and apart enough to be a clip of its own
        clips.append(Clip(0, st, en, float(it["peak"]), round(it["rank"], 2),
                          _reason(an, it["peak"], chat, chat_t, chat_delay, it["trigger"]), it["feat"],
                          round(it["tuned"], 2), strength(an, it["rank"]),
                          detect.breakdown(an, it["peak"], it["tuned"]), it["trigger"]))
    for c, q in zip(clips, grades(an, [c.score for c in clips])):
        c.quality = q
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
    if an.captions is not None and float(np.max(an.captions[max(0, i - 6):i + 4], initial=0)) > 0.5:
        parts.append("a line that lands")
    import twitchclips
    vc = twitchclips.reason(an, i)
    if vc:
        parts.append(vc)
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
        emoji = {"😂", "🤣", "💀", "🔥", "😱", "😬", "🤔", "😐"}
        near = chatsignal.human(chat[lo:hi])
        mood, _ = chat_mood(near)
        if mood:
            parts.insert(0, MOOD_LABEL[mood])
        for m in near:
            seen = set()
            if mood in ("confused", "both") and _CONFUSED_ASK.match(m[1].strip()) and "?" not in seen:
                seen.add("?")
                words["???"] = words.get("???", 0) + 1
            for tok in set(re.findall(r"\w+|[😂🤣💀🔥😱😬🤔😐]", m[1])):
                e = es.find(tok) if es else None
                low = tok.lower()
                if e:
                    key = e
                elif (len(tok) > 1 and low in HYPE) or tok in emoji:
                    key = tok
                elif mood and (_awkward_points(low, True)[1] or "-") in ((mood,) if mood != "both" else ("confused", "cringe")):
                    key = _awkward_word(low)
                else:
                    continue
                if key not in seen:
                    seen.add(key)
                    words[key] = words.get(key, 0) + 1
        top = sorted(words.items(), key=lambda kv: -kv[1])[:3]
        if top:
            parts.append(", ".join(f"{w} ×{k}" for w, k in top))
    if not parts:
        return "Activity peak"
    text = "; ".join(parts)
    return text[0].upper() + text[1:]


def make_thumb(video: str, t: float, out: str, width: int = 480, exact: bool = True) -> str:
    """exact=False takes the keyframe at or before t instead of decoding up to t: a third less CPU (measured,
    docs/audits/speed-audit-2026-11-08.md), for a clip's thumbnail, where a frame a second or two earlier looks the same."""
    tmp = out + ".tmp.jpg"
    seek = [] if exact else ["-noaccurate_seek", "-skip_frame", "nokey"]
    run([ffmpeg(), "-v", "error", "-y", "-nostdin", *seek, "-ss", f"{max(0, t):.2f}", "-i", video,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", tmp], timeout=60)
    os.replace(tmp, out)
    return out
