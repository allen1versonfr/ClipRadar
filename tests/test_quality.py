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
import re
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
# ---- text read from FFmpeg and the other tools is decoded as UTF-8: FFmpeg echoes file names, and a
# title like 名前 can't be decoded with Windows' default code page (a render was failing as "came out
# without sound" because the volume check couldn't read FFmpeg's answer)
import ast
SRC = os.path.join(HERE, "..", "src")
loose = []
for fn in sorted(os.listdir(SRC)):
    if fn.endswith(".py"):
        tree = ast.parse(open(os.path.join(SRC, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                kw = {k.arg: k.value for k in node.keywords}
                if isinstance(kw.get("text"), ast.Constant) and kw["text"].value is True and "encoding" not in kw:
                    loose.append(f"{fn}:{node.lineno}")
check("every tool output read as text is decoded as UTF-8", not loose, loose)
check("a 30 FPS stream is never turned into 60", engine.output_fps({"fps": 30}, R(fps=60)) == 30)
check("a chosen lower rate is used", engine.output_fps({"fps": 60}, R(fps=30)) == 30)
check("an unreadable rate falls back to 30", engine.output_fps({"fps": 0}, R()) == 30 and engine.output_fps({}, R()) == 30)

# ---- quality presets
p = exports.load()
check("Balanced by default, at the stream's frame rate", p["quality"] == "balanced" and exports.settings(p)["fps"] == 0)
s = exports.settings(dict(p, quality="fast"))
check("Fast export: quick encoder, up to 30 FPS", s["speed"] == "fast" and s["fps"] == 30, s)
check("High frame rate saved by 4.5-4.7 becomes Balanced (it made the same file)",
      exports.clean(dict(p, quality="smooth"))["quality"] == "balanced")
check("Balanced and High keep the stream's own frame rate", all(exports.QUALITY[k]["fps"] == 0 for k in ("balanced", "high")))
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

    # ---- watermark: on the export only, where you put it, inside the frame
    import hashlib
    import numpy as np
    dark = os.path.join(tmp, "dark.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=1080x1920:rate=30:duration=4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", dark], check=True)
    before = hashlib.md5(open(dark, "rb").read()).hexdigest()

    def frame(path, t=1.0):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080)

    def bright(img, x0, y0, x1, y1):
        return float(img[y0:y1, x0:x1].mean())

    def wm_render(name, **k):
        out = os.path.join(tmp, name)
        engine.render_clip(dark, 0.0, 3.0, out, R(layout="full", captions=False, hw_encode=False, speed="fast", **k),
                           title="", log=lambda m: None)
        return out

    out = wm_render("wm_tl.mp4", wm_text="@mychannel", wm_pos="tl", wm_opacity=100)
    img = frame(out)
    tl, br = bright(img, 0, 200, 540, 400), bright(img, 540, 1400, 1080, 1700)
    check("a text mark appears top-left, below TikTok's top bar", tl > 3 and br < 1, (tl, br))
    check("the export keeps its size", probe(out)[:2] == (1080, 1920), probe(out))
    out = wm_render("wm_br.mp4", wm_text="@mychannel", wm_pos="br", wm_opacity=100)
    img = frame(out)
    check("bottom-right puts it there instead", bright(img, 540, 1300, 1080, 1560) > 3 and bright(img, 0, 200, 540, 400) < 1)
    out = wm_render("wm_late.mp4", wm_text="@mychannel", wm_pos="tl", wm_opacity=100, wm_start=2.0)
    check("it appears only from its start time", bright(frame(out, 0.5), 0, 200, 540, 400) < 1
          and bright(frame(out, 2.5), 0, 200, 540, 400) > 3)
    logo = os.path.join(tmp, "logo.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:size=400x200", "-frames:v", "1", logo], check=True)
    out = wm_render("wm_img.mp4", wm_image=logo, wm_pos="tr", wm_opacity=50, wm_margin=40)
    img = frame(out)
    region = img[250:330, 1080 - 40 - 200:1080 - 60]
    check("an image mark goes where it's put (top-right)", 80 < float(region.mean()) < 170 and bright(img, 0, 200, 400, 400) < 1,
          float(region.mean()))
    check("at the opacity chosen (half: mid grey on black)", 100 < float(region.mean()) < 150, float(region.mean()))
    out = wm_render("wm_huge.mp4", wm_image=logo, wm_pos="br", wm_size=250, wm_margin=200)
    check("a big mark with a big margin still stays inside the frame and renders", probe(out)[:2] == (1080, 1920))
    check("the source video is never changed", hashlib.md5(open(dark, "rb").read()).hexdigest() == before)
    f, _ = engine.build_filter({"width": 1080, "height": 1920, "fps": 30}, R(layout="split"))
    for lay in ("split", "blur", "cam"):
        try:
            wm_render(f"wm_{lay}.mp4", layout=lay, wm_text="@me") if False else engine.render_clip(
                dark, 0.0, 1.5, os.path.join(tmp, f"wm_{lay}.mp4"),
                R(layout=lay, captions=False, hw_encode=False, speed="fast", wm_text="@me"), title="", log=lambda m: None)
            check(f"the watermark works with the {lay} layout", probe(os.path.join(tmp, f"wm_{lay}.mp4"))[:2] == (1080, 1920))
        except Exception as e:
            check(f"the watermark works with the {lay} layout", False, str(e)[-200:])

    # ---- a clip's own volume (Clip tab › Volume), measured
    def lufs(path):
        err = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        return float(m[-1]) if m else None
    noisy = os.path.join(tmp, "talk.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=30:duration=6",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.2:duration=6", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", noisy], check=True)
    levels = {}
    for g in (-6, 0, 3):
        out = os.path.join(tmp, f"gain{g}.mp4")
        o = R(layout="full", captions=False, hw_encode=False, speed="fast")
        o.gain_db = g
        engine.render_clip(noisy, 0.0, 5.0, out, o, title="", log=lambda m: None)
        levels[g] = lufs(out)
    check("a clip set quieter comes out quieter, louder louder (Even out volume on)",
          levels[-6] is not None and levels[-6] < levels[0] - 3 and levels[3] > levels[0] + 1.5, levels)
else:
    print("[skip] no ffmpeg here: renders not checked")

# ---- watermark settings
w = exports.clean(dict(exports.load(), watermark={"on": True, "text": "x" * 100, "pos": "zz", "opacity": 500, "size": 1}))["watermark"]
check("watermark settings are kept within range", len(w["text"]) == 60 and w["pos"] == "tl" and w["opacity"] == 100 and w["size"] == 40, w)
check("off by default", exports.load()["watermark"]["on"] is False)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ.setdefault("CLIPRADAR_WORK", tempfile.mkdtemp())
import server  # noqa: E402
check("off: no watermark in the render options", server._wm_opts({"on": False, "text": "hi"}) == {})
check("on: the render gets it", server._wm_opts({"on": True, "text": "hi", "pos": "br"})["wm_pos"] == "br")

# ---- picture check: clips mostly too dark or too blurry to watch get a warning (from the real-world
# test: a motion-blurred, nearly black IRL clip was suggested without a word)
if shutil.which("ffmpeg"):
    ptmp = tempfile.mkdtemp(prefix="vc_pic_")
    mk = lambda name, vf: subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",  # noqa: E731
                                          "testsrc2=size=1280x720:rate=30:duration=4", *(["-vf", vf] if vf else []),
                                          "-c:v", "libx264", "-preset", "ultrafast", os.path.join(ptmp, name)], check=True)
    mk("sharp.mp4", "")
    mk("blur.mp4", "boxblur=12:2")
    mk("dark.mp4", "eq=brightness=-0.75")
    pc = {n: engine.picture_check(os.path.join(ptmp, n + ".mp4"), 0, 4) for n in ("sharp", "blur", "dark")}
    check("picture check: a sharp, bright clip passes", pc["sharp"] == {"dark": 0.0, "blurry": 0.0}, pc["sharp"])
    check("picture check: a blurred clip is blurry", pc["blur"]["blurry"] >= 0.6, pc["blur"])
    check("picture check: a dark clip is dark", pc["dark"]["dark"] >= 0.6, pc["dark"])
    import server
    it = {"start": 0.0, "end": 4.0}
    server._check_picture(it, {"video": os.path.join(ptmp, "dark.mp4")})
    check("a dark clip gets a warning in Exports (it still renders)", "dark" in it.get("warning", ""), it)
    it = {"start": 0.0, "end": 4.0}
    server._check_picture(it, {"video": os.path.join(ptmp, "sharp.mp4")})
    check("a good clip gets none", not it.get("warning"), it)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
