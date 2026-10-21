"""
UI smoke test: the window's page loads and every view opens without a script error, at common
Windows window sizes, with no sideways scrolling. A script error stops the rest of the page from
working, and nothing else in the test suite would notice.

Needs Playwright and a Chromium-based browser: CLIPRADAR_BROWSER (a chrome/msedge executable), the
browser Playwright installed, or Microsoft Edge (always there on Windows).

  python tests/test_ui.py
"""

from __future__ import annotations

import os
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
sys.path.insert(0, os.path.join(HERE, "..", "src"))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIPPED: Playwright isn't installed (pip install playwright)")
    sys.exit(0)

import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def launch(p):
    exe = os.environ.get("CLIPRADAR_BROWSER")
    tries = [dict(executable_path=exe)] if exe else []
    pw = "/opt/pw-browsers"
    if os.path.isdir(pw):
        for d in sorted(os.listdir(pw)):
            cand = os.path.join(pw, d, "chrome-linux", "chrome")
            if d.startswith("chromium-") and os.path.isfile(cand):
                tries.append(dict(executable_path=cand))
    tries += [{}, dict(channel="msedge"), dict(channel="chrome")]
    for kw in tries:
        try:
            return p.chromium.launch(**kw)
        except Exception:
            continue
    return None


srv, url = server.start(7989)
with sync_playwright() as p:
    b = launch(p)
    if not b:
        print("SKIPPED: no Chromium-based browser found")
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        sys.exit(0)
    for w, h in ((1280, 720), (1366, 768), (1920, 1080)):
        pg = b.new_page(viewport={"width": w, "height": h})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errs.append(m.text))
        pg.goto(url)
        pg.wait_for_timeout(1500)
        for view in ("review", "exports", "settings", "home"):
            pg.click(f"button[data-view={view}]")
            pg.wait_for_timeout(500)
            over = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check(f"{w}x{h}: {view} opens without sideways scrolling", not over)
        check(f"{w}x{h}: no script errors", not errs, errs[:3])
        pg.close()
    pg = b.new_page()
    pg.goto(url)
    pg.wait_for_timeout(1200)
    pg.click("button[data-view=settings]")
    pg.wait_for_timeout(500)
    bg = pg.evaluate("getComputedStyle(document.querySelector('.btn.primary')).backgroundColor")
    check("the primary buttons use the signature yellow", bg.replace(" ", "") == "rgb(255,210,31)", bg)
    tabs = pg.evaluate("[...document.querySelectorAll('#setTikTok')].length")
    check("the old TikTok sign-in settings are gone", tabs == 0)
    b.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
