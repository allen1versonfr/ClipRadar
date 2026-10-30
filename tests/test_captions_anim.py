"""
Unit tests for animated captions: every animation preset builds valid captions that stay in sync
with the words (no overlapping lines, each word highlighted when it's said), reveal hides words
not said yet without moving the others, emphasis colours strong words, filler and punctuation
can be dropped, and each preset renders through FFmpeg/libass with every caption style.

  python tests/test_captions_anim.py
"""

from __future__ import annotations

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

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


R = engine.RenderOpts
WORDS = [{"start": 0.2 + i * 0.35, "end": 0.5 + i * 0.35, "word": w}
         for i, w in enumerate("um no way he actually hit that, insane! uh".split())]


def dialogues(ass):
    out = []
    for line in ass.splitlines():
        m = re.match(r"Dialogue: (\d+),(\d+):(\d+):([\d.]+),(\d+):(\d+):([\d.]+),Cap,", line)
        if m:
            t = lambda h, mi, se: int(h) * 3600 + int(mi) * 60 + float(se)  # noqa: E731
            out.append((t(*m.group(2, 3, 4)), t(*m.group(5, 6, 7)), line))
    return out


def ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


for motion in engine.CAPTION_MOTIONS:
    ass = engine.build_ass(WORDS, R(caption_motion=motion), 1300, 5.0)
    ds = dialogues(ass)
    check(f"{motion}: one caption line per word", len(ds) == len(WORDS), len(ds))
    check(f"{motion}: lines never overlap", all(a[1] <= b[0] + 1e-6 for a, b in zip(ds, ds[1:])))
    check(f"{motion}: each word's line starts when it's said", all(abs(d[0] - round(w["start"], 2)) < 0.011 for d, w in zip(ds, WORDS)))

word = engine.build_ass(WORDS, R(caption_motion="word"), 1300, 5.0)
check("word pop scales the word being said, then settles", "\\fscx132\\fscy132\\t(0,120,\\fscx100\\fscy100)" in word)
bounce = engine.build_ass(WORDS, R(caption_motion="bounce", caption_speed=200), 1300, 5.0)
check("bounce overshoots and settles, quicker at a higher speed", "\\t(0,30,\\fscx122\\fscy122)\\t(30,60,\\fscx100\\fscy100)" in bounce)
fade = dialogues(engine.build_ass(WORDS, R(caption_motion="fade", words_per_line=3), 1300, 5.0))
check("fade: each group fades in once, on its first word only", sum("\\fad(" in d[2] for d in fade) == 4, sum("\\fad(" in d[2] for d in fade))
slide = engine.build_ass(WORDS, R(caption_motion="slide"), 1300, 5.0)
check("slide: comes up into place", "\\move(540,1351,540,1300,0,240)" in slide, re.findall(r"\\move\([^)]*\)", slide)[:1])
rev = dialogues(engine.build_ass(WORDS[:3], R(caption_motion="reveal", words_per_line=3), 1300, 5.0))
check("reveal: words not said yet are invisible but keep their place", rev[0][2].count("\\alpha&HFF&") == 2 and rev[2][2].count("\\alpha&HFF&") == 0)
none = engine.build_ass(WORDS, R(caption_motion="none"), 1300, 5.0)
check("none: no animation tags", "\\t(" not in none and "\\fad" not in none and "\\move" not in none)
old = engine.build_ass(WORDS, R(caption_anim=True), 1300, 5.0)
check("saved styles from before still pop in (caption_anim)", "\\fscx114\\fscy114\\t(0,120" in old)
check("clean captions still don't pop", "\\t(" not in engine.build_ass(WORDS, R(caption_style="clean"), 1300, 5.0))
em = engine.build_ass(WORDS, R(caption_motion="none", caption_emph=True, caption_style="pop"), 1300, 5.0)
first = dialogues(em)[1][2]                                    # "NO" is being said; INSANE! isn't in this group
check("emphasis colours strong words even when they aren't being said", first.count("\\c&H0000E6FF&") >= 2, first)
check("no emphasis unless asked", dialogues(engine.build_ass(WORDS, R(caption_motion="none"), 1300, 5.0))[1][2].count("\\c&H0000E6FF&") == 1)

shown = [w["word"] for w in engine.caption_words(WORDS, R(caption_filler=False, caption_punct=False))]
check("filler words can be dropped", "um" not in shown and "uh" not in shown, shown)
check("punctuation can be dropped", "that" in shown and "insane" in shown, shown)
check("both kept by default", len(engine.caption_words(WORDS, R())) == len(WORDS))
kept = engine.caption_words(WORDS, R(caption_filler=False))
check("dropping words keeps the others' timing", all(w in WORDS for w in kept))

late = dialogues(engine.build_ass(WORDS, R(caption_offset=250), 1300, 5.0))
check("timing: captions can be moved later, every word by the same amount",
      all(abs(d[0] - round(w["start"] + 0.25, 2)) < 0.011 for d, w in zip(late, WORDS)), [d[0] for d in late[:3]])
early = dialogues(engine.build_ass(WORDS, R(caption_offset=-400), 1300, 5.0))
check("timing: moved earlier, never before the clip starts", early[0][0] == 0.0 and abs(early[1][0] - 0.15) < 0.011,
      [d[0] for d in early[:2]])
srt = os.path.join(tempfile.mkdtemp(prefix="vc_srt_"), "c.srt")
engine.write_srt(WORDS, R(caption_offset=500), srt, 5.0)
check("timing: the .srt file moves with the burned-in captions", "00:00:00,700 -->" in open(srt, encoding="utf-8").read(),
      open(srt, encoding="utf-8").read()[:60])
LONG = [{"start": 0.2 + i * 0.3, "end": 0.45 + i * 0.3, "word": w}
        for i, w in enumerate("absolutely unbelievable comeback tonight".split())]
wide = dialogues(engine.build_ass(LONG, R(words_per_line=4, caption_spacing=160), 1300, 5.0))
check("line spacing: a long caption is split into two balanced lines with a spacer between them",
      all(d[2].count("\\N") == 2 and "\\h" in d[2] for d in wide) and "UNBELIEVABLE\\N" in wide[0][2], wide[0][2][-90:])
check("line spacing: normal spacing leaves the wrapping to libass",
      not any("\\N" in d[2] for d in dialogues(engine.build_ass(LONG, R(words_per_line=4), 1300, 5.0))))
check("line spacing: a short caption stays on one line",
      not any("\\N" in d[2] for d in dialogues(engine.build_ass(WORDS[:2], R(caption_spacing=200), 1300, 5.0))))

# ---- titles: Yellow outline, a colour for Outline, the border, and a bigger size range
def title_style(o):
    return next(ln for ln in engine.build_ass(WORDS, o, 1300, 5.0, title="Chat lost it").splitlines() if ln.startswith("Style: Title,"))


def fields(ln):
    return ln.split(":", 1)[1].split(",")


y = fields(title_style(R(title_style="yellow")))
check("Yellow outline: yellow text with a black border", y[3] == "&H0000E6FF" and y[5] == "&H00000000" and y[15] == "1", y[3:6])
w = fields(title_style(R(title_style="outline")))
check("Outline keeps white text by default", w[3] == "&H00FFFFFF", w[3])
c = fields(title_style(R(title_style="outline", title_color="#00D1FF")))
check("Outline takes the colour you pick", c[3] == "&H00FFD100", c[3])
b100, b300 = int(fields(title_style(R(title_style="yellow")))[16]), int(fields(title_style(R(title_style="yellow", title_outline=300)))[16])
check("the border is about 3 times thicker at 300%", abs(b300 - 3 * b100) <= 2 and b100 >= 7, (b100, b300))
check("and gone at 0%", fields(title_style(R(title_style="outline", title_outline=0)))[16] == "0")
s100, s300 = int(fields(title_style(R(title_size=100)))[2]), int(fields(title_style(R(title_size=300)))[2])
check("titles go up to 300% (they stopped at 160%)", s300 == 3 * s100, (s100, s300))
cap = lambda o: int(fields(next(ln for ln in engine.build_ass(WORDS, o, 1300, 5.0).splitlines() if ln.startswith("Style: Cap,")))[2])  # noqa: E731
check("captions go up to 250% (they stopped at 160%)", cap(R(caption_size=250)) == int(86 * 2.5) and cap(R(caption_size=900)) == cap(R(caption_size=250)))

if shutil.which("ffmpeg"):
    tmp = tempfile.mkdtemp(prefix="vc_cap_")
    src = os.path.join(tmp, "s.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=5",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=5", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
    bad = []
    for motion in engine.CAPTION_MOTIONS:
        for style in engine.CAPTION_STYLES:
            out = os.path.join(tmp, f"{motion}_{style}.mp4")
            try:
                engine.render_clip(src, 0.0, 3.0, out, R(layout="blur", hw_encode=False, speed="fast", caption_style=style,
                                                         caption_motion=motion, caption_emph=True),
                                   words=WORDS, title="Test", log=lambda m: None)
                if not os.path.getsize(out):
                    bad.append(f"{motion}/{style}: empty")
            except Exception as e:
                bad.append(f"{motion}/{style}: {str(e)[-120:]}")
    # measured: the gap between the two lines really grows (bright rows on a black frame)
    import numpy as np
    black = os.path.join(tmp, "black.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=1080x1920:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", black], check=True)
    heights = {}
    for sp in (100, 200):
        out = os.path.join(tmp, f"sp{sp}.mp4")
        engine.render_clip(black, 0.0, 1.5, out, R(layout="full", hw_encode=False, speed="fast", caption_motion="none",
                                                   words_per_line=4, caption_spacing=sp),
                           words=[dict(w, start=0.1, end=1.4) for w in LONG], log=lambda m: None)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", out, "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True).stdout
        img = np.frombuffer(raw, np.uint8).reshape(1920, 1080)
        rows = np.where((img > 200).sum(axis=1) > 3)[0]
        heights[sp] = int(rows.max() - rows.min()) if rows.size else 0
    check("line spacing 200% makes the caption taller than 100% (measured on the rendered frame)",
          heights[200] > heights[100] * 1.25, heights)
    print(f"  caption height on the frame: {heights}")
    # measured: a Yellow outline title on a black frame is yellow inside a black border
    out = os.path.join(tmp, "yellow.mp4")
    engine.render_clip(black, 0.0, 1.0, out, R(layout="full", hw_encode=False, speed="fast", captions=False,
                                               title_style="yellow", title_size=250, title_outline=200), title="CHAT",
                       log=lambda m: None)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", out, "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
    yellow = (px[..., 0] > 200) & (px[..., 1] > 190) & (px[..., 2] < 90)
    rows = np.where(yellow.any(axis=1))[0]
    check("the rendered title is yellow, and big", rows.size and rows.max() - rows.min() > 90 and yellow.sum() > 10000,
          (int(rows.size), int(yellow.sum())))
    check(f"every preset renders with every caption style ({len(engine.CAPTION_MOTIONS) * len(engine.CAPTION_STYLES)} renders)", not bad, bad[:3])
else:
    print("[skip] no ffmpeg here: renders not checked")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
