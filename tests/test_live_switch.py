"""
Two streams clipped at once: opening the second one shows the second one, and it stays open. Before
4.18.2 the page went back to the first running stream every few seconds, because it treated "a live
session is running and you're on another one" as "Keep watching moved on to the next stream". It still
follows Keep watching: when the stream you're looking at ends and its next stream starts, it moves on.

The two sessions wait for streams that never come up (they're running, so this needs no video).

  python tests/test_live_switch.py
"""

from __future__ import annotations

import os
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

import server  # noqa: E402
from browser import launch  # noqa: E402

srv, url = server.start(7986)
one = server.start_live_session("http://127.0.0.1:9/one/live.m3u8", {"wait_for_live": True, "chat_channel": "streamerone"})
two = server.start_live_session("http://127.0.0.1:9/two/live.m3u8", {"wait_for_live": True, "chat_channel": "streamertwo"})
a, b = one.id, two.id
time.sleep(1.5)

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
        page.wait_for_function("ClipRadarTest.open() && ClipRadarTest.open().kind === 'live'", timeout=15000)
        check("with live clipping running, the page opens it", page.evaluate("ClipRadarTest.open().id") == a,
              page.evaluate("ClipRadarTest.open()"))
        check("both streams are running", [x.id for x in server.live_list()] == [a, b])

        # open the second stream from Running now, then wait through several status checks (every 5 s)
        page.click("[data-testid=nav-home]")
        page.wait_for_selector(f"[data-src-open-live='{b}']", timeout=15000)
        page.click(f"[data-src-open-live='{b}']")
        page.wait_for_function(f"ClipRadarTest.open().id === '{b}'", timeout=10000)
        check("opening the second stream shows it", True)
        seen = set()
        for _ in range(13):                                   # 13 s: two or three status checks
            page.wait_for_timeout(1000)
            seen.add(page.evaluate("ClipRadarTest.open().id"))
        check("and it stays open (no jumping back to the first)", seen == {b}, seen)

        # and back to the first, which stays too
        page.click("[data-testid=nav-home]")
        page.click(f"[data-src-open-live='{a}']")
        page.wait_for_function(f"ClipRadarTest.open().id === '{a}'", timeout=10000)
        page.wait_for_timeout(6500)
        check("switching back to the first works, and stays", page.evaluate("ClipRadarTest.open().id") == a)

        # Keep watching: the stream you're on ended and its next stream started: the page moves on to it
        server.live_sessions[a].extra["next"] = b
        page.wait_for_function(f"ClipRadarTest.open().id === '{b}'", timeout=12000)
        check("Keep watching's next stream is followed", True)
        check("and says the stream ended", "Stream ended" in page.locator("[data-testid=toasts]").inner_text())
        check("no errors in the page", not errors, errors[:5])
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
