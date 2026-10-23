"""
The new UI's Exports screen against the real server, real renders and a real browser: it's at /next/
while the original page stays at /, it follows a render through its stages to Ready without ever
showing a percentage outside encoding, it explains a real FFmpeg failure with its attempts, Retry
renders it, and a reload rebuilds the same list from the server (nothing duplicated).

Needs the React build (cd web && npm run build), FFmpeg and Playwright; skipped without them.

  python tests/test_web_exports.py
"""

from __future__ import annotations

import os
import re
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
os.environ.pop("CLIPRADAR_UI", None)
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
import exports  # noqa: E402
import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="vc_webex_")
SRC = os.path.join(TMP, "vod.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=30",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=30", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", SRC], check=True)
exports.save({"sounds": False})
srv, url = server.start(7995)


def item(iid, start, end, title):
    it = {"id": iid, "pid": "web", "start": start, "end": end, "title": title, "status": "queued", "stage": "queued",
          "progress": 0.0, "step": "Waiting", "error": "", "cancel": False, "workdir": TMP, "video": SRC, "live": "",
          "opts": {"layout": "split", "captions": False, "hw_encode": False}, "created": time.time(), "words": [],
          "export": dict(exports.settings(exports.load()), speed="high")}
    with server.queue_lock:
        server.queue.append(it)
    server.queue_wake.set()
    return it


def launch(p):
    """The same browsers tests/test_ui.py tries: CLIPRADAR_BROWSER, Playwright's Chromium, then Edge or Chrome."""
    exe = os.environ.get("CLIPRADAR_BROWSER")
    tries = [dict(executable_path=exe)] if exe else []
    if os.path.isdir("/opt/pw-browsers"):
        for d in sorted(os.listdir("/opt/pw-browsers")):
            cand = os.path.join("/opt/pw-browsers", d, "chrome-linux", "chrome")
            if d.startswith("chromium-") and os.path.isfile(cand):
                tries.append(dict(executable_path=cand))
    tries += [{}, dict(channel="msedge"), dict(channel="chrome")]
    for kw in tries:
        try:
            return p.chromium.launch(**kw)
        except Exception:
            continue
    return None


with sync_playwright() as pw:
    browser = launch(pw)
    if browser is None:
        print("SKIPPED: no Chromium-based browser (set CLIPRADAR_BROWSER)")
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        sys.exit(0)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(url + "/")
    check("the original page is still at /", page.locator("#exTable").count() == 1)
    page.goto(url + "/next/#exports")
    page.wait_for_selector("[data-testid=exports-empty]", timeout=15000)
    check("the new UI loads at /next/, with an empty Exports list", True)
    check("with the nav the UI test contract needs", all(page.locator(f"[data-testid=nav-{v}]").count() == 1
                                                          for v in ("home", "review", "exports", "settings")))

    # a real render, followed through its stages
    item("w1", 2.0, 22.0, "Watched render")
    seen = []
    end = time.time() + 120
    while time.time() < end:
        st = page.locator("[data-testid=export-row][data-id=w1] [data-testid=export-status]")
        if st.count():
            seen.append(st.inner_text())
            if seen[-1].startswith("Ready"):
                break
        time.sleep(0.15)
    check("a render reaches Ready on screen", seen and seen[-1].startswith("Ready"), seen[-5:])
    pct = [s for s in seen if "%" in s]
    other = [s for s in seen if "%" not in s and not s.startswith(("Ready", "Waiting"))]
    check("a percentage was shown while encoding", bool(pct), seen[:10])
    check("every percentage was during rendering", all(s.startswith(("Rendering", "Rendering again")) for s in pct), pct[:5])
    check("the other stages showed their step and time, not a percentage",
          all(re.search(r" · \d+:\d\d$", s) for s in other), other[:5])
    page.screenshot(path=os.environ.get("CLIPRADAR_SHOT") or os.path.join(TMP, "exports-done.png"))
    check("the finished clip plays in the preview", page.locator("[data-testid=export-detail] video").count() == 1)

    # a real FFmpeg failure, explained, then retried
    real_bf = engine.build_filter
    engine.build_filter = lambda info, o, path=None, face=None: ("[0:v]crop=4000:4000:0:0,scale=1080:1920,format=yuv420p[v]", 960.0)
    item("w2", 1.0, 5.0, "Broken layout")
    page.wait_for_selector("[data-testid=export-row][data-id=w2][data-status=error]", timeout=60000)
    engine.build_filter = real_bf
    page.locator("[data-testid=export-row][data-id=w2]").click()
    detail = page.locator("[data-testid=export-detail]")
    check("the failure says it's a layout problem", "layout" in detail.locator("[data-testid=export-error]").inner_text())
    detail.get_by_role("button", name="Details").click()
    d = page.locator("[data-testid=export-details]").inner_text()
    check("with FFmpeg's reason and the attempt", "crop" in d.lower() and "Attempt 1: error (layout)" in d, d[-300:])
    detail.get_by_role("button", name="Retry").click()
    page.wait_for_selector("[data-testid=export-row][data-id=w2][data-status=done]", timeout=120000)
    check("Retry renders it", True)

    # a reload rebuilds the same list from the server
    page.reload()
    page.wait_for_selector("[data-testid=export-row]", timeout=15000)
    ids = page.locator("[data-testid=export-row]").evaluate_all("rs => rs.map(r => r.dataset.id)")
    check("after a reload, the same clips, once each", ids == ["w2", "w1"], ids)
    check("no errors in the page", not errors, errors[:5])
    browser.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
