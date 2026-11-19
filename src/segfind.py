"""
Find segments (5.0.3): Export VOD splits a stream into the parts it's made of, each a video of its own in Segments.

A stream is a run of topics: talking about the day, a story, a game, a reaction, a break. The parts are found where
the topic changes, from what was said (Whisper's words, the same transcript captions and the flagged words check
use), where there's a long pause (a break, BRB), and where the game changes (Twitch's chapters in the chat file).

  - Topic changes: TextTiling (Hearst, 1997). The words of each minute are compared with the minutes around it;
    where the words used before and after a point have little in common, the topic moved there. That needs no model
    and works in any language Whisper writes.
  - Each part is between `min_len` and `max_len` long: the strongest changes are taken first, a part too long is
    split at its strongest change, and a part too short is joined to its neighbour.
  - Each edge is moved to the longest pause near it, so a part never starts or ends mid-sentence.
  - Each part is named from the words that set it apart from the rest of the stream ("nyc · trip · flight"), and
    scored by how much happens in it (chat and the moments Moments found, when the video was opened there) and how
    much is said. Parts that are mostly quiet (a break) start unticked.

  find(words, duration, ...) -> [{"start", "end", "name", "words", "score", "talk", "quiet", "why"}]
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

import numpy as np

STEP = 30.0               # seconds between the points where a part can start
BLOCK = 60.0              # seconds of words a point is judged on, each side (times WINDOW)
WINDOW = 3                # blocks each side compared
PAUSE = 12.0              # silence at least this long (seconds without a word) is a natural place to cut
QUIET_TALK = 0.25         # words cover less than this share of a part: a break, starts unticked
SNAP = 30.0               # an edge moves at most this far to a pause
LENGTHS = {"short": (120.0, 480.0), "medium": (300.0, 900.0), "long": (600.0, 1800.0)}

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# words that say nothing about the topic (English; other languages' fillers weigh little anyway, being everywhere)
_STOP = set("""the and that this with you your for are was were what when where which who whom whose why how have has had
not but all any can could would should will just like yeah yes okay really very about from they them their there then
than these those into out our ours she her him his its it's i'm don't didn't doesn't can't won't that's there's
gonna wanna gotta kinda sorta also some more most much many such only even still well right now here because been
being does did doing going get got getting know think thing things something anything everything nothing want make
made said say says see look looking one two lot way back over too off let let's chat guys guy man bro dude lol lmao
okay oh uh um hmm huh ah yo hey hi bye thank thanks please sorry actually literally basically honestly maybe
mean means kind sure stuff time today tonight people someone feel felt went come came take took tell told""".split())


def tokens(text: str) -> list:
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(text or "")) if w not in _STOP]


def _blocks(words: list, duration: float, step: float) -> list:
    """A Counter of the topic words said in each `step` seconds."""
    n = max(1, int(math.ceil(duration / step)))
    out = [Counter() for _ in range(n)]
    for w in words:
        i = int(float(w["start"]) // step)
        if 0 <= i < n:
            out[i].update(tokens(w.get("word", "")))
    return out


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def topic_shifts(words: list, duration: float, step: float = STEP, block: float = BLOCK, window: int = WINDOW) -> np.ndarray:
    """How strongly the topic changes at each point i * step (0 = not at all), TextTiling's depth score.
    Words before a point (window * block seconds) are compared with the words after it."""
    per = max(1, int(round(block / step)))
    blocks = _blocks(words, duration, step)
    n = len(blocks)
    sims = np.zeros(n + 1, dtype=np.float32)
    span = per * window
    for i in range(1, n):
        left, right = Counter(), Counter()
        for b in blocks[max(0, i - span):i]:
            left.update(b)
        for b in blocks[i:i + span]:
            right.update(b)
        sims[i] = _cos(left, right)
    depth = np.zeros(n + 1, dtype=np.float32)
    for i in range(1, n):
        lp = sims[i]
        for j in range(i - 1, 0, -1):            # climb to the peak on the left
            if sims[j] < lp:
                break
            lp = sims[j]
        rp = sims[i]
        for j in range(i + 1, n):
            if sims[j] < rp:
                break
            rp = sims[j]
        depth[i] = (lp - sims[i]) + (rp - sims[i])
    # a point where nothing much was said on either side (a break) isn't a topic change by itself: pauses() does that
    return depth


def pauses(words: list, duration: float, at_least: float = PAUSE) -> list:
    """Silences of at least `at_least` seconds: [(start, end)], from the gaps between the words."""
    out, last = [], 0.0
    for w in sorted(words, key=lambda w: w["start"]):
        if float(w["start"]) - last >= at_least:
            out.append((last, float(w["start"])))
        last = max(last, float(w["end"]))
    if duration - last >= at_least:
        out.append((last, duration))
    return out


def strength(words: list, duration: float, games: Optional[list] = None, step: float = STEP) -> np.ndarray:
    """How good a place each point i * step is to start a new part: a topic change, a long pause, a new game."""
    s = topic_shifts(words, duration, step)
    n = len(s)
    for a, b in pauses(words, duration):
        k = int(round(((a + b) / 2) / step))
        if 0 < k < n:
            s[k] += min(1.0, (b - a) / 60.0) + 0.3        # a minute of silence counts as much as a clear topic change
    for g in games or []:
        k = int(round(float(g.get("start", 0)) / step))
        if 0 < k < n:
            s[k] += 1.5                                   # the stream changed game: always a good place
    s[0] = 0.0
    return s


def split(s: np.ndarray, duration: float, min_len: float, max_len: float, step: float = STEP,
          forced: Optional[list] = None) -> list:
    """Where to cut [0, duration] into parts min_len..max_len long, strongest places first: [cut seconds].
    forced: places always cut (the game changed), unless one would leave a part under a minute."""
    cuts: list = []
    for t in sorted(forced or []):
        if 60 <= t <= duration - 60 and all(abs(t - c) >= 60 for c in cuts):
            cuts.append(round(t / step) * step)
    order = [int(i) for i in np.argsort(-s) if s[i] > 0]
    thresh = float(np.mean(s[s > 0]) + 0.5 * np.std(s[s > 0])) if (s > 0).any() else math.inf

    def ok(t: float) -> bool:
        pts = sorted(cuts + [t])
        edges = [0.0] + pts + [duration]
        return all(b - a >= min_len for a, b in zip(edges, edges[1:]))
    for i in order:                                       # clear changes: anywhere they leave long enough parts
        if s[i] < thresh:
            break
        t = i * step
        if ok(t):
            cuts.append(t)
    changed = True
    while changed:                                        # parts still too long: cut at their strongest place
        changed = False
        edges = [0.0] + sorted(cuts) + [duration]
        for a, b in zip(edges, edges[1:]):
            if b - a <= max_len:
                continue
            inside = [i for i in range(int(math.ceil((a + min_len) / step)), int((b - min_len) // step) + 1) if 0 < i < len(s)]
            if not inside:
                continue
            best = max(inside, key=lambda i: s[i])
            cuts.append(best * step)
            changed = True
            break
    return sorted(cuts)


def snap(t: float, words: list, duration: float, within: float = SNAP) -> float:
    """t moved to the middle of the longest pause near it (never into a sentence)."""
    if t <= 0 or t >= duration:
        return t
    near = sorted((w for w in words if t - within - 30 <= float(w["start"]) <= t + within + 30), key=lambda w: w["start"])
    best, gap = t, 0.0
    prev_end = max(0.0, t - within)
    for w in near + [{"start": t + within, "end": t + within}]:
        a, b = prev_end, float(w["start"])
        lo, hi = max(a, t - within), min(b, t + within)
        if hi > lo and b - a > gap:
            gap, best = b - a, (lo + hi) / 2
        prev_end = max(prev_end, float(w["end"]))
    return round(best, 2)


def name_for(counts: Counter, others: Counter, n_parts: int, df: Counter, k: int = 3) -> str:
    """The words that set a part apart: said often in it and in few other parts (tf-idf)."""
    if not counts:
        return ""
    scored = sorted(((c * math.log((1 + n_parts) / (1 + df[w])), w) for w, c in counts.items() if c >= 2), reverse=True)
    return " · ".join(w for _, w in scored[:k])


def find(words: list, duration: float, length: str = "medium", score: Optional[np.ndarray] = None,
         games: Optional[list] = None) -> list:
    """The parts of a stream: [{"start", "end", "name", "words", "score", "talk", "quiet", "why"}], in order.
    words: [{"start", "end", "word"}] (absolute seconds). score: per-second interest (Moments' score), optional."""
    if duration <= 0:
        return []
    min_len, max_len = LENGTHS.get(length, LENGTHS["medium"])
    if duration <= min_len * 1.5:
        cuts: list = []
    else:
        cuts = split(strength(words, duration, games), duration, min_len, max_len,
                     forced=[float(g.get("start", 0)) for g in games or []])
    edges = [0.0] + [snap(c, words, duration) for c in cuts] + [round(duration, 2)]
    edges = sorted(set(edges))
    parts = [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= 1.0]
    counts = [Counter() for _ in parts]
    talked = [0.0 for _ in parts]
    for w in words:
        mid = (float(w["start"]) + float(w["end"])) / 2
        for i, (a, b) in enumerate(parts):
            if a <= mid < b:
                counts[i].update(tokens(w.get("word", "")))
                talked[i] += max(0.0, float(w["end"]) - float(w["start"]))
                break
    df = Counter()
    for c in counts:
        df.update(set(c))
    sc = None if score is None or not len(score) else np.nan_to_num(np.asarray(score, dtype=np.float32))
    base = float(np.median(sc)) if sc is not None else 0.0
    out = []
    for i, (a, b) in enumerate(parts):
        talk = min(1.0, talked[i] / max(1.0, b - a))
        busy = float(np.clip(sc[int(a):int(math.ceil(b))] - base, 0, None).mean()) if sc is not None and b > a else 0.0
        quiet = talk < QUIET_TALK and busy < 0.3
        name = name_for(counts[i], Counter(), len(parts), df)
        why = "Mostly quiet (a break?)" if quiet else ("Lots happening" if busy >= 1.0 else "Talking" if talk >= 0.5 else "")
        out.append({"start": round(a, 2), "end": round(b, 2), "name": name, "words": sum(counts[i].values()),
                    "score": round(busy + talk, 2), "talk": round(talk, 2), "quiet": quiet, "why": why})
    return out
