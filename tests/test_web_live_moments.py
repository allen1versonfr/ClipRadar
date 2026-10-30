"""
Live moments the window can't play, in Review, in a real browser. Each live moment is its own video, so
one player shows clips from several. Before, once one moment needed Clip Radar's playable copy,
the next moment's clip asked for a copy it didn't have, got its original instead, and stayed blank (its
sound played, no picture) with no message. Now each moment gets its own copy and every one plays.

The moments are made in a codec this browser can't play (HEVC, or H.264 in browsers without it); the test
is skipped when it plays both. Needs FFmpeg, the React build (cd web && npm run build) and Playwright.

  python tests/test_web_live_moments.py
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
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_TEST"] = "1"
os.environ["CLIPRADAR_WAIT_POLL"] = "3"
os.environ["CLIPRADAR_PREVIEW_VP9"] = "1"      # the copy in VP9, which every test browser plays
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
    print("SKIPPED: FFmpeg isn't on PATH")
    sys.exit(0)

import engine  # noqa: E402
import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

from browser import launch  # noqa: E402

CODECS = {"hevc": (["-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1"], 'video/mp4; codecs="hvc1.1.6.L93.B0"'),
          "h264": (["-c:v", "libx264", "-preset", "ultrafast"], 'video/mp4; codecs="avc1.42E01E"')}
STATE = ("(() => { const v = document.querySelector('[data-testid=rv-video]');"
         " return v && {src: v.getAttribute('src'), w: v.videoWidth, rs: v.readyState} })()")

with sync_playwright() as pw:
    browser = launch(pw)
    if browser is None:
        print("SKIPPED: no Chromium-based browser (set CLIPRADAR_BROWSER)")
        sys.exit(0)
    probe = browser.new_page()
    probe.goto("about:blank")
    codec = next((k for k, (_a, mime) in CODECS.items()
                  if not probe.evaluate(f"document.createElement('video').canPlayType({mime!r})")
                  and engine.run([engine.ffmpeg(), "-hide_banner", "-encoders"]).stdout.count(CODECS[k][0][1])), None)
    probe.close()
    if not codec:
        browser.close()
        print("SKIPPED: this browser plays HEVC and H.264 (or FFmpeg can't make the one it doesn't)")
        sys.exit(0)

    srv, url = server.start(7979)
    sess = server.start_live_session("http://127.0.0.1:9/x/live.m3u8", {"wait_for_live": True, "chat_channel": "someone"})
    d = tempfile.mkdtemp(prefix="vc_mom_")
    for n, freq in ((1, 300), (2, 600)):
        v = os.path.join(d, f"moment_{n:04d}.mp4")
        subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=30", *CODECS[codec][0],
                        "-c:a", "aac", "-shortest", v], check=True)
        # what the recorder hands over when it has cut a moment
        server.live_on_moment(sess, {"video": v, "chat": [], "analysis": engine.analyze(v, [], events=False),
                                     "clip": engine.Clip(1, 5.0, 20.0, 10.0, 1.0, "test"), "offset": 100.0 * n,
                                     "manual": True, "number": n, "lat": {}, "aired": None})
    mids = list(sess.moments)
    print(f"  two {codec.upper()} moments, which this browser can't play", flush=True)
    try:
        page = browser.new_page(viewport={"width": 1366, "height": 860})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/")
        page.wait_for_function("window.ClipRadarTest && ClipRadarTest.open() && ClipRadarTest.open().kind === 'live'",
                               timeout=20000)
        page.wait_for_selector("[data-testid=rv-row]", timeout=20000)
        prev = None
        for step, i in enumerate((0, 1, 0)):
            page.locator("[data-testid=rv-row]").nth(i).click()
            st, deadline = None, time.time() + 60
            while time.time() < deadline:
                st = page.evaluate(STATE)
                if st and st["src"] and st["src"] != prev and st["w"] > 0 and st["rs"] >= 2:
                    break
                page.wait_for_timeout(500)
            mid = next((m for m in mids if m in ((st or {}).get("src") or "")), None)
            what = ("the first moment", "then the second moment", "and the first again")[step]
            check(f"{what} plays, picture and all (its own playable copy)",
                  bool(st and mid and st["w"] > 0 and st["rs"] >= 2 and st["src"] == f"/vod/{mid}?preview=1"
                       and (prev is None or mid not in prev)), st)
            prev = (st or {}).get("src")
        check("no errors in the page", not errors, errors[:5])
        page.close()
    finally:
        browser.close()
        for s in list(server.live_sessions.values()):
            try:
                s.stop()
            except Exception:
                pass
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
