"""
Clips viewers made from a Twitch VOD (4.45, src/twitchclips.py): read from Twitch, kept to this VOD, turned
into a signal that ranks the moments people clipped higher, and named in the clip's reason.

  python tests/test_twitchclips.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import twitchclips  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- which of Twitch's periods
now = time.time()
iso = lambda age: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - age))  # noqa: E731
check("a VOD from a few hours ago: the last day's clips", twitchclips._period(iso(5 * 3600), now) == "LAST_DAY")
check("three days old: the last week's", twitchclips._period(iso(3 * 86400), now) == "LAST_WEEK")
check("two weeks old: the last month's", twitchclips._period(iso(14 * 86400), now) == "LAST_MONTH")
check("older: all time", twitchclips._period(iso(90 * 86400), now) == "ALL_TIME")
check("no date: all time", twitchclips._period("", now) == "ALL_TIME")

# ---------------------------------------------------------------- only this VOD's clips
edges = [
    {"node": {"title": "MISSILE", "viewCount": 8260, "durationSeconds": 40, "videoOffsetSeconds": 900, "video": {"id": "42"}}},
    {"node": {"title": "other vod", "viewCount": 9000, "durationSeconds": 30, "videoOffsetSeconds": 100, "video": {"id": "41"}}},
    {"node": {"title": "no vod", "viewCount": 50, "durationSeconds": 30, "videoOffsetSeconds": None, "video": None}},
    {"node": {"title": "early", "viewCount": 3, "durationSeconds": 30, "videoOffsetSeconds": 200, "video": {"id": "42"}}},
]
got = twitchclips.parse(edges, "42")
check("clips from other VODs and without a place in the VOD are dropped", [c["title"] for c in got] == ["early", "MISSILE"], got)
check("each keeps where it is and its views", got[1] == {"start": 900.0, "end": 940.0, "views": 8260, "title": "MISSILE"}, got[1])

# ---------------------------------------------------------------- fetching (Twitch stood in for)
asked = []


def fake_gql(query, timeout=8.0):
    asked.append(query)
    if "video(id:" in query:
        return {"data": {"video": {"createdAt": iso(3600), "lengthSeconds": 3000, "owner": {"login": "somestreamer"}}}}
    return {"data": {"user": {"clips": {"edges": edges}}}}


real = twitchclips._gql
twitchclips._gql = fake_gql
wd = tempfile.mkdtemp(prefix="vc_vclips_")
d = twitchclips.refresh(wd, "42")
check("fetched: this VOD's clips, the last day's top 100 by views",
      len(d["clips"]) == 2 and "LAST_DAY" in asked[-1] and "VIEWS_DESC" in asked[-1] and "first:100" in asked[-1], (d, asked[-1:]))

# an older VOD: each period up to its age, and a clip in more than one list is counted once
asked.clear()
real_period = twitchclips._period
twitchclips._period = lambda iso_, now_=None: "LAST_MONTH"
d3 = twitchclips.fetch("42")
twitchclips._period = real_period
check("a VOD from weeks ago: the day's, the week's and the month's lists",
      [q for q in ("LAST_DAY", "LAST_WEEK", "LAST_MONTH") if any(q in a for a in asked)] == ["LAST_DAY", "LAST_WEEK", "LAST_MONTH"]
      and not any("ALL_TIME" in a for a in asked), asked)
check("and a clip in all three counts once", len(d3["clips"]) == 2, d3["clips"])
check("and saved in the project", twitchclips.load(wd).get("clips") == d["clips"])
n_asked = len(asked)
twitchclips.refresh(wd, "42")
check("opened again soon after: not asked again", len(asked) == n_asked)


def broken(query, timeout=8.0):
    raise OSError("no network")


twitchclips._gql = broken
old = twitchclips.load(wd)
old["fetched"] = 0
import persist  # noqa: E402
persist.write_json(os.path.join(wd, twitchclips.FILE), old)
d2 = twitchclips.refresh(wd, "42")
check("Twitch can't be reached: what was saved is kept", d2.get("clips") == d["clips"])
check("a link that isn't a VOD id is never sent", twitchclips.fetch("abc") is None)
twitchclips._gql = real

# ---------------------------------------------------------------- the signal
heat, people = twitchclips.signal({"clips": [{"start": 100, "end": 130, "views": 10},
                                             {"start": 105, "end": 132, "views": 5000},
                                             {"start": 110, "end": 128, "views": 300}]}, 600)
one, _ = twitchclips.signal({"clips": [{"start": 400, "end": 430, "views": 10}]}, 600)
check("three viewers clipping the same moment stand out", float(heat[125]) > 0.9, float(heat[125]))
check("one viewer alone counts for much less", float(one[425]) < 0.3, float(one[425]))
check("a clip counts most at its end (people clip right after the moment)", float(one[428]) > float(one[402]))
check("nothing anywhere else", float(heat[300]) == 0 and int(people[300]) == 0)
check("no clips: no signal", twitchclips.signal({"clips": []}, 600) == (None, None))
check("a well-watched clip counts for more than an unwatched one", twitchclips._weight(5000) > twitchclips._weight(10) >= 1.0)

# ---------------------------------------------------------------- in the ranking and the reason
n = 600
rng = np.random.default_rng(3)
an = engine.Analysis(float(n), np.zeros(n, np.float32), rng.normal(0, 0.3, n).astype(np.float32), False, True)
base = an.combine(60).copy()
twitchclips.apply(an, {"clips": [{"start": 300, "end": 330, "views": 2000}, {"start": 302, "end": 331, "views": 900},
                                  {"start": 305, "end": 329, "views": 40}]})
after = an.combine(60)
check("a clipped moment ranks higher than it did", float(after[320]) - float(base[320]) > 1.5,
      (float(base[320]), float(after[320])))
check("and the rest of the VOD doesn't move", abs(float(after[100]) - float(base[100])) < 1e-6)
check("the score breakdown names it", "viewers" in an.parts)
why = engine._reason(an, 325, None, [], 0.0)
check("the reason says who clipped it", "clipped by 3 viewers, 2.9k views" in why.lower(), why)
sub = an.part(290, 340)
check("a part of the analysis keeps it (live moments, re-ranking)", sub.vclips is not None and len(sub.vclips) == 50)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
