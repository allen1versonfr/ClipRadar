"""
Unit tests for titles from chat: a phrase many different chatters typed becomes a title; spam,
bots, F waves, one loud viewer, usernames, links and personal details never do.

  python tests/test_chattitles.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import chattitles  # noqa: E402
import titles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


EMOTES = {"kekw", "pog", "omegalul", "lul"}
filler = [("KEKW", f"x{i}") for i in range(10)] + [("what is this game", "y1"), ("hi", "y2"), ("lol", "y3")]

# 1. the phrase chat agreed on
agree = [("THE DOG WALKED IN", "a1"), ("the dog walked in KEKW", "a2"), ("lmao the dog walked in", "a3"),
         ("the dog walked innnnnn", "a4"), ("dog walked in omg", "a5")]
r = chattitles.find(agree + filler, EMOTES)
check("what many chatters typed becomes the title", r["title"] == 'Chat: "The dog walked in"', r)
check("supported by the number of people", r["people"] >= 4 and r["chatters"] >= 15, r)

# 2. one viewer spamming isn't chat agreeing
spam = [("he is so washed honestly", "loud1")] * 30
r = chattitles.find(spam + filler, EMOTES)
check("one viewer's message, however often, doesn't make a title", r["title"] == "", r)

# 3. F waves, emote walls and generic reactions don't
r = chattitles.find([("F", f"f{i}") for i in range(40)] + [("F F F", f"g{i}") for i in range(10)], EMOTES)
check("an F wave makes no title", r["title"] == "", r)
r = chattitles.find([("KEKW KEKW OMEGALUL", f"k{i}") for i in range(30)], EMOTES)
check("an emote wall makes no title", r["title"] == "", r)
r = chattitles.find([("LETS GOOOO", f"h{i}") for i in range(30)] + [("no way", f"n{i}") for i in range(30)], EMOTES)
check("everyday reactions are left to the reaction titles", r["title"] == "", r)

# 4. names, links and personal details never reach a title
r = chattitles.find([("ban cooluser99 now", f"b{i}") for i in range(10)] + [("lol", "cooluser99")], EMOTES)
check("a chatter's name is never in a title", "cooluser" not in r["title"].lower(), r)
r = chattitles.find([("check out mysite.com/free", f"l{i}") for i in range(10)], EMOTES)
check("links are dropped", "mysite" not in r["title"].lower(), r)
r = chattitles.find([("call 555 123 4567 now", f"p{i}") for i in range(10)], EMOTES)
check("numbers are dropped", not any(ch.isdigit() for ch in r["title"]), r)
r = chattitles.find([("@someone you missed it", f"m{i}") for i in range(10)], EMOTES)
check("mentions are dropped", "someone" not in r["title"].lower(), r)
r = chattitles.find([("streamy actually hit that", f"s{i}") for i in range(6)] + filler, EMOTES, streamer="streamy")
check("the streamer's own name may stay", r["title"] == 'Chat: "Streamy actually hit that"', r)

# 5. too little chat, or chat that doesn't agree: no title
check("two chatters aren't enough", chattitles.find([("the dog walked in", "a"), ("the dog walked in", "b")], EMOTES)["title"] == "")
check("no chat at all", chattitles.find([], EMOTES)["title"] == "")
check("bad input never raises", chattitles.find([(None, None), ("x", None), (123, "u")], EMOTES)["title"] == "")

# 6. in the generated titles: after a strong line said on stream, before the templates
t = titles.generate([], ["KEKW"] * 20, peak_rel=5, chat_title='Chat: "The dog walked in"')
check("with nothing said on stream, chat's phrase is title A", t["title_a"] == 'Chat: "The dog walked in"', t["title_a"])
check("and it's reported as the chat title", t["chat_title"] == 'Chat: "The dog walked in"')
check("the reaction templates are still there to pick", any("laugh" in x.lower() for x in t["titles"]), t["titles"])
words = [{"start": 4.0 + i * 0.3, "end": 4.25 + i * 0.3, "word": w} for i, w in enumerate("No way he just did that!".split())]
t = titles.generate(words, ["KEKW"] * 20, peak_rel=5, chat_title='Chat: "The dog walked in"')
check("a strong line said on stream still comes first", t["title_a"].startswith("No way"), t["title_a"])
check("with chat's phrase right after it", 'Chat: "The dog walked in"' in t["titles"][:3], t["titles"])
t = titles.generate([], ["KEKW"] * 20, peak_rel=5)
check("switched off (or nothing found): titles as before", t["chat_title"] == "" and not t["title_a"].startswith("Chat:"), t["title_a"])

# 7. F-spam doesn't make a clip a "fail" either
t = titles.generate([], ["F"] * 40, peak_rel=5)
check("an F wave doesn't decide the clip's mood", t["vibe"] != "fail", t["vibe"])

# 8. through the app: a clip's titles use chat's phrase, and the setting turns it off
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
import server  # noqa: E402
chat = [(100.0 + i, txt, u, "") for i, (txt, u) in enumerate(agree + filler)]
pr = {"chat": chat, "chat_delay": 6.0, "info": {"has_audio": True}, "streamer": "", "title": "t"}
d = {"start": 95.0, "end": 115.0, "peak": 100.0}
server._title_clip(pr, d, [], set())
check("a clip gets chat's phrase as its title", d["title"] == 'Chat: "The dog walked in"', d.get("title"))
server.set_title_prefs({"chat_titles": False})
d2 = {"start": 95.0, "end": 115.0, "peak": 100.0}
server._title_clip(pr, d2, [], set())
check("switched off in Settings, it isn't used", not d2["title"].startswith("Chat:"), d2.get("title"))
check("and the setting is remembered", server.title_prefs()["chat_titles"] is False)
server.set_title_prefs({"chat_titles": True})
broken = dict(pr, chat=[(100.0, None, None, "")])
d3 = {"start": 95.0, "end": 115.0, "peak": 100.0}
server._title_clip(broken, d3, [], set())
check("odd chat data still gives a normal title", bool(d3["title"]) and not d3["title"].startswith("Chat:"), d3.get("title"))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
