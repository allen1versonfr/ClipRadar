"""
Quiet gems: lines worth clipping that chat and loudness missed.

After a deep scan (the whole stream transcribed), every sentence is scored on
how it lands, not how loud it is:

  laughter right after it          the streamer or their guests cracked up
  chat reacting right after it     even a small, sudden reaction counts
  a sudden change in voice energy  dropping to a whisper or snapping louder
  how it's built                   short punchy line, a pause before or after,
                                   an exclamation, a question, strong words

Sentences that score well but sit in a calm stretch (no hype clip nearby)
become "quiet gems", each cut to include its setup and the reaction.
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np

import engine
import titles

PUNCH = re.compile(
    r"\b(plot twist|turns out|the thing is|i swear|no because|imagine|what if|apparently|to be fair|"
    r"not gonna lie|ngl|lowkey|literally|actually|bro|chat|nah|wait what|why would|how did|"
    r"who (?:did|does|is) that|that's (?:crazy|insane|wild)|i can't|you're joking|are you serious|"
    r"no shot|bet|deadass|for real|i'm (?:done|dead|crying))\b", re.I)
HALLUCINATION = re.compile(r"thank(s| you) for watching|subscribe|♪|amara\.org|\bsubtitles\b", re.I)
GEM_ID = 10000


def sentences(words: list) -> list:
    out, cur = [], []
    for w in words:
        txt = (w.get("word") or "").strip()
        if not txt:
            continue
        if cur and w["start"] - cur[-1]["end"] > 0.7:
            out.append(cur)
            cur = []
        cur.append(w)
        if txt[-1:] in ".?!":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    res = []
    for i, s in enumerate(out):
        text = " ".join(w["word"].strip() for w in s)
        res.append({"i": i, "start": s[0]["start"], "end": s[-1]["end"], "text": text, "n": len(s),
                    "gap_before": s[0]["start"] - out[i - 1][-1]["end"] if i else 9.0,
                    "gap_after": out[i + 1][0]["start"] - s[-1]["end"] if i + 1 < len(out) else 9.0})
    return res


def _shape(sent: dict) -> float:
    """How punchline-like a sentence is from its words and timing."""
    text = sent["text"]
    toks = [re.sub(r"[^\w']", "", t).lower() for t in text.split()]
    toks = [t for t in toks if t]
    if len(toks) < 2 or HALLUCINATION.search(text):
        return -2.0
    if all(t in titles.FILLER for t in toks):
        return -1.5
    s = 0.0
    if text.rstrip().endswith("!"):
        s += 0.8
    if "?" in text:
        s += 0.4
    s += min(1.0, 0.25 * sum(1 for t in toks if t in titles.STRONG))
    if PUNCH.search(text):
        s += 0.6
    if titles.PROFANITY.search(text):
        s += 0.5
    if 3 <= len(toks) <= 12:
        s += 0.5
    elif len(toks) > 25:
        s -= 0.8
    if sent["gap_before"] >= 0.8:
        s += 0.4
    if sent["gap_after"] >= 0.8:
        s += 0.4
    return s


def score_sentences(an: engine.Analysis, sents: list) -> list:
    n = len(an.score)
    laugh = an.event_level("laugh") if an.has_events else None
    loud = an.loud
    out = []
    for s in sents:
        a, e = int(s["start"]), int(s["end"])
        if a >= n:
            continue
        parts = {}
        sc = _shape(s)
        parts["shape"] = round(sc, 2)
        if laugh is not None:
            la = float(laugh[e: min(n, e + 5)].max()) if e < n else 0.0
            sc += 3.0 * la
            parts["laugh"] = round(la, 2)
        if an.has_chat:
            ch = float(np.clip(an.chat_z[max(0, e - 1): min(n, e + 5)].max(), 0, 6)) if e < n else 0.0
            sc += 0.35 * ch
            parts["chat"] = round(ch, 2)
        if loud is not None and a > 3:
            prev = loud[max(0, a - 20): a]
            prev = prev[prev > -75]
            cur = loud[a: max(a + 1, e + 1)]
            if len(prev) >= 3 and (cur > -75).any():
                shift = float(np.mean(cur[cur > -75]) - np.median(prev))
                sc += 0.12 * min(abs(shift), 12.0)
                parts["energy"] = round(shift, 1)
        out.append(dict(s, score=round(sc, 2), parts=parts))
    return out


def speech_curve(an: engine.Analysis, scored: list) -> np.ndarray:
    """Per-second 'how quotable' value, for learning and the timeline."""
    n = len(an.score)
    sp = np.zeros(n, dtype=np.float32)
    for s in scored:
        a, b = int(s["start"]), min(n, int(s["end"]) + 1)
        if a < n:
            sp[a:b] = np.maximum(sp[a:b], max(0.0, s["score"]))
    return sp


def find(an: engine.Analysis, words: list, taken: list, count: Optional[int] = None,
         chat: Optional[list] = None, chat_delay: float = 6.0) -> tuple:
    """(gem clips, per-second speech curve). taken: [(start, end)] of clips already suggested."""
    sents = sentences(words)
    scored = score_sentences(an, sents)
    curve = speech_curve(an, scored)
    an.speech = curve
    if count is None:
        count = int(min(30, max(3, round(2 * an.duration / 3600.0))))
    n = len(an.score)
    laugh = an.event_level("laugh") if an.has_events else None
    chat_t = engine.chat_times(chat) if chat else []
    used = list(taken)
    gems = []
    for s in sorted(scored, key=lambda x: -x["score"]):
        if len(gems) >= count or s["score"] < 2.2:
            break
        a, e = int(s["start"]), int(s["end"])
        lo, hi = max(0, a - 2), min(n, e + 7)
        if lo >= hi or float(an.score[lo:hi].max()) >= engine.GOOD:
            continue                     # the hype finder already sees this stretch
        idx = s["i"]
        # setup: earlier sentences that run into this one (up to ~15 s)
        st = s["start"]
        j = idx
        while j > 0 and s["start"] - sents[j - 1]["start"] <= 15 and sents[j]["start"] - sents[j - 1]["end"] < 2.5:
            j -= 1
            st = sents[j]["start"]
        if s["start"] - st < 6 and j > 0:
            st = sents[j - 1]["start"]
        st = max(0.0, st - 0.3)
        # reaction: as long as the laughing goes on (up to 8 s), else a short beat
        en = s["end"] + 2.5
        if laugh is not None and s["parts"].get("laugh", 0) > 0.15:
            k = e
            while k < min(n, e + 8) and laugh[k] > 0.08:
                k += 1
            en = max(en, k + 1.0)
        if en - st < engine.MIN_LEN:
            st = max(0.0, en - engine.MIN_LEN)
        if en - st > 60:
            st = en - 60
        st, en = engine.refine_with_words(st, en, s["start"], words, engine.MIN_LEN, 60.0)
        en = min(en, an.duration)
        if any(st < b - 2 and en > a2 + 2 for a2, b in used):
            continue
        used.append((st, en))
        bits = []
        if s["parts"].get("laugh", 0) > 0.2:
            bits.append("laughter after it")
        if s["parts"].get("chat", 0) > 1.5:
            bits.append("chat reacted")
        if abs(s["parts"].get("energy", 0)) >= 6:
            bits.append("voice changes")
        quote = titles._mask(s["text"])
        quote = quote if len(quote) <= 70 else quote[:67].rstrip() + "…"
        reason = f"Quiet gem: “{quote}”" + (f" ({', '.join(bits)})" if bits else "")
        peak = (s["start"] + s["end"]) / 2
        c = engine.Clip(GEM_ID + len(gems) + 1, round(st, 2), round(en, 2), round(peak, 2), s["score"], reason,
                        engine.clip_features(an, st, en, peak, chat, chat_t, chat_delay))
        gems.append(c)
    return gems, curve
