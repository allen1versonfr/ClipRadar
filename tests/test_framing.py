"""
Unit tests for 3.9 framing: finding faces, following one smoothly (no jitter, no whip pans, holding
through a moment it's hidden, falling back when unsure), picking who to follow, spotting a facecam,
the crop expression ffmpeg gets, captions moving off the face, and saved framings.

  python tests/test_framing.py
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
os.environ["VODCLIPPER_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import exports  # noqa: E402
import framing  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


FPS = framing.SAMPLE_FPS


def face_at(cx, cy=0.4, w=0.12, h=0.2, sc=0.99):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, sc)


# ---------------------------------------------------------------- the detector itself
face = np.load(os.path.join(HERE, "assets", "face96.npz"))["face"]
rng = np.random.default_rng(1)


def frame_with(x, y):
    f = rng.integers(60, 120, (framing.IN_H, framing.IN_W, 3), dtype=np.uint8)
    f[y:y + face.shape[0], x:x + face.shape[1]] = face
    return f


if framing.available():
    d = framing.detect(np.stack([frame_with(20, 60), frame_with(200, 60), rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)]))
    check("finds a face", len(d[0]) == 1 and d[0][0][4] > 0.9, d[0])
    cx0, cx1 = (d[0][0][0] + d[0][0][2]) / 2 if d[0] else 0, (d[1][0][0] + d[1][0][2]) / 2 if d[1] else 0
    check("in the right place", 0.1 < cx0 < 0.3 and 0.65 < cx1 < 0.85, (cx0, cx1))
    check("nothing in noise", d[2] == [], d[2])
    big = np.zeros((1, framing.IN_H * 2, framing.IN_W * 2, 3), dtype=np.uint8) + 90
    big[0, 30:30 + face.shape[0] // 2, 560:560 + face.shape[1] // 2] = face[::2, ::2]   # a small face, top right
    fine = framing.detect_fine(big)
    check("small faces found by looking at each quarter", len(fine[0]) == 1 and fine[0][0][0] > 0.8, fine[0])
else:
    check("face model is bundled", False, "src/models/face missing")

# ---------------------------------------------------------------- following
# the streamer walks from left to right over 10 s, then stands still
dets = [[face_at(0.2 + min(i / FPS, 10) * 0.05)] for i in range(int(20 * FPS))]
r = framing.follow(dets, FPS, crop_frac=0.32)
xs = [x for _, x in r["path"]]
check("follows the face", r["path"] and abs(xs[-1] - 0.7) < 0.05 and xs[0] < 0.3, (xs[0], xs[-1]))
speed = max(abs(b - a) / (tb - ta) for (ta, a), (tb, b) in zip(r["path"], r["path"][1:]))
check("never pans faster than the limit", speed <= framing.MAX_SPEED + 1e-6, speed)
check("high confidence", r["confidence"] > 0.8, r["confidence"])

# jitter: a face that wobbles a little doesn't move the crop
wob = [[face_at(0.5 + 0.015 * np.sin(i))] for i in range(80)]
r = framing.follow(wob, FPS, crop_frac=0.32)
xs = [x for _, x in r["path"]]
check("small wobbles don't move the crop", max(xs) - min(xs) < 0.01, (min(xs), max(xs)))

# a false detection for one frame doesn't yank the crop
spike = [[face_at(0.3)] for _ in range(40)]
spike[20] = [face_at(0.9, sc=0.8)]
r = framing.follow(spike, FPS, crop_frac=0.32)
check("a one-frame glitch is ignored", max(x for _, x in r["path"]) < 0.33, r["path"])

# hidden for 1.5 s: holds where it was
hid = [[face_at(0.3)] for _ in range(20)] + [[] for _ in range(6)] + [[face_at(0.3)] for _ in range(20)]
r = framing.follow(hid, FPS, crop_frac=0.32)
check("holds still while the face is hidden", all(abs(x - 0.3) < 0.02 for _, x in r["path"]), r["path"])

# gone for good: eases back to your focus point, not a jump
gone = [[face_at(0.25)] for _ in range(30)] + [[] for _ in range(40)]
r = framing.follow(gone, FPS, fallback=0.6, crop_frac=0.32)
xs = [x for _, x in r["path"]]
check("face gone: eases back to the focus point", abs(xs[-1] - 0.6) < 0.05 and max(abs(b - a) for a, b in zip(xs, xs[1:])) < 0.12, xs[-4:])

# low confidence: no path, crop stays put
few = [[face_at(0.5)] if i % 6 == 0 else [] for i in range(60)]
r = framing.follow(few, FPS, crop_frac=0.32)
check("face seen too rarely: crop stays put", r["path"] == [] and "stays put" in r["reason"], r["reason"])
check("no faces: says so", framing.follow([[] for _ in range(20)], FPS)["reason"] == "No face found")

# a facecam face (small, never moves) is not followed
cam = [[(0.86, 0.05, 0.92, 0.15, 0.99)] for _ in range(40)]
r = framing.follow(cam, FPS, crop_frac=0.32)
check("doesn't crop to a facecam in the corner", r["path"] == [] and "facecam" in r["reason"], r["reason"])

# crop never goes past the edge
edge = [[face_at(0.02)] for _ in range(20)]
r = framing.follow(edge, FPS, crop_frac=0.32)
check("crop stays inside the frame", all(x >= 0.16 - 1e-6 for _, x in r["path"]), r["path"][:2])

# who to follow
two = [[face_at(0.2, w=0.08), face_at(0.75, w=0.15)] for _ in range(20)]
pick = lambda t, pt=(0.5, 0.4): framing.follow(two, FPS, target=t, point=pt, crop_frac=0.32)["path"][-1][1]  # noqa: E731
check("automatic: the biggest face", abs(pick("auto") - 0.75) < 0.05)
check("left-most and right-most person", abs(pick("left") - 0.2) < 0.05 and abs(pick("right") - 0.75) < 0.05, (pick("left"), pick("right")))
check("nearest to the focus point", abs(pick("point", (0.1, 0.4)) - 0.2) < 0.05)

# ---------------------------------------------------------------- ffmpeg expression and paths
e = framing.x_expr([(0, 0.5), (1, 0.6), (2, 0.6)], 1920, 608)
check("crop expression moves between keyframes", e.startswith("'gte(t,0)*lt(t,1)*(656.0+192.000*(t-0))") and "gte(t,2)*848.0" in e, e)
check("one keyframe: a fixed crop", framing.x_expr([(0, 0.5)], 1920, 608) == "656")
p = [(0, 0.2), (2, 0.4), (4, 0.4)]
check("position at a time", abs(framing.path_at(p, 1) - 0.3) < 1e-9 and framing.path_at(p, 9) == 0.4)
sh = framing.shift(p, 1.0, 2.0)
check("part of a path, from 0", sh[0] == (0.0, 0.3) and sh[-1][0] == 2.0 and abs(sh[-1][1] - 0.4) < 1e-9, sh)
o = engine.RenderOpts(layout="full")
f, _ = engine.build_filter({"width": 1920, "height": 1080}, o, [(0, 0.3), (1, 0.4)])
check("the render crops along the path", "crop=608:1080:'gte(t,0)" in f, f[:80])
f, _ = engine.build_filter({"width": 1920, "height": 1080}, engine.RenderOpts(layout="cam", cam=(0.8, 0.0, 0.2, 0.35)))
check("facecam only fills the frame with the camera", f.startswith("[0:v]crop=384:378:1536:0,scale=1080:1920"), f[:60])
check("crop width for the shape", abs(framing.crop_frac({"width": 1920, "height": 1080}, o) - 0.3164) < 0.001)

# ---------------------------------------------------------------- facecam and captions
cam_dets = [[(0.86 + rng.normal(0, 0.003), 0.05, 0.92, 0.15, 0.99)] for _ in range(20)]
fc = framing.find_facecam(cam_dets)
check("spots a facecam and boxes head and shoulders", fc and fc["cam"][0] + fc["cam"][2] > 0.99 and fc["cam"][1] == 0.0
      and fc["cam"][2] < 0.3, fc)
check("a moving face isn't a facecam", framing.find_facecam([[face_at(0.1 + i * 0.04)] for i in range(20)]) is None)
check("captions move off the face", framing.caption_y(0.7, 0.2, 1920, 1344) != 1344)
check("captions stay put when the face is elsewhere", framing.caption_y(0.2, 0.15, 1920, 1344) == 1344)
_, cy = engine.build_filter({"width": 1920, "height": 1080}, o, [(0, 0.5)], {"face_y": 0.68, "face_h": 0.25})
top, bot = (0.68 - 0.25 * 0.7) * 1920, (0.68 + 0.25 * 0.9) * 1920
check("render captions avoid the face", cy <= top - 140 or cy >= bot + 140, (cy, top, bot))

# ---------------------------------------------------------------- saved framings
saved = exports.clean({"frame_presets": [{"name": "Cam right", "opts": {"layout": "split", "cam": [0.8, 0, 0.2, 0.35], "bad": 1,
                                                                        "track": "off"}}, {"name": "x", "opts": {"cam": [1, 2]}}]})
check("saved framings keep only framing settings", saved["frame_presets"][0] == {"name": "Cam right", "opts": {
    "layout": "split", "cam": [0.8, 0.0, 0.2, 0.35], "track": "off"}} and saved["frame_presets"][1]["opts"] == {}, saved["frame_presets"])

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
