"""
UI smoke test: the window's page loads and every view opens without a script error, at common
Windows window sizes, with no sideways scrolling. A script error stops the rest of the page from
working, and nothing else in the test suite would notice.

The test only touches the page through data-testid attributes and window.ClipRadarTest, so it runs
unchanged against any UI that keeps that contract (docs/react-migration.md). CLIPRADAR_UI picks which
UI is served (legacy or web).

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
            pg.click(f"[data-testid=nav-{view}]")
            pg.wait_for_timeout(500)
            over = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check(f"{w}x{h}: {view} opens without sideways scrolling", not over)
        check(f"{w}x{h}: no script errors", not errs, errs[:3])
        pg.close()
    pg = b.new_page()
    pg.goto(url)
    pg.wait_for_timeout(1200)
    pg.click("[data-testid=nav-home]")
    pg.wait_for_timeout(500)
    # on Home, where the button is: the new UI only draws the screen you're on
    bg = pg.evaluate("getComputedStyle(document.querySelector('[data-testid=find-clips]')).backgroundColor")
    check("the primary buttons use the accent green (DESIGN.md)", bg.replace(" ", "") == "rgb(47,227,111)", bg)
    live_bg = pg.evaluate("getComputedStyle([...document.querySelectorAll('button')]"
                          ".find(b => b.textContent.includes('Start live clipping'))).backgroundColor")
    check("Start live clipping is secondary: one main action on Home", live_bg.replace(" ", "") != "rgb(47,227,111)", live_bg)
    pg.click("[data-testid=nav-settings]")
    pg.wait_for_timeout(500)
    tabs = pg.evaluate("[...document.querySelectorAll('#setTikTok')].length")
    check("the old TikTok sign-in settings are gone", tabs == 0)

    # multi-clip export: open a real video, keep the top 3 in one go
    import shutil
    import subprocess
    import tempfile
    if shutil.which("ffmpeg"):
        tmp = tempfile.mkdtemp(prefix="vc_ui_")
        vod = os.path.join(tmp, "batch_test.mp4")
        # quiet tone with four loud bursts: four moments to suggest
        loud = "+".join(f"between(t,{t},{t + 2})" for t in (20, 45, 70, 95))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
                        "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.02", "-af", f"volume='if({loud},40,1)':eval=frame",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", vod],
                       check=True)
        pg.click("[data-testid=nav-home]")
        pg.fill("[data-testid=source-input]", vod)
        pg.click("[data-testid=find-clips]")
        pg.wait_for_selector("[data-testid=batch-bar]", state="visible", timeout=120000)
        pg.select_option("[data-testid=batch-count]", "3")
        pg.evaluate("ClipRadarTest.setOption('captions', false)")          # no Whisper download for this check
        sounds = sum(1 for e, _ in server.SOUND_LOG if e == "queued")
        pg.click("[data-testid=batch-keep]")
        pg.click("[data-testid=confirm-yes]")
        pg.wait_for_timeout(2500)
        with server.queue_lock:
            n = len(server.queue)
        check("Keep the top 3 queues three renders", n == 3, n)
        check("and plays one queued sound for the batch",
              sum(1 for e, _ in server.SOUND_LOG if e == "queued") - sounds == 1, server.SOUND_LOG)
        kept = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'kept').length")
        check("the three clips show as kept", kept == 3, kept)

        # a watermark layer dragged on the clip preview is saved where it was dropped
        import exports
        exports.save({"watermark": {"on": True, "marks": [{"kind": "text", "text": "@mychannel", "pos": "tl"}]}})
        pg.evaluate("ClipRadarTest.reloadExport()")
        pg.wait_for_timeout(800)
        r = pg.evaluate("ClipRadarTest.hitBox('wm0')")
        box = pg.locator("[data-testid=clip-preview]").bounding_box()
        k = box["width"] / pg.evaluate("ClipRadarTest.previewWidth()")
        if r:
            sx, sy = box["x"] + (r["x"] + r["w"] / 2) * k, box["y"] + (r["y"] + r["h"] / 2) * k
            pg.mouse.move(sx, sy)
            pg.mouse.down()
            pg.mouse.move(sx + 20, sy + box["height"] * 0.4, steps=8)
            pg.mouse.up()
            pg.wait_for_timeout(1000)
        m = exports.load()["watermark"]["marks"][0]
        check("the watermark shows in the clip preview", bool(r), r)
        check("and a layer dragged there is saved where it was dropped", m["pos"] == "free" and 0.45 < m["y"] < 0.7, m)

        # skip many at once: only clips under the score picked, then everything left; Undo brings them back
        n_todo = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'todo' && !c.gem).length")
        # alternate low and high, starting low, so at least one clip is under the threshold
        pg.evaluate("qs => ClipRadarTest.setTodoQualities(qs)", [80 if i % 2 else 30 for i in range(n_todo)])
        todo = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'todo' && !c.gem).map(c => c.quality)")
        check("there are clips under the score to skip", any(q < 50 for q in todo), todo)
        pg.select_option("[data-testid=skip-score]", "50")
        pg.click("[data-testid=batch-skip]")
        pg.wait_for_timeout(400)
        if len([q for q in todo if q < 50]) > 3:
            pg.click("[data-testid=confirm-yes]")
            pg.wait_for_timeout(400)
        left = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'todo' && !c.gem).map(c => c.quality)")
        check("Skip with a score skips only the clips scored under it", sorted(left) == sorted(q for q in todo if q >= 50), (todo, left))
        pg.click("[data-testid=toasts] [data-testid=toast-action]")
        pg.wait_for_timeout(300)
        back = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'todo' && !c.gem).length")
        check("and Undo brings them back", back == len(todo), (back, todo))
        pg.select_option("[data-testid=skip-score]", "0")
        pg.click("[data-testid=batch-skip]")
        pg.wait_for_timeout(400)
        if len(todo) > 3:
            pg.click("[data-testid=confirm-yes]")
            pg.wait_for_timeout(400)
        left = pg.evaluate("ClipRadarTest.clips().filter(c => c.state === 'todo' && !c.gem).length")
        check("Skip all clips leaves none to review", left == 0 and len(todo) > 0, (left, todo))
    else:
        print("[skip] no ffmpeg here: multi-clip export not checked")
    b.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
