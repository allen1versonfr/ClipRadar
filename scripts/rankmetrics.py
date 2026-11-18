"""
The metrics for scripts/eval_ranking.py (docs/plans/ranking-eval.md, sections 3 and 4): how many of the moments
viewers clipped on a Twitch VOD are among Clip Radar's top clips. A developer tool, not shipped.

Plain Python with no Clip Radar imports, so both sides of an A/B (this checkout and a git ref) are measured by
the same code, and tests/test_rankmetrics.py can check it on hand-made clip lists.

Viewers' clips -> truth moments (moments()):
  - clips whose ranges overlap, or whose ends are within JOIN_S of each other, are one moment
    (transitively: A joins B and B joins C makes one moment of all three);
  - a moment's span is from its earliest start to its latest end;
  - its payoff is the last PAYOFF_S seconds of its most viewed clip (Twitch's clip button takes the 30 s
    before the press, so the reaction is at the end). Ties go to the clip that ends later, then starts later;
  - clippers: distinct clip creators, counting a clip with no creator as one person of its own; views: summed.

Which moments count (truth()): "main" is clippers >= 2 or views >= 500, "loose" is every moment, "strict" is
clippers >= 3 or views >= 2000.

A clip of Clip Radar's finds a moment when it overlaps the moment's payoff by more than zero seconds: a clip
that ends exactly where the payoff starts misses it. It keeps the payoff when it contains all of it.
"""

from __future__ import annotations

import math
import statistics
from typing import Optional

JOIN_S = 20.0             # clips whose ends are this close are the same moment
PAYOFF_S = 10.0           # the payoff: the last this many seconds of the most viewed clip
SETS = {                  # name: (min clippers, min views); a moment counts when it has either
    "loose": (1, 0),
    "main": (2, 500),
    "strict": (3, 2000),
}
TOP_N = (5, 10, 20)
HEADLINE_N = 10


# ---------------------------------------------------------------------------------------------- truth moments
def _num(c: dict, k: str, default=0.0) -> float:
    try:
        v = float(c.get(k, default))
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def moments(clips: list, join: float = JOIN_S, payoff: float = PAYOFF_S) -> list:
    """Viewers' clips ({start, end, views, title, creator?}) grouped into moments, in time order:
    {start, end, payoff: [a, b], clippers, views, clips, title, best: index of the most viewed clip}."""
    cs = [c for c in (clips or []) if _num(c, "end") > _num(c, "start")]
    n = len(cs)
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        a1, b1 = _num(cs[i], "start"), _num(cs[i], "end")
        for j in range(i + 1, n):
            a2, b2 = _num(cs[j], "start"), _num(cs[j], "end")
            if (a1 < b2 and a2 < b1) or abs(b1 - b2) <= join:
                parent[root(j)] = root(i)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(cs[i])
    out = []
    for g in groups.values():
        top = max(g, key=lambda c: (int(_num(c, "views")), _num(c, "end"), _num(c, "start")))
        ts, te = _num(top, "start"), _num(top, "end")
        people, anon = set(), 0
        for c in g:
            who = str(c.get("creator") or "").strip().lower()
            if who:
                people.add(who)
            else:
                anon += 1
        out.append({"start": min(_num(c, "start") for c in g), "end": max(_num(c, "end") for c in g),
                    "payoff": [max(ts, te - payoff), te], "clippers": len(people) + anon,
                    "views": sum(int(_num(c, "views")) for c in g), "clips": len(g),
                    "title": str(top.get("title") or ""), "top_views": int(_num(top, "views"))})
    out.sort(key=lambda m: (m["start"], m["end"]))
    return out


def counts(m: dict, which: str = "main") -> bool:
    k, v = SETS[which]
    return m["clippers"] >= k or m["views"] >= v


def truth(ms: list, which: str = "main") -> list:
    return [m for m in ms if counts(m, which)]


# ---------------------------------------------------------------------------------------------- matching
def _overlap(a1: float, b1: float, a2: float, b2: float) -> float:
    return min(b1, b2) - max(a1, a2)


def finds(clip: dict, m: dict) -> bool:
    """The clip overlaps the moment's payoff (touching its edge isn't enough)."""
    return _overlap(_num(clip, "start"), _num(clip, "end"), m["payoff"][0], m["payoff"][1]) > 0


def keeps(clip: dict, m: dict) -> bool:
    """The clip contains the whole payoff."""
    return _num(clip, "start") <= m["payoff"][0] and m["payoff"][1] <= _num(clip, "end")


def first_rank(ranked: list, m: dict) -> Optional[int]:
    """1-based rank of the first clip that finds the moment, or None."""
    return next((i for i, c in enumerate(ranked, 1) if finds(c, m)), None)


def weight(m: dict) -> float:
    return math.log2(1 + max(0, m["views"]))


def best(ms: list) -> Optional[dict]:
    """The most viewed moment (ties: the one with more clippers, then the earlier one)."""
    return min(ms, key=lambda m: (-m["views"], -m["clippers"], m["start"])) if ms else None


# ---------------------------------------------------------------------------------------------- one VOD
def recall(ranked: list, ms: list, n: int) -> Optional[float]:
    """Share of the moments found by one of the top n clips. None without moments."""
    if not ms:
        return None
    top = ranked[:n]
    return sum(1 for m in ms if any(finds(c, m) for c in top)) / len(ms)


def weighted_recall(ranked: list, ms: list, n: int) -> Optional[float]:
    total = sum(weight(m) for m in ms)
    if total <= 0:
        return None
    top = ranked[:n]
    return sum(weight(m) for m in ms if any(finds(c, m) for c in top)) / total


def precision(ranked: list, viewer_clips: list, n: int) -> Optional[float]:
    """Share of the top n clips that overlap any viewer's clip at all (one-clipper ones too). None without clips."""
    top = ranked[:n]
    if not top:
        return None
    hit = sum(1 for c in top if any(_overlap(_num(c, "start"), _num(c, "end"), _num(v, "start"), _num(v, "end")) > 0
                                    for v in viewer_clips or []))
    return hit / len(top)


def payoff_kept(ranked: list, ms: list, n: int) -> Optional[float]:
    """Of the moments found in the top n, the share where a clip that found it contains the whole payoff."""
    top = ranked[:n]
    found = [m for m in ms if any(finds(c, m) for c in top)]
    if not found:
        return None
    return sum(1 for m in found if any(finds(c, m) and keeps(c, m) for c in top)) / len(found)


def score_vod(ranked: list, viewer_clips: list, auto: Optional[list] = None, which: str = "main",
              ms: Optional[list] = None) -> dict:
    """Every metric of section 4 for one VOD. ranked: Clip Radar's clips ({start, end}) in rank order, from a
    fixed count; auto: its Automatic list, when there is one. ms: the moments (moments(viewer_clips) if not given)."""
    ms = moments(viewer_clips) if ms is None else ms
    t = truth(ms, which)
    b = best(t)
    out = {"truth": len(t), "moments": len(ms), "clips": len(ranked)}
    for n in TOP_N:
        out[f"recall@{n}"] = recall(ranked, t, n)
    # the most recall@10 can be when each of the 10 clips finds a different moment: with 25 truth moments it's 0.4
    out[f"ceiling@{HEADLINE_N}"] = min(1.0, HEADLINE_N / len(t)) if t else None
    out[f"wrecall@{HEADLINE_N}"] = weighted_recall(ranked, t, HEADLINE_N)
    out[f"precision@{HEADLINE_N}"] = precision(ranked, viewer_clips, HEADLINE_N)
    out["payoff_kept"] = payoff_kept(ranked, t, HEADLINE_N)
    out["first_hit"] = first_rank(ranked, b) if b else None
    for k in SETS:
        if k != which:
            out[f"recall@{HEADLINE_N}_{k}"] = recall(ranked, truth(ms, k), HEADLINE_N)
    if auto is not None:
        out["auto_count"] = len(auto)
        out["auto_recall"] = recall(auto, t, len(auto))
    return out


def misses(ranked: list, ms: list, n: int = HEADLINE_N) -> list:
    """Moments none of the top n clips found, most viewed first, each with where it came in the full list (or None)."""
    top = ranked[:n]
    out = [dict(m, rank=first_rank(ranked, m)) for m in ms if not any(finds(c, m) for c in top)]
    out.sort(key=lambda m: (-m["views"], -m["clippers"], m["start"]))
    return out


# ---------------------------------------------------------------------------------------------- the corpus
METRICS = [f"recall@{n}" for n in TOP_N] + [f"ceiling@{HEADLINE_N}", f"wrecall@{HEADLINE_N}", f"precision@{HEADLINE_N}", "payoff_kept",
                                             "first_hit", "auto_count", "auto_recall"]


def summarize(per_vod: list, keys: Optional[list] = None) -> dict:
    """Each metric over VODs (each VOD counts once): {metric: {mean, sd, min, max, n}}. VODs where a metric
    is None (no truth moments, no Automatic run) are left out of that metric."""
    keys = keys or sorted({k for v in per_vod for k, x in v.items() if isinstance(x, (int, float)) and not isinstance(x, bool)})
    out = {}
    for k in keys:
        xs = [float(v[k]) for v in per_vod if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool)]
        out[k] = {"mean": statistics.fmean(xs) if xs else None, "sd": statistics.pstdev(xs) if len(xs) > 1 else 0.0 if xs else None,
                  "min": min(xs) if xs else None, "max": max(xs) if xs else None, "n": len(xs)}
    return out


def diff(new: dict, base: dict) -> dict:
    """Per metric, new mean minus base mean (summarize() outputs), None where either is missing."""
    out = {}
    for k in sorted(set(new) | set(base)):
        a, b = (new.get(k) or {}).get("mean"), (base.get(k) or {}).get("mean")
        out[k] = None if a is None or b is None else a - b
    return out
