"""
Export VOD in the new UI against the real server, in a real browser, on a generated video: the open video
is listed, the copyright scan's music stretches are shown and can be kept, the export runs and its file is
the whole video with the music muted, and the screen fits a narrow window. The scan's result is put in
directly (the sound model isn't needed); the export is real.

Needs FFmpeg, the React build (cd web && npm run build) and Playwright; skipped without them.

  python tests/test_web_vodexport.py [--shots DIR]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []
SHOTS = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else ""


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIPPED: Playwright isn't installed (pip install playwright)")
    sys.exit(0)
if not shutil.which("ffmpeg"):
    print("SKIPPED: FFmpeg isn't on PATH")
    sys.exit(0)

import numpy as np  # noqa: E402

import engine  # noqa: E402
import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

from browser import launch  # noqa: E402

engine.whisper_available = lambda: False          # captions and word muting aren't what this checks
srv, url = server.start(7994)


def call(path, body=None):
    rq = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq) as r:
        return json.load(r)


vod = os.path.join(tempfile.mkdtemp(prefix="vc_vx_"), "vod_export_test.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=60",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=60",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", vod], check=True)
job = call("/api/analyze", {"source": vod, "count": 3})["job"]
deadline = time.time() + 180
while time.time() < deadline:
    j = call("/api/job/" + job)
    if j["status"] != "running":
        break
    time.sleep(0.5)
check("the video is analysed", j["status"] == "done", j.get("error"))
pid = j["result"]["pid"]
server.projects[pid]["copyright"] = {"spans": [{"start": 5.0, "end": 15.0, "confidence": 0.92},
                                               {"start": 40.0, "end": 50.0, "confidence": 0.55}], "at": 1.0, "how": "test"}

with sync_playwright() as p:
    browser = launch(p)
    if browser is None:
        print("SKIPPED: no browser for Playwright")
        sys.exit(0)
    page = browser.new_page(viewport={"width": 1366, "height": 860})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url + "/#vod")
    page.get_by_test_id("vodx-video").first.wait_for()
    check("the open video is listed", "vod_export_test" in page.get_by_test_id("vodx-video").first.inner_text()
          or page.get_by_test_id("vodx-video").count() == 1)
    check("the nav has Export VOD", page.get_by_test_id("nav-vod").get_attribute("aria-current") == "page")
    summary = page.get_by_test_id("vodx-music-summary").inner_text()
    check("the music found is summed up", summary == "2 music stretches, 0:20 in all. All muted.", summary)
    page.get_by_test_id("vodx-span").nth(1).uncheck()            # keep the second stretch
    check("a stretch can be kept", "1 muted, 1 kept" in page.get_by_test_id("vodx-music-summary").inner_text())
    page.get_by_role("radio", name="480p").click()
    page.get_by_role("radio", name="Fast").click()
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, "vodx-desktop.png"), full_page=True)
    page.get_by_test_id("vodx-export").click()
    page.get_by_test_id("vodx-done").wait_for(timeout=120_000)
    check("the export finishes", "Saved" in page.get_by_test_id("vodx-done").inner_text())
    ex = server.jobs[server.projects[pid]["vodexport_job"]]
    r = ex.get("result") or {}
    info = engine.probe(r.get("path", vod))
    check("the file is the whole video", abs(info["duration"] - 60) < 0.6, info)

    def rms(at, ln=0.5):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at}", "-t", f"{ln}", "-i", r["path"], "-ac", "1",
                              "-f", "f32le", "-"], capture_output=True).stdout
        x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
        return float(np.sqrt((x ** 2).mean())) if x.size else -1.0
    check("the muted music is silent", rms(8) < 0.005, rms(8))
    check("the kept music plays", rms(44) > 0.05, rms(44))
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(200)
    wide = page.evaluate("[document.documentElement.scrollWidth, document.querySelector('.main').scrollWidth]")
    check("fits a narrow window", wide[0] <= 390 and wide[1] <= 390, wide)
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, "vodx-phone.png"), full_page=True)
    check("no errors in the page", not errors, errors)
    browser.close()

srv.shutdown()
print(f"\n{len(failures)} failed" if failures else "\nPASSED")
sys.exit(1 if failures else 0)
