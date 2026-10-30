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
      and {"#funny", "#funnymoments"} & set(r["hashtags"]) and "#fyp" not in r["hashtags"], r["hashtags"])
check("hashtags in the post when switched on", r["post"].endswith(" ".join(r["hashtags"])))
off = titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen", with_tags=False)
check("no hashtags when switched off", "#" not in off["post"] and off["post"] == off["description"], off["post"])
check("never more than five tags", len(titles.hashtags("funny", "Someone", "Minecraft")) <= 5)

check("plain description reads naturally", titles.describe(None, "", "Sam") == "A moment from Sam's stream 👀"
      and titles.describe(None, "") == "A moment from the stream 👀")

# Whisper mishearing noise is never a title (all seen on a real 6-hour stream)
for junk in ["5, 4, 5, 5, 5, 6,.p", "L!, c!, I'm d, b, b, t, c!", "TH, d, s,fore, dad, oh, u,ne",
             "Girls insp, h, wap, r, wap", "Uu, uu, uu, luumph, uu, uu, uu tha", "😂 Yippy 😆 👛 It's", "U dram fave, l!"]:
    check(f"garbled: {junk!r}", titles.garbled(junk))
for fine in ["No, actually, bro, what is it?", "How come, bruh", "Yo bud, sh!t!", "What the, what", "I never seen",
             "It's time to go, bruh", "He pranked calling people, right?", "No no no way"]:
    check(f"a real line isn't garbled: {fine!r}", not titles.garbled(fine))
rj = titles.generate(W("L!, c!, I'm d, b, b, t, c!", 1), laugh, 2.0, set())
check("a garbled line falls back to a template", rj["title_a"] in [titles.polish(t) for t in titles.VIBES["funny"]["templates"] + titles.NEUTRAL], rj["title_a"])

# automatic captions have no punctuation: long runs are cut at a pause, not mid-sentence
cap = ("okay I've had my little pink decorating moment and I loved every second of it this starter house is "
       "definitely the most colorful I've ever had").split()
ws, t = [], 0.6
for w in cap:
    ws.append({"start": t, "end": t + 0.25, "word": w})
    t += 0.3 + (0.45 if w in ("moment", "it") else 0.0)       # the speaker pauses after "moment" and "it"
pieces = [titles._phrase_text(p) for p in titles._phrases(ws)]
check("captions: no piece ends on a word that leads into the next", not any(p.split()[-1].lower() in titles.DANGLING_END for p in pieces[:-1]), pieces)
check("captions: cut where the speaker paused", any(p.endswith("of it") for p in pieces), pieces)
rc = titles.generate(ws, [], 5.0, set())
check("captions: the title is a whole thought", not rc["title_a"].lower().endswith(" this"), rc["title_a"])

# many clips of one mood: the templates take turns once they're all used
used, got = set(), []
for i in range(30):
    x = titles.generate([], ["KEKW"] * (8 + i % 7), float(i % 5), used)
    got.append(x["title_a"])
    used.add(x["title_a"].lower())
top = max(got.count(g) for g in set(got))
check("30 funny clips without words: no title on more than a quarter of them", top <= 30 // 4, sorted(got))

# a YouTube video: YouTube's tags and name, not Twitch's
ry = titles.generate(words, laugh, 4.0, set(), streamer="GeminiTay", platform="youtube")
check("YouTube: no Twitch tags", not any("twitch" in t for t in ry["hashtags"]) and "#youtube" in ry["hashtags"], ry["hashtags"])
check("YouTube: the description says YouTube", "Twitch" not in ry["description"], ry["description"])
check("Twitch is still the default", "#twitch" in titles.generate(words, laugh, 4.0, set(), streamer="X1")["hashtags"])

# ---------------------------------------------------------------- 4.36: mood, topic, variety, game hashtags
people = lambda texts: [(float(i), t, f"u{i}") for i, t in enumerate(texts)]  # noqa: E731
mixed = people(["KEKW", "LMAOOO", "L", "😂😂", "L", "hahaha", "OMEGALUL", "L", "xdd", "ICANT"])
check("a laughing wave with some L's in it is funny, not a fail", titles.generate([], mixed)["vibe"] == "funny")
one = [(float(i), "L", "same") for i in range(10)] + people(["KEKW", "LMAO", "😂"])
check("one person typing L ten times is one chatter", titles.generate([], one)["vibe"] == "funny")
hair = people(["NOOO not the hair", "dont cut the hair", "the hair 😭", "his hair was fine", "save the hair",
               "bro the hair", "hair cut LMAO", "KEKW", "noooo", "W"])
th = titles.generate([], hair, streamer="Speedy")
check("what chat talks about becomes the topic", th["topic"] == "hair", th["topic"])
check("and is in a title and a hashtag", any("the hair" in t for t in th["titles"]) and "#hair" in th["hashtags"], th)
for bad in (["thats cap", "CAP", "the cap is crazy", "big cap", "cap cap", "that cap"],
            ["the speedy show", "the speedy fans", "speedy speedy", "go speedy", "speedy W", "speedy pls"],
            ["the KEKW", "a KEKW", "KEKW", "KEKW", "KEKW", "KEKW"],
            ["the stream", "this stream", "stream lag", "stream", "stream ok", "the stream"]):
    tb = titles.generate([], people(bad * 2), streamer="Speedy")
    check(f"not a topic: {bad[0]!r}", tb["topic"] == "", tb["topic"])
check("a few people aren't a topic", titles.topic(people(["the car", "the car", "the car"])) == "")

# a stream's clips: titles, titles B and captions vary
used, got = set(), {"a": [], "b": [], "d": []}
for k in range(12):
    g = titles.generate([], people(["POGGG", "LETSGOOO", "W", "W", "INSANE", "🔥"] + ["W"] * k), peak_rel=k * 1.3,
                        used=used, streamer="Kai")
    used.update(x.lower() for x in (g["title"], g["title_a"], g["title_b"], g["description"]))
    got["a"].append(g["title_a"])
    got["b"].append(g["title_b"])
    got["d"].append(g["description"])
check("12 hype clips: 12 titles", len(set(got["a"])) == 12, got["a"])
check("and at least 10 different titles B and captions", len(set(got["b"])) >= 10 and len(set(got["d"])) >= 10, got)

check("the game is a hashtag, in the form people search", titles.hashtags("hype", "Kai", "Grand Theft Auto V")[:2] == ["#kai", "#gta5"]
      and "#minecraft" in titles.hashtags("funny", "x", "Minecraft"))
check("#gaming only for a game", "#gaming" in titles.hashtags("funny", "x", "Minecraft")
      and "#gaming" not in titles.hashtags("funny", "x", "Just Chatting") and "#gaming" not in titles.hashtags("funny", "x"))
check("Just Chatting has its own tag", "#justchatting" in titles.hashtags("funny", "x", "Just Chatting"))
check("YouTube's Gaming category is a game", "#gaming" in titles.hashtags("funny", "x", platform="youtube", category="Gaming")
      and "#gaming" not in titles.hashtags("funny", "x", platform="youtube", category="Entertainment"))
check("the streamer's clips tag when there's room", "#kaiclips" in titles.hashtags("funny", "Kai"))
full = titles.hashtags("funny", "Kai", "Minecraft", topic_word="creeper")
check("five at most, the platform's tag kept", len(full) == 5 and full[-1] == "#twitch" and full[:3] == ["#kai", "#minecraft", "#creeper"],
      full)
check("no #twitchclips on every clip any more", "#twitchclips" not in titles.hashtags("funny", "Kai"))

import engine  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
cf = tempfile.mktemp(suffix=".json")
json.dump({"streamer": {"name": "Kai", "id": "1"}, "video": {"chapters": [
    {"startMilliseconds": 0, "lengthMilliseconds": 600000, "gameDisplayName": "Just Chatting"},
    {"startMilliseconds": 600000, "lengthMilliseconds": 3000000, "gameDisplayName": "Minecraft"}]}, "comments": []},
    open(cf, "w"))
info = {}
engine.load_chat(cf, info)
check("Twitch chat replay: the game, and each chapter's", info.get("game") == "Minecraft" and len(info.get("games", [])) == 2
      and engine.game_at(info, 30) == "Just Chatting" and engine.game_at(info, 900) == "Minecraft", info)
check("no game known: ''", engine.game_at({}, 5) == "")
import sources  # noqa: E402
check("YouTube's category from the link check", sources.describe({"categories": ["Gaming"], "title": "x"})["category"] == "Gaming")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
