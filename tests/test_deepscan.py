"""
Unit tests for Deep Scan's lifecycle: it finishes, it stops when asked, and it reports errors
instead of hanging or finishing "with no gems" when it couldn't actually listen.
Whisper and FFmpeg are stood in for, so this runs in a second.

  python tests/test_deepscan.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402
import engine  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


calls = {"extract": 0, "transcribe": 0}
behaviour = {"audio": "ok", "slow": 0.0, "whisper": "ok"}


def fake_extract(video, start, length):
    calls["extract"] += 1
    if behaviour["audio"] == "broken":
        return None
    if behaviour["audio"] == "gap" and 120 <= start < 180:
        return None
    return np.zeros(int(16000 * min(length, 2)), dtype=np.float32)


def fake_transcribe(audio, model, lang, log, prompt=""):
    calls["transcribe"] += 1
    if behaviour["whisper"] == "fail":
        raise RuntimeError("Couldn't load Whisper. cpu: model download failed")
    time.sleep(behaviour["slow"])
    return [{"start": 0.5, "end": 0.9, "word": "hello"}]


engine.extract_audio = fake_extract
engine.transcribe = fake_transcribe
engine.whisper_available = lambda: True
server._vocab = lambda pr: ""
server._save_transcripts = lambda pr: None
server._update_gems = lambda pid, model, lang: server.projects[pid].__setitem__("gems", [])


def project(duration: float) -> str:
    pid = f"p{len(server.projects)}"
    server.projects[pid] = {"info": {"has_audio": True}, "analysis": types.SimpleNamespace(duration=duration),
                            "tx_lock": threading.Lock(), "video": "x.mp4", "clips": [], "workdir": tempfile.mkdtemp()}
    return pid


def wait(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = server.deep_status(server.projects[pid])
        if st["state"] != "running":
            return st
        time.sleep(0.02)
    return server.deep_status(server.projects[pid])


# 1. finishes
pid = project(600)
server.start_deep_scan(pid, "tiny", "en")
st = wait(pid)
check("finishes", st["state"] == "done" and st["progress"] == 1.0, st)
check("covers the whole video once", server._covered(server.projects[pid], "tiny", "en") >= 0.99)
n = calls["transcribe"]
server.projects[pid]["deep_done"] = False
server.start_deep_scan(pid, "tiny", "en")
wait(pid)
check("running it again transcribes nothing twice", calls["transcribe"] == n, (n, calls["transcribe"]))

# 2. a zero-length video ends at once
pid = project(0)
server.start_deep_scan(pid, "tiny", "en")
check("an empty video ends at once", wait(pid, 3)["state"] == "done")

# 3. stops when asked
behaviour["slow"] = 0.05
pid = project(3600)
job = server.start_deep_scan(pid, "tiny", "en")
time.sleep(0.2)
job["cancel"] = True
st = wait(pid, 5)
check("stops when asked", st["state"] == "cancelled" and job["status"] == "cancelled", (st, job["status"]))
check("and doesn't claim it finished", not server.projects[pid].get("deep_done"))
behaviour["slow"] = 0.0

# 4. sound can't be read: an error, not "done, no gems"
behaviour["audio"] = "broken"
pid = project(900)
server.start_deep_scan(pid, "tiny", "en")
st = wait(pid)
check("unreadable sound is an error", st["state"] == "error" and "sound" in st["error"], st)
check("and nothing is marked as transcribed", server._covered(server.projects[pid], "tiny", "en") == 0.0)

# 5. one unreadable minute is skipped, the rest carries on
behaviour["audio"] = "gap"
pid = project(600)
job = server.start_deep_scan(pid, "tiny", "en")
st = wait(pid)
check("a short unreadable stretch is skipped, not fatal", st["state"] == "done", st)
check("and said in the log", any("skipped" in line for line in job.get("log", [])), job.get("log"))
behaviour["audio"] = "ok"

# 6. Whisper can't load
behaviour["whisper"] = "fail"
pid = project(600)
server.start_deep_scan(pid, "tiny", "en")
st = wait(pid)
check("Whisper failing to load is reported", st["state"] == "error" and "Whisper" in st["error"], st)
behaviour["whisper"] = "ok"

# 7. starting twice joins the running scan
behaviour["slow"] = 0.05
pid = project(3600)
j1 = server.start_deep_scan(pid, "tiny", "en")
j2 = server.start_deep_scan(pid, "tiny", "en")
check("starting twice joins the running scan", j1 is j2)
j1["cancel"] = True
wait(pid, 5)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
