"""
Unit tests for the VOD download watchdog, from the real-world test of a 6-hour Twitch VOD: after
100% yt-dlp printed nothing for about 3 minutes while it joined the 18 GB file, so the download was
called stalled; and stopping it killed only yt-dlp's launcher while the real download carried on.

  - a download that is quiet but still writing its file is not stalled
  - a download that is quiet and writing nothing is stopped, with every process it started
  - after 100%, the step says the video is being put together

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

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
