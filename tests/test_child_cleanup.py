"""
FFmpeg doesn't outlive Clip Radar (Windows). A stand-in for the app starts a long FFmpeg the way
the app does (procjob.adopt), then is ended the hard way (TerminateProcess, like a crash or Task
Manager's End task). Its FFmpeg must end too. As a control, the same without adopt() leaves FFmpeg
running, which shows the test can tell the difference.

  python tests/test_child_cleanup.py          (Windows with FFmpeg on PATH; skipped elsewhere)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


if sys.platform != "win32" or not shutil.which("ffmpeg"):
    print("SKIPPED: needs Windows and FFmpeg")
    sys.exit(0)

sys.path.insert(0, SRC)
import procjob  # noqa: E402

# the stand-in app: start FFmpeg (two minutes in real time, -re: without it a fast PC is done in a second),
# print its process id, then wait to be killed
APP = r"""
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import procjob
p = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                      "-t", "120", "-f", "null", "-"], stdin=subprocess.DEVNULL)
ok = procjob.adopt(p) if sys.argv[2] == "adopt" else False
print(p.pid, ok, flush=True)
time.sleep(600)
"""


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def run(mode: str):
    app = subprocess.Popen([sys.executable, "-c", APP, SRC, mode], stdout=subprocess.PIPE, text=True)
    line = app.stdout.readline().split()
    pid, adopted = int(line[0]), line[1] == "True"
    time.sleep(1.0)
    running = alive(pid)
    app.kill()                                   # TerminateProcess: no cleanup code runs in the app
    app.wait(timeout=10)
    end = time.time() + 10
    while alive(pid) and time.time() < end:
        time.sleep(0.2)
    return pid, adopted, running, alive(pid)


pid, adopted, running, after = run("adopt")
check("FFmpeg is tied to the app", adopted and running, (adopted, running))
check("killing the app ends its FFmpeg", not after, f"FFmpeg {pid} still running")

pid, adopted, running, after = run("plain")
check("control: without it, FFmpeg outlives a killed app (so the check above means something)", running and after,
      (running, after))
subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

check("adopt() is harmless for a process that has already finished",
      procjob.adopt(subprocess.Popen([sys.executable, "-c", "pass"])) in (True, False))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
