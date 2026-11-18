"""
Segments (segments.py): what a segment keeps once stretches are cut out of it, a segment checked as the screen
sent it, edits kept on disk, a save that would overwrite a newer edit refused, a caption line typed by hand as
timed words, and, where FFmpeg is installed: segments of one video exported in their own order as one file,
the cut stretch left out, a segment turned down, the 30-minute limit, and edited captions in the subtitle file.

  python tests/test_segments.py
"""

from __future__ import annotations

import json
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
import paths  # noqa: E402
import segments  # noqa: E402
import server  # noqa: E402
import listening  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- what a segment keeps
s = {"start": 0.0, "end": 60.0, "cuts": [[40, 50], [10, 20]]}
check("cuts are left out, in order", segments.kept(s) == [(0.0, 10.0), (20.0, 40.0), (50.0, 60.0)], segments.kept(s))
check("overlapping cuts count once", segments.kept({"start": 0, "end": 60, "cuts": [[10, 30], [20, 40]]}) == [(0, 10), (40, 60)])
check("a sliver a cut leaves isn't kept", segments.kept({"start": 0, "end": 60, "cuts": [[0.2, 60]]}) == [])
check("the length is what's kept", segments.seg_len(s) == 40.0)

# ---- a segment as the screen sent it
c = segments.clean_segment({"start": -5, "end": 999, "cuts": [[50, 2000], ["x"], [3, 3.05]], "gain_db": 40, "name": "  a \n b ",
                            "lines": [{"start": 1, "end": 2, "text": " hi  there "}, {"start": 500, "end": 600, "text": "late"}]}, 120)
check("times kept inside the video", c and c["start"] == 0 and c["end"] == 120, c)
check("cuts kept inside the segment, junk dropped", c and c["cuts"] == [[50.0, 120.0]], c)
check("volume kept to what the renderer takes", c and c["gain_db"] == 12.0, c)
check("names and lines tidied, lines outside dropped", c and c["name"] == "a b" and c["lines"] == [{"start": 1.0, "end": 2.0, "text": "hi there"}], c)
check("lines not edited stay None (captions from what was heard)", segments.clean_segment({"start": 0, "end": 5}, 10)["lines"] is None)
check("too short is nothing", segments.clean_segment({"start": 3, "end": 3.2}, 10) is None)

# ---- a caption line typed by hand as timed words
ws = segments.line_words({"start": 10.0, "end": 12.0, "text": "no way he did that"})
check("a word for each word typed, in order", [w["word"] for w in ws] == ["no", "way", "he", "did", "that"], ws)
check("inside the line's own time", ws and ws[0]["start"] >= 10.0 and ws[-1]["end"] <= 12.0 + 1e-6, ws)
check("one after another", all(a["end"] <= b["start"] + 1e-6 and a["start"] < a["end"] for a, b in zip(ws, ws[1:])), ws)
check("an empty line has no words", segments.line_words({"start": 1, "end": 2, "text": "  "}) == [])

# ---- edits, kept on disk
PID = "sg1"
work = tempfile.mkdtemp(prefix="vc_sg_")
video = os.path.join(work, "vod.mp4")
an = engine.Analysis(duration=40.0, chat_z=np.zeros(41), audio_z=np.zeros(41), has_chat=False, has_audio=True, events={})
server.projects[PID] = {"workdir": work, "video": video, "title": "My stream", "analysis": an,
                        "info": {"has_audio": True, "duration": 40.0}, "tx_lock": threading.Lock()}
open(video, "wb").close()
e = segments.add(server, PID, 5, 10)
check("sending a part makes an edit named after the video", e["name"] == "My stream edit" and len(e["segments"]) == 1, e)
check("and names the segment", e["segments"][0]["name"] == "Segment 1", e)
e = segments.add(server, PID, 20, 30, "The fight", e["id"])
check("the next part goes on the end of it", [x["name"] for x in e["segments"]] == ["Segment 1", "The fight"], e)
with open(paths.DATA_DIR / "segments.json", encoding="utf-8") as f:
    check("it's on disk", json.load(f)["edits"][0]["id"] == e["id"])
try:
    segments.add(server, PID, 7, 7.2)
    check("a part too short is refused", False)
except ValueError:
    check("a part too short is refused", True)
old = e["ver"]
segs = [dict(x) for x in reversed(e["segments"])]
r = segments.save({"id": e["id"], "ver": old, "name": "Best bits", "segments": segs})
check("a save puts them in the new order", [x["name"] for x in r["segments"]] == ["The fight", "Segment 1"] and r["name"] == "Best bits", r)
try:
    segments.save({"id": e["id"], "ver": old, "name": "stale", "segments": []})
    check("a save from an old version is refused", False)
except segments.Conflict:
    check("a save from an old version is refused", segments._find(e["id"])["name"] == "Best bits")
segments._edits = None
check("edits come back after a restart", segments._find(e["id"])["name"] == "Best bits")
v = segments.view(server)
check("the screen lists it with its length", v["edits"][0]["length"] == 15.0 and v["max"] == 1800.0, v["edits"][0])


def wait(job, s=240):
    end = time.time() + s
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.1)
    return job


if shutil.which("ffmpeg"):
    os.remove(video)
    # a picture whose brightness says the second it is (so the order can be checked), and a steady tone
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40",
                    "-vf", "geq=lum='T*6':cb=128:cr=128", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", video], check=True)
    server.projects[PID]["info"] = engine.probe(video)
    listening.ensure_words = lambda pr, a, b, *x, **k: []
    listening._save_transcripts = lambda pr: None

    def luma(path, at):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", path, "-frames:v", "1", "-vf", "format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        return float(np.frombuffer(raw, dtype=np.uint8).mean()) if raw else -1.0

    def rms(path, at, ln=0.5):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at}", "-t", f"{ln}", "-i", path, "-ac", "1",
                              "-f", "f32le", "-"], capture_output=True).stdout
        x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
        return float(np.sqrt((x ** 2).mean())) if x.size else -1.0

    cur = segments._find(e["id"])
    segs = [{"id": "x", "name": "Late", "start": 30, "end": 36, "cuts": [[32, 34]], "gain_db": -12},
            {"id": "y", "name": "Early", "start": 5, "end": 9, "lines": [{"start": 6, "end": 8, "text": "edited by hand"}]},
            {"id": "z", "name": "Not used", "start": 15, "end": 20, "on": False}]
    segments.save({"id": cur["id"], "ver": cur["ver"], "name": "Best bits", "segments": segs})
    job = wait(segments.start_export(server, e["id"], {"captions": "srt", "size": "source", "quality": "fast", "hw_encode": False}))
    res = job.get("result") or {}
    check("the edit exports", job["status"] == "done" and os.path.isfile(res.get("path", "")), job.get("error"))
    if job["status"] == "done":
        out = res["path"]
        d = engine.probe(out)["duration"]
        check("as long as what's kept: 4 s of Late (2 cut out) and 4 s of Early, not the unticked one", abs(d - 8.0) < 0.35, d)
        check("in the edit's order: Late first", luma(out, 0.5) > luma(out, 5.0) + 40, (luma(out, 0.5), luma(out, 5.0)))
        check("the cut stretch is left out (32 s jumps to 34 s)", abs(luma(out, 2.5) - 34 * 6 % 256) < 30, luma(out, 2.5))
        check("Late is turned down, Early isn't", rms(out, 0.5) < rms(out, 5.5) * 0.5, (rms(out, 0.5), rms(out, 5.5)))
        srt = os.path.join(res["folder"], res["srt"]) if res.get("srt") else ""
        text = open(srt, encoding="utf-8").read() if srt and os.path.isfile(srt) else ""
        check("captions edited by hand are in the subtitle file", "edited" in text and "hand" in text, text[:200])
        check("named after the edit", res["name"].startswith("Best bits"), res)
    cur = segments._find(e["id"])
    segments.save({"id": cur["id"], "ver": cur["ver"], "name": "Too long",
                   "segments": [{"id": "a", "start": 0, "end": 40}] * 46})
    job = wait(segments.start_export(server, e["id"], {"captions": "off"}))
    check("more than 30 minutes is refused, saying how long it is", job["status"] == "error" and "30:00" in (job.get("error") or ""), job.get("error"))
else:
    print("[skip] no ffmpeg here: the edit isn't rendered")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
