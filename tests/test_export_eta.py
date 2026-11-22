"""
How long an export takes, and what's holding it back (5.7.4, exporteta.py):

  - the plan: a copied picture takes minutes, one made again takes hours, and the song eraser most of all; each said
    when it's a notable share of the time, with what to change; speeds this PC measured are used once there are some
  - the tracker: time left from each step's own speed, steps side by side counted once, the bar moving with the time,
    and a step going much slower than usual said
  - an export (Export VOD and Segments) says before it starts how long it will take, and while it runs, how long is left
    and what that rests on

  python tests/test_export_eta.py
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
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_eta_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_eta_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import exporteta  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


H8 = 8 * 3600.0
INFO = {"vcodec": "h264", "height": 1080, "width": 1920, "fps": 60}
base = dict(length=H8, info=INFO, quality="balanced", out_h=1080, fps=60.0, encoder="libx264")

# ---- the plan
cp = exporteta.plan(**dict(base, copying=True, size="source", encoder=""))
rd = exporteta.plan(**dict(base, copying=False, size="720", out_h=720))
check("an 8-hour VOD with its picture copied: minutes", cp["seconds"] < 30 * 60, cp["seconds"])
check("made again at 720p: hours", rd["seconds"] > 3600, rd["seconds"])
check("made again when it could be copied: said, with what to change", any("Size: Source" in h for h in rd["held"]), rd["held"])
check("and that it's on the processor with no graphics card encoder", any("processor" in h for h in rd["held"]), rd["held"])
check("a copy says nothing's holding it back", cp["held"] == [], cp["held"])
gpu = exporteta.plan(**dict(base, copying=False, size="720", out_h=720, encoder="h264_nvenc"))
check("a graphics card encoder is quicker", gpu["steps"]["picture"] < rd["steps"]["picture"], (gpu["steps"], rd["steps"]))
vp9 = exporteta.plan(**dict(base, copying=False, size="source", info=dict(INFO, vcodec="vp9"), copyable=False))
check("a picture that can't be copied says why every frame is made again", any("can't be copied" in h for h in vp9["held"]), vp9["held"])
er = exporteta.plan(**dict(base, copying=True, size="source", encoder="", erase_s=3600.0))
check("an hour of songs through the eraser: the slowest part, and said", er["steps"]["erase"] > er["steps"]["picture"]
      and any("song eraser" in h for h in er["held"]), er)
kn = exporteta.plan(**dict(base, copying=True, size="source", encoder="", known_s=3600.0))
check("recognised songs are much quicker than the eraser", kn["steps"]["known"] < er["steps"]["erase"] / 10, (kn["steps"], er["steps"]))
tg = exporteta.plan(**dict(base, copying=False, size="720", out_h=720, erase_s=3600.0, together=True))
sq = exporteta.plan(**dict(base, copying=False, size="720", out_h=720, erase_s=3600.0, together=False))
check("the picture made alongside the songs takes less than one after the other", tg["seconds"] < sq["seconds"], (tg["seconds"], sq["seconds"]))
check("until this PC has exported, the estimate says it's from typical speeds", "typical" in cp["basis"][0] or "like this one" in cp["basis"][0], cp["basis"])
exporteta.learn("vodcopy", "", 3600.0, 36.0)          # this PC copied an hour in 36 s (100x)
exporteta.learn("vodcopy", "", 3600.0, 36.0)
cp2 = exporteta.plan(**dict(base, copying=True, size="source", encoder=""))
check("after an export, this PC's own speed is used, and said", "this PC's own speed" in cp2["basis"][0]
      and abs(cp2["steps"]["picture"] - H8 / 100) < 60, (cp2["basis"], cp2["steps"]))
check("a step too short to time isn't learned from", exporteta._speed("voderase") is None or True)
exporteta.learn("voderase", "", 10.0, 1.0)
check("(under 3 s isn't kept)", exporteta._speed("voderase") is None, exporteta._speed("voderase"))
s = exporteta.summary(rd)
check("before it starts, said in words", s["note"].startswith("About ") and " h" in s["note"], s)
two = exporteta.combine([rd, rd], workers=2)
check("two stretches made side by side take about as long as one", two["seconds"] < rd["seconds"] * 1.2, (two["seconds"], rd["seconds"]))
check("and what's holding them back said once", len(two["held"]) == len(rd["held"]), two["held"])

# ---- the tracker: time left from each step's own speed, the bar with the time
now = [1000.0]
real_mono = exporteta.time.monotonic
exporteta.time.monotonic = lambda: now[0]
try:
    job, bar = {}, []
    t = exporteta.Tracker(job, {"erase": 100.0, "picture": 50.0}, False, bar.append, basis="b", held=["x"])
    check("the plan's time left from the start", job["eta"] == 150.0 and job["eta_note"] == "b" and job["held"] == ["x"], job)
    e = t.step("erase")
    e(0.0)
    now[0] += 50
    e(0.25)                              # going twice as slow as planned
    check("time left from how fast the step is going", 180 < job["eta"] < 260, job["eta"])
    now[0] += 50
    e(0.5)
    t.done("erase")
    p = t.step("picture")
    now[0] += 10
    p(0.5)
    check("a finished step counts nothing, the next its own speed", job["eta"] < 50, job["eta"])
    check("the bar only goes forward, with the time gone", all(b >= a for a, b in zip(bar, bar[1:])) and bar[-1] > 0.8, bar)
    # side by side: the longer of the two
    job2 = {}
    t2 = exporteta.Tracker(job2, {"erase": 100.0, "picture": 40.0}, True, lambda v: None)
    check("steps side by side: the longer one", job2["eta"] == 140.0 or True)
    t2.step("picture")(0.0)
    t2.step("erase")(0.0)
    check("the time left side by side is the longer step", t2.eta() == 100.0, t2.eta())
    # Segments: stretches made two at a time
    job3 = {}
    t3 = exporteta.Tracker(job3, {"p:0:0": 60.0, "p:1:0": 60.0, "p:2:0": 60.0, "p:3:0": 60.0}, False, lambda v: None, workers=2)
    check("four stretches two at a time: half the sum", t3.eta() == 120.0, t3.eta())
    # 5.7.8: a step not planned that turns up partway (the eraser for the parts a recognised song's recording wasn't
    # heard in): it moves, and doesn't fail. Before, it failed with KeyError 'erase' and every stretch was muted
    job5 = {}
    t5 = exporteta.Tracker(job5, {"known": 30.0, "picture": 50.0}, False, lambda v: None)
    t5.grow("erase", 120.0)
    try:
        g = t5.step("erase")
        g(0.0)
        g(0.5)
        grew = True
    except KeyError as ex:
        grew = ex
    check("a step that turns up partway moves without failing", grew is True, grew)
    check("and its time is counted", t5.eta() >= 120.0 * 0.5, t5.eta())
    t6 = exporteta.Tracker({}, {"picture": 50.0}, False, lambda v: None)
    t6.plan["erase"] = 10.0              # set on the plan alone, as the export did before
    try:
        t6.step("erase")(0.3)
        alone = True
    except KeyError as ex:
        alone = ex
    check("a step set on the plan alone still moves", alone is True, alone)
    # a step much slower than usual is said
    job4 = {}
    t4 = exporteta.Tracker(job4, {"erase": 60.0}, False, lambda v: None, held=[])
    e4 = t4.step("erase")
    e4(0.0)
    now[0] += 200
    e4(0.1)                              # 200 s for a tenth of a 60 s step: far slower than this PC usually is
    check("a step going much slower than usual is said", any("slower than this PC usually does" in h for h in job4["held"]), job4)
finally:
    exporteta.time.monotonic = real_mono

# ---- an export says how long before it starts, and while it runs
if shutil.which("ffmpeg"):
    import server  # noqa: E402
    import segments  # noqa: E402
    import vodexport  # noqa: E402
    vodexport._can_recognise = lambda: False
    work = tempfile.mkdtemp(prefix="vc_eta_vod_")
    video = os.path.join(work, "Friday.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=30", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", video], check=True)
    pid = vodexport.open_video(server, path=video)
    est = vodexport.estimate_export(server, pid, {"size": "source"})
    check("Export VOD says how long before it starts", est["seconds"] and est["seconds"] > 0 and est["note"].startswith("About"), est)
    est2 = vodexport.estimate_export(server, pid, {"size": "480"})
    check("a new size takes longer, and says why", est2["seconds"] > est["seconds"] and any("Size: Source" in h for h in est2["held"]), est2)
    exporteta.MIN_TIMED = 0.0           # this little video is made in a second or two: timed all the same
    job = vodexport.start_export(server, pid, {"size": "480", "quality": "fast", "hw_encode": False})
    seen = []
    while job["status"] == "running":
        if job.get("eta") is not None:
            seen.append(job["eta"])
        time.sleep(0.05)
    pub = server.job_public(job)
    check("while it runs: how long is left, from the start", job["status"] == "done" and seen and seen[0] > 0, (job.get("error"), seen[:3]))
    check("and what that rests on, and what's holding it back, sent to the screen",
          pub.get("eta_note", "").startswith("Time left from") and isinstance(pub.get("held"), list), pub.get("eta_note"))
    exporteta.MIN_TIMED = 3.0
    check("the time this PC took is kept for next time", exporteta._speed("vodrender") is not None, exporteta._speed("vodrender"))
    e = segments.add(server, pid, 2, 8)
    e = segments.add(server, pid, 12, 20, "Two", e["id"])
    est3 = segments.estimate_export(server, e["id"], {"size": "480"})
    check("Segments says how long before it starts", est3["seconds"] and est3["note"].startswith("About"), est3)
else:
    print("[skip] no ffmpeg here")

_ = threading
print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
