"""
Clips that are already cut (an X post clipped from a stream, a clip you saved): Home › The video is ›
Already a clip opens the whole video as one clip, ready for captions and a layout, with nothing to find;
Automatic does that for a short X video with no chat, and a long video still gets its moments found.
Runs the real job that opens a video (server.do_analyze) on a local file, with FFmpeg.

  python tests/test_precut.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- when the whole video is the clip (no FFmpeg needed)
an = types.SimpleNamespace(duration=42.0)
x = types.SimpleNamespace(info={"platform": "x"}, chat=[])
tw = types.SimpleNamespace(info={"platform": "twitch"}, chat=[])
check("Automatic: a short X video is already a clip", server._precut({}, x, an))
check("Automatic: a Twitch video isn't", not server._precut({}, tw, an))
check("Automatic: a long X video isn't", not server._precut({}, x, types.SimpleNamespace(duration=900.0)))
check("Already a clip: any video, when you say so", server._precut({"whole": True}, tw, an))
check("Find the clips: never, when you say so", not server._precut({"whole": False}, x, an))
try:
    server._precut({"whole": True}, tw, types.SimpleNamespace(duration=3 * 3600.0))
    check("a 3-hour video can't be one clip, and it says why", False)
except RuntimeError as e:
    check("a 3-hour video can't be one clip, and it says why", "too long to be one clip" in str(e), str(e))

if not shutil.which("ffmpeg"):
    print("PASSED (the rest needs FFmpeg)" if not failures else f"FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)

# ---- the real job, on a 20-second clip
tmp = tempfile.mkdtemp(prefix="vc_precut_")
clip = os.path.join(tmp, "already cut.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", clip], check=True, timeout=120)
job = server.new_job("analyze")
view = server.do_analyze(job, {"source": clip, "whole": True, "events": False, "emotes": False})
cs = view["clips"]
check("it opens as one clip", len(cs) == 1, len(cs))
c = cs[0] if cs else {}
check("the clip is the whole video", c.get("start") == 0 and abs(c.get("end", 0) - 20) < 0.2, (c.get("start"), c.get("end")))
check("marked as yours and whole, with no score", c.get("whole") is True and c.get("manual") is True and not c.get("quality"),
      {k: c.get(k) for k in ("whole", "manual", "quality")})
check("Review says what to do next", "already a clip" in view.get("pick_note", ""), view.get("pick_note"))
check("the bar reaches the end", job["progress"] >= 0.99, job["progress"])

# opened again: still the one whole clip, still with no score
import time  # noqa: E402
time.sleep(1.5)                                  # the project is saved a moment after opening
view2 = server.do_analyze(server.new_job("analyze"), {"source": clip, "whole": True, "events": False, "emotes": False})
c2 = view2["clips"][0] if view2["clips"] else {}
check("opened again: the same whole clip, no score", len(view2["clips"]) == 1 and c2.get("whole") and not c2.get("quality")
      and view2.get("restored"), (len(view2["clips"]), c2.get("quality"), view2.get("restored")))

# a pre-cut clip renders whole, cropped and stacked like any other
out = os.path.join(tmp, "out.mp4")
import engine  # noqa: E402
engine.render_clip(server.projects[view["pid"]]["video"], c["start"], c["end"], out,
                   engine.RenderOpts(layout="split", cam=(0, 0, .3, .3), cams=[(.7, 0, .3, .3)], captions=False, hw_encode=False))
p = engine.probe(out)
check("and renders whole, in the layout you pick", (p["width"], p["height"]) == (1080, 1920) and abs(p["duration"] - 20) < 0.3,
      (p["width"], p["height"], p["duration"]))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
