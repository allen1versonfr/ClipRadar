"""
What Review's live preview needs to draw punch-in and a title moved off the face (4.47.1,
POST /api/preview/extras, server.preview_extras):

  - the shape: punch, punch_at (seconds into the clip), face_y, face_h, followed
  - the beats are engine.punch_beats', made clip-relative; Punch-in off gives none
  - the face is the one _framing_for gives the render; no face (or a layout that doesn't follow one) gives null
  - asked again (scrubbing, replaying), nothing is worked out again
  - over HTTP: a project that isn't open, and a bad clip

  python tests/test_preview_extras.py
"""

from __future__ import annotations

import http.client
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
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_NO_BROWSER"] = "1"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


score = np.zeros(120, dtype=np.float32)
score[[18, 30, 44]] = [3.0, 5.0, 4.0]          # three clear peaks inside a 10..50 s clip
an = engine.Analysis(duration=120.0, chat_z=np.zeros(120), audio_z=np.zeros(120), has_chat=False, has_audio=True, score=score)
pr = {"analysis": an, "video": "", "info": {"width": 1920, "height": 1080, "duration": 120.0}, "workdir": tempfile.mkdtemp()}
server.projects["pv"] = pr

calls = []
face = {"path": [(0.0, 0.5), (5.0, 0.55)], "face_y": 0.21, "face_h": 0.1, "confidence": 0.9, "reason": "Following the face"}


def fake_framing(p, a, b, ro, log=None):
    calls.append((a, b, ro.layout, ro.track))
    if engine.effective_layout(ro) != "full" or ro.track != "auto":
        return None
    return dict(face)


server._framing_for = fake_framing
server._preview_cache.clear()

o = {"layout": "full", "track": "auto", "punch": "strong", "aspect": "9:16"}
r = server.preview_extras("pv", pr, 10.0, 50.0, o)
want = engine.punch_beats(an, 10.0, 50.0)
check("the shape", set(r) == {"punch", "punch_at", "face_y", "face_h", "followed"}, sorted(r))
check("the beats are punch_beats', seconds into the clip", r["punch_at"] == want and want, (r["punch_at"], want))
check("beats inside the clip", all(0 <= t <= 40 for t in r["punch_at"]), r["punch_at"])
check("the face the render follows", (r["face_y"], r["face_h"], r["followed"]) == (0.21, 0.1, True), r)
check("json-safe", json.loads(json.dumps(r)) == r)

n = len(calls)
r2 = server.preview_extras("pv", pr, 10.0, 50.0, dict(o, caption_color="#ff0000", title_y=0.3))
check("asked again: from the cache, nothing worked out again", r2 is r and len(calls) == n, len(calls) - n)
server.preview_extras("pv", pr, 10.0, 50.004, o)
check("a hundredth of a second the same clip", len(calls) == n)
server.preview_extras("pv", pr, 11.0, 50.0, o)
check("a trimmed clip is worked out again", len(calls) == n + 1)

off = server.preview_extras("pv", pr, 10.0, 50.0, dict(o, punch="off"))
check("Punch-in off: no beats", off["punch_at"] == [] and off["punch"] == "off", off)
odd = server.preview_extras("pv", pr, 10.0, 50.0, dict(o, punch="huge"))
check("an unknown Punch-in: off", odd["punch_at"] == [] and odd["punch"] == "off", odd)
check("off still has the face", off["face_y"] == 0.21)

blur = server.preview_extras("pv", pr, 10.0, 50.0, dict(o, layout="blur"))
check("a layout that doesn't follow a face: face null", blur["face_y"] is None and blur["face_h"] is None and not blur["followed"], blur)
check("and its beats are still there", blur["punch_at"] == want)
still = server.preview_extras("pv", pr, 10.0, 50.0, dict(o, track="off"))
check("Follow face off: face null", still["face_y"] is None and not still["followed"], still)

face = {"path": [], "face_y": None, "face_h": None, "reason": "No face found"}
none = server.preview_extras("pv", pr, 12.0, 50.0, o)
check("no face found: null, not followed", none["face_y"] is None and none["face_h"] is None and not none["followed"], none)
face = {"path": [], "face_y": 0.4, "face_h": 0.12, "reason": "Held still"}
held = server.preview_extras("pv", pr, 13.0, 50.0, o)
check("a face that isn't followed: there (punch focus), not followed", held["face_y"] == 0.4 and not held["followed"], held)

flat = dict(pr, analysis=engine.Analysis(duration=120.0, chat_z=np.zeros(120), audio_z=np.zeros(120), has_chat=False,
                                          has_audio=True, score=np.ones(120, dtype=np.float32)))
check("a flat clip: no beats", server.preview_extras("pv", flat, 10.0, 50.0, o)["punch_at"] == [])
check("no analysis: no beats", server.preview_extras("pv", dict(pr, analysis=None), 10.0, 50.0, o)["punch_at"] == [])

for i in range(server.PREVIEW_CACHE_MAX + 20):
    server.preview_extras("pv", pr, 10.0, 60.0 + i, dict(o, punch="off"))
check("the cache stays bounded", len(server._preview_cache) <= server.PREVIEW_CACHE_MAX, len(server._preview_cache))

# ---------------------------------------------------------------- over HTTP
PORT = 7993
srv, url = server.start(PORT)


def post(obj):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request("POST", "/api/preview/extras", body=json.dumps(obj).encode(),
              headers={"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json"})
    resp = c.getresponse()
    data = resp.read()
    c.close()
    return resp.status, json.loads(data or b"{}")


try:
    face = {"path": [(0.0, 0.5)], "face_y": 0.3, "face_h": 0.1}
    st, body = post({"pid": "pv", "start": 20, "end": 50, "opts": o})
    check("POST answers the same", st == 200 and body.get("followed") is True and body.get("face_y") == 0.3
          and body.get("punch_at") == engine.punch_beats(an, 20.0, 50.0), (st, body))
    st, body = post({"pid": "nope", "start": 0, "end": 10, "opts": o})
    check("a project that isn't open: 404", st == 404, st)
    st, body = post({"pid": "pv", "start": "x", "end": 10})
    check("a bad clip: 400", st == 400, st)
    st, body = post({"pid": "pv", "start": 30, "end": 10})
    check("an end before the start: 400", st == 400, st)
finally:
    srv.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
