"""
Find segments (5.0.3): a stream split where the topic changes, at pauses and at a new game, into parts of the length
asked for, each edge in a pause, each part named from its own words, and a break left unticked.

  python tests/test_segfind.py
"""

from __future__ import annotations

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import numpy as np  # noqa: E402

import segfind  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


rng = random.Random(4)
TOPICS = {
    "trip": "nyc trip flight hotel subway pizza brooklyn airport taxi times square broadway manhattan".split(),
    "life": "mom family school college friends job apartment rent sister brother birthday advice".split(),
    "game": "boss level sword armor quest dungeon dragon potion inventory spawn health damage".split(),
}
FILL = "the and so like it was really just you know i mean that this".split()


def talk(a, b, topic, words):
    """someone talking from a to b about topic: a word every 0.4 s, a short breath every 8 s"""
    t = a
    while t < b - 0.4:
        if int(t) % 8 == 0 and t - int(t) < 0.4:
            t += 0.9
            continue
        w = rng.choice(TOPICS[topic]) if rng.random() < 0.35 else rng.choice(FILL)
        words.append({"start": round(t, 2), "end": round(t + 0.3, 2), "word": w})
        t += 0.4


words: list = []
talk(0, 900, "life", words)              # 0-15 min: life
talk(900, 1500, "trip", words)           # 15-25 min: the trip
# 25-27 min: a break, nothing said
talk(1620, 2700, "game", words)          # 27-45 min: a game
DUR = 2700.0

parts = segfind.find(words, DUR, "medium")
edges = [p["start"] for p in parts[1:]]
print("  parts:", [(round(p["start"]), round(p["end"]), p["name"], p["quiet"]) for p in parts])
check("the stream is split into parts covering all of it", parts[0]["start"] == 0 and parts[-1]["end"] == DUR
      and all(abs(a["end"] - b["start"]) < 1e-6 for a, b in zip(parts, parts[1:])))
check("every part is 5 to 15 minutes long (medium)", all(300 - 1 <= p["end"] - p["start"] <= 900 + 1 for p in parts),
      [p["end"] - p["start"] for p in parts])
check("a part starts where the trip story starts (15:00, within 45 s)", any(abs(e - 900) <= 45 for e in edges), edges)
check("and around the break before the game (25:00 to 27:00)", any(1490 <= e <= 1640 for e in edges), edges)
trip = [p for p in parts if p["start"] <= 1200 < p["end"]][0]
check("the trip part is named from its own words", any(w in trip["name"] for w in TOPICS["trip"]), trip["name"])
check("no part's name is filler", all(not any(f == w for f in FILL for w in p["name"].split(" · ")) for p in parts),
      [p["name"] for p in parts])
for p in parts:
    inside = [w for w in words if p["start"] < w["start"] < p["end"]]
    if p["start"] > 0:
        first = min((w["start"] for w in words if w["start"] >= p["start"]), default=p["start"])
        check(f"the part at {p['start']:.0f} s starts in a pause, not mid-word", not any(w["start"] < p["start"] < w["end"] for w in words))
        break

# a break of its own is left unticked
words2: list = []
talk(0, 600, "life", words2)
talk(1200, 1800, "game", words2)
parts2 = segfind.find(words2, 1800.0, "short")
quiet = [p for p in parts2 if p["quiet"]]
check("a stretch where nothing is said is its own part, marked quiet", quiet and all(p["start"] >= 540 and p["end"] <= 1260 for p in quiet),
      [(p["start"], p["end"], p["quiet"]) for p in parts2])

# a new game is always a place to cut
parts3 = segfind.find(words, DUR, "long", games=[{"start": 1980, "game": "Elden Ring"}])
check("the game changing is a place to cut (long parts)", any(abs(p["start"] - 1980) <= 45 for p in parts3),
      [p["start"] for p in parts3])
check("long parts are 10 to 30 minutes", all(600 - 1 <= p["end"] - p["start"] <= 1800 + 1 for p in parts3), [p["end"] - p["start"] for p in parts3])

# a short video is one part; no words at all still gives parts of the right length
check("a video shorter than a part is one part", len(segfind.find(words[:100], 400.0, "medium")) == 1)
blank = segfind.find([], 3600.0, "medium", score=np.zeros(3601))
check("with nothing heard the video is still split into parts of the right length",
      blank and all(300 - 1 <= p["end"] - p["start"] <= 900 + 1 for p in blank) and all(p["quiet"] for p in blank),
      [(p["start"], p["end"]) for p in blank])
check("filler and short words are never topic words", segfind.tokens("So yeah I mean the NYC trip was like LOL") == ["nyc", "trip"])

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
