"""
A title that hasn't been moved isn't put on the streamer's face (4.45, engine.title_top).

  python tests/test_title_place.py
"""

from __future__ import annotations

import os
import re
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

import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


H = 1920
o = engine.RenderOpts(layout="full")
args = dict(H=H, fs=80, wrap=820, pad=20, caption_y=H * 0.78)
start = int(0.05 * H) + 20


def top(face, title="He did not just do that", opts=o):
    return engine.title_top(title, opts, face=face, ty=start, **args)


check("no face: where it always was", top(None) == start)
check("a face lower down, clear of the title: unchanged", top({"face_y": 0.5, "face_h": 0.15}) == start)
high = {"face_y": 0.12, "face_h": 0.1}         # a face near the top of the frame: the title would sit on it
moved = top(high)
face_top, face_bot = (0.12 - 0.1 * 0.9) * H, (0.12 + 0.1 * 0.6) * H
check("a face under the title: the title moves below it (no room above)", moved >= face_bot, (moved, face_bot))
check("and stays clear of the captions", moved + 2 * 80 < H * 0.78 - 100, moved)
lower = {"face_y": 0.21, "face_h": 0.1}       # under a one-line title, with room above it
check("a face just under a short title: the title goes above it", top(lower, "Short one") < (0.21 - 0.1 * 0.9) * H - 24,
      top(lower, "Short one"))
check("a long title with no room above: below the face instead", top({"face_y": 0.3, "face_h": 0.12},
      "A much longer title that wraps onto three whole lines of text here for sure") > (0.3 + 0.12 * 0.6) * H)
dragged = engine.RenderOpts(layout="full", title_y=0.1)
check("a title you dragged stays exactly where you put it", top(high, opts=dragged) == start)
check("a face that fills the frame: left where it was (nowhere better)",
      top({"face_y": 0.45, "face_h": 0.8}) == start)

ass = engine.build_ass([], o, H * 0.78, 10.0, "He did not just do that", high)
y = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
check("the rendered title uses it", y == moved, (y, moved))
ass2 = engine.build_ass([], o, H * 0.78, 10.0, "He did not just do that")
ass3 = engine.build_ass([], o, H * 0.78, 10.0, "He did not just do that", {"face_y": 0.6, "face_h": 0.1})
check("a face clear of the title: the same file as without one", ass2 == ass3)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
