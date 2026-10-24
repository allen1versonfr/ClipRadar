"""
The Review preview plays videos the app window can't (4.18.1): HEVC or AV1 from Twitch's enhanced
streams or OBS, or a container it can't read. When the window can't play the video, Clip Radar makes a
preview copy it can play and switches to it at the same moment; renders still use the original.

Here the source is MPEG-4 Part 2 in an AVI, which no Chromium-based browser plays (FFmpeg reads it).
The copy is VP9 and Opus here (CLIPRADAR_PREVIEW_VP9), because Playwright's Chromium can't play the
H.264 the app's copy uses; in the app window H.264 always plays.

  python tests/test_preview_fallback.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_PREVIEW_VP9"] = "1"
os.environ.pop("CLIPRADAR_UI", None)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


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
    print("SKIPPED: FFmpeg isn't installed")
    sys.exit(0)

import engine  # noqa: E402
import server  # noqa: E402
from browser import launch  # noqa: E402

tmp = tempfile.mkdtemp(prefix="vc_prev_")
vod = os.path.join(tmp, "old camera.avi")
loud = "+".join(f"between(t,{t},{t + 2})" for t in (20, 45, 70, 95))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
                "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.02", "-af", f"volume='if({loud},40,1)':eval=frame",
                "-shortest", "-c:v", "mpeg4", "-q:v", "6", "-c:a", "mp3", vod], check=True)

srv, url = server.start(7988)
with sync_playwright() as pw:
    browser = launch(pw)
    if browser is None:
        print("SKIPPED: no Chromium-based browser (set CLIPRADAR_BROWSER)")
        sys.exit(0)
    page = browser.new_page(viewport={"width": 1366, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(url + "/")
        page.wait_for_timeout(800)
        page.fill("[data-testid=source-input]", vod)
        page.click("[data-testid=find-clips]")
        page.wait_for_selector("[data-testid=batch-bar]", state="visible", timeout=120000)
        page.wait_for_function("document.querySelector('#vod').src.includes('preview=1')", timeout=120000)
        check("a video the window can't play gets a preview copy, without asking", True)
        page.wait_for_function("document.querySelector('#vod').readyState >= 2 && document.querySelector('#vod').videoWidth > 0",
                               timeout=30000)
        st = page.evaluate("(() => { const v = document.querySelector('#vod'); return {w: v.videoWidth, h: v.videoHeight,"
                           " t: v.currentTime, badge: document.querySelector('#phoneBadge').textContent} })()")
        check("the copy plays, with a picture no taller than the source (360p here)", st["w"] == 640 and st["h"] == 360, st)
        check("no 'can't play' badge once it plays", "can't play" not in st["badge"], st)
        page.evaluate("document.querySelector('#vod').play()")
        t0 = page.evaluate("document.querySelector('#vod').currentTime")
        page.wait_for_timeout(1500)
        t1 = page.evaluate("document.querySelector('#vod').currentTime")
        check("and it actually plays (time moves)", t1 > t0 + 0.5, (t0, t1))
        pid = page.evaluate("document.querySelector('#vod').dataset.src.split('/vod/')[1]")
        pr = server.projects[pid]
        check("the copy is in Clip Radar's temp files, not next to your video",
              os.path.isfile(server.preview_path(pr)) and not os.path.dirname(server.preview_path(pr)).startswith(tmp)
              and os.listdir(tmp) == ["old camera.avi"], (server.preview_path(pr), os.listdir(tmp)))
        check("and it's made once per video", server.start_preview(pid) == {"ready": True})
        check("the project still renders from the original", pr["video"] == vod)
        check("no errors in the page", not errors, errors[:5])
    finally:
        browser.close()

# the copy itself: H.264 and AAC (what the app makes), never taller than 480 or than the source
out = os.path.join(tmp, "copy.mp4")
os.environ.pop("CLIPRADAR_PREVIEW_VP9")
big = os.path.join(tmp, "big.mkv")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=3",
                "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "libopus", big], check=True)
seen = []
engine.make_preview(big, out, 3.0, seen.append)
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,height,r_frame_rate", "-of", "csv=p=0", out],
                       capture_output=True, text=True).stdout.split()
check("the app's copy is H.264 480p at 30 fps with AAC sound", probe == ["h264,480,30/1", "aac,0/0"], probe)
check("and reports its progress", seen and seen[-1] > 0.9, seen[-3:])
check("nothing half-made is left behind", sorted(os.listdir(tmp)) == ["big.mkv", "copy.mp4", "old camera.avi"], os.listdir(tmp))
try:
    engine.make_preview(os.path.join(tmp, "nope.mp4"), os.path.join(tmp, "x.mp4"), 3.0)
    check("a video it can't read fails with a reason", False)
except RuntimeError as e:
    check("a video it can't read fails with a reason", "Couldn't make a preview copy" in str(e)
          and not os.path.exists(os.path.join(tmp, "x.mp4.part.mp4")), str(e))

server.SHUTTING_DOWN[0] = True
srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
