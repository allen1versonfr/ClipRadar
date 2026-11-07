"""
The prompt and the answer checks for local-AI titles (src/llm_prompts.py, issue #49 sections 5.1 to 5.4).
No model: answers are written here the way a model could get them wrong.

  python tests/test_llm_prompts.py
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
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="llmp_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import llm_prompts as lp  # noqa: E402
import safety  # noqa: E402
import titles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def W(text, t0=0.0):
    return [{"start": t0 + i * 0.4, "end": t0 + i * 0.4 + 0.3, "word": w} for i, w in enumerate(text.split())]


# ---------------------------------------------------------------- chat: agreed phrases, never who said them
chat = [(10.0, "REF", "alice"), (10.5, "ref", "bob"), (11, "REF", "bob"), (11.2, "REF", "carol"),
        (12, "@dave look at this", "erin"), (12.5, "erin you're wrong", "frank"), (13, "go to evil.com now", "gus"),
        (14, "TKO", "hal"), (14.5, "TKO", "ivy"), (15, "lol", "jo")]
top = lp.top_chat(chat)
check("most agreed first, each chatter once", top[0] == "REF (x3)" and top[1] == "TKO (x2)", top)
joined = " ".join(top).lower()
check("no usernames reach the prompt (as authors, @mentions or by name)",
      not any(u in joined for u in ("alice", "bob", "carol", "dave", "erin", "frank", "hal", "ivy")), top)
check("links are left out", "evil" not in joined, top)
check("at most 15", len(lp.top_chat([(i, f"msg {i}", f"u{i}") for i in range(40)])) == 15)
check("plain texts work too", lp.top_chat(["gg", "gg", "wow"]) == ["gg (x2)", "wow"], lp.top_chat(["gg", "gg", "wow"]))

# ---------------------------------------------------------------- the transcript excerpt
long_words = W(" ".join(f"w{i}" for i in range(400)))
ex = lp.excerpt(long_words, peak_rel=100.0)
check("at most 600 characters", len(ex) <= 600, len(ex))
check("centred on the peak", "w250" in ex and "w0 " not in ex, ex[:40])
check("short ones whole", lp.excerpt(W("no way he did that")) == "no way he did that")

# ---------------------------------------------------------------- the prompt
info = titles.generate(W("no way he actually knocked him out", 1), ["KEKW"] * 10, 2.0, set(), streamer="Jason",
                       viewer_titles=["TKO"], chat_title='Chat: "REF"')
cands = lp.candidates(info, ["TKO", "scripted??"])
check("candidates: the built-in ones, then viewers' titles not already there, no repeats",
      cands[:len(info["titles"])] == info["titles"] and cands.count("TKO") == 1 and "scripted??" in cands, cands)
ctx = {"candidates": cands, "transcript": lp.excerpt(long_words, 100.0), "chat": lp.top_chat(chat * 3),
       "quote": info["quote"], "vibe": info["vibe"], "topic": info["topic"], "streamer": "Jason", "game": "Boxing"}
p = lp.build(ctx)
check("ideas are numbered from 1", "1. " + cands[0] in p and f"{len(cands)}. {cands[-1]}" in p)
check("the inputs are labelled", all(k in p for k in ("Transcript:", "Chat (most agreed first):", "Title ideas:", "Streamer: Jason")))
full = lp.INSTRUCTIONS + "\n" + p
check("a full prompt stays near 700 tokens", lp.approx_tokens(full) <= 760, lp.approx_tokens(full))
req = lp.request(ctx, seed=12345678901)
check("the request is constrained to the schema, with a fixed seed",
      req["response_format"]["json_schema"]["schema"] == lp.SCHEMA and 0 <= req["seed"] < 2 ** 31
      and req["temperature"] == 0.3 and req["messages"][0]["content"] == lp.INSTRUCTIONS)
check("nothing said, no chat: still a prompt", "(nothing said)" in lp.build({"candidates": ["A"]}) and "(no chat)" in lp.build({"candidates": ["A"]}))

# ---------------------------------------------------------------- the answer checks (section 5.4)
cfg = dict(safety.DEFAULTS, censor="bleep", custom=["springfield"])
ctx2 = {"candidates": ["No way he actually knocked him out", "TKO", "Chat lost it at this"],
        "transcript": "no way he actually knocked him out in round 2", "chat": ["REF (x3)", "TKO (x2)"],
        "streamer": "Jason", "game": "Boxing"}


def run(ans, used=None, builtin="Chat lost it at this"):
    return lp.check(ans, ctx2, used or set(), builtin, cfg)


ok = run({"pick": 1, "title": "Jason knocks him out in round 2", "caption": "Chat wanted the REF."})
check("a light edit from the inputs is used", ok["outcome"] == "used" and ok["title"] == "Jason knocks him out in round 2"
      and ok["pick"] == 1 and ok["caption"] == "Chat wanted the REF.", ok)
check("an empty title", run({"pick": 1, "title": "  ", "caption": ""})["outcome"] == "rejected:empty")
check("too long", run({"pick": 1, "title": "x " * 40, "caption": ""})["outcome"] == "rejected:too_long")
check("all caps when the idea wasn't", run({"pick": 1, "title": "NO WAY HE KNOCKED HIM OUT", "caption": ""})["outcome"] == "rejected:caps")
check("caps kept when the idea was", run({"pick": 2, "title": "TKO", "caption": ""})["outcome"] == "used")
check("a link", run({"pick": 1, "title": "Watch at jason.tv", "caption": ""})["outcome"] == "rejected:link")
check("an @", run({"pick": 1, "title": "He knocked @someone out", "caption": ""})["outcome"] == "rejected:mention")
check("a hashtag", run({"pick": 1, "title": "He knocked him out #boxing", "caption": ""})["outcome"] == "rejected:hashtag")
check("an emoji", run({"pick": 1, "title": "He knocked him out 🥊", "caption": ""})["outcome"] == "rejected:emoji")
check("an invented name", run({"pick": 1, "title": "Jason knocks out Mike Tyson", "caption": ""})["outcome"] == "rejected:invented")
check("an invented number", run({"pick": 1, "title": "Knocked out in round 3", "caption": ""})["outcome"] == "rejected:invented")
check("a name from the inputs, possessive", run({"pick": 1, "title": "Round 2 was Jason's", "caption": ""})["outcome"] == "used")
check("'I' is never a name", run({"pick": 1, "title": "No way, I can't believe he knocked him out", "caption": ""})["outcome"] == "used")
check("garbled", run({"pick": 0, "title": "Uu, uu, uu, b, t", "caption": ""})["outcome"] == "rejected:garbled")
check("already another clip's title", run({"pick": 2, "title": "TKO", "caption": ""}, used={"tko"})["outcome"] == "rejected:used")
check("tidied like every title", run({"pick": 1, "title": "no way no way no way no way he knocked him out", "caption": ""})["title"]
      == titles.polish("no way no way no way no way he knocked him out"))
ctx2["transcript"] += " springfield"
fl = run({"pick": 0, "title": "Knocked out in Springfield", "caption": ""})
check("a flag the built-in title didn't have: the built-in one is kept", fl["outcome"] == "rejected:flagged", fl)
same = run({"pick": 0, "title": "Knocked out in Springfield", "caption": ""}, builtin="Springfield knockout")
check("the same flag as the built-in title: used, masked", same["outcome"] == "used" and "Springfield" not in same["title"], same)
check("not JSON in the shape asked", run("TKO")["outcome"] == "rejected:shape" and run({"title": 3})["outcome"] == "rejected:shape")
bad_pick = run({"pick": 99, "title": "TKO", "caption": ""})
check("a pick out of range counts as none", bad_pick["pick"] == 0 and bad_pick["outcome"] == "used", bad_pick)
cap = run({"pick": 1, "title": "He knocked him out", "caption": "Mike Tyson would be proud"})
check("a caption with invented names is dropped, the title still used", cap["outcome"] == "used" and cap["caption"] == "", cap)
check("the prompt version is a number to put in records", isinstance(lp.PROMPT_VERSION, int))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
