"""
Export VOD (vodexport.py): the copyright scan's music stretches, the export's settings, many muted spans
as a commands file (engine.mute_commands), and, where FFmpeg is installed, a whole video exported with
music and flagged words muted, captions burned in and saved as a subtitle file. Whisper is stood in for.

  python tests/test_vodexport.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

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

import numpy as np  # noqa: E402

import engine  # noqa: E402
import server  # noqa: E402
import vodexport  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- music stretches
m = np.zeros(120, dtype=np.float32)
m[10:40] = 0.9          # a song
m[44:60] = 0.8          # the same song after a 4 s break: one stretch
m[80:83] = 0.95         # a 3 s sound alert: not listed
sp = vodexport.music_spans(m, 120)
check("a song is one stretch, across a short break", len(sp) == 1, sp)
check("padded a second each side", sp and sp[0]["start"] == 9.0 and sp[0]["end"] == 61.0, sp)
check("with how sure it is", sp and 0.6 < sp[0]["confidence"] <= 0.9, sp)
check("silence has none", vodexport.music_spans(np.zeros(50), 50) == [])
check("nothing heard has none", vodexport.music_spans(None, 0) == [])
m2 = np.zeros(30, dtype=np.float32)
m2[20:] = 1.0
check("music to the end stops at the end", vodexport.music_spans(m2, 30)[0]["end"] == 30, vodexport.music_spans(m2, 30))

# ---- settings
o = vodexport.clean_options({"captions": "nope", "words": "bleep", "music": [[5, 9], ["x", 1], [50, 900], [3, 3.05]],
                             "size": 720, "caption_size": "abc", "quality": "high"}, 100)
check("an unknown captions choice is burned in", o["captions"] == "burn")
check("music spans are kept inside the video, bad ones dropped", o["music"] == [(5.0, 9.0), (50.0, 100.0)], o["music"])
check("size and quality are read", o["size"] == "720" and o["quality"] == "high", o)
check("a bad caption size is the default", o["caption_size"] == 60)
ro = vodexport.render_opts(o, {"width": 1920, "height": 1080})
check("a 16:9 video is shown whole in a wide frame", ro.layout == "full" and ro.aspect == "16:9" and ro.track == "off")
check("a 4:3 video sits on a blurred background", vodexport.render_opts(o, {"width": 1440, "height": 1080}).layout == "blur")
check("the stream keeps its own volume", ro.loudness == 0.0)

# ---- many spans as commands
cmds = engine.mute_commands([(1, 2, "mute"), (1.5, 3, "bleep"), (5, 6, "mute")])
lines = cmds.strip().splitlines()
check("overlapping spans are joined, a bleep winning", len(lines) == 2 and lines[0].startswith("1.000-3.000")
      and "volume@bp volume 0.6" in lines[0], cmds)
check("a mute only silences", "volume@bp" not in lines[1] and "volume@cm volume 0" in lines[1], cmds)

# ---- flagged words
words = [{"start": 1.0, "end": 1.3, "word": "this"}, {"start": 1.4, "end": 1.8, "word": "fucking"},
         {"start": 1.9, "end": 2.2, "word": "game"}]
w2, spans, n = vodexport.word_spans(words, "mute")
check("a swear word is muted and masked", n == 1 and spans[0][2] == "mute" and w2[1]["word"] != "fucking", (spans, w2))
check("word muting off leaves the words", vodexport.word_spans(words, "off")[1] == [])

# ---- the copyright scan from what opening the video heard
PID = "vx1"
work = tempfile.mkdtemp(prefix="vc_vx_")
video = os.path.join(work, "vod.mp4")
an = engine.Analysis(duration=40.0, chat_z=np.zeros(41), audio_z=np.zeros(41), has_chat=False, has_audio=True,
                     events={"laugh": np.zeros(41), "music": np.r_[np.zeros(10), np.ones(15), np.zeros(16)]})
server.projects[PID] = {"workdir": work, "video": video, "title": "My stream", "analysis": an,
                        "info": {"has_audio": True, "duration": 40.0}, "tx_lock": threading.Lock()}
open(video, "wb").close()
res = vodexport.scan_copyright(server.projects[PID])
check("the scan uses what opening the video heard", res["how"].startswith("heard when") and len(res["spans"]) == 1, res)
server.projects[PID].pop("copyright")
check("and is remembered for this video", (vodexport.load_scan(server.projects[PID]) or {}).get("spans") == res["spans"])
v = vodexport.view(server)
check("the screen lists the open video with its scan", [x["pid"] for x in v["videos"]] == [PID]
      and v["videos"][0]["scan"]["spans"] == res["spans"], v)


def wait(job, s=120):
    end = time.time() + s
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.1)
    return job


if shutil.which("ffmpeg"):
    os.remove(video)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", video], check=True)
    server.projects[PID]["info"] = engine.probe(video)
    said = [{"start": 30.0 + k * 0.3, "end": 30.25 + k * 0.3, "word": "fucking" if k == 2 else "word"} for k in range(6)]
    server.ensure_words = lambda pr, a, b, *x, **k: [w for w in said if a <= w["start"] < b]
    engine.whisper_available = lambda: True
    server._save_transcripts = lambda pr: None
    # 30 short music spans: more than SPANS_INLINE, so they go to the commands file
    music = [[1.0 + i * 0.8, 1.4 + i * 0.8] for i in range(30)]
    job = vodexport.start_export(server, PID, {"captions": "both", "words": "mute", "music": music, "size": "480",
                                               "quality": "fast", "hw_encode": False})
    wait(job)
    r = job.get("result") or {}
    check("the whole video exports", job["status"] == "done", (job.get("error"), job["log"][-5:]))
    if job["status"] == "done":
        info = engine.probe(r["path"])
        check("all of it, in a wide frame", abs(info["duration"] - 40) < 0.6 and info["width"] / info["height"] > 1.7, info)
        check("with a subtitle file next to it", r["srt"] and os.path.isfile(os.path.join(r["folder"], r["srt"])), r)
        check("music and the flagged word counted", r["muted_music"] == 30 and r["muted_words"] == 1, r)

        def rms(at, ln=0.25):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at}", "-t", f"{ln}", "-i", r["path"], "-ac", "1",
                                  "-f", "f32le", "-"], capture_output=True).stdout
            x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
            return float(np.sqrt((x ** 2).mean())) if x.size else -1.0
        check("music stretches are silent", rms(1.05) < 0.005 and rms(9.05) < 0.005, (rms(1.05), rms(9.05)))
        check("between them the sound plays", rms(1.5, 0.2) > 0.05, rms(1.5, 0.2))
        check("the flagged word is silent", rms(30.62, 0.15) < 0.005, rms(30.62, 0.15))
        check("the rest plays", rms(36) > 0.05, rms(36))
        left = [f for f in os.listdir(r["folder"]) if f.startswith("vc_")]
        check("no working files left behind", not left, left)
else:
    print("[skip] no ffmpeg here: the export isn't rendered")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
