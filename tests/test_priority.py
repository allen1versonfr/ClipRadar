"""
Keep the PC responsive (5.4.2): with Settings › Performance on "Keep the PC responsive" (the default), Clip Radar's own
background work runs below normal priority once the first job starts, not only the render's FFmpeg; "Finish faster"
puts it back. Windows only (elsewhere it's skipped).

  python tests/test_priority.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_prio_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


import render_queue  # noqa: E402
import server  # noqa: E402

if sys.platform != "win32":
    print("[skip] process priority classes are Windows")
    render_queue.open_priority_gate()
    render_queue.background_priority()          # a no-op elsewhere, and never an error
    check("elsewhere it changes nothing and doesn't fail", render_queue._lowered[0] is False)
else:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    k.GetPriorityClass.restype = wintypes.DWORD
    prio = lambda: k.GetPriorityClass(k.GetCurrentProcess())      # noqa: E731
    BELOW, NORMAL = 0x00004000, 0x00000020
    render_queue._set_priority(NORMAL)            # however this runner started Python
    render_queue.apply_perf({"renders": 1, "priority": "responsive"})
    job = server.new_job("test")
    server.run_job(job, lambda: prio())
    import time
    end = time.time() + 10
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    check("a job that starts before the window is open leaves Clip Radar at normal priority (the window inherits it)",
          job.get("result") == NORMAL and prio() == NORMAL, (hex(job.get("result") or 0), hex(prio())))
    render_queue.open_priority_gate()
    check("once the window is open, a job that asked while it waited lowers it at once", prio() == BELOW, hex(prio()))
    render_queue.apply_perf({"renders": 1, "priority": "fast"})
    render_queue.apply_perf({"renders": 1, "priority": "responsive"})
    job = server.new_job("test")
    server.run_job(job, lambda: prio())
    end = time.time() + 10
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    check("a background job runs below normal", job.get("result") == BELOW, hex(job.get("result") or 0))
    check("and so does what starts after it", prio() == BELOW, hex(prio()))
    render_queue.apply_perf({"renders": 1, "priority": "fast"})
    check("Finish faster puts it back to normal", prio() == NORMAL, hex(prio()))
    job = server.new_job("test")
    server.run_job(job, lambda: prio())
    end = time.time() + 10
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    check("and jobs then run at normal priority", job.get("result") == NORMAL, hex(job.get("result") or 0))

print(f"\n{len(failures)} failed" if failures else "\nall passed")
os._exit(1 if failures else 0)
