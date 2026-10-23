"""
TikTok covers aren't black, whichever encoder rendered the clip. TikTok takes the frame at 0:00 as
the cover; 4.15 still gave black covers on a PC that renders on the GPU (AMD), so every render now
checks its own first frame. Here a stand-in GPU encoder makes the first frame black (the symptom),
and the render must notice, render that clip again on the CPU, stop using the GPU encoder for the
session, and say so in the attempt's details. A GPU encoder that does its job is left alone, and a
clip that really opens on black isn't "fixed".

  python tests/test_black_cover.py
"""

from __future__ import annotations

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

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


if not shutil.which("ffmpeg"):
    print("SKIPPED: FFmpeg isn't installed")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="vc_cover_")
SRC = os.path.join(TMP, "vod.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", SRC], check=True)
DARK = os.path.join(TMP, "dark.mp4")                  # a stream that really is black at this point
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=1280x720:rate=30:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", DARK], check=True)

REAL_POPEN = subprocess.Popen
FAKE = "h264_fakegpu"


class StandInGpu(REAL_POPEN):
    """FFmpeg as asked, except a render 'on the GPU' (the made-up encoder name) is done with x264
    and, when black_first is set, comes out with its first frame black, like the bad GPU renders."""
    black_first = True

    def __init__(self, cmd, *a, **kw):
        self._fake = isinstance(cmd, list) and FAKE in cmd
        if self._fake:
            cmd = ["libx264" if c == FAKE else c for c in cmd]
            self._out = os.path.join(kw.get("cwd") or ".", cmd[-1])
        super().__init__(cmd, *a, **kw)

    def wait(self, timeout=None):
        rc = super().wait(timeout)
        if self._fake and rc == 0 and StandInGpu.black_first and not getattr(self, "_blacked", False):
            self._blacked = True
            fixed = self._out + ".black.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", self._out, "-vf", "fade=t=in:s=0:n=1",
                            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-movflags", "+faststart+negative_cts_offsets", fixed], check=True)
            os.replace(fixed, self._out)
        return rc


def gpu(**kw):
    engine._encoder_cache.clear()
    engine._encoder_cache["hw"] = (FAKE, ["-c:v", FAKE, "-preset", "ultrafast"])
    return engine.RenderOpts(layout="split", captions=False, hw_encode=True, **kw)


def render(name, src, o, length=4.0):
    out = os.path.join(TMP, name)
    diag, logs = {}, []
    engine.render_clip(src, 2.0, 2.0 + length, out, o, title="", log=logs.append, diag=diag)
    return out, diag, logs


subprocess.Popen = StandInGpu
try:
    # 1. the GPU encoder blacks the first frame: caught, rendered again on the CPU
    StandInGpu.black_first = True
    out, diag, logs = render("gpu_black.mp4", SRC, gpu())
    check("the black first frame is caught", any("black first frame" in m for m in logs), logs)
    check("the clip is rendered again on the CPU", diag.get("encoder") == "libx264"
          and "black first frame" in diag.get("fallback", ""), diag)
    check("and its first frame isn't black any more", not engine.black_start(out, 4.0)
          and (engine._frame_luma(out) or 0) > 40 and (engine._frame_luma(out, ignore_editlist=True) or 0) > 40,
          (engine._frame_luma(out), engine._frame_luma(out, ignore_editlist=True)))
    check("the attempt says so", diag.get("black_start") is False, diag)
    check("the GPU encoder isn't used again this session", engine._encoder_cache.get("hw") is None
          and engine._encoder_cache.get("black_start") == FAKE, engine._encoder_cache)
    out2, diag2, _ = render("after.mp4", SRC, engine.RenderOpts(layout="split", captions=False, hw_encode=True))
    check("the next clip goes straight to the CPU", diag2.get("encoder") == "libx264" and not diag2.get("fallback"), diag2)

    # 2. a GPU encoder that does its job is left alone
    StandInGpu.black_first = False
    out, diag, logs = render("gpu_ok.mp4", SRC, gpu())
    check("a good GPU render stays on the GPU", diag.get("encoder") == FAKE and not diag.get("fallback")
          and not any("black" in m for m in logs), (diag, logs))
    check("with a picture at 0:00", (engine._frame_luma(out) or 0) > 40)

    # 3. a clip that really opens on black is not "fixed"
    StandInGpu.black_first = True
    out, diag, logs = render("dark.mp4", DARK, gpu(), length=3.0)
    check("a clip that is black anyway isn't rendered again", diag.get("encoder") == FAKE
          and not any("black first frame" in m for m in logs), (diag, logs))
finally:
    subprocess.Popen = REAL_POPEN

# 4. the check itself, on files made directly
plain = os.path.join(TMP, "plain.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-t", "3", "-i", SRC, "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", plain], check=True)
blacked = os.path.join(TMP, "blacked.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", plain, "-vf", "fade=t=in:s=0:n=1", "-c:v", "libx264", "-preset",
                "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", blacked], check=True)
check("a normal clip isn't flagged", not engine.black_start(plain, 3.0))
check("a clip opening on one black frame is flagged", engine.black_start(blacked, 3.0))
check("an unreadable file isn't flagged (and doesn't crash)", not engine.black_start(os.path.join(TMP, "nope.mp4"), 3.0))

shutil.rmtree(TMP, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
