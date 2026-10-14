"""
Unit tests for what Clip Radar remembers between runs (3.2): lasting clip numbers,
the saved review (project.json), the render queue (queue.json) and the analysis cache.

  python tests/test_persist.py
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

import numpy as np  # noqa: E402

import engine  # noqa: E402
import persist  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="vc_persist_")

# ---------------------------------------------------------------- numbers stick to moments
rng = np.random.default_rng(3)
moments = sorted(rng.choice(np.arange(60, 7000, 90), 40, replace=False).astype(float))
book = {"next": 1, "moments": []}


def pick(peaks, jitter=0.0):
    taken, out = set(), {}
    for p in peaks:
        q = p + jitter
        n = persist.number_for(book, q - 10, q + 15, q, "hype", taken)
        taken.add(n)
        out[p] = n
    return out


first = pick(moments[:10])
check("first pick is numbered 1..10 in rank order", list(first.values()) == list(range(1, 11)), first)
more = pick(moments[:20], jitter=1.5)            # a different chat weight nudges every peak a little
check("more clips: the first ten keep their numbers", all(more[p] == first[p] for p in moments[:10]))
check("more clips: new moments get new numbers", sorted(more[p] for p in moments[10:20]) == list(range(11, 21)))
fewer = pick(moments[:5])
check("fewer clips: survivors keep their numbers", all(fewer[p] == first[p] for p in moments[:5]))
back = pick(moments[:20], jitter=-2.0)
check("moments that come back get their old numbers", back == more, (back, more))
other = pick(moments[20:22])
check("numbers are never reused", sorted(other.values()) == [21, 22], other)
n1 = persist.number_for(book, 100, 120, 110, "gem", set())
n2 = persist.number_for(book, 100, 120, 110, "gem", set())
check("gems share the counter but match only gems", n1 == n2 == 23)
taken = {first[moments[0]]}
n3 = persist.number_for(book, moments[0] - 5, moments[0] + 20, moments[0], "hype", taken)
check("a second clip on a moment that's taken gets its own number", n3 not in taken and n3 == 24)
check("overlap alone counts as the same moment",
      persist.same_moment({"start": 100, "end": 160, "peak": 110}, 120, 150, 145) is not None)
check("far-apart clips are different moments",
      persist.same_moment({"start": 100, "end": 130, "peak": 110}, 140, 170, 150) is None)

check("touched: kept/skipped/trimmed/retitled/added",
      all(persist.touched(d) for d in ({"state": "kept"}, {"state": "skipped"}, {"userTrim": True},
                                       {"userTitle": True}, {"wordsEdited": True}, {"manual": True}))
      and not persist.touched({"state": "todo", "title": "x"}))

# ---------------------------------------------------------------- project.json round trip
class An:
    chat_weight_pct = 40.0


pr = {"source": "https://www.twitch.tv/videos/1", "title": "Stream", "streamer": "someone", "count": 25,
      "length": 30, "analysis": An(), "book": book, "deep_done": True, "deep_model": "base",
      "clips": [{"id": 3, "start": 10.0, "end": 40.0, "peak": 20.0, "state": "kept", "title": "Mine", "userTitle": True,
                 "pid": "abc", "vod": "/vod/abc", "thumb": "/files/abc/thumbs/t_28.jpg", "isNew": True}],
      "gems": [{"id": 23, "start": 100, "end": 120, "peak": 110, "kind": "gem"}]}
path = os.path.join(tmp, "project.json")
persist.write_json(path, persist.project_payload(pr, "12345"))
back = persist.load_project(path, "12345")
check("project.json round trip", back and back["clips"][0]["title"] == "Mine" and back["clips"][0]["state"] == "kept"
      and {k: back["settings"][k] for k in ("count", "length", "chat_weight")} == {"count": 25, "length": 30, "chat_weight": 40.0} and back["book"]["next"] == book["next"]
      and back["gems"][0]["id"] == 23 and back["deep_done"], back and back.get("settings"))
check("per-run fields aren't saved", back and not ({"pid", "vod", "isNew"} & set(back["clips"][0])))
check("no temp files left behind", sorted(os.listdir(tmp)) == ["project.json"], os.listdir(tmp))
check("a review for a different video is set aside, not applied",
      persist.load_project(path, "999") is None and os.path.exists(os.path.join(tmp, "project.old.json"))
      and not os.path.exists(path))
with open(path, "w") as f:
    f.write('{"v": 1, "clips": [')                # cut off half-way
check("a damaged project.json is ignored", persist.load_project(path, "12345") is None)
check("review summary", persist.summary([{"state": "kept"}, {"state": "skipped"}, {}, {"kind": "gem", "state": "kept"}])
      == {"kept": 1, "skipped": 1, "todo": 1})

# ---------------------------------------------------------------- queue.json
done_file = os.path.join(tmp, "Clip.mp4")
open(done_file, "wb").write(b"x" * 10)
items = [
    {"id": "a1", "start": 1, "end": 20, "status": "done", "path": done_file, "cancel": False},
    {"id": "a2", "start": 1, "end": 20, "status": "done", "path": os.path.join(tmp, "gone.mp4")},
    {"id": "a3", "start": 1, "end": 20, "status": "rendering", "progress": 0.6, "step": "Rendering", "cancel": True},
    {"id": "a4", "start": 1, "end": 20, "status": "queued"},
    {"id": "a5", "start": 1, "end": 20, "status": "error", "error": "Drive full"},
    {"id": "a6", "start": 1, "end": 20, "status": "cancelled"},
    {"id": "a7", "start": 1, "end": 20, "status": "done", "path": done_file,
     "tiktok": {"state": "posting", "mode": "draft", "at": 5}},
]
data = json.loads(json.dumps(persist.queue_payload(items)))
check("queue.json leaves out cancelled items and the cancel flag",
      [i["id"] for i in data["items"]] == ["a1", "a2", "a3", "a4", "a5", "a7"] and "cancel" not in data["items"][0])
got = {i["id"]: i for i in persist.restore_queue(data)}
check("finished clips come back if their file is there", "a1" in got and "a2" not in got)
check("an unfinished render starts again", got.get("a3", {}).get("status") == "queued"
      and got["a3"]["progress"] == 0 and got["a3"]["cancel"] is False)
check("queued and failed renders come back", got.get("a4", {}).get("status") == "queued" and got.get("a5", {}).get("status") == "error")
check("an upload cut off by closing says so", got.get("a7", {}).get("tiktok", {}).get("state") == "error")
check("junk queue file is ignored", persist.restore_queue({"v": 99}) == [] and persist.restore_queue(None) == [])

# ---------------------------------------------------------------- analysis cache
an = engine.Analysis(120.0, np.linspace(0, 1, 121, dtype=np.float32), np.linspace(1, 0, 121, dtype=np.float32), True, True,
                     events={"laugh": np.full(121, 0.2, dtype=np.float32)}, loud=np.full(121, -20.0, dtype=np.float32),
                     fine=np.zeros(1210, dtype=np.float32), chat_stats={"chatters": 5, "messages": np.int64(9)})
npz = os.path.join(tmp, "analysis.npz")
engine._save_analysis(npz, "key1", an)
re_an = engine._load_analysis(npz, "key1")
check("analysis cache round trip", re_an is not None and re_an.duration == 120 and np.allclose(re_an.chat_z, an.chat_z)
      and np.allclose(re_an.events["laugh"], 0.2) and re_an.fine.shape == (1210,) and re_an.chat_stats["messages"] == 9)
check("a cache from another version or video isn't used", engine._load_analysis(npz, "key2") is None)
re_an.combine(60)
an.combine(60)
check("cached analysis ranks the same", np.allclose(re_an.score, an.score))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
