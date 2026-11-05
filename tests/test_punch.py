"""
Punch-in (4.45, Layout › Punch-in): a short push toward the subject on a clip's biggest moments.

  - the beats come from the clip's own score peaks, apart from each other, never at its very edges
  - a flat clip gets none
  - off, or no beats: the filter is exactly what it was
  - rendered (needs FFmpeg): every layout works, the clip keeps its length and frame count, and the
    push is centred (it zoomed from the top-left corner with scale + crop)

  python tests/test_punch.py
"""

from __future__ import annotations

import os
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

import numpy as np  # noqa: E402

import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


class An:
    def __init__(self, score):
        self.score = np.asarray(score, dtype=np.float32)


# ---------------------------------------------------------------- choosing the beats
sc = np.zeros(300, np.float32)
for t, v in ((105, 6.0), (107, 5.0), (120, 4.0), (140, 7.0), (101, 3.0)):
    sc[t] = v
beats = engine.punch_beats(An(sc), 100, 150)
check("the clip's peaks, in order, as seconds into it", beats == [5.0, 20.0, 40.0], beats)
check("two peaks closer than the gap: only the stronger", 7.0 not in beats)
check("never in the first moments of the clip", 1.0 not in beats)
check("at most four", len(engine.punch_beats(An(np.tile([0, 0, 0, 0, 0, 9], 60)), 0, 300)) == engine.PUNCH_MAX)
check("a flat clip gets none", engine.punch_beats(An(np.ones(300)), 0, 60) == [])
check("no analysis: none", engine.punch_beats(None, 0, 60) == [])

# ---------------------------------------------------------------- the filter
info = {"width": 1280, "height": 720, "fps": 30.0, "duration": 12.0, "has_audio": True}
plain = engine.build_filter(info, engine.RenderOpts(layout="full", track="off"))[0]
check("off: the layout's filter is unchanged",
      engine.build_filter(info, engine.RenderOpts(layout="full", track="off", punch="off", punch_at=[3.0]))[0] == plain)
check("on but no beats: unchanged",
      engine.build_filter(info, engine.RenderOpts(layout="full", track="off", punch="strong", punch_at=[]))[0] == plain)
on = engine.build_filter(info, engine.RenderOpts(layout="full", track="off", punch="strong", punch_at=[3.0]))[0]
check("on: a zoom on the beat", "zoompan" in on and "0.14" in on, on[:200])
check("the frame rate is set before it (zoompan numbers frames at its own rate)", on.index(",fps=") < on.index("zoompan"))
split = engine.build_filter(info, engine.RenderOpts(layout="split", punch="subtle", punch_at=[3.0]))[0]
check("Cam + game: it pushes in on the facecam, not the game", "[cam]null" in split and "[camp][game]vstack" in split, split)

# ---------------------------------------------------------------- rendered
ff = engine.which("ffmpeg")
if ff:
    d = tempfile.mkdtemp(prefix="vc_punch_")
    src = os.path.join(d, "bars.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "smptehdbars=size=1280x720:rate=30", "-f", "lavfi",
                    "-i", "sine", "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
                   check=True, stdin=subprocess.DEVNULL)
    def frames(path):
        return subprocess.run([engine.ffprobe(), "-v", "error", "-select_streams", "v", "-count_packets",
                               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
                              capture_output=True, text=True).stdout.strip()

    for lay in ("split", "full", "cam", "blur", "square"):
        got = {}
        try:
            for punch in ("off", "strong"):
                out = os.path.join(d, f"{lay}.mp4" if punch == "strong" else f"{lay}_off.mp4")
                engine.render_clip(src, 0, 8, out, engine.RenderOpts(layout=lay, track="off", hw_encode=False, captions=False,
                                                                      punch=punch, punch_at=[3.0]), words=[])
                p = engine.probe(out)
                got[punch] = (round(p["duration"], 1), p["width"], p["height"], frames(out))
            check(f"{lay}: renders, with the same length, size and frame count as without it",
                  got["strong"] == got["off"] and got["strong"][1:3] == (1080, 1920), got)
        except Exception as e:
            check(f"{lay}: renders", False, str(e)[-300:])

    def edges(t):
        # one frame at 270x480 as raw RGB, the colour bars' edges along one row
        raw = subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", os.path.join(d, "full.mp4"), "-frames:v", "1",
                              "-vf", "scale=270:480", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout
        row = np.frombuffer(raw, np.uint8).reshape(480, 270, 3)[100].astype(int)
        return [x for x in range(1, 270) if int(np.abs(row[x] - row[x - 1]).sum()) > 60]
    before, during, after = edges(1), edges(3.5), edges(6.5)
    check("pushed in on the beat: the bars spread out from the centre",
          during and before and during[0] < before[0] and during[-1] > before[-1], (before, during))
    check("and back out after it", after == before, (before, after))
else:
    print("[skip] no FFmpeg: rendering isn't checked")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
