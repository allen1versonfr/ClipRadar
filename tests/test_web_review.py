"""
The new UI's Review against the real server, in a real browser, on a generated video: the open videos
are listed, a video opens with its clips, the selected clip plays its stretch, a title you type is saved
with the VOD, Keep queues a render with your render style and moves on, Skip (the X key) moves it to
Skipped and asks why, a video the window can't play switches to the preview copy, Skip in bulk asks first and Undo brings the clips back, a reload keeps what you
did, "Edit in the current UI" opens the same clip there, and the screen fits a narrow window.

Needs FFmpeg, the React build (cd web && npm run build) and Playwright; skipped without them.

  python tests/test_web_review.py
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
os.environ.pop("CLIPRADAR_UI", None)
os.environ["CLIPRADAR_PREVIEW_VP9"] = "1"      # test browsers have no H.264: the preview copy they can play
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

import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)

from browser import launch  # noqa: E402

srv, url = server.start(7996)


def call(path, body=None):
    rq = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq) as r:
        return json.load(r)


# a quiet tone with five loud bursts: five moments to suggest
vod = os.path.join(tempfile.mkdtemp(prefix="vc_rv_"), "review_test.mp4")
loud = "+".join(f"between(t,{t},{t + 2})" for t in (15, 35, 55, 75, 95))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=110",
                "-f", "lavfi", "-i", "anoisesrc=d=110:a=0.02", "-af", f"volume='if({loud},40,1)':eval=frame",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", vod], check=True)
job = call("/api/analyze", {"source": vod, "count": 5})["job"]
deadline = time.time() + 180
while time.time() < deadline:
    j = call("/api/job/" + job)
    if j["status"] != "running":
        break
    time.sleep(0.5)
check("the video is analysed", j["status"] == "done", j.get("error"))
pid = j["result"]["pid"]
pr = server.projects[pid]
n = len(pr["clips"])
check("there are clips to review", n >= 3, n)


def server_clip(cid):
    return next(d for d in server.projects[pid]["clips"] if d["id"] == cid)


def wait_until(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return fn()


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
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.add_init_script("localStorage.setItem('vc_opts', JSON.stringify({captions: false}))")      # no Whisper download here

    # the open videos are listed, and one click opens it
    page.goto(url + "/next/#review")
    page.get_by_role("link", name="review_test").click()
    page.wait_for_selector("[data-testid=rv-row]", timeout=15000)
    check("a video opens in Review from the list of open videos", page.evaluate("location.hash") == f"#review/{pid}")
    rows = page.locator("[data-testid=rv-row]")
    check("every clip to review is listed", rows.count() == n, (rows.count(), n))
    first = sorted(pr["clips"], key=lambda d: pr["clips"].index(d))[0]
    check("the first clip is selected", rows.first.get_attribute("aria-current") == "true")

    # the player shows the selected clip's stretch of the video
    # (this browser can't play H.264, so this also checks the switch to Clip Radar's preview copy)
    page.wait_for_function("(() => { const v = document.querySelector('[data-testid=rv-video]'); return v && v.readyState >= 1 })()", timeout=60000)
    check("a video the window can't play switches to the preview copy", "preview=1" in page.evaluate("document.querySelector('[data-testid=rv-video]').currentSrc"))
    page.wait_for_timeout(600)
    t = page.evaluate("document.querySelector('[data-testid=rv-video]').currentTime")
    check("the player starts at the clip", first["start"] - 0.5 <= t <= first["end"] + 0.5, (t, first["start"], first["end"]))
    page.get_by_label("Preview volume").fill("30")
    check("the preview volume is the one both UIs share", abs(float(page.evaluate("localStorage.getItem('vc_volume')")) - 0.3) < 0.01)

    # a typed title is saved with the VOD
    box = page.get_by_test_id("rv-title-input")
    box.fill("My own title")
    check("a typed title is saved with the VOD", wait_until(lambda: server_clip(first["id"]).get("title") == "My own title"
                                                             and server_clip(first["id"]).get("userTitle")), server_clip(first["id"]))
    page.locator("h1").click()

    # Keep queues a render with your render style, and moves on to the next clip
    page.get_by_test_id("rv-keep").click()
    ok = wait_until(lambda: any(q.get("clip_id") == first["id"] and q["pid"] == pid for q in server.queue))
    it = next((q for q in server.queue if q.get("clip_id") == first["id"]), {})
    check("Keep queues the clip's render", ok, [q.get("clip_id") for q in server.queue])
    check("with its title and your render style", it.get("title") == "My own title" and (it.get("opts") or {}).get("captions") is False,
          {k: it.get(k) for k in ("title",)})
    check("and the clip is kept on the server", wait_until(lambda: server_clip(first["id"]).get("state") == "kept"))
    second = page.locator("[data-testid=rv-row][aria-current=true]").inner_text()
    check("Review moves on to the next clip", "My own title" not in second)
    page.get_by_role("tab", name="All").click()
    page.wait_for_function("document.querySelector('[data-testid=rv-rows]').textContent.match(/In render queue|Rendering|Rendered/)", timeout=10000)
    check("the kept clip shows its render", True)
    page.get_by_role("tab", name="To review").click()

    # Skip by key: moves to Skipped and asks why (optional)
    cur_title = page.locator("[data-testid=rv-row][aria-current=true] .rv-row-t").inner_text()
    cur_id = next(d["id"] for d in pr["clips"] if (d.get("title") or "Untitled") == cur_title and d.get("state", "todo") == "todo")
    page.keyboard.press("x")
    check("X skips the clip", wait_until(lambda: server_clip(cur_id).get("state") == "skipped"), server_clip(cur_id).get("state"))
    page.get_by_test_id("toasts").get_by_role("button", name="Bad cut").click()
    check("and why can be told", True)

    # Skip in bulk asks first (more than 3), and Undo brings them back
    left = page.locator("[data-testid=rv-row]").count()
    page.get_by_test_id("rv-batch-skip").click()
    if left > 3:
        page.get_by_role("alertdialog").get_by_role("button", name="Skip").click()
    check("Skip all left skips every clip still to review", wait_until(
        lambda: not [d for d in server.projects[pid]["clips"] if d.get("state", "todo") == "todo"]),
        [d.get("state") for d in server.projects[pid]["clips"]])
    page.get_by_test_id("toasts").get_by_role("button", name="Undo").click()
    check("Undo brings them back", wait_until(
        lambda: len([d for d in server.projects[pid]["clips"] if d.get("state", "todo") == "todo"]) == left))

    # a reload keeps what you did
    page.reload()
    page.wait_for_selector("[data-testid=rv-row]", timeout=15000)
    page.get_by_role("tab", name="Skipped").click()
    check("after a reload, the skipped clip is still skipped", page.locator("[data-testid=rv-row]").count() == 1)
    page.get_by_role("tab", name="To review").click()

    # the current UI's editor opens on the same clip
    sel = page.locator("[data-testid=rv-row][aria-current=true] .rv-row-t").inner_text()
    sel_id = next(d["id"] for d in server.projects[pid]["clips"] if (d.get("title") or "Untitled") == sel and d.get("state", "todo") == "todo")
    page.get_by_role("link", name="Edit in the current UI").click()
    page.wait_for_url("**/legacy/**", timeout=15000)
    page.wait_for_function("window.ClipRadarTest && ClipRadarTest.current()", timeout=15000)
    check("'Edit in the current UI' opens the same clip there", page.evaluate("ClipRadarTest.current()") == f"{pid}:{sel_id}",
          (page.evaluate("ClipRadarTest.current()"), sel_id))

    # narrow window: no sideways scrolling
    page.goto(url + f"/next/#review/{pid}")
    page.wait_for_selector("[data-testid=rv-row]", timeout=15000)
    page.set_viewport_size({"width": 420, "height": 860})
    page.wait_for_timeout(300)
    over = page.evaluate("Math.max(document.documentElement.scrollWidth, document.querySelector('.main').scrollWidth)"
                         " - document.documentElement.clientWidth")
    check("Review fits a 420 px window", over <= 0, over)
    page.screenshot(path=os.environ.get("CLIPRADAR_SHOT") or os.path.join(tempfile.gettempdir(), "review.png"), full_page=True)
    check("no errors in either page", not errors, errors[:5])
    browser.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
