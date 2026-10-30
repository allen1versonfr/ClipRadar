"""
Opening an ordinary YouTube video (no chat): what used to wait for the download now runs beside it.

A stand-in for yt-dlp plays YouTube: looking a video up takes a while, comments take longer, the video
and its sound download one after the other. Checked:
  - the downloads start from the link check's metadata (--load-info-json), not a second lookup
  - comments, captions and Most replayed are read while the video downloads
  - the sound is downloaded on its own, listened to while the video downloads, and handed to the video's
    download, which joins it instead of fetching it again
  - the early listening is the same as listening to the downloaded video
  - when the video's download got to the sound first, it's left alone
  - the whole thing takes about as long as the video's download, not the sum of everything

  python tests/test_youtube_speed.py
"""

from __future__ import annotations

import json
import os
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
import ytextra  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="vc_yt_")
src_mp4, src_m4a = os.path.join(tmp, "src.mp4"), os.path.join(tmp, "src.m4a")
# 40 s of video; the sound is quiet with a loud stretch at 25-28 s
subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=40",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
                "-filter_complex", "[1:a]volume='if(between(t,25,28),1,0.02)':eval=frame[a]", "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src_mp4], check=True)
subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-i", src_mp4, "-vn", "-c", "copy", src_m4a], check=True)

heat = [{"start_time": i * 0.4, "end_time": (i + 1) * 0.4, "value": 1.0 if 62 <= i <= 70 else 0.1} for i in range(100)]
INFO = {"id": "abcdefghijk", "title": "A video", "duration": 40, "uploader": "Someone", "uploader_id": "@someone",
        "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk", "heatmap": heat, "language": "en",
        "filesize_approx": 1000, "live_status": "not_live",
        "requested_formats": [{"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none"},
                              {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}]}
open(os.path.join(tmp, "info.json"), "w").write(json.dumps(INFO))

FAKE = r'''
import json, os, shutil, sys, time
a = sys.argv[1:]
E = os.environ
log = lambda *x: open(E["FAKE_LOG"], "a").write(json.dumps([time.monotonic(), *x]) + "\n")
loaded = "--load-info-json" in a
if not loaded:
    time.sleep(float(E["LOOKUP_S"]))                      # YouTube looked up: page, player, formats
if "--write-comments" in a:
    log("comments")
    time.sleep(float(E["COMMENTS_S"]))
    print(json.dumps({"comments": [{"text": "0:26 no way", "author_id": f"u{k}", "like_count": 0, "parent": "root"}
                                   for k in range(4)]}))
    sys.exit(0)
if "-J" in a:
    log("probe")
    print(open(E["FAKE_INFO"]).read())
    sys.exit(0)
out = a[a.index("-o") + 1]
folder = os.path.dirname(out)
if "sound.%(ext)s" in out:                                # the sound on its own
    log("sound", loaded)
    dst = out.replace("%(ext)s", "m4a")
    open(dst + ".part", "wb").close()
    time.sleep(float(E["SOUND_S"]))
    shutil.copyfile(E["FAKE_M4A"], dst + ".part")
    os.replace(dst + ".part", dst)
    sys.exit(0)
log("video", loaded)
vpart = os.path.join(folder, "vod.f137.mp4")
for k in range(1, 11):
    print(f"VCPROG {k * 90} 900 None None None 1000000 1", flush=True)
    open(vpart + ".part", "ab").write(b"x" * 1000)
    time.sleep(float(E["VIDEO_S"]) / 10)
os.replace(vpart + ".part", vpart)
apart = os.path.join(folder, "vod.f140.m4a")
if os.path.isfile(apart):
    print(f"[download] {apart} has already been downloaded", flush=True)
    log("sound part reused")
else:
    log("sound part fetched")
    open(apart + ".part", "wb").close()
    for k in range(1, 6):
        print(f"VCPROG {k * 20} 100 None None None 1000000 1", flush=True)
        time.sleep(float(E["AUDIO_S"]) / 5)
    shutil.copyfile(E["FAKE_M4A"], apart + ".part")
    os.replace(apart + ".part", apart)
shutil.copyfile(E["FAKE_MP4"], os.path.join(folder, "vod.mp4"))
os.remove(vpart); os.remove(apart)
'''
bindir = os.path.join(tmp, "bin")
os.makedirs(bindir)
script = os.path.join(tmp, "fake_ytdlp.py")
open(script, "w").write(FAKE)
if os.name == "nt":
    fake_exe = os.path.join(bindir, "yt-dlp.bat")
    open(fake_exe, "w").write(f'@"{sys.executable}" "{script}" %*\n')
else:
    fake_exe = os.path.join(bindir, "yt-dlp")
    open(fake_exe, "w").write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    os.chmod(fake_exe, 0o755)
# the stand-in, never a real yt-dlp (CI has one in tools/, which is looked in before PATH)
_which = engine.which
engine.which = lambda name: fake_exe if name == "yt-dlp" else _which(name)
# a lookup is a few seconds on YouTube, comments ten or more, the video most of the time, its sound a tenth
TIMES = dict(LOOKUP_S=0.8, COMMENTS_S=1.5, VIDEO_S=2.5, AUDIO_S=0.8, SOUND_S=0.6)
os.environ.update(FAKE_INFO=os.path.join(tmp, "info.json"), FAKE_MP4=src_mp4, FAKE_M4A=src_m4a,
                  **{k: str(v) for k, v in TIMES.items()})


def open_video(name):
    logf = os.path.join(tmp, name + ".log")
    os.environ["FAKE_LOG"] = logf
    root = tempfile.mkdtemp(prefix="vc_ytw_")
    logs = []
    t0 = time.monotonic()
    # max_height 0 (Best available): its format, bv*+ba/b, has none of the < > ^ that cmd.exe would take for
    # redirection or escapes when it runs the stand-in's .bat on Windows (the real yt-dlp.exe isn't a .bat)
    s = engine.acquire("https://www.youtube.com/watch?v=abcdefghijk", root, max_height=0, log=logs.append,
                       early_sound=True, events=False)
    took = time.monotonic() - t0
    sound = s.sound() if s.sound else None
    calls = [json.loads(ln) for ln in open(logf)]
    return s, sound, took, calls, logs


s, sound, took, calls, logs = open_video("first")
what = [c[1] for c in calls]
at = {c[1]: c[0] for c in calls}
check("the video downloaded", s.video.endswith("vod.mp4") and os.path.getsize(s.video) > 1000, s.video)
check("YouTube was looked up for the link check and the comments only",
      what.count("probe") == 1 and what.count("comments") == 1 and len(calls) == 5, what)
check("the downloads started from the link check's metadata",
      all(c[2] for c in calls if c[1] in ("video", "sound")), calls)
check("comments were read while the video downloaded", at["comments"] < at["video"] + TIMES["VIDEO_S"] / 2, calls)
check("the sound was downloaded on its own, beside the video", abs(at["sound"] - at["video"]) < 1.0, calls)
check("and the video's download used it instead of fetching it again",
      "sound part reused" in what and "sound part fetched" not in what, what)
yt = ytextra.load(s.workdir)
check("comments and Most replayed were saved", len(yt.get("comments") or []) == 4 and len(yt.get("heatmap") or []) == 100,
      sorted(yt))
check("the link check's metadata file is gone afterwards", not os.path.exists(os.path.join(s.workdir, "probe.info.json")))
check("nothing of the sound is left behind",
      not os.path.exists(os.path.join(s.workdir, "sound_first"))
      and not [f for f in os.listdir(os.path.dirname(s.video)) if f.startswith(("vod.", "sound")) and f != "vod.mp4"],
      os.listdir(os.path.dirname(s.video)))
check("the sound was listened to while the video downloaded", sound is not None)
if sound is not None:
    loud, _fine, _ev = sound
    ref, _rf, _re = engine.measure_audio(s.video, 40, False)
    n = min(len(loud), len(ref)) - 1
    import numpy as np
    check("and it hears the same as listening to the video", n > 35 and float(np.max(np.abs(loud[:n] - ref[:n]))) < 0.5,
          float(np.max(np.abs(loud[:n] - ref[:n]))) if n > 0 else n)
    check("the loud stretch is where it is in the video", 24 <= int(np.argmax(loud[:n])) <= 28, int(np.argmax(loud[:n])))
# one after the other, it was: lookup, video and its sound, then comments
serial = TIMES["LOOKUP_S"] * 3 + TIMES["VIDEO_S"] + TIMES["AUDIO_S"] + TIMES["COMMENTS_S"]
alongside = TIMES["LOOKUP_S"] + TIMES["VIDEO_S"]
print(f"  opened in {took:.1f} s (one after the other: about {serial:.1f} s; the link check and the video alone: "
      f"{alongside:.1f} s)", flush=True)
check("it takes about as long as the link check and the video, not everything added up", took < alongside + 2.5,
      round(took, 2))

# the video's download got to the sound first: it's left alone, and nothing breaks
os.environ.update(VIDEO_S="0.2", SOUND_S="2.0")
s2, sound2, _took2, calls2, _l2 = open_video("second")
what2 = [c[1] for c in calls2]
check("the video's download got to the sound first: it fetched its own", "sound part fetched" in what2, what2)
check("and the video is fine", os.path.getsize(s2.video) == os.path.getsize(src_mp4))
check("and the early listening still worked", sound2 is not None)
check("and nothing of the sound is left in the downloads folder",
      not [f for f in os.listdir(os.path.dirname(s2.video)) if f.startswith(("vod.", "sound")) and f != "vod.mp4"],
      os.listdir(os.path.dirname(s2.video)))
os.environ.update(VIDEO_S=str(TIMES["VIDEO_S"]), SOUND_S=str(TIMES["SOUND_S"]))

# ---- the pieces
d = tempfile.mkdtemp(prefix="vc_ho_")
tgt = os.path.join(d, "vod.f140.m4a")
open(tgt + ".part", "wb").close()
check("handing over: the download has started on that part already, so it's left alone",
      engine._hand_over(src_m4a, tgt, lambda m: None) is False and not os.path.exists(tgt))
os.remove(tgt + ".part")
check("handing over: otherwise a copy goes where the download looks for it",
      engine._hand_over(src_m4a, tgt, lambda m: None) is True and os.path.getsize(tgt) == os.path.getsize(src_m4a)
      and os.path.exists(src_m4a) and os.listdir(d) == ["vod.f140.m4a"], os.listdir(d))
check("the sound part: video and sound apart", engine._sound_part(INFO) == ("140", "m4a"))
check("the sound part: one file with both, nothing to hand over",
      engine._sound_part({"format_id": "18", "vcodec": "avc1", "acodec": "mp4a"}) is None)
check("the sound part: a video without sound, nothing to hand over", engine._sound_part(
    {"requested_formats": [{"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
                           {"format_id": "138", "ext": "mp4", "vcodec": "avc1", "acodec": "none"}]}) is None)
check("the sound part: an odd format id is never put in a file name", engine._sound_part(
    {"requested_formats": [INFO["requested_formats"][0], dict(INFO["requested_formats"][1], format_id="../x")]}) is None)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
