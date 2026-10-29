"""
Upload to TikTok from Exports, end to end: each UI (the new one at /, the previous one at /legacy/) in a real browser, the real server, and
Clip Radar's TikTok window (a second, headless browser) on a stand-in for TikTok Studio
(tests/fake_studio.py). The caption you edit is the one TikTok gets, and is kept with the clip; with
Press Post off the clip waits for you in TikTok Studio, with it on TikTok gets the post and Exports
says Posted; a step that fails says which, and shows the video so you can finish by hand.

  python tests/test_studio_handoff.py
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
os.environ["CLIPRADAR_NO_BROWSER"] = "1"               # the manual way records what it would open
os.environ["CLIPRADAR_STUDIO_HEADLESS"] = "1"
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

import fake_studio  # noqa: E402

FAKE = fake_studio.start()
os.environ["CLIPRADAR_STUDIO_URL"] = FAKE.url                # before studio is imported
import server  # noqa: E402
import studio  # noqa: E402
import studio_auto  # noqa: E402
from browser import launch  # noqa: E402

with sync_playwright() as pw:
    if not studio_auto.find_browser() and os.path.isfile(pw.chromium.executable_path):
        os.environ["CLIPRADAR_STUDIO_BROWSER"] = pw.chromium.executable_path
    browser = launch(pw)
    if browser is None or not studio_auto.find_browser():
        print("SKIPPED: no Chromium-based browser here")
        sys.exit(0)

    tmp = tempfile.mkdtemp(prefix="vc_handoff_")
    clip = os.path.join(tmp, "Chat lost it.mp4")
    with open(clip, "wb") as f:
        f.write(os.urandom(150_000))
    with server.queue_lock:
        server.queue.append({"id": "tt1", "pid": "p1", "clip_id": 1, "rank": 1, "start": 0.0, "end": 12.0,
                             "title": "Chat lost it", "post": "Chat lost it #twitch", "thumb": "", "status": "done",
                             "stage": "done", "progress": 1.0, "step": "Done", "error": "", "path": clip,
                             "name": os.path.basename(clip), "folder": tmp, "created": time.time(), "opts": {}})
    srv, url = server.start(7990)
    # the same journey on each UI: where its parts are
    UIS = [("new UI", "/#exports", {"row": "[data-testid=export-row]", "table": "[data-testid=exports-table]", "auto": "[data-testid=tt-auto]",
                                     "press": "[data-testid=tt-press]", "press_click": "[data-testid=tt-press]", "caption": "[data-testid=tt-caption]",
                                     "status": "[data-testid=tt-status]", "panel": "[data-testid=tt-panel]", "hint": "[data-testid=tt-panel]"}),
           ("previous UI", "/legacy/", {"row": "#exTable tr", "table": "#exTable", "auto": "#ttAuto", "press": "#ttPress",
                                         "press_click": "#ttPressRow .switch", "caption": "#ttCaption", "status": "#ttStatus",
                                         "panel": "#ttPanel", "hint": "#ttPressHint"})]
    page = None
    try:
        for ui, path, S in UIS:
            with server.queue_lock:                            # each UI starts from a clip not posted yet
                it = next(x for x in server.queue if x["id"] == "tt1")
                it.pop("handoff", None)
                it["post"] = "Chat lost it #twitch"
            FAKE.mode, FAKE.posted[:] = "ok", []
            studio_auto.save_settings(press_post=False)
            studio.opened.clear()
            page = browser.new_page(viewport={"width": 1366, "height": 860})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url + path)
            page.wait_for_timeout(800)
            page.click("[data-testid=nav-exports]")
            page.locator(S["row"], has_text="Chat lost it").first.click()
            page.wait_for_selector(S["auto"], state="visible", timeout=10000)
            check(f"{ui}: Upload to TikTok is the panel's main action", "primary" in page.get_attribute(S["auto"], "class"))
            check(f"{ui}: Press Post for me starts off", not page.is_checked(S["press"]))

            # 1. Press Post off: the video and caption go into TikTok Studio; you press Post
            CAP = "Chat could not stop laughing 😂 #twitch #kaicenat"
            page.fill(S["caption"], CAP)
            page.click(S["auto"])
            page.wait_for_function(f"document.querySelector('{S['status']}') && document.querySelector('{S['status']}').textContent.includes('are in TikTok Studio')", timeout=60000)
            with server.queue_lock:
                it = next(x for x in server.queue if x["id"] == "tt1")
            check(f"{ui}: the clip waits for you in TikTok Studio", it["handoff"]["auto"] == "ready" and not FAKE.posted, (it["handoff"], FAKE.posted))
            check(f"{ui}: the caption you wrote is kept with the clip", it["post"] == CAP, it["post"])
            shots = os.environ.get("CLIPRADAR_SHOT_DIR")
            if shots:
                page.locator(S["panel"]).screenshot(path=os.path.join(shots, f"tt_ready_{ui[:3]}.png"))
            page.wait_for_function(f"document.querySelector('{S['table']}').textContent.includes('In Studio, press Post')", timeout=5000)
            check(f"{ui}: Exports says it's in Studio, waiting for Post", True)

            # 2. Press Post on: TikTok gets the post, and Exports says Posted
            page.click(S["press_click"])                       # the switch, as you would
            page.wait_for_timeout(400)
            check(f"{ui}: Press Post for me is remembered", studio_auto.load_settings()["press_post"] is True)
            check(f"{ui}: and warns that it posts straight to the account", "posts straight to your account" in page.inner_text(S["hint"]))
            page.click(S["auto"])
            page.wait_for_function(f"document.querySelector('{S['status']}') && document.querySelector('{S['status']}').textContent.includes('Posted at')", timeout=60000)
            check(f"{ui}: TikTok gets the clip and the caption", len(FAKE.posted) == 1 and FAKE.posted[0]["name"] == "Chat lost it.mp4"
                  and FAKE.posted[0]["caption"].split() == CAP.split(), FAKE.posted)
            page.wait_for_function(f"[...document.querySelectorAll('{S['row']}')].some(r => r.textContent.includes('Chat lost it') && r.textContent.includes('Posted'))", timeout=5000)
            check(f"{ui}: and Exports says Posted", True)

            # 3. TikTok changed the page: the step that stopped is named, and the video is shown to finish by hand
            FAKE.mode = "changed"
            orig = studio_auto._wait
            studio_auto._wait = lambda pg, what, seconds, chk, every=0.5: orig(pg, what, min(seconds, 3), chk, every)
            studio.opened.clear()
            page.click(S["auto"])
            page.wait_for_function(f"document.querySelector('{S['status']}') && document.querySelector('{S['status']}').textContent.includes('The upload stopped')", timeout=60000)
            studio_auto._wait = orig
            if shots:
                page.locator(S["panel"]).screenshot(path=os.path.join(shots, f"tt_failed_{ui[:3]}.png"))
            check(f"{ui}: a step that stops is named", "stopped while putting the video in" in page.inner_text(S["status"]), page.inner_text(S["status"]))
            check(f"{ui}: and the video is shown in File Explorer to finish by hand", ("reveal", clip) in studio.opened, studio.opened)
            page.wait_for_function(f"document.querySelector('{S['table']}').textContent.includes('Upload stopped')", timeout=5000)
            check(f"{ui}: Exports says the upload stopped", True)
            check(f"{ui}: no errors in the page", not errors, errors[:5])
            page.close()
    finally:
        p = studio_auto._state.get("proc")
        if p:
            p.kill()
        browser.close()
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        FAKE.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
