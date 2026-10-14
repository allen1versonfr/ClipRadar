"""
Unit tests for the Content Check review workflow (3.3): flag fingerprints, decisions,
clip statuses, the automatic fixes and their undo, settings, and the render gate.

  python tests/test_checkflow.py
"""

from __future__ import annotations

import json
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

import checkflow  # noqa: E402
import safety  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def words(text, t0):
    out, t = [], t0
    for w in text.split():
        out.append({"start": round(t, 2), "end": round(t + 0.35, 2), "word": w})
        t += 0.4
    return out


def clip(**kw):
    # a 30 s clip at 100-130 s of the stream: swearing at 108 s, a phone number at 120 s
    d = {"id": 1, "start": 100.0, "end": 130.0, "title": "this is so fucking good", "post": "", "words":
         words("ok here we go", 101) + words("what the fuck was that", 108) + words("call me at 555 123 4567 lol", 120)}
    d.update(kw)
    return d


cfg = safety.load_config()
d = clip()
r = checkflow.check(d, cfg)
cats = sorted({(f["source"], f["cat"]) for f in r["flags"]})
check("flags speech, personal info and the title", ("speech", "profanity") in cats and ("speech", "personal") in cats
      and ("title", "profanity") in cats, cats)
check("a new flag needs review", r["status"] == "needs_review" and r["open"] == len(r["flags"]), r["status"])
fps = [f["fp"] for f in r["flags"]]
check("every flag has its own fingerprint", len(set(fps)) == len(fps), fps)
swear = next(f for f in r["flags"] if f["source"] == "speech" and f["cat"] == "profanity")
phone = next(f for f in r["flags"] if f["cat"] == "personal")
title_f = next(f for f in r["flags"] if f["source"] == "title")
check("swearing can be muted, bleeped or cut", set(swear["fixes"]) == {"mute", "bleep", "cut_end", "cut_start"}, swear["fixes"])
short = clip(end=124.0)
ph2 = next(f for f in checkflow.check(short, cfg)["flags"] if f["cat"] == "personal")
check("flag near the end can't cut the start away", "cut_start" not in ph2["fixes"] and "cut_end" in ph2["fixes"], ph2["fixes"])
check("title flag can be hidden in the title", title_f["fixes"] == ["mask_title"])
check("no audio: no mute or bleep", "mute" not in checkflow.fixes_for(swear, d, has_audio=False))

# fingerprint survives trimming, changes with the words
d2 = clip(start=104.0)
fp2 = [f["fp"] for f in checkflow.check(d2, cfg)["flags"] if f["source"] == "speech" and f["cat"] == "profanity"]
check("same flag after a trim", fp2 == [swear["fp"]], (fp2, swear["fp"]))

# D: dismiss
checkflow.act(d, "dismiss", {"fp": phone["fp"]}, cfg)
r = checkflow.check(d, cfg)
check("a dismissed flag stays dismissed after a new scan", next(f for f in r["flags"] if f["fp"] == phone["fp"])["status"] == "dismissed")
check("still needs review while other flags are open", r["status"] == "needs_review")
# A: leave the rest
checkflow.act(d, "leave", {}, cfg)
r = checkflow.check(d, cfg)
check("left alone: reviewed (not 'safe')", r["status"] == "reviewed" and r["label"] == "Reviewed", r["status"])
d["words"] = [dict(w, word="frick") if w["word"] == "fuck" else w for w in d["words"]]
d["words"].append({"start": 125.0, "end": 125.3, "word": "shit"})
r = checkflow.check(d, cfg)
check("new words bring a new flag back to review", r["status"] == "needs_review" and r["open"] == 1, [(f["text"], f["status"]) for f in r["flags"]])
# rules change -> decisions looked at again
safety.save_config({"custom": ["frick"]})
cfg2 = safety.load_config()
r = checkflow.check(d, cfg2)
check("changing the rules brings left-alone flags back", r["open"] == len(r["flags"]), [(f["text"], f["status"]) for f in r["flags"]])
safety.save_config({"custom": []})
cfg = safety.load_config()

# C: fixes and undo
d = clip()
r = checkflow.check(d, cfg)
swear = next(f for f in r["flags"] if f["source"] == "speech" and f["cat"] == "profanity")
fx = checkflow.act(d, "fix", {"fp": swear["fp"], "kind": "bleep"}, cfg)
r = checkflow.check(d, cfg)
fl = next(f for f in r["flags"] if f["fp"] == swear["fp"])
check("bleep: the flag shows as corrected, with the original kept", fl["status"] == "corrected" and fl["fixed"] == "bleep"
      and fl["text"] == swear["text"])
check("bleep span covers the words (stream time)", checkflow.render_spans(d) == [(fx["span"][0], fx["span"][1], "bleep")]
      and 108.3 < fx["span"][0] < 108.9, fx["span"])
checkflow.act(d, "undo", {"fix": fx["id"]}, cfg)
r = checkflow.check(d, cfg)
check("undo brings the flag back", next(f for f in r["flags"] if f["fp"] == swear["fp"])["status"] == "open"
      and not checkflow.render_spans(d))
phone = next(f for f in r["flags"] if f["cat"] == "personal")
fx = checkflow.act(d, "fix", {"fp": phone["fp"], "kind": "cut_end"}, cfg)
r = checkflow.check(d, cfg)
check("cut: the clip ends before the flag and the flag is gone", d["end"] < 121.2 and d["userTrim"]
      and not any(f["cat"] == "personal" for f in r["flags"]), (d["end"], [f["cat"] for f in r["flags"]]))
checkflow.act(d, "undo", {"fix": fx["id"]}, cfg)
check("undo puts the original end back", d["end"] == 130.0 and not d.get("userTrim"))
r = checkflow.check(d, cfg)
title_f = next(f for f in r["flags"] if f["source"] == "title")
checkflow.act(d, "fix", {"fp": title_f["fp"], "kind": "mask_title"}, cfg)
check("hide in title", d["title"] == "this is so f****** good" and d["userTitle"], d["title"])
for f in checkflow.check(d, cfg)["flags"]:
    if f["status"] == "open":
        checkflow.act(d, "fix", {"fp": f["fp"], "kind": "mute"}, cfg)
r = checkflow.check(d, cfg)
check("everything fixed: corrected", r["status"] == "corrected", [(f["text"], f["status"]) for f in r["flags"]])
try:
    checkflow.act(d, "fix", {"fp": "nope", "kind": "mute"}, cfg)
    check("unknown flag is refused", False)
except ValueError:
    check("unknown flag is refused", True)

# E: exclude
checkflow.act(d, "exclude", {"on": True}, cfg)
checkflow.refresh(d, cfg)
check("excluded shows as excluded and can't render", d["safety"]["status"] == "excluded" and checkflow.blocks_render(d, cfg))
checkflow.act(d, "exclude", {"on": False}, cfg)
checkflow.refresh(d, cfg)
check("included again", d["safety"]["status"] == "corrected" and not checkflow.blocks_render(d, cfg))

# recheck drops leave/dismiss but keeps fixes
d = clip()
r = checkflow.check(d, cfg)
checkflow.act(d, "dismiss", {}, cfg)
check("dismiss all", checkflow.check(d, cfg)["status"] == "dismissed")
checkflow.act(d, "recheck", {}, cfg)
check("a new check shows dismissed flags again", checkflow.check(d, cfg)["status"] == "needs_review")

# remember_dismissed off: dismissals last only for this run
d = clip()
checkflow.check(d, cfg)
checkflow.act(d, "dismiss", {}, cfg)
for dec in d["check"]["decisions"].values():
    dec["session"] = "earlier-run"
cfg_nm = dict(cfg, remember_dismissed=False)
check("not remembering dismissals: back after a restart", checkflow.check(d, cfg_nm)["status"] == "needs_review")
check("remembering dismissals: still dismissed", checkflow.check(d, cfg)["status"] == "dismissed")

# block setting
d = clip()
checkflow.check(d, cfg)
check("'ask' doesn't block", checkflow.blocks_render(d, cfg) is None)
check("'block' blocks unreviewed flags", checkflow.blocks_render(d, dict(cfg, export_flagged="block")))
checkflow.act(d, "ack", {}, cfg)
checkflow.refresh(d, cfg)
check("after acknowledging it can render", checkflow.blocks_render(d, dict(cfg, export_flagged="block")) is None
      and d["check"]["log"][-1]["action"] == "ack")

# sensitivity
d = clip(title="nice", words=words("what the fuck", 105))
check("'everything' flags swearing", checkflow.check(d, cfg)["flags"])
check("'only serious' skips swearing", not checkflow.check(d, dict(cfg, sensitivity="serious"))["flags"])

# stale after an edit
d = clip()
checkflow.check(d, cfg)
d["title"] = "a different title"
checkflow.mark_changed(d)
check("an edit marks the check out of date", d["check"]["stale"])
checkflow.check(d, cfg)
check("checking again clears it", not d["check"]["stale"])

# audit trail and storage
check("audit trail keeps every decision", len(d["check"]["log"]) == 0 and json.dumps(d))
d2 = clip()
checkflow.check(d2, cfg)
checkflow.act(d2, "leave", {}, cfg)
check("audit trail records leaving it alone", d2["check"]["log"][-1]["action"] == "leave")
check("off: status says so", checkflow.check(clip(), dict(cfg, enabled=False))["status"] == "off")

# settings validation
safety.save_config({"sensitivity": "bogus", "export_flagged": "block", "notify": "later", "auto_recheck": False})
c2 = safety.load_config()
check("settings saved and checked", c2["sensitivity"] == "all" and c2["export_flagged"] == "block" and c2["notify"] == "later"
      and c2["auto_recheck"] is False, c2)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
