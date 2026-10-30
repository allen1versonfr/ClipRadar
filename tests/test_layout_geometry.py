"""
Layout geometry against the real FFmpeg: every crop a layout makes fits the frames it gets, a clip
renders in the layout that was chosen (not blur-fit), and a face-following crop stays inside the
picture for the whole clip, however long. Blur-fit is only what's left when FFmpeg refuses a crop
that fits; then the clip says so and keeps the first failure for a bug report.

The fault this guards (fixed in 4.16.0): FFmpeg 8 and later refuse an expression nested deeper than about 100
levels, and the face-following crop used one flat sum with a term per half second, so every
Whole-frame clip over about 48 s with a face in it fell back to blur-fit.

Each output is checked as a viewer would see it: its size, the layout used, sound, length, a clean
decode to the end, and where practical its pixels (a source whose brightness is its x position shows
where the crop was at any moment).

  python tests/test_layout_geometry.py          (about 3-5 minutes on 4 cores)
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
import framing  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- without FFmpeg: the geometry itself
for args, good in (((1920, 1080, 608, 1080, 0, 0), True), ((1920, 1080, 608, 1080, 1312, 0), True),
                   ((1920, 1080, 608, 1080, 1313, 0), False), ((1920, 1080, 608, 1080, -2, 0), False),
                   ((1920, 1080, 1922, 1080, 0, 0), False), ((1920, 1080, 607, 1080, 0, 0), False),
                   ((1920, 1080, 0, 1080, 0, 0), False), ((1920, 1080, 600, 1000, 0, 82), False),
                   ((1920, 1080, 608, 1080, "'1+t'", 0), False),        # not clamped to the frame
                   ((1920, 1080, 608, 1080, "'clip(t,0,1312)'", 0), True)):
    try:
        engine.crop_filter(*args)
        ok = True
    except engine.RenderInputError as e:
        ok = False
        kind = e.kind
    check(f"crop {args[2]}x{args[3]} at {args[4]},{args[5]} of {args[0]}x{args[1]}: {'kept' if good else 'refused'}",
          ok == good and (ok or kind == "layout"))
deep = "'clip(" + "(" * 60 + "t" + ")" * 60 + ",0,1312)'"
try:
    engine.crop_filter(1920, 1080, 608, 1080, deep, 0)
    check("an expression nested too deeply for FFmpeg is refused before FFmpeg runs", False)
except engine.RenderInputError as e:
    check("an expression nested too deeply for FFmpeg is refused before FFmpeg runs", e.kind == "layout")
for v, want in ((float("nan"), 0.5), (None, 0.5), ("x", 0.5), (float("inf"), 0.5), (3, 1.0), (-1, 0.0), ("0.25", 0.25)):
    check(f"a saved setting {v!r} is read as {want}", engine._num(v, 0.5, 0.0, 1.0) == want)
# stale settings: zoom from an edited file, NaN focus, a facecam share out of range
f, _ = engine.build_filter({"width": 1920, "height": 1080, "fps": 30},
                           engine.RenderOpts(layout="full", game_zoom=1e9, game_x=float("nan"), cam_height=float("inf")))
check("an impossible zoom is held to the most the app allows", "crop=152:270:" in f, f[:80])
f, _ = engine.build_filter({"width": 1920, "height": 1080, "fps": 30}, engine.RenderOpts(layout="split", cam_height=None))
check("a missing camera share uses the default", "scale=1080:634:" in f, f[:200])

# several facecams: side by side over the game, stacked when they're all there is; each fills its own cell
info = {"width": 1920, "height": 1080, "fps": 30}
two = [(0.75, 0.0, 0.25, 0.25)]
f, cy = engine.build_filter(info, engine.RenderOpts(layout="split", cams=two))
check("Cam + game with 2 facecams: side by side, each half the width", "hstack=inputs=2" in f
      and f.count("scale=540:634:") == 2 and cy == 634, f[:300])
f, cy = engine.build_filter(info, engine.RenderOpts(layout="cam", cams=two * 2))
check("Facecam only with 3: stacked, a third of the height each", "vstack=inputs=3" in f and f.count("scale=1080:640:") == 3, f[:300])
f, _ = engine.build_filter(info, engine.RenderOpts(layout="split", cams=two * 6))
check("at most 4 facecams", "hstack=inputs=4" in f, f[:200])
f, _ = engine.build_filter(info, engine.RenderOpts(layout="split"))
check("one facecam: the same filter as before", "hstack" not in f and "split=2[c0][b]" in f, f[:200])
try:
    engine.build_filter(info, engine.RenderOpts(layout="split", cams=[(0.5, 0.5, 0.0, 0.0)]))
    check("an empty extra facecam box is a setting to fix, named", False)
except engine.RenderInputError as e:
    check("an empty extra facecam box is a setting to fix, named", e.kind == "layout_settings", e.kind)

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    print("PASSED (FFmpeg parts skipped: FFmpeg isn't installed)" if not failures else f"FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)

TMP = tempfile.mkdtemp(prefix="vc_geom_")
ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
print(ver)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args], check=True, timeout=300)


def src(name, size, rate, dur, *extra, gradient=False, fmt="yuv420p"):
    path = os.path.join(TMP, name)
    pic = (f"nullsrc=size={size}:rate={rate}:duration={dur},geq=lum='X*255/W':cb=128:cr=128" if gradient
           else f"testsrc2=size={size}:rate={rate}:duration={dur}")
    ff("-f", "lavfi", "-i", pic, "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}", *extra,
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", fmt, "-c:a", "aac", "-shortest", path)
    return path


# FFmpeg's own depth limit on this build: the fault only shows on builds that have it
deep_sum = "+".join(["1"] * 150)
LIMITED = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:duration=0.1",
                          "-vf", f"crop=32:32:'{deep_sum}':0", "-f", "null", "-"], capture_output=True).returncode != 0
print(f"this FFmpeg {'refuses' if LIMITED else 'accepts'} deeply nested expressions")

LAND = src("land.mp4", "1920x1080", 60, 8)
PORT = src("port.mp4", "1080x1920", 30, 8)
SQUARE = src("square.mp4", "1080x1080", 30, 8)
ODD = src("odd.mkv", "853x479", 30, 8, fmt="yuv444p")
TINY = src("tiny.mkv", "35x21", 25, 8, fmt="yuv444p")
GRAD = src("grad.mp4", "1920x1080", 30, 70, gradient=True)
# like the affected clip's source: a Twitch VOD at 59.94 with uneven frame times
VFR = os.path.join(TMP, "vfr.mp4")
ff("-i", src("vfr_raw.mp4", "1920x1080", "60000/1001", 8), "-vf", "setpts=PTS+0.004*sin(N)/TB", "-fps_mode", "vfr",
   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", VFR)
rot_raw = src("rot_raw.mp4", "1280x720", 30, 8)
ROT = os.path.join(TMP, "rot.mp4")
ff("-display_rotation:v:0", "90", "-i", rot_raw, "-c", "copy", ROT)
seg = [src(f"seg{i}.ts", size, 30, 4) for i, size in enumerate(("1920x1080", "1280x720"))]
MIXED = os.path.join(TMP, "mixed.ts")
with open(MIXED, "wb") as f:
    for p in seg:
        f.write(open(p, "rb").read())


def ffprobe(path, *args):
    p = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", path], capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout or "{}")


def verify(out, want_size, length, audio=True):
    bad = []
    if not os.path.isfile(out) or os.path.getsize(out) < 1000:
        return ["no file"]
    info = ffprobe(out, "-show_streams", "-show_format")
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if len(vs) != 1 or (vs[0]["width"], vs[0]["height"]) != tuple(want_size):
        bad.append(f"video {[(s['width'], s['height']) for s in vs]}, wanted {want_size}")
    if audio != any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        bad.append("sound missing" if audio else "sound added")
    dur = float(info.get("format", {}).get("duration") or 0)
    if abs(dur - length) > 0.35:
        bad.append(f"length {dur:.2f}s, wanted {length:.2f}s")
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", out, "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or p.stderr.strip():
        bad.append("doesn't decode cleanly: " + p.stderr.strip()[:200])
    left = [f for f in os.listdir(os.path.dirname(out)) if f.startswith("vc_")]
    if left:
        bad.append(f"temporary files left: {left}")
    return bad


def render(video, start, length, framing_=None, **kw):
    """(output path, diag, warnings, error or None)."""
    d = tempfile.mkdtemp(dir=TMP)
    out = os.path.join(d, "clip.mp4")
    diag, warned = {}, []
    opts = dict(captions=False, hw_encode=False, speed="fast")
    opts.update(kw)
    try:
        engine.render_clip(video, start, start + length, out, engine.RenderOpts(**opts), framing=framing_,
                           diag=diag, warn=warned.append, log=lambda m: None)
        return out, diag, warned, None
    except Exception as e:
        return out, diag, warned, e


def columns(path, t, width):
    """Brightness of the leftmost and rightmost columns of the frame at t (the crop's left and right edges
    on the gradient source, where brightness = x * 255 / source width)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                        "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    row = p.stdout[width * 100: width * 101]      # one row of the brightness plane, a little way down
    if len(row) < width:
        return None
    return sum(row[2:6]) / 4, sum(row[-6:-2]) / 4


def expected_left(path, t, sw, cw):
    """Where the crop's left edge should be: each keyframe's position held inside the picture, and
    straight lines between them."""
    xs = [(k, min(max(c * sw - cw / 2, 0), sw - cw)) for k, c in framing.clean_path(path)]
    return framing.path_at(xs, t)


def off_path(path, got, t, sw, cw, tol=6):
    """'' when the crop's edges read at t are where the path puts them (give or take a frame either way,
    and a little blur), else what's wrong."""
    if got is None:
        return f"no frame at {t}s"
    lefts = [expected_left(path, max(0.0, t + dt), sw, cw) * 255 / sw for dt in (-0.05, -0.025, 0, 0.025, 0.05)]
    left_ok = min(lefts) - tol <= got[0] <= max(lefts) + tol
    right_ok = min(lefts) + cw * 255 / sw - tol <= got[1] <= max(lefts) + cw * 255 / sw + tol
    return "" if left_ok and right_ok else (f"at {t}s the crop's edges read {round(got[0])}..{round(got[1])}, "
                                           f"wanted about {lefts[2]:.0f}..{lefts[2] + cw * 255 / sw:.0f}")


# 1-5. every layout and shape, on landscape, portrait, square, odd, tiny, VFR, rotated and size-changing
# sources, rendered in the layout chosen: no fallback, the right size, sound, length, a clean decode
LAYOUTS = [("Cam + game", dict(layout="split", cam=(0.7, 0.0, 0.3, 0.3))), ("Whole frame", dict(layout="full")),
           ("Facecam only", dict(layout="cam", cam=(0.0, 0.6, 0.4, 0.4))), ("Blur-fit", dict(layout="blur")),
           ("zoomed in all the way, at the edge", dict(layout="full", game_zoom=2.0, game_x=1.0)),
           ("zoomed blur-fit", dict(layout="blur", game_zoom=2.0, game_x=0.0)),
           ("Square blur", dict(layout="square")), ("zoomed square blur, at the edge", dict(layout="square", game_zoom=2.0, game_x=1.0)),
           ("Cam + game, 3 facecams", dict(layout="split", cam=(0.0, 0.0, 0.3, 0.3), cams=[(0.7, 0.0, 0.3, 0.3), (0.35, 0.7, 0.3, 0.3)])),
           ("Facecam only, 2 facecams", dict(layout="cam", cam=(0.0, 0.0, 0.4, 0.4), cams=[(0.6, 0.6, 0.4, 0.4)]))]
SOURCES = [("landscape", LAND), ("portrait", PORT), ("square", SQUARE), ("odd 853x479", ODD), ("tiny 35x21", TINY),
           ("59.94 fps VFR", VFR), ("rotated phone video", ROT), ("size change mid-clip", MIXED)]
for sname, path in SOURCES:
    for aspect in ("9:16", "1:1", "16:9"):
        for lname, lo in LAYOUTS:
            if sname not in ("landscape", "portrait") and aspect != "9:16" and lname not in ("Cam + game", "Whole frame"):
                continue                      # every shape on the two main sources, the rest on 9:16 and two layouts
            start = 2.5 if "size change" in sname else 1.0
            t0 = time.time()
            out, diag, warned, err = render(path, start, 3.0, aspect=aspect, scale=0.5, **lo)
            want = lname if lname in engine.LAYOUT_NAMES.values() else engine.LAYOUT_NAMES[engine.effective_layout(
                engine.RenderOpts(aspect=aspect, **lo))]
            if lo["layout"] == "split" and aspect == "16:9":
                want = "Whole frame"          # Cam + game in a wide frame is the whole frame
            bad = [f"render failed: {str(err).strip()[-200:]}"] if err else verify(out, engine.final_size(
                engine.RenderOpts(aspect=aspect, scale=0.5)), 3.0)
            if not err and (diag.get("fallback") or warned or diag.get("layout_used") != want):
                bad.append(f"layout used {diag.get('layout_used')!r} (wanted {want!r}), fallback {diag.get('fallback')!r}")
            check(f"{sname} / {aspect} / {lname} ({time.time() - t0:.1f}s)", not bad, "; ".join(bad))

# Square blur: a sharp square in the middle of the frame, over the blurred picture. Read off the
# gradient source (brightness = x * 255 / width) with Focus at the far left: in the square the left edge
# is the picture's left edge (~0) and the right edge ~143 (a 1080-wide square of a 1920-wide picture);
# outside it is the blurred, darkened copy, which doesn't jump to the picture's edge there
def row(path, t, width, y):
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                        "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    r = p.stdout[width * y: width * (y + 1)]
    return (sum(r[2:6]) / 4, sum(r[-6:-2]) / 4) if len(r) == width else None


for aspect, (ow, oh) in (("9:16", (1080, 1920)), ("16:9", (1920, 1080))):
    out, diag, warned, err = render(GRAD, 1.0, 2.0, layout="square", game_x=0.0, aspect=aspect)
    bad = [f"render failed: {str(err)[-300:]}"] if err else verify(out, (ow, oh), 2.0)
    if not bad:
        side = min(ow, oh)
        x0 = (ow - side) // 2
        mid = row(out, 1.0, ow, oh // 2)
        if aspect == "9:16":
            band = row(out, 1.0, ow, (oh - side) // 4)           # above the square
            if not (mid and band and mid[0] < 15 and 128 < mid[1] < 158 and band[0] > 30):
                bad.append(f"square row {mid}, band above {band}")
        else:                                                   # 16:9: the square sits between blurred sides
            p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", "1", "-i", out, "-frames:v", "1",
                                "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
            r = p.stdout[ow * (oh // 2): ow * (oh // 2 + 1)]
            edge_l, edge_r = sum(r[x0 + 2:x0 + 6]) / 4, sum(r[x0 + side - 6:x0 + side - 2]) / 4
            outside = sum(r[x0 - 8:x0 - 4]) / 4                # the blurred copy just left of the square
            if not (edge_l < 12 and 128 < edge_r < 158 and outside > 15):
                bad.append(f"square edges {edge_l:.0f}..{edge_r:.0f}, just outside {outside:.0f}")
    if not err and (diag.get("fallback") or diag.get("layout_used") != "Square blur"):
        bad.append(f"layout used {diag.get('layout_used')!r}, fallback {diag.get('fallback')!r}")
    check(f"Square blur {aspect}: a square of the picture, centred on the blurred copy", not bad, "; ".join(bad))

# 6. facecam boxes touching each edge, a tiny one, the whole frame; empty and malformed ones are settings to fix
for name, box in (("top-left corner", (0, 0, 0.25, 0.25)), ("top-right corner", (0.75, 0, 0.25, 0.25)),
                  ("bottom-left corner", (0, 0.75, 0.25, 0.25)), ("bottom-right corner", (0.75, 0.75, 0.25, 0.25)),
                  ("hanging off the right and bottom", (0.9, 0.9, 0.5, 0.5)), ("the whole frame", (0, 0, 1, 1)),
                  ("tiny (10 px)", (0.5, 0.5, 0.0055, 0.01))):
    for layout in ("split", "cam"):
        out, diag, warned, err = render(LAND, 1.0, 2.0, layout=layout, cam=box, scale=0.5)
        bad = [f"render failed: {str(err)[-200:]}"] if err else verify(out, (540, 960), 2.0)
        if not err and diag.get("fallback"):
            bad.append(f"fell back: {diag['fallback']}")
        check(f"facecam box at {name} / {layout}", not bad, "; ".join(bad))
for name, box in (("empty, in the corner", (1, 1, 0, 0)), ("nearly nothing", (0.2, 0.2, 0.001, 0.3)),
                  ("not numbers", ("a", None, 0.2, 0.2)), ("NaN", (float("nan"), 0, 0.2, 0.2)), ("too short", (0.1, 0.2))):
    out, diag, warned, err = render(LAND, 1.0, 2.0, layout="split", cam=box)
    left = os.listdir(os.path.dirname(out))
    check(f"facecam box {name}: a setting to fix, in plain words, nothing left behind",
          isinstance(err, engine.RenderInputError) and err.kind == "layout_settings" and "facecam box" in str(err)
          and not left and "cmd" not in diag, (err, left))

# 7-8. face paths: from the clip's first frame to its last, repeated times, a long gap, the edges of the
# picture, junk keyframes. The crop's left edge is read off the gradient source at moments through the clip
SW, CW = 1920, 608                            # 9:16 from 1920x1080: a 608-wide crop
PATHS = {
    "edge to edge": [(0, 0.0), (1, 1.0), (2, 0.0), (3, 1.0)],
    "repeated times": [(0, 0.2), (0, 0.8), (1, 0.8), (1, 0.8), (1.5, 0.3), (3, 0.3)],
    "a long gap": [(0, 0.15), (2.9, 0.85)],
    "starts late, ends early": [(1.0, 0.3), (2.0, 0.7)],
    "out of order, with junk": [(2, 0.7), (0, 0.3), (1, None), (1.5, float("nan")), ("x", 0.5), (2.5, 1.5)],
}
for pname, path in PATHS.items():
    out, diag, warned, err = render(GRAD, 5.0, 3.0, framing_={"path": path}, layout="full")
    bad = [f"render failed: {str(err)[-300:]}"] if err else verify(out, (1080, 1920), 3.0)
    if not err and (diag.get("fallback") or diag.get("layout_used") != "Whole frame, following the face"):
        bad.append(f"layout used {diag.get('layout_used')!r}, fallback {diag.get('fallback')!r}")
    if not bad:
        for t in (0.0, 0.5, 1.25, 2.0, 2.9):
            why = off_path(path, columns(out, t, 1080), t, SW, CW)
            if why:
                bad.append(why)
    check(f"face path, {pname}: the crop follows it and stays inside the picture", not bad, "; ".join(bad))

# the affected clip: 1:1, 54.5 s, following a face the whole way (110 keyframes)
long_path = framing.shift([(i * 0.5, 0.5 + 0.45 * ((i // 8) % 2 * 2 - 1) * min(1, (i % 8) / 4)) for i in range(140)], 3.0, 54.5)
t0 = time.time()
out, diag, warned, err = render(GRAD, 3.0, 54.5, framing_={"path": long_path}, layout="full", aspect="1:1")
bad = [f"render failed: {str(err)[-300:]}"] if err else verify(out, (1080, 1080), 54.5)
if not err and (diag.get("fallback") or warned or diag.get("layout_used") != "Whole frame, following the face"):
    bad.append(f"layout used {diag.get('layout_used')!r}, fallback {diag.get('fallback')!r}, warned {warned}")
cw1 = 1080                                     # 1:1 from 1920x1080: a 1080-wide crop
if not bad:
    for t in (0.0, 10.25, 27.0, 41.5, 54.0):
        why = off_path(long_path, columns(out, t, 1080), t, SW, cw1)
        if why:
            bad.append(why)
check(f"a 54.5 s 1:1 clip following a face renders in its layout, the crop on the path ({time.time() - t0:.0f}s)",
      not bad, "; ".join(bad))

# 10. the original failure, then the corrected render. On a build with the depth limit, the old flat
# expression is the real fault; on one without it, FFmpeg is handed an expression it can't parse.


def old_x_expr(path, sw, cw):                  # the expression before 4.16.0: one flat sum, a level per keyframe
    pts = [(t, min(max(c * sw - cw / 2, 0), sw - cw)) for t, c in path]
    terms = [f"gte(t,{t0:g})*lt(t,{t1:g})*({a:.1f}+{(b - a) / (t1 - t0):.3f}*(t-{t0:g}))"
             for (t0, a), (t1, b) in zip(pts, pts[1:]) if t1 > t0]
    expr = "+".join(terms) + f"+gte(t,{pts[-1][0]:g})*{pts[-1][1]:.1f}+lt(t,{pts[0][0]:g})*{pts[0][1]:.1f}"
    return f"'clip({expr},0,{sw - cw})'"


real_x_expr = framing.x_expr
framing.x_expr = old_x_expr if LIMITED else (lambda path, sw, cw: f"'clip(nosuchfunction(t),0,{sw - cw})'")
out, diag, warned, err = render(GRAD, 3.0, 54.5 if LIMITED else 3.0, framing_={"path": long_path}, layout="full", aspect="1:1",
                                scale=0.5)
framing.x_expr = real_x_expr
ff1 = diag.get("first_failure") or {}
check("the original failure: FFmpeg refuses the face-following crop, and the clip keeps its layout without following",
      not err and diag.get("layout_used") == "Whole frame, not following the face" and warned and "follow the face" in warned[0],
      (err, diag.get("layout_used"), warned))
check("the first failure is kept: layout, FFmpeg's exit code and reason, the graph that failed",
      ff1.get("layout") == "Whole frame, following the face" and ff1.get("exit") not in (None, 0)
      and "parsed_crop" in ff1.get("reason", "").lower() and "crop=1080:1080:'clip(" in ff1.get("filter", ""), ff1)
check("and the attempt's own command and exit code are the first failure's", "Whole frame, following the face failed (exit"
      in diag.get("fallback", ""), diag.get("fallback"))
out, diag, warned, err = render(GRAD, 3.0, 54.5 if LIMITED else 3.0, framing_={"path": long_path}, layout="full", aspect="1:1",
                                scale=0.5)
check("then the corrected render: the same clip in its own layout, following the face, no warning",
      not err and not warned and not diag.get("fallback") and diag.get("layout_used") == "Whole frame, following the face",
      (err, diag.get("layout_used"), diag.get("fallback")))

# 9. a crop that doesn't fit is a named bug, before FFmpeg runs: no fallback, no files, classified as layout
framing.x_expr = lambda path, sw, cw: "'t*500'"          # a moving crop that isn't held inside the picture
out, diag, warned, err = render(LAND, 1.0, 2.0, framing_={"path": [(0, 0.1), (2, 0.9)]}, layout="full", title_style="box",
                                wm_text="@me")
framing.x_expr = real_x_expr
import server  # noqa: E402

check("a crop that could leave the picture fails as a layout bug, before FFmpeg, with nothing left behind",
      isinstance(err, engine.RenderInputError) and err.kind == "layout" and "doesn't fit this 1920x1080 video" in str(err)
      and "cmd" not in diag and not os.listdir(os.path.dirname(out)) and server.classify_render_error(err)[0] == "layout", err)

# 11. blur-fit is still there for a crop FFmpeg won't make although it fits
real_cf = engine.crop_filter
engine.crop_filter = lambda sw, sh, w, h, x, y: real_cf(sw, sh, w, h, x, y).replace("crop=", "crop=4000:4000:0:0,crop=", 1)
out, diag, warned, err = render(LAND, 1.0, 3.0, layout="split", cam=(0.7, 0, 0.3, 0.3))
bad = [f"render failed: {str(err)[-300:]}"] if err else verify(out, (1080, 1920), 3.0)
check("a crop FFmpeg refuses: the whole picture on a blurred background, said so", not bad and diag.get("layout_used") == "Blur-fit"
      and warned and "blurred background" in warned[0] and (diag.get("first_failure") or {}).get("layout") == "Cam + game",
      "; ".join(bad) or (diag.get("layout_used"), warned))
# a GPU encoder isn't blamed (or dropped for the session) for a graph that can't be built
calls = []
real_popen, real_ve = subprocess.Popen, engine.video_encoder
subprocess.Popen = lambda cmd, *a, **k: (calls.append(cmd), real_popen(cmd, *a, **k))[1]
engine.video_encoder = lambda o: ("h264_test", real_ve(engine.RenderOpts(hw_encode=False))[1])
engine._encoder_cache["hw"] = "h264_test"
out, diag, warned, err = render(LAND, 1.0, 2.0, layout="full")
subprocess.Popen, engine.video_encoder, engine.crop_filter = real_popen, real_ve, real_cf
renders = [c for c in calls if "-filter_complex" in c]
check("a graph that can't be built isn't retried on the CPU (it would fail the same way)", not err and len(renders) == 2
      and "used the CPU" not in diag.get("fallback", "") and engine._encoder_cache.get("hw") == "h264_test",
      (len(renders), diag.get("fallback"), engine._encoder_cache.get("hw")))
engine._encoder_cache.pop("hw", None)
# when blur-fit can't be made either: a failure that says both, keeping the first one's words
real_bf = engine.build_filter
engine.build_filter = lambda info, o, path=None, face=None: ("[0:v]crop=4000:4000:0:0,scale=1080:1920,format=yuv420p[v]", 960.0)
out, diag, warned, err = render(LAND, 1.0, 2.0, layout="full")
engine.build_filter = real_bf
check("when blur-fit fails too, the render fails and says both, keeping the first failure's command",
      isinstance(err, RuntimeError) and "and Blur-fit failed too" in str(err) and "and Blur-fit failed too" in diag.get("fallback", "")
      and diag.get("fallback_stderr") and diag.get("exit") not in (None, 0) and not warned
      and not os.listdir(os.path.dirname(out)), (str(err)[-300:], diag.get("fallback")))

# 12. nothing left behind: success, cancel, failure
d = tempfile.mkdtemp(dir=TMP)
n = [0]


def stop_soon():
    n[0] += 1
    if n[0] > 40:
        raise engine.Cancelled()


try:
    engine.render_clip(GRAD, 1.0, 30.0, os.path.join(d, "clip.mp4"), engine.RenderOpts(layout="full", captions=False, hw_encode=False,
                                                                                         speed="fast", wm_text="@me"),
                       title="Cancel me", check=stop_soon, framing={"path": long_path})
    check("a cancelled render stops", False, "it finished")
except engine.Cancelled:
    check("a cancelled render stops and leaves nothing behind", not os.listdir(d), os.listdir(d))
out, diag, warned, err = render(LAND, 1.0, 2.0, layout="split", title_style="box", wm_text="@me")
check("a finished render leaves only the clip", not err and os.listdir(os.path.dirname(out)) == ["clip.mp4"],
      os.listdir(os.path.dirname(out)))

shutil.rmtree(TMP, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {len(failures)}: " + ", ".join(failures))
sys.exit(1 if failures else 0)
