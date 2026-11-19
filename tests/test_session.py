"""
Where you left off (5.0.2): close Clip Radar and open it again, and the videos open in Moments and Export VOD come
back, a VOD with your review as you left it. A video deleted since isn't opened (and never downloaded again).

  python tests/test_session.py
"""

from __future__ import annotations

import os
import shutil
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
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_ses_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_ses_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


import session  # noqa: E402
import server  # noqa: E402
import vodexport  # noqa: E402

check("nothing to restore the first time", session.restore(server) == {"vods": 0, "light": 0, "failed": 0}
      and session.start(server) is None)

if not shutil.which("ffmpeg"):
    print("[skip] no ffmpeg here")
else:
    folder = tempfile.mkdtemp(prefix="vc_ses_vid_")

    def make(name, secs=40):
        p = os.path.join(folder, name)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=15:duration={secs}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", "-shortest", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", p], check=True)
        return p

    vod, other, gone = make("stream one.mp4"), make("export me.mp4"), make("deleted later.mp4")

    def wait(job, s=180):
        end = time.time() + s
        while job["status"] == "running" and time.time() < end:
            time.sleep(0.1)
        return job

    job = server.new_job("analyze")
    server.run_job(job, lambda: server.do_analyze(job, {"source": vod, "count": 3, "events": False}))
    wait(job)
    check("a VOD opens for Moments", job["status"] == "done", job.get("error"))
    pid = job["result"]["pid"]
    pr = server.projects[pid]
    pr["clips"][0]["state"] = "kept"
    server.save_project(pid)
    light = vodexport.open_video(server, path=other)
    dead = vodexport.open_video(server, path=gone)
    saved = session.load()
    check("both are remembered", len(saved["vods"]) == 1 and len(saved["light"]) == 2, saved)

    # ---- Clip Radar closes; it opens again
    server.projects.clear()
    os.remove(gone)
    done = session.restore(server, wait_job=wait)
    check("the VOD is back in Moments, the export video in Export VOD", done == {"vods": 1, "light": 1, "failed": 0}, done)
    check("under the same ids, so the page finds them", pid in server.projects and light in server.projects
          and vodexport.usable(server.projects[light]), list(server.projects))
    check("the review as it was left", server.projects[pid]["clips"][0]["state"] == "kept" and server.projects[pid].get("restored"))
    check("a video deleted since isn't opened", dead not in server.projects)
    check("nothing is still being restored", session.state["restoring"] == 0)
    check("opening it again doesn't list it twice", len(session.load()["vods"]) == 1, session.load())

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
