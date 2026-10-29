"""
The new UI's Review against the real server, in a real browser, on a generated video: the open videos
are listed, a video opens with its clips, the selected clip plays its stretch, a title you type is saved
with the VOD, the Text and Layout tabs set the shared render style, a caption line can be added, fixed and undone, the content check box tries, makes and undoes a fix, excludes, removes and brings back a clip, the clip preview matches the current UI's (with those choices) and the title can be dragged on it, trimming works (buttons, undo / redo, dragging the end on the timeline, I, Restore), Keep queues a render with your render style and moves on, Skip (the X key) moves it to
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

import safety  # noqa: E402
import server  # noqa: E402

safety.save_config({"custom": ["watch"]})        # flags the suggested title "Watch till the end": a clip to check

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


# quiet noise (always the same) with four loud bursts: four moments to suggest
vod = os.path.join(tempfile.mkdtemp(prefix="vc_rv_"), "review_test.mp4")
loud = "+".join(f"between(t,{t},{t + 2})" for t in (20, 45, 70, 95))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
                "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.02:seed=7", "-af", f"volume='if({loud},40,1)':eval=frame",
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


# titles keep "arriving" the whole time, as while Whisper works on a real PC: the page keeps asking
# /api/clips for them, so its answers meet your edits (the race behind 4.21.0's first CI failure)
def _listening_forever(pid, order, model, lang, on_done=None):
    job = server.new_job("listen")
    server.projects[pid]["listen_job"] = job["id"]
    return job


server.start_listening = _listening_forever


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
    page.add_init_script("if (!localStorage.getItem('vc_opts')) localStorage.setItem('vc_opts', JSON.stringify({captions: false}))")  # no Whisper download here

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
    page.wait_for_function("(() => { const v = document.querySelector('[data-testid=rv-video]'); return v && v.readyState >= 1 })()", timeout=60000)
    # Playwright's own Chromium has no H.264 and must switch to the copy; Edge and Chrome play the original
    h264 = page.evaluate("document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"') !== ''")
    copy = "preview=1" in page.evaluate("document.querySelector('[data-testid=rv-video]').currentSrc")
    check("a video the window can play plays as it is" if h264 else "a video the window can't play switches to the preview copy",
          copy != h264, {"h264": h264, "copy": copy})
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

    # trimming: the buttons, undo and redo, dragging the end on the timeline, I at the playhead, Restore
    c0 = dict(server_clip(first["id"]))
    page.get_by_role("button", name="Start: 1 second later").click()
    check("+1s moves the start a second later, saved as your trim", wait_until(
        lambda: abs(server_clip(first["id"])["start"] - (c0["start"] + 1)) < 0.01 and server_clip(first["id"]).get("userTrim")),
        (server_clip(first["id"])["start"], c0["start"]))
    page.locator("h1").click()
    page.keyboard.press("Control+z")
    check("Ctrl+Z undoes it", wait_until(lambda: abs(server_clip(first["id"])["start"] - c0["start"]) < 0.01), server_clip(first["id"])["start"])
    page.keyboard.press("Control+Shift+z")
    check("Ctrl+Shift+Z redoes it", wait_until(lambda: abs(server_clip(first["id"])["start"] - (c0["start"] + 1)) < 0.01))
    cl = dict(server_clip(first["id"]))           # a copy: the server changes its own
    page.get_by_role("button", name="Fit", exact=True).click()
    page.wait_for_timeout(200)
    page.evaluate("document.querySelector('[data-testid=rv-timeline]').scrollIntoView({block: 'center'})")   # clear of the toasts
    a, b = (float(page.get_by_test_id("rv-timeline").get_attribute(k)) for k in ("data-a", "data-b"))
    tl = page.get_by_test_id("rv-timeline").bounding_box()
    xe = tl["x"] + (cl["end"] - a) / (b - a) * tl["width"] - 2
    y = tl["y"] + tl["height"] / 2
    page.mouse.move(xe, y)
    page.mouse.down()
    page.mouse.move(xe - 3 / (b - a) * tl["width"], y, steps=6)
    page.mouse.up()
    check("dragging the clip's end on the timeline trims it", wait_until(
        lambda: 2 < cl["end"] - server_clip(first["id"])["end"] < 4), (cl["end"], server_clip(first["id"])["end"]))
    page.evaluate(f"document.querySelector('[data-testid=rv-video]').pause(); document.querySelector('[data-testid=rv-video]').currentTime = {cl['start'] + 2}")
    page.wait_for_timeout(300)
    page.locator("h1").click()
    page.keyboard.press("i")
    check("I starts the clip at the playhead", wait_until(lambda: abs(server_clip(first["id"])["start"] - (cl["start"] + 2)) < 0.1),
          (server_clip(first["id"])["start"], cl["start"] + 2))
    page.get_by_role("button", name="Restore original cut").click()
    check("Restore original cut goes back to the cut Clip Radar found", wait_until(
        lambda: abs(server_clip(first["id"])["start"] - c0["orig"][0]) < 0.01 and abs(server_clip(first["id"])["end"] - c0["orig"][1]) < 0.01
        and not server_clip(first["id"]).get("userTrim")), (server_clip(first["id"]), c0.get("orig")))
    page.keyboard.press("Control+z")
    trimmed = wait_until(lambda: abs(server_clip(first["id"])["start"] - (cl["start"] + 2)) < 0.1)
    check("and Undo brings the trim back", trimmed, server_clip(first["id"])["start"])

    # Keep queues a render with your render style, and moves on to the next clip
    page.get_by_test_id("rv-keep").click()
    ok = wait_until(lambda: any(q.get("clip_id") == first["id"] and q["pid"] == pid for q in server.queue))
    it = next((q for q in server.queue if q.get("clip_id") == first["id"]), {})
    check("Keep queues the clip's render", ok, [q.get("clip_id") for q in server.queue])
    check("with its title and your render style", it.get("title") == "My own title" and (it.get("opts") or {}).get("captions") is False,
          {k: it.get(k) for k in ("title",)})
    check("and your trim", abs(it.get("start", 0) - server_clip(first["id"])["start"]) < 0.01
          and abs(it.get("end", 0) - server_clip(first["id"])["end"]) < 0.01, (it.get("start"), it.get("end"), server_clip(first["id"])))
    check("and the clip is kept on the server", wait_until(lambda: server_clip(first["id"]).get("state") == "kept"))
    second = page.locator("[data-testid=rv-row][aria-current=true]").inner_text()
    check("Review moves on to the next clip", "My own title" not in second)
    page.get_by_role("tab", name="All").click()
    page.wait_for_function("document.querySelector('[data-testid=rv-rows]').textContent.match(/In render queue|Rendering|Rendered/)", timeout=10000)
    check("the kept clip shows its render", True)
    page.get_by_role("tab", name="To review").click()

    # Skip by key: moves to Skipped and asks why (optional)
    cur_title = page.get_by_test_id("rv-title-input").input_value()
    cur_id = next(d["id"] for d in pr["clips"] if (d.get("title") or "Untitled") == cur_title and d.get("state", "todo") == "todo")
    page.keyboard.press("x")
    check("X skips the clip", wait_until(lambda: server_clip(cur_id).get("state") == "skipped"), server_clip(cur_id).get("state"))
    page.get_by_test_id("toasts").get_by_role("button", name="Bad cut").click()
    check("and why can be told", True)

    # Skip in bulk asks first (more than 3), and Undo brings them back
    left = page.locator("[data-testid=rv-row]").count()
    page.get_by_test_id("batch-skip").click()
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

    # Text and Layout tabs: the render style both UIs share (vc_opts), saved framings on the server
    def vc_opts():
        return json.loads(page.evaluate("localStorage.getItem('vc_opts')") or "{}")
    page.get_by_role("tab", name="Text", exact=True).click()
    page.get_by_role("radio", name="Outline").click()
    page.get_by_role("radiogroup", name="Title font").get_by_role("radio", name="Anton").click()
    check("the Text tab sets the title style and font", (vc_opts().get("title_style"), vc_opts().get("title_font")) == ("outline", "anton"), vc_opts())
    page.get_by_role("tab", name="Layout", exact=True).click()
    page.get_by_role("radio", name="Cam + game").click()
    pk = page.get_by_test_id("rv-picker")
    page.evaluate("document.querySelector('[data-testid=rv-picker]').scrollIntoView({block: 'center'})")
    page.wait_for_function("document.querySelector('[data-testid=rv-picker] img').naturalWidth > 0", timeout=15000)
    bb = pk.bounding_box()
    page.mouse.move(bb["x"] + bb["width"] * 0.6, bb["y"] + bb["height"] * 0.1)
    page.mouse.down()
    page.mouse.move(bb["x"] + bb["width"] * 0.9, bb["y"] + bb["height"] * 0.4, steps=6)
    page.mouse.up()
    cam = vc_opts().get("cam") or [0, 0, 0, 0]
    check("drawing the camera box sets it", abs(cam[0] - 0.6) < 0.03 and abs(cam[2] - 0.3) < 0.03 and abs(cam[3] - 0.3) < 0.03, cam)
    page.get_by_role("button", name="Save this framing…").click()
    page.get_by_label("Name this framing").fill("Cam top right")
    page.get_by_role("button", name="Save", exact=True).click()
    import exports  # noqa: E402
    check("a saved framing is kept on the server", wait_until(lambda: any(p["name"] == "Cam top right" for p in exports.load().get("frame_presets", []))),
          exports.load().get("frame_presets"))
    page.get_by_role("radio", name="Blur fit").click()
    check("the Layout tab sets the layout", vc_opts().get("layout") == "blur", vc_opts().get("layout"))
    page.get_by_role("tab", name="Clip", exact=True).click()

    # Captions tab: add a line at the playhead, fix it, saved with the VOD; Undo all edits
    page.get_by_role("tab", name="Text", exact=True).click()
    page.get_by_label("Captions on the video").check()
    page.get_by_role("tab", name="Captions", exact=True).click()
    page.get_by_role("button", name="+ Add caption at playhead").click()
    line = page.get_by_test_id("rv-captions").locator("input[value='New caption']").element_handle()    # the element: its value changes
    line.fill("Hello there friend")
    line.press("Enter")

    def edited():
        return next((d for d in server.projects[pid]["clips"] if d.get("wordsEdited")), None)
    ok = wait_until(lambda: edited() is not None and [w["word"] for w in edited().get("words_edit", [])][-3:] == ["Hello", "there", "friend"])
    check("a caption line added and fixed in the new UI is saved with the VOD", ok, edited() and edited().get("words_edit"))
    ed_id = edited()["id"] if edited() else None
    page.get_by_role("button", name="Undo all edits").click()
    check("Undo all edits goes back to what Whisper heard", wait_until(lambda: not server_clip(ed_id).get("wordsEdited")) if ed_id else False)
    page.get_by_role("tab", name="Clip", exact=True).click()

    # content check: the Flagged tab, a fix tried and made, undone, exclude and include, remove and undo
    page.get_by_role("tab", name="Flagged").click()
    page.get_by_test_id("rv-row").first.click()
    cc = page.get_by_test_id("content-check")
    cc.wait_for(timeout=10000)
    flagged = next(d for d in server.projects[pid]["clips"] if (d.get("safety") or {}).get("open"))
    check("the Flagged tab lists the flagged clip, and its box says it needs review", "Needs review" in cc.inner_text(), cc.inner_text()[:200])
    cc.get_by_role("button", name="Hide it in the title").first.click()
    prop = page.get_by_test_id("cc-proposal")
    prop.wait_for(timeout=10000)
    check("a fix is shown before it's made", "New title:" in prop.inner_text() and "W****" in prop.inner_text(), prop.inner_text())
    prop.get_by_role("button", name="Hide it in the title").click()
    check("making it masks the title on the server", wait_until(lambda: server_clip(flagged["id"]).get("title", "").startswith("W****")),
          server_clip(flagged["id"]).get("title"))
    cc.get_by_role("button", name="Undo fix").click()
    check("Undo fix brings the title back", wait_until(lambda: server_clip(flagged["id"]).get("title") == flagged["title"]),
          server_clip(flagged["id"]).get("title"))
    cc.get_by_role("button", name="Exclude from export").click()
    check("Exclude from export is kept on the server", wait_until(lambda: (server_clip(flagged["id"]).get("check") or {}).get("excluded")))
    cc.get_by_role("button", name="Include in export").click()
    check("and Include undoes it", wait_until(lambda: not (server_clip(flagged["id"]).get("check") or {}).get("excluded")))
    cc.get_by_role("button", name="Remove…").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Remove").click()
    gone = wait_until(lambda: not any(d["id"] == flagged["id"] for d in server.projects[pid]["clips"]))
    check("Remove takes the clip out of the project", gone)
    page.get_by_test_id("toasts").get_by_role("button", name="Undo").click()
    check("and Undo brings it back", wait_until(lambda: any(d["id"] == flagged["id"] for d in server.projects[pid]["clips"])))
    page.get_by_role("tab", name="To review").click()

    # the clip preview: the same picture as the current UI's, and the title can be dragged
    page.mouse.move(5, 5)
    page.evaluate("(() => { const v = document.querySelector('[data-testid=rv-video]'); v.pause(); })()")
    at = page.evaluate("document.querySelector('[data-testid=rv-video]').currentTime")
    shrink = """(sel) => { const c = document.createElement('canvas'); c.width = 54; c.height = 96;
        c.getContext('2d').drawImage(document.querySelector(sel), 0, 0, 54, 96); return Array.from(c.getContext('2d').getImageData(0, 0, 54, 96).data) }"""
    page.wait_for_timeout(400)
    new_px = page.evaluate(shrink, "[data-testid=clip-preview]")
    page.evaluate("document.querySelector('[data-testid=clip-preview]').scrollIntoView({block: 'center'})")
    pv = page.get_by_test_id("clip-preview").bounding_box()
    page.mouse.move(pv["x"] + pv["width"] / 2, pv["y"] + pv["height"] * 0.07)
    page.mouse.down()
    page.mouse.move(pv["x"] + pv["width"] / 2, pv["y"] + pv["height"] * 0.3, steps=8)
    page.mouse.up()
    o = json.loads(page.evaluate("localStorage.getItem('vc_opts')") or "{}")
    check("dragging the title on the preview moves it, in the render style both UIs share", 0.2 < o.get("title_y", 0) < 0.4, o.get("title_y"))
    page.evaluate("localStorage.setItem('vc_opts', JSON.stringify(Object.assign(JSON.parse(localStorage.getItem('vc_opts')), {title_y: 0.05})))")

    # the current UI's editor opens on the same clip
    sel = page.get_by_test_id("rv-title-input").input_value()
    sel_id = next(d["id"] for d in server.projects[pid]["clips"] if (d.get("title") or "Untitled") == sel and d.get("state", "todo") == "todo")
    page.get_by_role("link", name="Edit in the current UI").click()
    page.wait_for_url("**/legacy/**", timeout=15000)
    page.wait_for_function("window.ClipRadarTest && ClipRadarTest.current()", timeout=15000)
    check("'Edit in the current UI' opens the same clip there", page.evaluate("ClipRadarTest.current()") == f"{pid}:{sel_id}",
          (page.evaluate("ClipRadarTest.current()"), sel_id))
    page.wait_for_function("document.querySelector('#vod').readyState >= 2", timeout=60000)
    page.evaluate(f"(() => {{ const v = document.querySelector('#vod'); v.pause(); v.currentTime = {at}; }})()")
    page.wait_for_function(f"Math.abs(document.querySelector('#vod').currentTime - {at}) < 0.05 && !document.querySelector('#vod').seeking", timeout=15000)
    page.mouse.move(5, 5)
    page.wait_for_timeout(600)
    old_px = page.evaluate(shrink, "#phone")
    diff = sum(abs(a - b) for a, b in zip(new_px, old_px)) / len(new_px)
    check("the new preview draws the same picture as the current UI's", diff < 10, round(diff, 1))

    # the whole stream: its facts, the overview, previewing any moment and making a clip there
    page.goto(url + f"/next/#review/{pid}")
    page.wait_for_selector("[data-testid=rv-row]", timeout=15000)
    check("the header says how many strong moments the stream has", "strong moment" in page.get_by_test_id("rv-facts").inner_text(),
          page.get_by_test_id("rv-facts").inner_text())
    dur = pr["analysis"].duration
    gap = next(t for t in range(5, int(dur) - 5) if not any(d["start"] - 3 <= t <= d["end"] + 3 for d in pr["clips"] + pr.get("gems", [])))
    page.evaluate("document.querySelector('[data-testid=rv-heat]').scrollIntoView({block: 'center'})")
    hb = page.get_by_test_id("rv-heat").bounding_box()
    page.mouse.click(hb["x"] + gap / dur * hb["width"], hb["y"] + hb["height"] / 2)
    free = page.get_by_test_id("rv-free")
    free.wait_for(timeout=5000)
    check("clicking the overview outside the clips previews that moment", "Previewing" in free.inner_text(), free.inner_text())
    page.wait_for_timeout(500)
    vt = page.evaluate("document.querySelector('[data-testid=rv-video]').currentTime")
    check("the player goes to that moment", abs(vt - gap) < 3, (vt, gap))
    before = {d["id"] for d in pr["clips"]}
    page.get_by_test_id("rv-add-here").click()
    check("Make a clip here adds a clip of yours there", wait_until(lambda: any(
        d.get("manual") and d["start"] - 1 <= gap + 3 and d["end"] + 1 >= gap for d in server.projects[pid]["clips"] if d["id"] not in before)),
        [(d["id"], d["start"], d["end"]) for d in server.projects[pid]["clips"]])
    added = next(d["id"] for d in server.projects[pid]["clips"] if d["id"] not in before)
    check("and opens it", page.locator("[data-testid=rv-row][aria-current=true]").count() == 1 and free.count() == 0)
    page.evaluate("document.querySelector('[data-testid=rv-heat]').scrollIntoView({block: 'center'})")
    hb = page.get_by_test_id("rv-heat").bounding_box()
    cs = server.projects[pid]["clips"]
    other = next(d for d in cs if d["id"] != added and not any(
        e is not d and e["start"] <= (d["start"] + d["end"]) / 2 <= e["end"] for e in cs))   # nothing else covers its middle
    page.mouse.click(hb["x"] + (other["start"] + other["end"]) / 2 / dur * hb["width"], hb["y"] + hb["height"] / 2)
    check("clicking a clip on the overview opens it", wait_until(lambda: page.get_by_test_id("rv-title-input").input_value() == (other.get("title") or "")),
          (page.get_by_test_id("rv-title-input").input_value(), other.get("title")))

    # exact preview: a few seconds rendered by the export pipeline, shown over the preview
    page.get_by_test_id("rv-exact-btn").click()
    ex = page.get_by_test_id("rv-exact")
    ex.wait_for(timeout=120000)
    src = ex.get_attribute("src") or ""
    check("Exact preview renders a few seconds and shows them", src.startswith("/files/") and urllib.request.urlopen(url + src).status == 200, src)
    if h264:
        check("and plays them", wait_until(lambda: page.evaluate("document.querySelector('[data-testid=rv-exact]').readyState") >= 2, 30))
    page.keyboard.press("Escape")
    check("Esc goes back to the live preview", wait_until(lambda: ex.count() == 0, 5))

    # detection settings: Try compares, Apply re-ranks, and your clip stays
    page.get_by_test_id("rv-detect-open").click()
    dlg = page.get_by_role("dialog")
    dlg.get_by_test_id("rv-det-preset").select_option("gaming")
    dlg.get_by_test_id("rv-det-try").click()
    cmp_box = dlg.get_by_test_id("rv-det-cmp")
    cmp_box.wait_for(timeout=10000)
    check("Try says what the settings would suggest, without changing anything", "With these settings" in cmp_box.inner_text()
          and (pr.get("detect") or {}).get("preset", "any") != "gaming", cmp_box.inner_text())
    dlg.get_by_test_id("rv-det-apply").click()
    check("Apply re-ranks with them", wait_until(lambda: (server.projects[pid].get("detect") or {}).get("preset") == "gaming"),
          server.projects[pid].get("detect"))
    check("and the clip you made stays", any(d["id"] == added for d in server.projects[pid]["clips"]))
    check("the dialog closes", page.get_by_role("dialog").count() == 0)

    # title B with the streamer's name
    if any(d.get("title_a") for d in server.projects[pid]["clips"]):
        page.get_by_test_id("rv-streamer").click()
        page.get_by_test_id("rv-streamer-input").fill("Tester")
        page.get_by_role("button", name="Save").click()
        check("the streamer's name is saved for title B", wait_until(lambda: server.projects[pid].get("streamer_display") == "Tester"),
              server.projects[pid].get("streamer_display"))
    else:
        check("clips have title choices to pick from", False, "no title_a on any clip")

    # a live session: the real /api/live routes on a stand-in session whose one moment is this video's first clip
    class _Sess:
        id, extra, moments, clipped, stopped = "lv1", {}, [pid], [], []
        status, frequency, min_quality = "recording", "normal", 0

        def public(self):
            return {"id": self.id, "channel": "testchannel", "title": "Test live", "status": self.status, "recorded": 300.0,
                    "curve": [1.0] * 60, "chat_per_min": 12, "chat_connected": True, "chat_state": "ok", "chat_delay": 3.0,
                    "frequency": self.frequency, "min_quality": self.min_quality, "forming": [], "latency": {}, "skipped": {}}

        def clip_now(self, seconds):
            self.clipped.append(seconds)

        def set_frequency(self, level):
            self.frequency = level

        def stop(self):
            self.stopped.append(True)
            self.status = "stopped"
    sess = _Sess()
    server.live_sessions[sess.id] = sess
    page.goto(url + "/next/#review/live/lv1")
    page.get_by_test_id("rv-live").wait_for(timeout=15000)
    check("a live session opens in the new Review", page.get_by_role("heading", name="testchannel").count() == 1
          and page.get_by_test_id("rv-live-state").inner_text() == "Live")
    page.get_by_role("tab", name="All").click()
    check("its moment is listed", page.locator("[data-testid=rv-row]").count() == 1)
    page.get_by_test_id("rv-live-clip").click()
    check("Clip last 30s asks the session for a clip", wait_until(lambda: sess.clipped == [30.0]), sess.clipped)
    page.get_by_label("How many clips to make").select_option("more")
    check("how many clips is changed on the session", wait_until(lambda: sess.frequency == "more"), sess.frequency)
    page.get_by_test_id("rv-live-auto").check()
    check("Hands-free is switched on for the session", wait_until(lambda: sess.extra.get("auto_render") is True), sess.extra)
    page.get_by_test_id("rv-live-stop").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Stop").click()
    check("Stop stops it, after asking", wait_until(lambda: sess.stopped == [True]))
    check("and the header says so", wait_until(lambda: page.get_by_test_id("rv-live-state").inner_text() == "Stopped", 5))
    del server.live_sessions[sess.id]

    if os.environ.get("CLIPRADAR_SHOT_WIDE"):
        page.screenshot(path=os.environ["CLIPRADAR_SHOT_WIDE"])
    # narrow window: no sideways scrolling
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
