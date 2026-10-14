"""Unit tests for the content check: python tests/test_safety.py"""
import os
import sys
import tempfile

os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="vc_safety_"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import safety  # noqa: E402

CFG = dict(safety.DEFAULTS, custom=["Springfield", "john smith"])
fails = []


def words(text, step=0.4):
    return [{"start": i * step, "end": i * step + 0.3, "word": w} for i, w in enumerate(text.split())]


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def cats(res):
    return sorted({f["cat"] for f in res["flags"]})


r = safety.scan(words("okay chat let's go that was clean"), cfg=CFG)
check("clean speech is clean", r["level"] == "clean" and not r["flags"], r)
r = safety.scan(words("what the FUUUCK was that"), cfg=CFG)
check("stretched swear word", cats(r) == ["profanity"] and r["level"] == "low", r["flags"])
r = safety.scan(words("bro just kys already"), cfg=CFG)
check("telling someone to self-harm is high", cats(r) == ["harassment"] and r["level"] == "high")
r = safety.scan(words("you should kill yourself honestly"), cfg=CFG)
check("phrase across words", cats(r) == ["harassment"] and r["flags"][0]["first"] == 2 and r["flags"][0]["last"] == 3)
r = safety.scan(words("call me at 555 123 4567 tonight"), cfg=CFG)
check("phone number", cats(r) == ["personal"] and r["level"] == "high", r["flags"])
r = safety.scan(words("I live at 42 Evergreen Terrace lol"), cfg=CFG)
check("street address", cats(r) == ["personal"], r["flags"])
r = safety.scan(words("email me at clipper at gmail dot com"), cfg=CFG)
check("spoken email", cats(r) == ["personal"], r["flags"])
r = safety.scan(words("greetings from springfield everyone"), cfg=CFG)
check("custom word", cats(r) == ["custom"] and r["level"] == "high")
r = safety.scan(words("hey John Smith what's up"), cfg=CFG)
check("custom phrase", cats(r) == ["custom"])
r = safety.scan(words("classic assassin dickens scunthorpe"), cfg=CFG)
check("no false hits inside other words", not r["flags"], r["flags"])
r = safety.scan(words("sh1t that was c0caine"), cfg=CFG)
check("leetspeak", cats(r) == ["drugs", "profanity"], r["flags"])
r = safety.scan([], title="Clean title", post="Funny moment #twitch #fyp", cfg=CFG)
check("clean title and post", r["level"] == "clean")
r = safety.scan([], title="This bitch is insane", post="#casino #fyp", cfg=CFG)
check("title and post are scanned", sorted((f["source"], f["cat"]) for f in r["flags"]) ==
      [("post", "gambling"), ("title", "profanity")], r["flags"])
off = dict(CFG, categories=dict(CFG["categories"], profanity=False))
r = safety.scan(words("holy shit"), cfg=off)
check("a category can be switched off", not r["flags"])
r = safety.scan(words("holy shit"), cfg=dict(CFG, enabled=False))
check("scanner can be switched off", r == {"level": "clean", "flags": [], "enabled": False})

w = words("what the fuck man")
r = safety.scan(w, cfg=CFG)
nw, spans, mode = safety.censored(w, r, dict(CFG, censor="bleep"))
check("censor masks the word", [x["word"] for x in nw] == ["what", "the", "f***", "man"], nw)
check("censor gives the bleep span", mode == "bleep" and len(spans) == 1 and abs(spans[0][0] - 0.75) < 1e-6, spans)
nw, spans, mode = safety.censored(w, r, dict(CFG, censor="mask"))
check("mask mode only touches text", spans == [] and nw[2]["word"] == "f***")
nw, spans, mode = safety.censored(w, r, dict(CFG, censor="off"))
check("censor off leaves words", nw == w and spans == [])
check("mask_text title", safety.mask_text("Bro said FUCK this", dict(CFG, censor="bleep")) == "Bro said F*** this")
check("mask keeps punctuation", safety.mask("fucking!") == "f******!")

print(f"\n{'FAILED' if fails else 'PASSED'}: {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
