"""
Unit tests for Settings › Clip ranking › What it learned: it shows the stored decisions as facts,
says plainly when there isn't enough data, reports what the fitted model shifts only once there is
one, keeps the built-in rules separate, and forgetting empties it.

  python tests/test_ranking_explain.py
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

import learning  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def feat(laugh, audio):
    return {"chat": 2.0, "audio": audio, "laugh": laugh, "scream": 0, "cheer": 0, "speech": 0.5,
            "length": 0.5, "clipreq": 0, "pos": 0.5, "emotes": 1.0}


x = learning.explain("newstreamer")
check("nothing stored: says so", x["status"] == "not_enough" and x["facts"]["decisions"] == 0, x["status_text"])
check("and claims nothing learned", x["learned"] == [] and x["compare"] == [])
check("the built-in rules are listed separately", len(x["rules"]) >= 4)
check("never calls it AI", "isn't an ai" in x["method"].lower() and "logistic regression" in x["method"])

for i in range(3):
    learning.record("streamy", f"k{i}", feat(0.9, 1.0), "keep")
x = learning.explain("streamy")
check("a few keeps but no skips: still not enough, and says what's missing",
      x["status"] == "not_enough" and "skipped" in x["status_text"] and "5 more decisions" in x["status_text"], x["status_text"])
check("the facts are there already", x["facts"]["kept"] == 3 and x["facts"]["skipped"] == 0)

for i in range(3, 12):
    learning.record("streamy", f"k{i}", feat(0.9, 1.0), "keep")
for i in range(12):
    learning.record("streamy", f"s{i}", feat(0.0, 4.0), "skip")
learning.record("streamy", "p1", feat(0.95, 1.2), "post")
learning.record_reason("streamy", "noise")
x = learning.explain("streamy")
check("enough decisions: tuned for this streamer", x["status"] == "tuned" and x["trust"] > 0, x["status_text"])
check("facts count keeps, skips and posts", (x["facts"]["kept"], x["facts"]["skipped"], x["facts"]["posted"]) == (13, 12, 1), x["facts"])
check("skip reasons are listed", x["facts"]["reasons"] == [{"label": "Just a loud noise", "n": 1}], x["facts"]["reasons"])
cmp = {c["key"]: c for c in x["compare"]}
check("kept clips had more laughter (a fact from the stored values)", cmp["laugh"]["says"] == "higher in clips you kept", cmp["laugh"])
check("and were quieter", cmp["audio"]["says"] == "lower in clips you kept", cmp["audio"])
check("signals that didn't differ say so", cmp["chat"]["says"] == "about the same", cmp["chat"])
lr = {l["key"]: l for l in x["learned"]}
check("the model moves laughter up", lr.get("laugh", {}).get("direction") == "up", x["learned"])
check("and loudness down", lr.get("audio", {}).get("direction") == "down", x["learned"])

x = learning.explain("otherstreamer")
check("another streamer with no decisions borrows all your picks, and says so", x["status"] == "using_all", x["status_text"])

learning.set_enabled(False)
x = learning.explain("streamy")
check("switched off: says ranking uses the rules only, and learned nothing applies", x["status"] == "off" and x["learned"] == [])
learning.set_enabled(True)

learning.forget("streamy")
x = learning.explain("streamy")
check("forgetting a streamer empties it", x["facts"]["decisions"] == 0 and x["status"] != "tuned")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
