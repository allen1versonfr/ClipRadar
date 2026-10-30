"""
Unit tests for the VOD download watchdog, from the real-world test of a 6-hour Twitch VOD: after
100% yt-dlp printed nothing for about 3 minutes while it joined the 18 GB file, so the download was
called stalled; and stopping it killed only yt-dlp's launcher while the real download carried on.

  - a download that is quiet but still writing its file is not stalled
  - a download that is quiet and writing nothing is stopped, with every process it started
  - after 100%, the step says the video is being put together, and how far that has got
  - the space check counts a Twitch VOD's size (from its bitrate) and the join's second copy
  - background work (chat, the early sound) runs once per project, and its errors reach the caller

  python tests/test_download.py
"""

from __future__ import annotations

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

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# a stand-in for yt-dlp: prints progress to 100%, then goes quiet and either keeps growing the
# output (joining) or hangs with a child process (a wedged download), then finishes
FAKE = r'''
import os, subprocess, sys, time
mode = os.environ["FAKE_MODE"]
out = [a for a in sys.argv if "vod.%(ext)s" in a][0].replace("%(ext)s", "mp4")
folder = os.path.dirname(out)
for k in range(1, 11):
    print(f"VCPROG {k * 100} 1000 None None None 5000000 {10 - k}", flush=True)
    time.sleep(0.05)
if mode == "join":
    with open(out, "wb") as f:                     # the downloaded pieces, before they're joined
        f.write(b"x" * 100000 * int(os.environ["FAKE_JOIN_S"]))
    with open(os.path.join(folder, "vod.temp.mp4"), "wb") as f:
        for _ in range(int(os.environ["FAKE_JOIN_S"])):
            f.write(b"x" * 100000); f.flush(); time.sleep(1)
    os.replace(os.path.join(folder, "vod.temp.mp4"), out)
else:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(os.environ["FAKE_PID"], "w").write(str(child.pid))
    time.sleep(120)
'''

tmp = tempfile.mkdtemp(prefix="vc_dl_")
script = os.path.join(tmp, "fake_ytdlp.py")
open(script, "w").write(FAKE)
if os.name == "nt":
    exe = os.path.join(tmp, "yt-dlp.bat")
    open(exe, "w").write(f'@"{sys.executable}" "{script}" %*\n')
else:
    exe = os.path.join(tmp, "yt-dlp")
    open(exe, "w").write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    os.chmod(exe, 0o755)

engine.DOWNLOAD_STALL_S = 4
steps, logs = [], []


def run(mode, **env):
    work = tempfile.mkdtemp(prefix="vc_dlw_")
    os.environ.update(FAKE_MODE=mode, **{k: str(v) for k, v in env.items()})
    t0 = time.monotonic()
    try:
        r = engine._ytdlp_cli(exe, "https://www.twitch.tv/videos/1", work, "best", "", logs.append, lambda f: None,
                              steps.append, lambda: None)
        return r, None, time.monotonic() - t0
    except RuntimeError as e:
        return None, str(e), time.monotonic() - t0


# quiet for 9 s after 100% (more than twice the stall limit) but writing: not a stall
r, err, took = run("join", FAKE_JOIN_S=9)
check("quiet but still writing its file: the download finishes", r is not None and r[0].endswith("vod.mp4"), err)
check("the step says the video is being put together", any("together" in s for s in steps), steps[-3:])
pcts = [int(s.rsplit(" ", 1)[1].rstrip("%")) for s in steps if s.startswith("Putting the video together, ")]
check("and how far the joining has got", pcts and all(0 < p <= 100 for p in pcts) and pcts == sorted(pcts), steps[-5:])

# quiet and writing nothing: stopped after the limit, child processes included
pidfile = os.path.join(tmp, "child.pid")
r, err, took = run("hang", FAKE_PID=pidfile)
check("quiet and writing nothing: stopped as stalled", r is None and err and "stopped" in err and took < 30, (err, round(took, 1)))
time.sleep(1)
child = int(open(pidfile).read()) if os.path.exists(pidfile) else None


def alive(pid):
    if pid is None:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                    # a zombie that hasn't been reaped yet is gone too
        return "Z" not in open(f"/proc/{pid}/stat").read().split()[2]
    except OSError:
        return True


check("and everything it started is stopped too (no download left running behind)", child and not alive(child), child)

# disk space: Twitch gives no size, only the bitrate; the join keeps two copies on disk for a while
check("a Twitch VOD's size comes from its bitrate and length",
      abs(engine._size_of({"tbr": 7927.062, "duration": 33050}) - 32.75e9) < 0.01e9, engine._size_of({"tbr": 7927.062, "duration": 33050}))
check("a size yt-dlp gives is used as it is", engine._size_of({"filesize": 5e9, "tbr": 1, "duration": 1}) == 5e9)
check("no size, no bitrate: unknown", engine._size_of({}) == 0 and engine._size_of(None) == 0)
import locations  # noqa: E402
real_free = locations.free_space
try:
    locations.free_space = lambda d: 40e9
    try:
        engine._check_download_space(tempfile.mkdtemp(), 32.75e9)
        stopped = False
    except RuntimeError as e:
        stopped = "Not enough space" in str(e)
    check("40 GB free isn't enough for a 33 GB VOD (it needs room for the join's second copy)", stopped)
    locations.free_space = lambda d: 80e9
    engine._check_download_space(tempfile.mkdtemp(), 32.75e9)
    check("80 GB free is", True)
finally:
    locations.free_space = real_free

# the join's progress, from the file being written against what goes in
jd = tempfile.mkdtemp(prefix="vc_join_")
check("not joining: no join progress", engine._join_progress(jd) is None)
open(os.path.join(jd, "vod.mp4"), "wb").write(b"x" * 1000)
open(os.path.join(jd, "vod.temp.mp4"), "wb").write(b"x" * 250)
check("a quarter written: 25%", abs((engine._join_progress(jd) or 0) - 0.25) < 1e-9, engine._join_progress(jd))

# background work: one per key at a time, so a resumed download picks up its chat download still running
gate = __import__("threading").Event()
a = engine._Soon.start(("chat", "x"), lambda s: gate.wait(5) and "first")
b = engine._Soon.start(("chat", "x"), lambda s: "second")
check("started again while running: the same work, not a second copy", a is b)
gate.set()
check("its result", a.wait() == "first")
c = engine._Soon.start(("chat", "x"), lambda s: "again")
check("once it's finished, starting it again runs it again", c is not a and c.wait() == "again")
d = engine._Soon.start(("x", "y"), lambda s: 1 / 0)
try:
    d.wait()
    raised = False
except ZeroDivisionError:
    raised = True
check("an error in the background reaches whoever waits for it", raised and d.mark.is_set())

# ---------------------------------------------------------------- one bar for a video and its sound
seen = []
st = engine._Stepper(seen.append, lambda s: None, lambda s: None)
st.parts([900, 100])
for d in range(0, 901, 300):
    st.update(d, 900, None, None, None, None)
after_video = seen[-1]
for d in (0, 50, 100):
    st.update(d, 100, None, None, None, None)
check("video then sound: the bar doesn't start again for the sound", seen == sorted(seen), seen)
check("the video is its share of the bar, by size", abs(after_video - 0.9 * 0.98) < 0.01, after_video)
check("and the sound brings it to the end", seen[-1] > 0.97, seen[-1])
seen2 = []
st = engine._Stepper(seen2.append, lambda s: None, lambda s: None)
st.parts([0, 0])
st.update(500, 1000, None, None, None, None)
check("sizes unknown: the video counts for 90%", abs(seen2[-1] - 0.45 * 0.98) < 0.01, seen2)
seen3 = []
st = engine._Stepper(seen3.append, lambda s: None, lambda s: None)
for i, n in ((10, 100), (50, 100), (40, 100)):     # a piece count that goes back never moves the bar back
    st.update(None, None, i, n, None, None)
check("the bar never goes backwards", seen3 == sorted(seen3), seen3)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
