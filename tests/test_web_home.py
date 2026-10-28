"""
The new UI's Home screen against the real server and a real video, in a real browser: it's the
default screen at /next/, Find clips opens a video with the options you chose, Running now shows it,
and when it's ready the video opens in Review (the original page's, until Review is ported) with
its clips. Recent then lists it, and the settings you picked are the ones the original page uses.

Needs the React build (cd web && npm run build), FFmpeg and Playwright; skipped without them.

  python tests/test_web_home.py
"""

from __future__ import annotations

import json
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

import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

from browser import launch  # noqa: E402

tmp = tempfile.mkdtemp(prefix="vc_webhome_")
vod = os.path.join(tmp, "home_test.mp4")
# quiet noise with four loud bursts: moments to suggest (the same source tests/test_ui.py uses)
loud = "+".join(f"between(t,{t},{t + 2})" for t in (20, 45, 70, 95))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
                "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.02", "-af", f"volume='if({loud},40,1)':eval=frame",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", vod], check=True)
srv, url = server.start(7997)

with sync_playwright() as pw:
    browser = launch(pw)
    if browser is None:
        print("SKIPPED: no Chromium-based browser (set CLIPRADAR_BROWSER)")
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        sys.exit(0)
    page = browser.new_page(viewport={"width": 1366, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(url + "/next/")
    page.wait_for_selector("[data-testid=find-clips]", timeout=15000)
    check("Home is the new UI's first screen", page.locator("h1", has_text="New project").count() == 1)
    bg = page.evaluate("getComputedStyle(document.querySelector('[data-testid=find-clips]')).backgroundColor")
    check("Find clips is the accent green (DESIGN.md)", bg.replace(" ", "") == "rgb(47,227,111)", bg)
    live_bg = page.evaluate("getComputedStyle([...document.querySelectorAll('button')]"
                            ".find(b => b.textContent.includes('Start live clipping'))).backgroundColor")
    check("Start live clipping is a secondary button, so Find clips is the one main action",
          live_bg.replace(" ", "") != "rgb(47,227,111)", live_bg)
    check("no tools banner when the tools are there", page.locator("[data-testid=tools-banner]").count() == 0)

    # choices made here are the original page's choices too
    vodcard = page.get_by_role("region", name="Past broadcast or video")
    vodcard.get_by_label("Clips to suggest").select_option("5")
    vodcard.get_by_label("Stream type").select_option("gaming")
    page.screenshot(path=os.environ.get("CLIPRADAR_SHOT") or os.path.join(tmp, "home.png"), full_page=True)
    page.fill("[data-testid=source-input]", vod)
    page.click("[data-testid=find-clips]")
    page.wait_for_selector("[data-testid=vod-job]", timeout=15000)
    check("Find clips shows its progress", True)
    page.wait_for_selector("[data-testid=running-now] [data-testid=source-row]", timeout=15000)
    check("and Running now lists the video", "home_test" in page.locator("[data-testid=running-now]").inner_text())

    # ready: Review opens with its clips (the original page's Review, opened by the handoff)
    page.wait_for_url("**/legacy/**", timeout=180000)
    page.wait_for_selector("[data-testid=batch-bar]", state="visible", timeout=60000)
    clips = page.evaluate("ClipRadarTest.clips()")
    check("the finished video opens in Review with its clips", 1 <= len(clips) <= 5, clips)
    check("and the hand-over link is cleaned up", page.evaluate("location.hash") == "", page.evaluate("location.hash"))
    stored = page.evaluate("({count: localStorage.getItem('vc_count2'), preset: localStorage.getItem('vc_preset'),"
                           " cw: localStorage.getItem('vc_cw'), src: localStorage.getItem('vc_src')})")
    check("the options picked in the new UI are the original page's options",
          stored["count"] == '"5"' and stored["preset"] == '"gaming"' and stored["cw"] == "55"
          and json.loads(stored["src"]) == vod, stored)
    job = next((j for j in server.jobs.values() if j["kind"] == "analyze"), {})
    check("the server was asked with those options", (job.get("body") or {}).get("count") == 5
          and (job.get("body") or {}).get("detect", {}).get("preset") == "gaming", (job.get("body") or {}))

    # back in the new UI: Recent has it, and a click opens it again (no second download)
    page.goto(url + "/next/#home")
    page.wait_for_selector("[data-testid=recent-row]", timeout=15000)
    check("Recent lists the video", "home_test" in page.locator("[data-testid=recent-table]").inner_text())
    page.locator("[data-testid=recent-row]").first.click()
    page.wait_for_url("**/legacy/**", timeout=60000)
    page.wait_for_selector("[data-testid=batch-bar]", state="visible", timeout=60000)
    check("a Recent video opens again in Review", len(page.evaluate("ClipRadarTest.clips()")) >= 1)
    # what the new UI doesn't have yet opens in the original page, on that screen
    page.goto(url + "/legacy/#view=settings")      # the hand-over link the new UI uses (legacyViewUrl)
    page.wait_for_selector("#view-settings", state="visible", timeout=15000)
    check("a #view=settings hand-over lands on Settings", page.locator("#view-home").is_hidden())
    page.evaluate("location.hash = '#view=exports'")
    page.wait_for_selector("#view-exports", state="visible", timeout=5000)
    check("a hand-over link works without a reload too", page.locator("#view-settings").is_hidden()
          and page.evaluate("location.hash") == "", page.evaluate("location.hash"))
    check("no errors in either page", not errors, errors[:5])
    browser.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
