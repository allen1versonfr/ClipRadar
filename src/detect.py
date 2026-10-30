"""
How moments are scored (3.5): stream-type presets, the extra signals, the 0-100 score and its
breakdown, the automatic chat delay and delayed reactions.

The per-second score is a sum of parts, each roughly in "standard deviations above this stream's
normal", so a big streamer and a small one are judged against themselves:

  chat       how much more chat is reacting than usual (cleaned: chatters, not messages)
  audio      how much louder than the last few minutes
  laughter / screams / cheering   from the sound detector
  emotes     a wave of 7TV/BTTV/FFZ (and Twitch) emotes from many chatters at once
  surge      chat speeding up fast (the first seconds of a reaction)
  reaction   a sudden reaction right after a quiet stretch
  noise      a loud blip nobody reacted to counts for less

None of this can promise a clip will do well. It ranks moments that look like reactions.
"""

from __future__ import annotations

import math
import re
from typing import Optional

import numpy as np

DETECTION_VERSION = 2

PRESETS = {
    # chat: default chat-vs-audio mix (%). laugh/scream/cheer: points for a clear event.
    # emotes/surge/react: weight of the extra signals. min/max: clip length (s). back: how far back
    # (s) to look for what set off a late chat reaction.
    "any": {"label": "Any stream", "chat": 60, "laugh": 4.0, "scream": 3.0, "cheer": 2.0,
            "emotes": 0.3, "surge": 0.3, "react": 1.0, "min": 15, "max": 90, "back": 25,
            "hint": "Balanced. A good start for most streams."},
    "gaming": {"label": "Gaming", "chat": 55, "laugh": 3.5, "scream": 3.5, "cheer": 2.5,
               "emotes": 0.35, "surge": 0.35, "react": 1.2, "min": 15, "max": 60, "back": 25,
               "hint": "Clutches, fails and jump scares: screams and sudden reactions count more."},
    "irl": {"label": "IRL / Just Chatting", "chat": 65, "laugh": 4.5, "scream": 2.5, "cheer": 2.0,
            "emotes": 0.35, "surge": 0.3, "react": 0.8, "min": 20, "max": 90, "back": 30,
            "hint": "Chat and laughter lead; a little more setup before each moment."},
    "reactions": {"label": "Reactions", "chat": 50, "laugh": 5.0, "scream": 4.0, "cheer": 2.0,
                  "emotes": 0.3, "surge": 0.3, "react": 1.5, "min": 15, "max": 60, "back": 20,
                  "hint": "The streamer's own reaction matters most: laughs, screams, sudden outbursts."},
    "interviews": {"label": "Interviews", "chat": 50, "laugh": 3.0, "scream": 1.5, "cheer": 1.5,
                   "emotes": 0.2, "surge": 0.25, "react": 0.6, "min": 30, "max": 120, "back": 35,
                   "hint": "Longer clips that keep the question with the answer."},
    "podcasts": {"label": "Podcasts", "chat": 45, "laugh": 5.0, "scream": 1.5, "cheer": 1.5,
                 "emotes": 0.2, "surge": 0.25, "react": 0.8, "min": 30, "max": 150, "back": 35,
                 "hint": "Long, talky clips around the big laughs."},
}
SENSITIVITY = {
    # floor: weakest moment considered when the number of clips is automatic; count: how many clips
    "fewer": {"label": "Fewer, stronger", "floor": 2.3, "count": 0.6},
    "normal": {"label": "Normal", "floor": 1.5, "count": 1.0},
    "more": {"label": "More", "floor": 1.0, "count": 1.6},
}
MIN_LEN_LIMIT, MAX_LEN_LIMIT = 3.0, 180.0


def preset(key: Optional[str]) -> dict:
    return dict(PRESETS.get(key or "any", PRESETS["any"]), key=key if key in PRESETS else "any")


def settings(d: Optional[dict]) -> dict:
    """Clean detection settings: preset, sensitivity, min/max length, chat delay ('auto' or seconds)."""
    d = d or {}
    p = preset(d.get("preset"))
    try:
        mn = float(d.get("min_len") or p["min"])
        mx = float(d.get("max_len") or p["max"])
    except (TypeError, ValueError):
        mn, mx = p["min"], p["max"]
    mn = min(max(mn, MIN_LEN_LIMIT), MAX_LEN_LIMIT - 1)
    mx = min(max(mx, mn + 1), MAX_LEN_LIMIT)
    delay = d.get("chat_delay", "auto")
    if delay != "auto":
        try:
            delay = min(max(float(delay), 0.0), 30.0)
        except (TypeError, ValueError):
            delay = "auto"
    return {"preset": p["key"], "sensitivity": d.get("sensitivity") if d.get("sensitivity") in SENSITIVITY else "normal",
            "min_len": round(mn, 1), "max_len": round(mx, 1), "chat_delay": delay, "version": DETECTION_VERSION}


def describe(s: dict, used_delay: Optional[float] = None, has_chat: bool = True) -> str:
    p = preset(s.get("preset"))
    d = s.get("chat_delay", "auto")
    dd = ("no chat" if not has_chat else f"chat delay {used_delay:.0f} s (worked out)" if d == "auto" and used_delay is not None
          else "chat delay 6 s (couldn't work it out)" if d == "auto" else f"chat delay {float(d):.0f} s")
    return (f"Detection v{DETECTION_VERSION} · {p['label']} · {SENSITIVITY[s.get('sensitivity', 'normal')]['label']} · "
            f"{s.get('min_len', p['min']):.0f}–{s.get('max_len', p['max']):.0f} s · {dd}")


# ---------------------------------------------------------------- extra signals

def _roll(x, win):
    import engine
    return engine._rolling(x, win, "mean")


def _rz(x):
    import engine
    return engine._robust_z(x)


def surge_signal(rate: np.ndarray) -> np.ndarray:
    """Chat speeding up: messages in the last 3 s against the 4 s before, relative to normal."""
    r = _roll(rate, 3)
    prev = np.concatenate([np.repeat(r[:1], 4), r[:-4]]) if len(r) > 4 else r
    base = np.median(rate) if len(rate) else 0
    d = (r - prev) / (math.sqrt(max(base, 0)) + 1.0)
    return np.clip(_rz(d), 0, 6).astype(np.float32)


_TOK = re.compile(r"[\w:]+")


def emote_in(text: str, user: str, kind: str, es):
    """The channel emote a chat message reacts with, or None (noise and F-spam never count).
    Shared by VODs (emote_rate) and live clipping (live.Session.add_chat)."""
    import chatsignal
    if not es or chatsignal.is_noise(text, user, kind) or chatsignal.low_info(text):
        return None
    for tok in _TOK.findall(text or ""):
        e = es.find(tok)
        if e:
            return e
    return None


def emote_rate(chat: list, n: int, delay: float, es) -> tuple:
    """(emote use per second by distinct chatters, {second: (top emote, count)}). Bots, commands and
    notices don't count, and each chatter counts once per 8 s."""
    rate = np.zeros(n, dtype=np.float32)
    top: dict = {}
    if not es or not chat:
        return rate, top
    last: dict = {}
    per: dict = {}
    for m in chat:
        t, text = m[0], m[1]
        user = m[2] if len(m) > 2 else ""
        kind = m[3] if len(m) > 3 else ""
        found = emote_in(text, user, kind, es)
        if not found:
            continue
        if user:
            lt = last.get(user)
            if lt is not None and t - lt < 8:
                continue
            last[user] = t
        i = int(t - delay)
        if 0 <= i < n:
            rate[i] += 1.0
            d = per.setdefault(i // 10, {})
            d[found] = d.get(found, 0) + 1
    for blk, d in per.items():
        e, c = max(d.items(), key=lambda kv: kv[1])
        if c >= 5 and c >= 0.4 * sum(d.values()):
            top[blk] = (e, c)
    return rate, top


def emote_signal(rate: np.ndarray) -> np.ndarray:
    if float((rate > 0).sum()) < 10:
        return np.zeros(len(rate), dtype=np.float32)
    short = _roll(rate, 8)
    import engine
    base = engine._rolling(rate, 300, "median")
    s = (short - base) / (np.sqrt(np.maximum(base, 0)) + 1.0)
    return np.clip(_rz(s), 0, 8).astype(np.float32)


def react_signal(an) -> np.ndarray:
    """A sudden reaction right after a quiet stretch (the streamer goes silent, then explodes)."""
    n = len(an.audio_z)
    out = np.zeros(n, dtype=np.float32)
    if not an.has_audio or an.loud is None or n < 20:
        return out
    loud = np.asarray(an.loud, dtype=np.float32)[:n]
    ok = loud > -75
    if ok.sum() < 20:
        return out
    quiet_db = float(np.percentile(loud[ok], 30))
    before = np.concatenate([np.full(8, 0.0), _roll(loud, 8)[:-8]]) if n > 8 else loud
    ev = np.zeros(n, dtype=np.float32)
    if an.has_events:
        ev = np.maximum(an.event_level("laugh"), an.event_level("scream"))
    hit = (before < quiet_db) & ((an.audio_z > 2.5) | (ev > 0.35))
    for i in np.flatnonzero(hit):
        if i < 8:
            continue
        v = min(2.0, 0.4 * max(float(an.audio_z[i]) - 1.0, 0) + 1.5 * float(ev[i]))
        out[i:i + 4] = np.maximum(out[i:i + 4], v)
    return out


def noise_mult(an) -> np.ndarray:
    """1 everywhere, lower around a loud blip (under 2.5 s) that neither chat nor the sound detector
    reacted to: a door slam, an alert sound, a dropped mic."""
    n = len(an.audio_z)
    mult = np.ones(n, dtype=np.float32)
    if not an.has_audio or n < 10:
        return mult
    a = an.audio_z
    loud = a > 1.5
    ev = np.zeros(n, dtype=np.float32)
    if an.has_events:
        for k in ("laugh", "scream", "cheer"):
            ev = np.maximum(ev, an.event_level(k))
    i = 0
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < n and loud[j]:
            j += 1
        raw = 99
        if an.loud is not None and len(an.loud) >= j:
            seg = np.asarray(an.loud[max(0, i - 3):min(n, j + 3)], dtype=np.float32)
            ref = float(np.median(an.loud[max(0, i - 60):i])) if i > 5 else float(np.median(seg))
            raw = int((seg > ref + 8).sum())
        if (j - i <= 2 or (j - i <= 7 and raw <= 2)) and float(a[i:j].max()) > 2.5:
            chat_after = float(an.chat_z[i:min(n, j + 15)].max()) if an.has_chat else 0.0
            evn = float(ev[max(0, i - 2):min(n, j + 4)].max())
            if evn < 0.25 and (not an.has_chat or chat_after < 2.0):
                f = 0.55 if (an.has_chat or an.has_events) else 0.8
                mult[max(0, i - 2):min(n, j + 3)] = np.minimum(mult[max(0, i - 2):min(n, j + 3)], f)
        i = j
    return mult


def enrich(an, chat: list, delay: float, es=None, rate: Optional[np.ndarray] = None):
    """Add the extra signals to an Analysis (cheap; done after loading a cached analysis too)."""
    n = len(an.audio_z)
    if rate is None and chat:
        import engine
        rate, _ = engine.chat_rate(chat, n, delay)
    rate = np.zeros(n, dtype=np.float32) if rate is None else np.asarray(rate, dtype=np.float32)[:n]
    an.surge = surge_signal(rate) if an.has_chat else np.zeros(n, dtype=np.float32)
    er, top = emote_rate(chat, n, delay, es)
    an.emote_rate = er
    an.emote_z = emote_signal(er)
    an.emote_top = top
    an.react = react_signal(an)
    an.noise = noise_mult(an)
    an.emotes_known = len(es) if es else 0


def weigher(es):
    """chat_weight that also counts 7TV/BTTV/FFZ emotes (used by live mode's chat scorer)."""
    import engine

    def w(text: str) -> float:
        base = engine.chat_weight(text)
        extra = 0.0
        for tok in _TOK.findall(text or ""):
            e = es.find(tok) if es else None
            if e and tok.lower() not in engine.HYPE:
                extra += 0.5 + (0.5 if es.by_name[e][1] else 0.0)
        return min(8.0, base + min(extra, 3.0))
    return w


# ---------------------------------------------------------------- chat delay

def estimate_delay(rate0: np.ndarray, an_audio: np.ndarray, events: Optional[dict], default: float = 6.0) -> tuple:
    """How many seconds chat lags behind the stream, from when chat reacts compared with when the
    stream got loud or laughed. rate0: chat reaction per second, not shifted. Returns (seconds, sure)."""
    n = min(len(rate0), len(an_audio))
    if n < 300 or float((rate0 > 0).sum()) < 50:
        return default, False
    import engine
    trig = np.clip(np.asarray(an_audio[:n], dtype=np.float32), 0, 8)
    if events:
        for k, w in (("laugh", 3.0), ("scream", 3.0), ("cheer", 2.0)):
            if k in events and events[k] is not None:
                trig = trig + w * np.clip(engine._fit(events[k], n), 0, 1)
    base = engine._rolling(rate0[:n], 300, "median")
    c = (rate0[:n] - base) / (np.sqrt(np.maximum(base, 0)) + 1.0)
    # compare when things start: the stream gets loud or laughs, then chat starts to rise
    onset = lambda x: _roll(np.clip(np.diff(x, prepend=x[:1]), 0, None), 3)  # noqa: E731
    t, c = onset(trig), onset(c)
    t = t - t.mean()
    c = c - c.mean()
    if t.std() < 1e-6 or c.std() < 1e-6:
        return default, False
    # delays are looked for in 1-15 s, but "how much stands out" is judged against 1-30 s: chat's reaction
    # is spread over several seconds, so a real peak is wide, and a median over 1-15 s alone sits half
    # inside it (real-world test, a 6-hour IRL stream: lags 2-6 all ~0.05, median over 1-15 s 0.033 vs
    # 0.007 over 1-30 s, so a clear 4 s delay read as "couldn't tell")
    lags = list(range(1, 31))
    corr = []
    for L in lags:
        a, b = t[:-L], c[L:]
        corr.append(float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)))
    k = int(np.argmax(corr[:15]))
    best, med = corr[k], float(np.median(corr))
    # what chance alone gives shrinks with length (about 1/sqrt(n/3): onsets are ~3 s wide). Short
    # streams keep the old fixed bars; a long one needs 3x and 2x that (that 6-hour stream: 0.012 by this
    # estimate, its peak 0.053; random data of that length peaks under 0.016, so the bar of 0.035 holds)
    noise = 1.0 / math.sqrt(max(1.0, n / 3))
    if best < min(0.12, max(0.03, 3 * noise)) or best - med < min(0.08, max(0.02, 2 * noise)):
        return default, False
    return float(min(max(lags[k], 2), 12)), True


def shift(rate0: np.ndarray, lag: float) -> np.ndarray:
    L = int(round(lag))
    if L <= 0:
        return rate0.copy()
    return np.concatenate([rate0[L:], np.zeros(L, dtype=rate0.dtype)])


# ---------------------------------------------------------------- triggers, score, breakdown

def find_trigger(an, peak: int, back: float = 25.0) -> Optional[float]:
    """For a moment led by chat: the loud or funny bit in the seconds before that chat reacted to."""
    n = len(an.audio_z)
    p = int(min(max(0, peak), n - 1))
    if not an.has_chat:
        return None
    lead = float(an.chat_z[max(0, p - 2):p + 3].max())
    own = float(an.audio_z[max(0, p - 2):p + 3].max())
    if lead < 2.0 or own > lead:                # not chat-led: the peak is the event itself
        return None
    a, b = max(0, int(p - back)), max(0, p - 3)
    if b <= a:
        return None
    sig = np.clip(an.audio_z[a:b], 0, None).astype(np.float32)
    if an.has_events:
        sig = sig + 4 * np.maximum(an.event_level("laugh")[a:b], an.event_level("scream")[a:b])
    k = int(np.argmax(sig))
    return float(a + k) if float(sig[k]) >= 2.5 else None


def quality(rank: float) -> int:
    """0-100 from how strong a moment is on its own. 50 is a clear moment, 75 a strong one. Not a promise
    of views. A stream's clips are graded with ceiling() too (engine.grades), so 90+ stays its best few."""
    return int(round(100 * (1 - math.exp(-max(0.0, rank) / 3.5))))


def ceiling(pos: int, total: int) -> int:
    """The highest score the moment at pos (0 = the stream's best) of total separate moments can get.
    Strength alone let a busy 9-hour stream grade 60 clips 90+; a score is also where a moment stands
    in its stream: 90+ is only its best few (about 8%, at most 5), 75+ its top quarter (at most 20)."""
    total = max(1, total, pos + 1)
    top = min(5, max(1, round(total * 0.08)))
    strong = min(20, max(top + 1, round(total * 0.25)))
    if pos < top:
        return int(round(99 - 9 * pos / max(1, top - 1)))
    if pos < strong:
        return int(round(89 - 14 * (pos - top) / max(1, strong - top - 1)))
    return int(round(74 - 24 * min(1.0, (pos - strong) / max(1, total - strong - 1))))


def breakdown(an, peak: float, tuned: float = 0.0) -> dict:
    """Points each signal adds at the moment's peak (same 5 s window as the score)."""
    parts = getattr(an, "parts", None) or {}
    n = len(an.score)
    p = int(min(max(0, peak), n - 1))
    a, b = max(0, p - 2), min(n, p + 3)
    out = {k: round(float(np.mean(v[a:b])), 2) for k, v in parts.items()}
    total = sum(out.values())
    mult = float(np.mean(an.noise[a:b])) if getattr(an, "noise", None) is not None else 1.0
    if mult < 0.999:
        out["noise"] = round(total * (mult - 1), 2)
    if abs(tuned) > 0.01:
        out["tuned"] = round(float(tuned), 2)
    return {k: v for k, v in out.items() if abs(v) >= 0.05}
