"""
Unit tests for export quality and frame rate, and the blur-fit layout: the stream's own frame rate
by default (up to 60), never raised above it; the quality presets; and blur-fit keeping the whole
picture inside the frame, centred, for wide, square, tall and very narrow sources. The renders use
the real FFmpeg filter graph when FFmpeg is installed.

  python tests/test_quality.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
import exports  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


R = engine.RenderOpts
# ---- frame rate
check("by default, the stream's own frame rate", engine.output_fps({"fps": 60}, R()) == 60 and engine.output_fps({"fps": 29.97}, R()) == 29.97)
check("capped at 60", engine.output_fps({"fps": 120}, R()) == 60)
check("a 30 FPS stream is never turned into 60", engine.output_fps({"fps": 30}, R(fps=60)) == 30)
check("a chosen lower rate is used", engine.output_fps({"fps": 60}, R(fps=30)) == 30)
check("an unreadable rate falls back to 30", engine.output_fps({"fps": 0}, R()) == 30 and engine.output_fps({}, R()) == 30)

# ---- quality presets
p = exports.load()
check("Balanced by default, at the stream's frame rate", p["quality"] == "balanced" and exports.settings(p)["fps"] == 0)
s = exports.settings(dict(p, quality="fast"))
check("Fast export: quick encoder, up to 30 FPS", s["speed"] == "fast" and s["fps"] == 30, s)
s = exports.settings(dict(p, quality="smooth"))
check("High frame rate asks for 60 (still never above the stream)", s["fps"] == 60, s)
s = exports.settings(dict(p, quality="high", custom={"fps": 24}))
check("a frame rate picked under Advanced wins", s["fps"] == 24 and s["speed"] == "high", s)
check("an unknown quality falls back to Balanced", exports.clean(dict(p, quality="ultra"))["quality"] == "balanced")
enc = lambda **k: engine.video_encoder(R(hw_encode=False, **k))[1]  # noqa: E731
check("Fast uses x264 veryfast, a little smaller", "veryfast" in enc(speed="fast") and enc(speed="fast")[enc(speed="fast").index("-crf") + 1] == "22")
check("High uses x264 slow, a little sharper", "slow" in enc(speed="high") and enc(speed="high")[enc(speed="high").index("-crf") + 1] == "18")
check("Balanced is as before", "medium" in enc() and enc()[enc().index("-crf") + 1] == "20")
check("the description names the quality and frame rate", "Balanced" in exports.describe(exports.settings(p))
      and "the stream's frame rate" in exports.describe(exports.settings(p)), exports.describe(exports.settings(p)))

# ---- blur-fit geometry (filter text)
def blur(w, h, aspect="9:16"):
    f, _ = engine.build_filter({"width": w, "height": h, "fps": 30}, R(layout="blur", aspect=aspect))
    return f
f = blur(1080, 1920)
check("a tall source fits the height, centred, not cut off", "scale=1080:1920" in f and "overlay=(W-w)/2:(H-h)/2" in f, f)
f = blur(720, 1600)
check("a very narrow source is fitted by height and centred", "scale=864:1920" in f, f)
check("the blurred background always covers the frame", "force_original_aspect_ratio=increase,crop=270:480" in f, f)
f = blur(1920, 1080)
check("a wide source fills the width", "scale=1080:608" in f, f)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "json", path], capture_output=True, text=True).stdout
    st = json.loads(out)["streams"][0]
    a, b = st["r_frame_rate"].split("/")
    return st["width"], st["height"], round(int(a) / int(b), 2)


if shutil.which("ffmpeg") and shutil.which("ffprobe"):
    tmp = tempfile.mkdtemp(prefix="vc_q_")
    for (w, h, fps) in ((1280, 720, 60), (720, 720, 30), (540, 960, 30), (360, 800, 25)):
        src = os.path.join(tmp, f"src_{w}x{h}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-shortest", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
        out = os.path.join(tmp, f"out_{w}x{h}.mp4")
        try:
            engine.render_clip(src, 0.0, 2.0, out, R(layout="blur", captions=False, hw_encode=False, speed="fast"),
                               title="", log=lambda m: None)
            got = probe(out)
            check(f"blur-fit renders a {w}x{h} {fps} FPS source at 1080x1920, {fps} FPS", got == (1080, 1920, fps), got)
        except Exception as e:
            check(f"blur-fit renders a {w}x{h} {fps} FPS source at 1080x1920, {fps} FPS", False, str(e)[-300:])
    out = os.path.join(tmp, "smooth.mp4")
    engine.render_clip(os.path.join(tmp, "src_720x720.mp4"), 0.0, 2.0, out,
                       R(layout="full", captions=False, hw_encode=False, fps=60), title="", log=lambda m: None)
    check("asking 60 FPS of a 30 FPS stream renders 30, not doubled frames", probe(out)[2] == 30, probe(out))
else:
    print("[skip] no ffmpeg here: renders not checked")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
