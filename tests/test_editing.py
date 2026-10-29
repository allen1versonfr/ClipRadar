"""
Unit tests for 3.4 editing: output shapes (vertical, square, wide) in the render filters and
caption layout, and reading a video's frame rate.

  python tests/test_editing.py
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
os.environ.setdefault("CLIPRADAR_WORK", tempfile.mkdtemp(prefix="vc_test_"))   # server is imported below
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


info = {"width": 1920, "height": 1080, "has_audio": True, "duration": 60}
for aspect, (w, h) in engine.SHAPES.items():
    for layout in ("split", "full", "blur"):
        o = engine.RenderOpts(layout=layout, aspect=aspect, cam=(0.0, 0.0, 0.25, 0.25))
        f, cap_y = engine.build_filter(info, o)
        eff = engine.effective_layout(o)
        sizes = re.findall(r"scale=(\d+):(\d+)", f)
        if eff == "split":
            top = int(re.search(r"scale=(\d+):(\d+):force_original", f).group(2))
            bottom = [s for s in sizes if int(s[0]) == w and int(s[1]) != top]
            ok = bottom and top + int(bottom[-1][1]) == h
        elif eff == "full":
            ok = (str(w), str(h)) in sizes
        else:
            ok = f"scale={w}:{h}" in f
        check(f"{aspect} {layout}: frame is {w}x{h}", ok, f[:200])
        check(f"{aspect} {layout}: captions inside the frame", 0 < cap_y < h, cap_y)
    ass = engine.build_ass([{"start": 0, "end": 0.4, "word": "hello"}], engine.RenderOpts(aspect=aspect), h * 0.7, 3.0, "A title")
    check(f"{aspect}: caption canvas {w}x{h}", f"PlayResX: {w}" in ass and f"PlayResY: {h}" in ass)
check("wide cam + game uses the whole frame", engine.effective_layout(engine.RenderOpts(layout="split", aspect="16:9")) == "full")
check("unknown shape falls back to vertical", engine.out_size(engine.RenderOpts(aspect="4:3")) == (1080, 1920))
check("frame rate from avg_frame_rate", engine._fps({"avg_frame_rate": "60000/1001"}) == 59.94)
check("frame rate falls back to r_frame_rate", engine._fps({"avg_frame_rate": "0/0", "r_frame_rate": "30/1"}) == 30.0)
check("unreadable frame rate is 30", engine._fps({}) == 30.0)

# the editor timeline's data (/api/strip): NaN and infinities (silence's -inf loudness, a model's NaN) would
# make the whole answer unreadable JSON in the browser, so every number sent is finite
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402
import server  # noqa: E402

_bad = np.array([1.0, np.nan, -np.inf, np.inf, 2.0] * 4, dtype=np.float32)
_an = SimpleNamespace(duration=20.0, fine=None, loud=_bad, has_events=True, event_level=lambda k: _bad,
                      has_chat=True, chat_z=_bad, emote_z=_bad)
_st = server.strip_data({"analysis": _an}, 0, 20)
try:
    json.loads(json.dumps(_st, allow_nan=False))
    _ok = True
except ValueError:
    _ok = False
check("the timeline's data is finite JSON, even with NaN or infinities in a signal", _ok, _st)
check("a missing loudness reads as the quietest", _st["wave"][:5] == [1.0, 1.0, 1.0, 1.0, 2.0], _st["wave"][:5])

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
