"""
Unit tests for 3.6 titles and captions: title A and B, the streamer's name, tidying, no invented
claims, no repeats across clips, descriptions kept apart from titles, and hashtags.

  python tests/test_titles.py
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import titles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def W(text, t0=0.0):
    return [{"start": t0 + i * 0.35, "end": t0 + i * 0.35 + 0.3, "word": w} for i, w in enumerate(text.split())]


laugh = ["KEKW"] * 12 + ["LUL"] * 4
words = W("um so NO WAY HE ACTUALLY DID THAT!!!", 2)

r = titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen")
check("two titles: A and B", r["title_a"] and r["title_b"] and r["title_a"] != r["title_b"], (r["title_a"], r["title_b"]))
check("B has the streamer's name once", r["title_b"].count("JasonTheWeen") == 1 and r["title_b_named"], r["title_b"])
check("A doesn't shout", not re.search(r"[A-Z]{4,}", r["title_a"]), r["title_a"])
check("one '!' at most in a row", "!!" not in r["title_a"])
check("A is the line that was said", r["title_a"].lower().startswith("no way he actually did that"), r["title_a"])
check("both kept as options", r["title_a"] in r["titles"] and r["title_b"] in r["titles"])
check("pick B puts B on the video", titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen", pick="b")["title"] == r["title_b"])

r0 = titles.generate(words, laugh, 4.0, set(), streamer="")
check("no name known: B is still a real second choice", r0["title_b"] and r0["title_b"] != r0["title_a"] and not r0["title_b_named"],
      (r0["title_a"], r0["title_b"]))
check("no name known: no placeholder name", "{n}" not in " ".join(r0["titles"]))

# a name already in the line isn't repeated
rn = titles.generate(W("Jason you are actually insane!", 1), laugh, 2.0, set(), streamer="Jason")
check("a name that's already in the line isn't added again", rn["title_b"].lower().count("jason") == 1, rn["title_b"])

# no invented claims: templates only talk about chat's reaction or ask you to watch
claims = re.compile(r"\b(clutch|best play|cleanest|world record|insane play|why i stream|goat)\b", re.I)
all_templates = [t for v in titles.VIBES.values() for t in v["templates"] + v["named"]] + titles.NEUTRAL + titles.NEUTRAL_NAMED
check("no template claims an outcome the clip can't show", not [t for t in all_templates if claims.search(t)],
      [t for t in all_templates if claims.search(t)])
check("no template shouts", not [t for t in all_templates if re.search(r"\b[A-Z]{3,}\b", t.replace("{n}", ""))])

# no repeats across clips
used: set = set()
got = []
for i in range(4):
    x = titles.generate([], laugh, None, used, streamer="Sam")
    got.append(x["title_a"])
    used.add(x["title_a"].lower())
check("different clips get different titles", len(set(got)) == 4, got)

# tidying
check("tidy: shouting to normal case", titles.polish("WHAT IS GOING ON HERE") == "What is going on here")
check("tidy: stammered repeats", titles.polish("no no no no no no way") == "No no no way")
check("tidy: trailing filler", titles.polish("i cant believe this like") == "I can't believe this")
long = "so then I walked over to the other side of the map and found the whole squad waiting there for me"
check("tidy: long lines end on a word", len(titles.polish(long)) <= 61 and titles.polish(long).endswith("..."), titles.polish(long))

# descriptions and hashtags
check("description isn't the title", r["description"] != r["title_a"] and r["description"] != r["title_b"], r["description"])
check("description names the stream when known", "JasonTheWeen" in r["description"], r["description"])
check("a few relevant hashtags", 2 <= len(r["hashtags"]) <= titles.MAX_TAGS and "#jasontheween" in r["hashtags"]
      and "#funny" in r["hashtags"] and "#fyp" not in r["hashtags"], r["hashtags"])
check("hashtags in the post when switched on", r["post"].endswith(" ".join(r["hashtags"])))
off = titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen", with_tags=False)
check("no hashtags when switched off", "#" not in off["post"] and off["post"] == off["description"], off["post"])
check("never more than five tags", len(titles.hashtags("funny", "Someone", "Minecraft")) <= 5)

check("plain description reads naturally", titles.describe(None, "", "Sam") == "A moment from Sam's stream 👀"
      and titles.describe(None, "") == "A moment from the stream 👀")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
