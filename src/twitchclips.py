"""
Clips viewers made from a Twitch VOD (4.45), as a signal for ranking its moments.

When people watch a stream or its VOD, some of them clip what they liked. Twitch records each clip's VOD,
where in it the clip starts, how long it is and how many views it has had. That's a different kind of
evidence from chat and sound: not how loud the reaction was, but that someone thought the moment was worth
keeping, and that others watched it. It finds what chat and audio miss: a quiet clutch play, a moment chat
was too busy to react to, a long setup whose payoff is what got clipped.

Where they come from: Twitch's public GQL (the same endpoint and web client id emotes.py uses for a
channel's id), with no sign-in. Twitch doesn't let anonymous clients page past the first page of a list
(the second page fails its integrity check, measured 5 Nov 2026). So it asks for the channel's 100 most
viewed clips of the last day, then the last week and so on up to the period that covers the VOD's age,
and keeps the ones from this VOD. Shorter periods find more of a recent VOD's clips: on an 11-hour xQc
VOD a day old, the last day's list had 85 of its clips and the last week's only 12. On an older VOD
it's the clips that stood out that month, which is still what counts.

Everything here is optional: no clips, an old VOD, a network error or a format change gives nothing, and
ranking is as before. Saved as twitch_clips.json in the project and fetched again after REFRESH_S,
since clips keep being made from a VOD for days.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np

FILE = "twitch_clips.json"
GAIN = 3.0               # how much a moment many viewers clipped can add (audio z-scores are 0-8), as Most replayed
REFRESH_S = 6 * 3600     # fetched again when the saved list is older than this
PER_PAGE = 100           # Twitch's page size; anonymous clients get the first page only
VIEWS_HALF = 200         # views at which a clip counts for half again as much as an unwatched one
PERIODS = ("LAST_DAY", "LAST_WEEK", "LAST_MONTH", "ALL_TIME")
MAX_CLIP_S = 90          # a clip over twice this long says little about where the moment is: left out


def _period(created_iso: str, now: Optional[float] = None) -> str:
    """The shortest of Twitch's clip periods that covers a VOD this old."""
    try:
        t = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return "ALL_TIME"
    age = (now if now is not None else time.time()) - t
    for limit, name in ((86400 * 0.9, "LAST_DAY"), (7 * 86400 * 0.95, "LAST_WEEK"), (30 * 86400 * 0.95, "LAST_MONTH")):
        if age < limit:
            return name
    return "ALL_TIME"


def _gql(query: str, timeout: float = 8.0) -> dict:
    import emotes
    body = json.dumps({"query": query}).encode()
    return emotes._get(emotes.GQL, timeout, body, {"Client-ID": emotes.WEB_CLIENT_ID, "Content-Type": "application/json"})


def fetch(vod_id: str, log=lambda m: None, timeout: float = 8.0) -> Optional[dict]:
    """{"vod", "fetched", "period", "clips": [{start, end, views, title}]}, or None when Twitch couldn't be
    asked (the caller keeps what it had)."""
    if not str(vod_id).isdigit():
        return None
    try:
        v = (_gql('query{video(id:"%s"){createdAt lengthSeconds owner{login}}}' % vod_id, timeout).get("data") or {}).get("video")
        if not v or not (v.get("owner") or {}).get("login"):
            return {"vod": vod_id, "fetched": time.time(), "period": "", "clips": []}
        period = _period(v.get("createdAt") or "")
        login = json.dumps(v["owner"]["login"])
        edges: list = []
        for p in PERIODS[:PERIODS.index(period) + 1]:
            d = _gql('query{user(login:%s){clips(first:%d,criteria:{period:%s,sort:VIEWS_DESC}){edges{node{'
                     'id title viewCount durationSeconds videoOffsetSeconds video{id}}}}}}' % (login, PER_PAGE, p), timeout)
            edges += ((((d.get("data") or {}).get("user") or {}).get("clips") or {}).get("edges")) or []
    except Exception as e:
        log(f"Couldn't read the clips viewers made ({str(e)[:80]}).")
        return None
    return {"vod": vod_id, "fetched": time.time(), "period": period, "length": v.get("lengthSeconds") or 0,
            "clips": parse(edges, vod_id)}


def parse(edges: list, vod_id: str) -> list:
    """The clips in a GQL answer that come from this VOD, as {start, end, views, title}."""
    out, seen = [], set()
    for e in edges or []:
        n = (e or {}).get("node") or {}
        if str(((n.get("video") or {}).get("id")) or "") != str(vod_id):
            continue
        key = n.get("id") or (n.get("videoOffsetSeconds"), n.get("title"))
        if key in seen:                  # the same clip in the day's list and the week's
            continue
        seen.add(key)
        off, dur = n.get("videoOffsetSeconds"), n.get("durationSeconds")
        if not isinstance(off, (int, float)) or off < 0 or not isinstance(dur, (int, float)) or dur <= 0:
            continue
        out.append({"start": float(off), "end": float(off) + float(dur), "views": int(n.get("viewCount") or 0),
                    "title": str(n.get("title") or "")[:140]})
    out.sort(key=lambda c: c["start"])
    return out


def load(workdir: str) -> dict:
    try:
        with open(os.path.join(workdir, FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh(workdir: str, vod_id: str, log=lambda m: None) -> dict:
    """The saved list, fetched again when it's missing or older than REFRESH_S."""
    have = load(workdir)
    if have.get("vod") == vod_id and time.time() - float(have.get("fetched") or 0) < REFRESH_S:
        return have
    got = fetch(vod_id, log)
    if got is None:
        return have
    import persist
    try:
        persist.write_json(os.path.join(workdir, FILE), got, indent=1)
    except Exception:
        pass
    n = len(got["clips"])
    if n:
        log(f"Viewers clipped {n} moment{'s' if n != 1 else ''} of this VOD "
            f"({sum(c['views'] for c in got['clips']):,} views between them).")
    return got


def _weight(views: int) -> float:
    """One clip counts 1, and up to 3 with views: 200 views is 1.5, 1,000 about 2.3."""
    return 1.0 + min(2.0, math.log2(1.0 + max(0, views) / VIEWS_HALF) * 0.5)


def signal(d: dict, n: int) -> tuple:
    """(0..1 per second: how much this second was clipped, clips covering each second), or (None, None).
    A clip covers its own stretch. Its last part counts most, since people clip right after the moment
    (Twitch's clip button takes the 30 s before it). 0..1 is against a few weighted clips, so one clip
    alone never makes a moment, but three or four people clipping the same thing stand out."""
    clips = (d or {}).get("clips") or []
    if not clips or n <= 0:
        return None, None
    heat = np.zeros(n, dtype=np.float32)
    people = np.zeros(n, dtype=np.int32)
    for c in clips:
        a, b = int(max(0, c["start"])), int(min(n, math.ceil(c["end"])))
        if b - a < 1 or c["end"] - c["start"] > MAX_CLIP_S * 2:
            continue
        w = _weight(c.get("views", 0))
        ramp = np.linspace(0.4, 1.0, b - a, dtype=np.float32)
        heat[a:b] += w * ramp
        people[a:b] += 1
    if not heat.any():
        return None, None
    return np.clip(heat / 4.0, 0, 1).astype(np.float32), people


def apply(an, d: dict):
    """Attach the viewers' clips to an Analysis (engine.Analysis.combine mixes them in)."""
    an.vclips, an.vclip_people = signal(d, len(an.audio_z))
    an.vclip_views = _views_per_second(d, len(an.audio_z)) if an.vclips is not None else None
    return an


def _views_per_second(d: dict, n: int) -> np.ndarray:
    v = np.zeros(n, dtype=np.int64)
    for c in (d or {}).get("clips") or []:
        a, b = int(max(0, c["start"])), int(min(n, math.ceil(c["end"])))
        if b > a:
            v[a:b] += int(c.get("views", 0))
    return v


def reason(an, i: int) -> str:
    """"clipped by 4 viewers, 12k views" near second i, or ''."""
    p = getattr(an, "vclip_people", None)
    if p is None:
        return ""
    a, b = max(0, i - 15), min(len(p), i + 10)
    k = int(np.max(p[a:b], initial=0))
    if k < 1:
        return ""
    views = int(np.max(an.vclip_views[a:b], initial=0)) if getattr(an, "vclip_views", None) is not None else 0
    who = "a viewer" if k == 1 else f"{k} viewers"
    if views >= 1000:
        return f"clipped by {who}, {views / 1000:.0f}k views" if views >= 10000 else f"clipped by {who}, {views / 1000:.1f}k views"
    if views > 0:
        return f"clipped by {who}, {views} views"
    return f"clipped by {who}"
