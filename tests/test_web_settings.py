"""
The new UI's Settings screen against the real server, in a real browser. What you change there is
what the server and the current UI use: how many clips render at once, render priority, download
quality and the update settings. Storage counts the VODs and clips, and Delete VODs asks first and
then deletes only the VODs. A Twitch token Twitch can't accept is explained, an update check that
can't reach GitHub says so, the sections not ported yet open the current UI's Settings, and the
screen fits a narrow window without sideways scrolling.

Needs the React build (cd web && npm run build) and Playwright; skipped without them.

  python tests/test_web_settings.py
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
WORK = os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
VODS = []
for name in ("stream_one", "stream_two"):           # two projects, each with a VOD and a rendered clip
    d = os.path.join(WORK, name)
    os.makedirs(os.path.join(d, "tiktok"))
    with open(os.path.join(d, "meta.json"), "w") as f:
        f.write("{}")
    VODS.append(os.path.join(d, "vod.mp4"))
    with open(VODS[-1], "wb") as f:
        f.write(os.urandom(3_000_000))
    with open(os.path.join(d, "tiktok", "clip.mp4"), "wb") as f:
        f.write(os.urandom(800_000))
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"     # nothing listens: checks fail, quickly
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

import cleanup  # noqa: E402
import exports  # noqa: E402
import server  # noqa: E402
import updater  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

from browser import launch  # noqa: E402

srv, url = server.start(7992)

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
    expected_4xx = ("/api/twitch/token",)          # the token that isn't one, on purpose
    page.on("console", lambda m: errors.append(m.text) if m.type == "error"
            and not m.text.startswith("Failed to load resource") else None)
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400
            and not r.url.endswith(expected_4xx) else None)

    page.goto(url + "/next/#settings")
    page.wait_for_selector("[data-testid=set-about]", timeout=15000)
    check("Settings is a screen of the new UI", page.locator("[data-testid=not-ported-settings]").count() == 0
          and page.locator("h1", has_text="Settings").count() == 1)

    # Performance: saved on the server, where renders and downloads read it
    perf = page.locator("[data-testid=set-perf]")
    perf.get_by_label("Clips rendering at once").select_option("2")
    perf.get_by_role("radio", name="Finish faster").click()
    page.wait_for_function("document.querySelector('[data-testid=set-perf] [role=radio][aria-checked=true]')"
                           ".textContent === 'Finish faster'", timeout=5000)
    prefs = exports.load()
    check("clips rendering at once and priority are saved on the server", prefs.get("renders") == 2
          and prefs.get("priority") == "fast", {k: prefs.get(k) for k in ("renders", "priority")})
    check("and this PC's details are shown", "processor threads" in perf.locator("[data-testid=perf-info]").inner_text())

    # Downloads and tools: the same choice as in the current UI
    page.locator("[data-testid=set-tools]").get_by_label("Download quality").select_option("720")
    rows = page.locator("[data-testid=tool-table] tr").all_inner_texts()
    check("every tool is listed with whether it's installed", len(rows) == 5 and "FFmpeg" in rows[0], rows)

    # Twitch: a token Twitch can't use is explained, and nothing is saved
    tw = page.locator("[data-testid=set-twitch]")
    if not tw.locator("details").evaluate("d => d.open"):
        tw.locator("summary").click()
    tw.get_by_label("Twitch sign-in token").fill("not a token")
    tw.get_by_role("button", name="Save").click()
    page.wait_for_function("document.querySelector('[data-testid=set-twitch]').textContent.includes(\"doesn't look like\")",
                           timeout=10000)
    check("a token that isn't one is explained", True)
    check("and you're still signed out", tw.locator("[data-testid=tw-state]").inner_text() == "Not signed in")

    # Updates: a check that can't reach GitHub says so; the automatic check can be turned off
    upd = page.locator("[data-testid=set-updates]")
    upd.get_by_role("button", name="Check for updates").click()
    page.wait_for_function("document.querySelector('[data-testid=upd-state]').className.includes('err')", timeout=20000)
    check("a failed update check says why", len(upd.locator("[data-testid=upd-state]").inner_text()) > 10,
          upd.locator("[data-testid=upd-state]").inner_text())
    upd.get_by_label("Check for updates automatically").uncheck()
    page.wait_for_timeout(500)
    check("automatic update checks can be turned off", updater.view().get("auto") is False, updater.view().get("auto"))
    check("the clip hotkey says whether it works here", page.locator("[data-testid=hk-state]").inner_text() != "")

    # Storage: the space VODs and clips use; Delete VODs asks first, then deletes only the VODs
    st = page.locator("[data-testid=set-storage]")
    page.wait_for_function("document.querySelector('[data-testid=vod-use]').textContent.includes('2 streams')", timeout=10000)
    check("Storage counts the VODs and clips", st.locator("[data-testid=clip-use]").inner_text().startswith("2 clips"),
          st.locator("[data-testid=clip-use]").inner_text())
    check("and lists every folder", st.locator("[data-testid=loc-list] li").count() >= 4)
    st.get_by_role("button", name="Delete VODs").click()
    dialog = page.get_by_role("alertdialog", name="Delete 2 downloaded streams?")
    dialog.get_by_role("button", name="Cancel").click()
    check("Cancel keeps the VODs", all(os.path.exists(v) for v in VODS))
    st.get_by_role("button", name="Delete VODs").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Delete VODs").click()
    page.wait_for_function("document.querySelector('[data-testid=vod-use]').textContent === 'None'", timeout=15000)
    check("Delete VODs deletes the VODs and keeps the clips", not any(os.path.exists(v) for v in VODS)
          and all(os.path.exists(os.path.join(WORK, n, "tiktok", "clip.mp4")) for n in ("stream_one", "stream_two")))
    check("and says so", "Deleted 2 streams" in page.locator("[data-testid=toasts]").inner_text())
    auto = st.get_by_label("Delete a VOD when you're finished with it")
    auto.click()
    page.get_by_role("alertdialog").get_by_role("button", name="Cancel").click()
    check("deleting finished VODs automatically stays off after Cancel", not auto.is_checked() and not cleanup.load()["vods"])

    # narrow window: no sideways scrolling
    page.set_viewport_size({"width": 420, "height": 860})
    page.wait_for_timeout(300)
    over = page.evaluate("Math.max(document.documentElement.scrollWidth, document.querySelector('.main').scrollWidth)"
                         " - document.documentElement.clientWidth")
    check("Settings fits a 420 px window", over <= 0, over)
    page.screenshot(path=os.environ.get("CLIPRADAR_SHOT") or os.path.join(tempfile.gettempdir(), "settings.png"))
    page.set_viewport_size({"width": 1366, "height": 860})

    # the sections that aren't ported open in the current UI, which has the same values
    page.get_by_role("link", name="Open them in the current UI").click()
    page.wait_for_url("**/legacy/**", timeout=15000)
    page.wait_for_selector("#view-settings", state="visible", timeout=15000)
    check("'Open them in the current UI' lands on its Settings", page.locator("#view-home").is_hidden())
    page.wait_for_function("document.querySelector('#pfRenders').value === '2'", timeout=10000)
    check("the current UI shows the new UI's choices", page.evaluate(
        "[document.querySelector('#pfRenders').value, document.querySelector('#maxh').value,"
        " document.querySelector('#pfPriority button.on')?.dataset.v, document.querySelector('#updAuto').checked]")
        == ["2", "720", "fast", False], page.evaluate(
        "[document.querySelector('#pfRenders').value, document.querySelector('#maxh').value,"
        " document.querySelector('#pfPriority button.on')?.dataset.v, document.querySelector('#updAuto').checked]"))
    check("no errors in either page", not errors, errors[:5])
    browser.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
