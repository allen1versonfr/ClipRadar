"""
The step timer (issue 47): what it adds up, the one line it logs, and what a render and a title report through it.

  python tests/test_timing.py
"""

from __future__ import annotations

import os
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
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "src", "stubs"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import gpu  # noqa: E402
import server  # noqa: E402
import timing  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


t = timing.Timer("test", "a clip")
seen = []
step = t.marking(seen.append)
step("Downloading the video")
time.sleep(0.03)
step("Putting the video together, 40%")
step("Putting the video together, 80%")
step("Downloading the video")
time.sleep(0.03)
t.add("Whisper", 2.5)
t.add("Whisper", 1.5)
t.add("clock moved back", -5)
out = t.done()
check("a job's step() still gets its text", seen[1] == "Putting the video together, 40%", seen)
check("a step with a changing number is one step", sum(k.startswith("Putting") for k in out["steps"]) == 1, out)
check("a step seen twice adds up", out["steps"]["Downloading the video"] >= 0.05, out)
check("timed elsewhere adds up too", out["steps"]["Whisper"] == 4.0, out)
check("time never goes backwards", out["steps"]["clock moved back"] == 0.0, out)
check("the job is kept", timing.recent()[-1]["name"] == "a clip")
for n in range(timing.KEEP + 5):
    timing.Timer("many").done()
check("only the recent ones are kept", len(timing.recent()) == timing.KEEP)
blank = timing.Timer("blank")
blank.marking(lambda m: None)("100%")
check("a step that's only a number keeps its text", "100%" in blank.done()["steps"])

# a title: what it waited for, reading the sound and Whisper are told apart
gpu.choose, real_choose = (lambda task, size, log=None: {"engine": "whisper", "why": ""}), gpu.choose
real = engine.extract_audio, engine.transcribe
engine.extract_audio = lambda v, a, n: np.zeros(int(16000 * n), dtype=np.float32)
engine.transcribe = lambda *a, **k: [{"start": 1.0, "end": 1.5, "word": "hi"}]
server._vocab = lambda p: ""
try:
    pr = {"tx_lock": threading.Lock(), "duration": 60.0, "video": "x.mp4"}
    t = timing.Timer("title")
    server.ensure_words(pr, 0, 10, "small", "en", task="titles", timer=t)
    server.ensure_words(pr, 0, 10, "small", "en", task="titles", timer=t)
    check("a title's steps: the sound, then Whisper on the CPU", set(t.steps) == {"reading the sound", "Whisper (CPU)"}, t.steps)
    pr2 = {"tx_lock": threading.Lock(), "duration": 60.0, "video": "x.mp4"}
    check("nobody has to time it", len(server.ensure_words(pr2, 0, 10, "small", "en", task="titles")) == 1)
finally:
    engine.extract_audio, engine.transcribe = real
    gpu.choose = real_choose

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
