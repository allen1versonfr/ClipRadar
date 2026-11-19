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
import locations  # noqa: E402
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
check("the screen lists it with its length, and no longest an edit can be (5.0)", v["edits"][0]["length"] == 15.0 and "max" not in v, v)


def wait(job, s=240):
    end = time.time() + s
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.1)
    return job


# ---- Find segments (5.0.3): the video split into parts, and the ticked ones sent to a new edit together
import vodexport  # noqa: E402
import whisper  # noqa: E402
FPID = "sgf"
fwork = tempfile.mkdtemp(prefix="vc_sgf_")
fvideo = os.path.join(fwork, "long.mp4")
open(fvideo, "wb").close()
said = []
for k in range(0, 1800 * 2):          # half an hour: a story about a trip, then a game, two words a second
    t_ = k * 0.5
    topic = "nyc trip flight hotel subway pizza" if t_ < 900 else "boss sword dungeon dragon potion armor"
    said.append({"start": t_, "end": t_ + 0.3, "word": topic.split()[k % 6] if k % 3 == 0 else "and"})
server.projects[FPID] = {"workdir": fwork, "video": fvideo, "title": "Big stream", "light": True, "live": "",
                         "info": {"has_audio": True, "duration": 1800.0}, "duration": 1800.0, "tx_lock": threading.Lock()}
heard_ranges = []
real_hear, real_avail = vodexport.hear_all, whisper.whisper_available
vodexport.hear_all = lambda S, pr, m, l, a, b, *x, **k: heard_ranges.append((a, b)) or said
whisper.whisper_available = lambda: True
job = vodexport.start_segfind(server, FPID, {"length": "short"})
end = time.time() + 30
while job["status"] == "running" and time.time() < end:
    time.sleep(0.05)
vodexport.hear_all, whisper.whisper_available = real_hear, real_avail
found = vodexport.load_found(server.projects[FPID]) or {}
fparts = found.get("parts") or []
check("Find segments listens to the whole video once", heard_ranges == [(0.0, 1800.0)], heard_ranges)
check("and splits it where the topic changes (15:00)", job["status"] == "done" and any(abs(p_["start"] - 900) <= 45 for p_ in fparts),
      (job.get("error"), [(p_["start"], p_["end"], p_["name"]) for p_ in fparts]))
check("each part named from its words", any("trip" in p_["name"] or "nyc" in p_["name"] for p_ in fparts)
      and any("dragon" in p_["name"] or "sword" in p_["name"] or "boss" in p_["name"] for p_ in fparts), [p_["name"] for p_ in fparts])
server.projects[FPID].pop("vod_found")
check("kept with the video, and shown on the screen", (vodexport.load_found(server.projects[FPID]) or {}).get("parts") == fparts
      and [x for x in vodexport.view(server)["videos"] if x["pid"] == FPID][0]["found"]["parts"] == fparts)
ed = segments.add_many(server, FPID, [{"start": p_["start"], "end": p_["end"], "name": p_["name"]} for p_ in fparts[:2]],
                       name="Big stream segments")
check("the ticked parts go to a new edit together, each a segment, in order", ed["name"] == "Big stream segments"
      and [s_["start"] for s_ in ed["segments"]] == [p_["start"] for p_ in fparts[:2]]
      and [s_["name"] for s_ in ed["segments"]] == [p_["name"] for p_ in fparts[:2]], ed)
try:
    segments.add_many(server, FPID, [])
    check("sending nothing is refused", False)
except ValueError:
    check("sending nothing is refused", True)
segments.delete(ed["id"])

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
    opts = {"captions": "srt", "size": "source", "quality": "fast", "hw_encode": False}
    # 5.0.1: each segment is a video of its own (the default)
    job = wait(segments.start_export(server, e["id"], opts))
    res = job.get("result") or {}
    check("each ticked segment exports as its own video", job["status"] == "done" and [f["name"] for f in res.get("files", [])]
          == ["Late.mp4", "Early.mp4"], (job.get("error"), res.get("files")))
    if job["status"] == "done":
        late, early = (f["path"] for f in res["files"])
        check("in a folder named after the edit, in the Segments folder",
              os.path.normcase(res["folder"]) == os.path.normcase(os.path.join(locations.segments_dir(), "Best bits")), res["folder"])
        check("each only as long as what it keeps (Late: 6 s with 2 cut out)", abs(engine.probe(late)["duration"] - 4.0) < 0.35
              and abs(engine.probe(early)["duration"] - 4.0) < 0.35, (engine.probe(late)["duration"], engine.probe(early)["duration"]))
        check("Late's cut stretch is left out", abs(luma(late, 2.5) - 34 * 6 % 256) < 30, luma(late, 2.5))
        check("no subtitle file, even when asked for (5.1.2: YouTube makes its own captions)",
              not os.path.isfile(os.path.splitext(early)[0] + ".srt"))
    # the Segments folder on another drive than the temporary folder: a rename across drives fails (WinError 17)
    real_rename, real_replace = os.rename, os.replace

    def other_drive(real):
        def go(a, b, *x, **k):
            if "part_" in os.path.basename(str(a)):
                raise OSError(18, "The system cannot move the file to a different disk drive")
            return real(a, b, *x, **k)
        return go
    os.rename, os.replace = other_drive(real_rename), other_drive(real_replace)
    try:
        job = wait(segments.start_export(server, e["id"], dict(opts, segment="y")))
    finally:
        os.rename, os.replace = real_rename, real_replace
    check("a segment exports when the Segments folder is on another drive (5.0.2)", job["status"] == "done"
          and os.path.isfile((job.get("result") or {}).get("path", "")), job.get("error"))
    job = wait(segments.start_export(server, e["id"], dict(opts, segment="z")))
    res = job.get("result") or {}
    check("one segment exports on its own, even unticked, straight into the Segments folder", job["status"] == "done"
          and [f["name"] for f in res["files"]] == ["Not used.mp4"]
          and os.path.normcase(res["folder"]) == os.path.normcase(locations.segments_dir()), (job.get("error"), res))
    # and joined into one video, as before 5.0.1
    job = wait(segments.start_export(server, e["id"], dict(opts, mode="join")))
    res = job.get("result") or {}
    check("the edit exports", job["status"] == "done" and os.path.isfile(res.get("path", "")), job.get("error"))
    if job["status"] == "done":
        out = res["path"]
        d = engine.probe(out)["duration"]
        check("as long as what's kept: 4 s of Late (2 cut out) and 4 s of Early, not the unticked one", abs(d - 8.0) < 0.35, d)
        check("in the edit's order: Late first", luma(out, 0.5) > luma(out, 5.0) + 40, (luma(out, 0.5), luma(out, 5.0)))
        check("the cut stretch is left out (32 s jumps to 34 s)", abs(luma(out, 2.5) - 34 * 6 % 256) < 30, luma(out, 2.5))
        check("Late is turned down, Early isn't", rms(out, 0.5) < rms(out, 5.5) * 0.5, (rms(out, 0.5), rms(out, 5.5)))
        check("no captions in the joined video either", not res.get("srt"), res.get("srt"))
        check("named after the edit", res["name"].startswith("Best bits"), res)
        check("saved in the Segments folder, not with the clips (5.0)",
              os.path.normcase(res["folder"]) == os.path.normcase(locations.segments_dir())
              and os.path.normcase(res["folder"]) != os.path.normcase(locations.clips_dir(server.projects[e["pid"]]["workdir"])), res["folder"])
    check("an edit over 30 minutes isn't refused any more (5.0)", not hasattr(segments, "MAX_LEN"))

    # ---- 5.1.1: nothing drawn on the picture, so it's copied, not made again (a VOD's keyframes: one every 2 s)
    import vodexport  # noqa: E402
    kv = os.path.join(work, "keyed.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40",
                    "-vf", "geq=lum='T*6':cb=128:cr=128", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
                    "-keyint_min", "60", "-sc_threshold", "0", "-c:a", "aac", "-shortest", kv], check=True)
    kf = vodexport.keyframes(kv, [5.0, 31.0])
    check("the keyframes near each cut are found", [k for k in kf if k <= 12] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0][:len([k for k in kf if k <= 12])]
          and 4.0 in kf and 30.0 in kf, kf)
    snap = vodexport.snap_to_keyframes([(5.0, 9.0), (33.0, 38.0)], kf)
    check("a segment starts on the keyframe before it, after a cut on the one after", snap == [(4.0, 9.0), (34.0, 38.0)], snap)
    check("an edge too far from a keyframe isn't copied", vodexport.snap_to_keyframes([(5.0, 9.0)], [0.0, 10.0]) == [None])
    server.projects[PID]["video"] = kv
    server.projects[PID]["info"] = engine.probe(kv)
    cur = segments._find(e["id"])
    segments.save({"id": cur["id"], "ver": cur["ver"], "name": "Copied",
                   "segments": [{"id": "c1", "name": "Plain", "start": 5, "end": 15},
                                {"id": "c2", "name": "With a cut", "start": 20, "end": 36, "cuts": [[25, 31]]}]})
    t0 = time.time()
    job = wait(segments.start_export(server, e["id"], {"captions": "off", "size": "source", "quality": "balanced", "hw_encode": False}))
    res = job.get("result") or {}
    check("both copied, not made again", job["status"] == "done" and res.get("copied") == 2, (job.get("error"), res.get("copied")))
    if job["status"] == "done":
        plain, cut = (f["path"] for f in res["files"])
        dp, dc = engine.probe(plain)["duration"], engine.probe(cut)["duration"]
        check("each as long as asked, plus at most the keyframe before (Plain: 10 s from 4 s)", abs(dp - 11.0) < 0.3, dp)
        check("a cut copied from the keyframe after it (20-25, then 32-36)", abs(dc - 9.0) < 0.3, dc)
        check("Plain starts at the keyframe before 5 s (4 s in)", abs(luma(plain, 0.2) - luma(kv, 4.2)) < 2, (luma(plain, 0.2), luma(kv, 4.2)))
        check("the cut stretch isn't shown: 25 s jumps to 32 s", abs(luma(cut, 5.5) - luma(kv, 32.5)) < 2, (luma(cut, 5.5), luma(kv, 32.5)))
        sa = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "csv=p=0", cut],
                            capture_output=True, text=True).stdout.strip()
        check("the sound is as long as the picture (in step)", abs(float(sa or 0) - dc) < 0.15, (sa, dc))

else:
    print("[skip] no ffmpeg here: the edit isn't rendered")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
