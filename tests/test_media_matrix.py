"""
Real-media render matrix: every layout and export format against the kinds of source video people
actually feed the app (portrait phone video, odd sizes, no sound, variable frame rate, rotated phone
video, HEVC and VP9, a resolution change part-way, a damaged file), rendered with the real FFmpeg
filter graph. Each output is checked as a viewer would see it: it opens, it has the right size and
length, it has sound exactly when the source does, its first frame is at 0:00, and it decodes to the end.

A source that can't be rendered must fail quickly with a plain-words error, never hang or leave a
file behind.

  python tests/test_media_matrix.py            (about 2-4 minutes on 4 cores)
  CLIPRADAR_MATRIX=quick python tests/test_media_matrix.py   (one layout per source)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

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


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    print("SKIPPED: FFmpeg isn't installed")
    sys.exit(0)

QUICK = os.environ.get("CLIPRADAR_MATRIX") == "quick"
TMP = tempfile.mkdtemp(prefix="vc_matrix_")
LEN = 3.0


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args], check=True, timeout=120)


def src(name, *args):
    path = os.path.join(TMP, name)
    ff(*args, path)
    return path


tone = ["-f", "lavfi", "-i", "sine=frequency=330:duration=8"]
SOURCES = {
    "landscape 1080p60": src("land.mp4", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=8", *tone,
                             "-c:v", "libx264", "-preset", "ultrafast", "-g", "120", "-pix_fmt", "yuv420p", "-c:a", "aac"),
    "portrait 1080x1920": src("port.mp4", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=8", *tone,
                              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
    "odd size 853x479": src("odd.mkv", "-f", "lavfi", "-i", "testsrc2=size=853x479:rate=30:duration=8", *tone,
                            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-c:a", "aac"),
    "tiny 160x90": src("tiny.mp4", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=8", *tone,
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"),
    "no audio": src("mute.mp4", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"),
    # frames at uneven times, like a phone recording or OBS under load
    "variable frame rate": src("vfr.mkv", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60:duration=8", *tone,
                               "-vf", "select='not(mod(n\\,3))+not(mod(n\\,7))',setpts=N/(37*TB)+0.002*sin(N)/TB",
                               "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                               "-c:a", "aac"),
    "hevc 10-bit": src("hevc.mp4", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", *tone,
                       "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error",
                       "-tag:v", "hvc1", "-c:a", "aac"),
    "vp9 webm": src("vp9.webm", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", *tone,
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1M", "-c:a", "libopus"),
}
# a phone video stored sideways with a rotation flag (the picture is 720x1280 once turned)
rot_raw = src("rot_raw.mp4", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", *tone,
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac")
rot = os.path.join(TMP, "rot.mp4")
ff("-display_rotation:v:0", "90", "-i", rot_raw, "-c", "copy", rot)
SOURCES["rotated phone video"] = rot
# a live recording that crosses a Twitch ad break: the size changes part-way
a = src("seg_a.ts", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=4", "-f", "lavfi", "-i",
        "sine=frequency=330:duration=4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac")
b = src("seg_b.ts", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4", "-f", "lavfi", "-i",
        "sine=frequency=330:duration=4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac")
mixed = os.path.join(TMP, "mixed.ts")
with open(mixed, "wb") as f:
    for p in (a, b):
        f.write(open(p, "rb").read())
SOURCES["size change mid-clip"] = mixed


def ffprobe(path, *args):
    p = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", path], capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout or "{}")


def verify(out, want_size, has_audio, length):
    """Problems a viewer would notice in a rendered file, or [] when it's fine."""
    bad = []
    if not os.path.isfile(out) or os.path.getsize(out) < 1000:
        return ["no file"]
    info = ffprobe(out, "-show_streams", "-show_format")
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    aus = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if len(vs) != 1:
        return [f"{len(vs)} video streams"]
    v = vs[0]
    if (v["width"], v["height"]) != want_size:
        bad.append(f"size {v['width']}x{v['height']}, wanted {want_size[0]}x{want_size[1]}")
    if v.get("pix_fmt") != "yuv420p":
        bad.append(f"pixel format {v.get('pix_fmt')} (phones need yuv420p)")
    if bool(aus) != has_audio:
        bad.append("sound missing" if has_audio else "silent track added to a source with no sound")
    dur = float(info.get("format", {}).get("duration") or 0)
    if abs(dur - length) > 0.35:
        bad.append(f"length {dur:.2f}s, wanted {length:.2f}s")
    first = ffprobe(out, "-ignore_editlist", "1", "-select_streams", "v", "-show_entries", "frame=pts_time",
                    "-read_intervals", "%+#1")
    pts = [float(fr.get("pts_time", -1)) for fr in first.get("frames", [])]
    if not pts or abs(pts[0]) > 1e-3:
        bad.append(f"first frame at {pts[:1]} (TikTok's cover would be black)")
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", out, "-f", "null", "-"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or p.stderr.strip():
        bad.append("doesn't decode cleanly: " + p.stderr.strip()[:200])
    leftovers = [f for f in os.listdir(os.path.dirname(out)) if f.startswith("vc_")]
    if leftovers:
        bad.append(f"temporary files left: {leftovers}")
    return bad


R = engine.RenderOpts
WORDS = [{"word": w, "start": 0.2 + i * 0.4, "end": 0.5 + i * 0.4} for i, w in enumerate("this is a test clip ok".split())]
LAYOUTS = [
    ("split", dict(layout="split", cam=(0.7, 0.0, 0.3, 0.3))),
    ("full", dict(layout="full")),
    ("cam", dict(layout="cam", cam=(0.0, 0.6, 0.4, 0.4))),
    ("blur", dict(layout="blur")),
    ("full zoomed", dict(layout="full", game_zoom=1.6, game_x=0.9)),
    ("split, crop in the corner", dict(layout="split", cam=(0.75, 0.75, 0.25, 0.25))),
]
if QUICK:
    LAYOUTS = LAYOUTS[:1] + LAYOUTS[3:4]

t_all = time.time()
for sname, path in SOURCES.items():
    info = engine.probe(path)
    for lname, lo in LAYOUTS:
        outdir = os.path.join(TMP, "out_" + str(abs(hash((sname, lname)))))
        os.makedirs(outdir)
        out = os.path.join(outdir, "clip.mp4")
        # the size-change source changes at 4 s: render across it
        start = 2.5 if "size change" in sname else 1.3
        t0 = time.time()
        diag = {}
        try:
            engine.render_clip(path, start, start + LEN, out, R(captions=True, hw_encode=False, speed="fast", **lo),
                               title="Matrix test", words=WORDS, diag=diag)
            bad = verify(out, (1080, 1920), info["has_audio"], LEN)
            # the same size comes out of blur-fit, so a layout that fell back would pass the checks above
            want = engine.LAYOUT_NAMES[lo["layout"]]
            if diag.get("fallback") or diag.get("layout_used") != want:
                bad.append(f"rendered as {diag.get('layout_used')!r}, not {want!r} ({diag.get('fallback')})")
        except Exception as e:
            bad = ["render failed: " + str(e).strip().splitlines()[-1][:300]]
        check(f"{sname} / {lname} ({time.time() - t0:.1f}s)", not bad, "; ".join(bad))

# export formats, on the landscape source
land = SOURCES["landscape 1080p60"]
FORMATS = [
    ("HEVC mp4", dict(vcodec="hevc"), "mp4", (1080, 1920)),
    ("VP9 webm", dict(vcodec="vp9", container="webm"), "webm", (1080, 1920)),
    ("mov", dict(container="mov"), "mov", (1080, 1920)),
    ("X size (0.6667)", dict(scale=0.6667), "mp4", (720, 1280)),
    ("half size, 30 fps, bitrate", dict(scale=0.5, fps=30, vbitrate=4000), "mp4", (540, 960)),
    ("16:9", dict(aspect="16:9"), "mp4", engine.SHAPES["16:9"]),
    ("1:1", dict(aspect="1:1"), "mp4", engine.SHAPES["1:1"]),
    ("watermark + loudness + volume", dict(wm_text="@me", wm_pos="br", loudness=-14.0, gain_db=6.0), "mp4", (1080, 1920)),
    ("denoise + voice", dict(denoise=True, voice=True), "mp4", (1080, 1920)),
]
if QUICK:
    FORMATS = FORMATS[:2]
for fname, kw, ext, size in FORMATS:
    outdir = tempfile.mkdtemp(prefix="fmt_", dir=TMP)
    out = os.path.join(outdir, "clip." + ext)
    t0 = time.time()
    diag = {}
    try:
        engine.render_clip(land, 1.3, 1.3 + LEN, out, R(layout="split", captions=True, hw_encode=False, speed="fast", **kw),
                           title="Format test", words=WORDS, diag=diag)
        bad = verify(out, size, True, LEN)
        if "fell back" in diag.get("fallback", "") or "failed (exit" in diag.get("fallback", ""):
            bad.append(f"layout fell back: {diag['fallback']}")
    except Exception as e:
        bad = ["render failed: " + str(e).strip().splitlines()[-1][:300]]
    check(f"format {fname} ({time.time() - t0:.1f}s)", not bad, "; ".join(bad))

# sources that can't be rendered: a quick, plain error, and nothing left behind
broken = os.path.join(TMP, "broken.mp4")
with open(land, "rb") as f:
    data = f.read()
with open(broken, "wb") as f:
    f.write(data[: len(data) // 3])                         # a download that stopped a third of the way
garbage = os.path.join(TMP, "garbage.mp4")
with open(garbage, "wb") as f:
    f.write(os.urandom(200_000))
audio_only = src("audio.m4a", *tone, "-c:a", "aac")
for bname, path, start in (("a truncated download", broken, 1.0), ("a file that isn't video", garbage, 0.0),
                           ("a sound-only file", audio_only, 0.0), ("a clip past the end of the video", land, 30.0)):
    outdir = tempfile.mkdtemp(prefix="bad_", dir=TMP)
    out = os.path.join(outdir, "clip.mp4")
    t0 = time.time()
    err = ""
    try:
        engine.render_clip(path, start, start + LEN, out, R(layout="split", captions=False, hw_encode=False, speed="fast"))
        problems = verify(out, (1080, 1920), True, LEN)
        err = "rendered" + (": " + "; ".join(problems) if problems else " fine")
    except Exception as e:
        err = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
    took = time.time() - t0
    left = os.listdir(outdir)
    if bname == "a truncated download":
        # the part before the damage is fine: rendering it is right, a broken file isn't
        ok = (err.startswith("rendered fine") or (not err.startswith("rendered") and not left)) and took < 60
    else:
        ok = not err.startswith("rendered") and not left and took < 60
    check(f"{bname}: fails fast and clean ({took:.1f}s): {err[:120]}", ok, f"files left: {left}" if left else err)

print(f"\nmatrix took {time.time() - t_all:.0f}s")
shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print(f"FAILED: {len(failures)} of the matrix: " + ", ".join(failures))
    sys.exit(1)
print("PASSED")
