"""
Unit tests for 3.7 exports: presets and Advanced changes, file name templates, caption word
swaps and the words Whisper should expect, the audio chain, encoder settings, sizes and .srt files.

  python tests/test_exports.py
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------- presets and settings
p = exports.load()
check("defaults: TikTok, burned captions", p["preset"] == "tiktok" and p["subtitles"] == "burn")
s = exports.settings(dict(p, preset="x"))
check("X preset: 720 wide, 2:20 limit", s["scale"] < 0.7 and s["max_len"] == 140 and s["container"] == "mp4", s)
s = exports.settings(dict(p, custom={"container": "webm"}))
check("WebM brings VP9 and Opus", s["vcodec"] == "vp9" and s["acodec"] == "opus", s)
s = exports.settings(dict(p, custom={"vcodec": "vp9"}))
check("VP9 goes in a WebM", s["container"] == "webm" and s["acodec"] == "opus", s)
s = exports.settings(dict(p, custom={"acodec": "opus", "container": "mp4"}))
check("MP4 keeps AAC", s["acodec"] == "aac", s)
c = exports.clean({"preset": "bogus", "custom": {"fps": 7, "crf": 99, "vbitrate": -5, "scale": "0.5"}, "renders": 9,
                   "downloads": 0, "priority": "max", "subtitles": "x", "template": "no fields"})
check("settings kept sensible", c["preset"] == "tiktok" and c["custom"] == {"crf": 34, "vbitrate": 0, "scale": 0.5}
      and c["renders"] == 3 and c["downloads"] == 1 and c["priority"] == "responsive" and c["subtitles"] == "burn"
      and c["template"] == exports.DEFAULT_TEMPLATE, c)
exports.save({"preset": "shorts", "custom": {"fps": 60}})
check("saved settings come back", exports.load()["custom"] == {"fps": 60} and exports.load()["preset"] == "shorts")
exports.save({"preset": "reels"})
check("a new preset starts from its own settings", exports.load()["custom"] == {})
check("settings described in words", "Instagram Reels" in exports.describe(exports.settings()) and "1080×1920" in exports.describe(exports.settings()))
check("X described at its size", "720×1280" in exports.describe(exports.settings(dict(exports.load(), preset="x"))))
cp = exports.clean({"cap_presets": [{"name": "Bold", "opts": {"caption_color": "#ff0", "evil": 1, "cap_outline": 150}}, {"name": ""}]})
check("saved caption styles keep only style keys", cp["cap_presets"] == [{"name": "Bold", "opts": {"caption_color": "#ff0", "cap_outline": 150}}], cp["cap_presets"])

# ---------------------------------------------------------------- file names
f = {"title": "No way he did that?!", "streamer": "Sam", "number": "03", "date": "2026-10-13", "hashtags": ""}
check("template fills in", exports.file_name("{streamer} - {title}", f) == "Sam - No way he did that!", exports.file_name("{streamer} - {title}", f))
check("empty fields drop out cleanly", exports.file_name("{hashtags} - {title} - {nothing}", f) == "No way he did that!",
      exports.file_name("{hashtags} - {title} - {nothing}", f))
check("number and date", exports.file_name("{number}_{date}", f) == "03_2026-10-13")
check("names Windows reserves are made safe", exports.file_name("{title}", {"title": "CON"}) == "_CON"
      and exports.file_name("{title}", {"title": "com1"}) == "_com1")
check("characters Windows refuses are removed", exports.file_name("{title}", {"title": 'a<b>c:"d/e\\f|g?h*'}) == "abcdefgh")
check("long names are cut", len(exports.file_name("{title}", {"title": "word " * 80})) <= exports.MAX_NAME)
check("nothing left: 'clip'", exports.file_name("{hashtags}", {}) == "clip")
fl = exports.fields_for({"title": "T", "hashtags": ["#a", "#b"]}, {"source": "https://www.twitch.tv/videos/1", "streamer": "sam",
                                                                  "title": "Stream"}, "x", 4)
check("fields from the clip and project", fl["platform"] == "Twitch" and fl["number"] == "04" and fl["preset"] == "X"
      and fl["hashtags"] == "a b" and fl["streamer"] == "sam", fl)

# ---------------------------------------------------------------- caption words
W = lambda s: [{"start": i, "end": i + 0.5, "word": w} for i, w in enumerate(s.split())]  # noqa: E731
r = exports.replace_words(W('jasn said "jasn!" JASN um'), [["jasn", "Jason"], ["um", ""]])
check("replace words keeps case and punctuation", [w["word"] for w in r] == ["Jason", "said", '"Jason!"', "JASON"], [w["word"] for w in r])
check("replace keeps timing", r[2]["start"] == 2)
check("no pairs: unchanged", exports.replace_words(W("a b"), []) == W("a b"))
pr = exports.prompt("JasonTheWeen", {"KEKW", "ICANT"}, ["Fortnite", "jasontheween"])
check("words Whisper should expect", pr.startswith("JasonTheWeen, Fortnite") and "KEKW" in pr and pr.lower().count("jasontheween") == 1, pr)

# ---------------------------------------------------------------- audio
o = engine.RenderOpts()
check("audio: loudness to -14", engine.audio_filter(o) == "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000", engine.audio_filter(o))
o = engine.RenderOpts(loudness=0.0, denoise=True, voice=True)
a = engine.audio_filter(o)
check("audio: noise, voice, then a limiter", a.startswith("afftdn") and "highpass" in a and "alimiter" in a and "loudnorm" not in a, a)

# ---------------------------------------------------------------- encoders and sizes
name, args = engine.video_encoder(engine.RenderOpts(vcodec="hevc", hw_encode=False))
check("H.265 plays on Apple devices", name == "libx265" and "hvc1" in args, args)
name, args = engine.video_encoder(engine.RenderOpts(container="webm", hw_encode=False))
check("WebM uses VP9", name == "libvpx-vp9", args)
name, args = engine.video_encoder(engine.RenderOpts(vbitrate=12000, hw_encode=False))
check("a bitrate replaces quality", "-b:v" in args and "12000k" in args and "-crf" not in args and "18000k" in args, args)
check("full size", engine.final_size(engine.RenderOpts()) == (1080, 1920))
check("X size", engine.final_size(engine.RenderOpts(scale=0.6667)) == (720, 1280), engine.final_size(engine.RenderOpts(scale=0.6667)))
check("square scaled", engine.final_size(engine.RenderOpts(aspect="1:1", scale=0.5)) == (540, 540))

# ---------------------------------------------------------------- .srt
d = tempfile.mkdtemp()
path = os.path.join(d, "c.srt")
engine.write_srt(W("one two three four five six seven eight"), engine.RenderOpts(words_per_line=4, uppercase=False), path, 10.0)
txt = open(path, encoding="utf-8").read()
check("srt file: numbered cues with times", txt.startswith("1\n00:00:00,000 --> ") and "\n2\n" in txt and "one two" in txt, txt[:120])

# Crop rectangles edited to the far bottom-right must never create a zero/negative crop.
for layout in ("split", "cam"):
    filt, _ = engine.build_filter({"width": 1920, "height": 1080}, engine.RenderOpts(
        layout=layout, cam=(1.0, 1.0, 0.0, 0.0)))
    import re
    crops = [tuple(map(int, m)) for m in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", filt)]
    check(f"{layout} crop remains valid at source boundary",
          bool(crops) and all(w >= 2 and h >= 2 and x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080
                              for w, h, x, y in crops), crops)

speed_k = exports.speed_key("libx264", exports.settings())
exports.record_speed(speed_k, 2.0)
exports.record_speed(speed_k, 1.0)
check("render speed remembered (smoothed)", abs(exports.speed(speed_k) - 1.6) < 1e-6 and exports.speed("libx264|other") == 1.6,
      exports.speed(speed_k))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
