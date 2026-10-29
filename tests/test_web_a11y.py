"""
Accessibility of the new UI, screen by screen, against the real server with a real video open:
axe-core (WCAG 2.2 A and AA rules) on Home, Review (each tab, the Flagged tab, the detection settings
dialog, a confirm dialog), Exports with a rendered clip, and Settings; every control reachable with Tab
shows where the focus is; and on a touch screen every control is at least 44 px tall and wide.

Needs FFmpeg, the React build (cd web && npm run build), axe-core (cd web && npm ci) and Playwright;
skipped without them. CLIPRADAR_A11Y_REPORT=<file> writes every finding as JSON.

  python tests/test_web_a11y.py
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
os.environ["CLIPRADAR_PREVIEW_VP9"] = "1"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []
report = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIPPED: Playwright isn't installed (pip install playwright)")
    sys.exit(0)
AXE = os.path.join(HERE, "..", "web", "node_modules", "axe-core", "axe.min.js")
if not shutil.which("ffmpeg"):
    print("SKIPPED: FFmpeg isn't on PATH")
    sys.exit(0)
if not os.path.isfile(AXE):
    print("SKIPPED: axe-core isn't installed (cd web && npm ci)")
    sys.exit(0)

import safety  # noqa: E402
import server  # noqa: E402

if not (server.WEB_DIR / "index.html").is_file():
    print("SKIPPED: the React UI isn't built (cd web && npm ci && npm run build)")
    sys.exit(0)
safety.save_config({"custom": ["watch"]})        # a flagged clip, so the content check box shows
from browser import launch  # noqa: E402

srv, url = server.start(7995)


def call(path, body=None):
    rq = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq) as r:
        return json.load(r)


vod = os.path.join(tempfile.mkdtemp(prefix="vc_a11y_"), "a11y_test.mp4")
loud = "+".join(f"between(t,{t},{t + 2})" for t in (20, 45, 70, 95))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
                "-f", "lavfi", "-i", "anoisesrc=d=120:a=0.02:seed=7", "-af", f"volume='if({loud},40,1)':eval=frame",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", vod], check=True)
job = call("/api/analyze", {"source": vod, "count": 5})["job"]
deadline = time.time() + 180
while time.time() < deadline and call("/api/job/" + job)["status"] == "running":
    time.sleep(0.5)
pid = call("/api/job/" + job)["result"]["pid"]
server.start_listening = lambda pid, order, model, lang, on_done=None: server.new_job("listen")
first = server.projects[pid]["clips"][0]
call("/api/queue/add", {"pid": pid, "clip": {"id": first["id"], "rank": first["id"], "start": first["start"], "end": first["end"],
                                             "title": "A11y", "post": "", "thumb": "", "gain": 0},
                        "opts": {"captions": False}, "sound": False})
with server.queue_lock:
    qid = server.queue[-1]["id"]
deadline = time.time() + 180
while time.time() < deadline:
    with server.queue_lock:
        st = next((q["status"] for q in server.queue if q["id"] == qid), "")
    if st in ("done", "error"):
        break
    time.sleep(0.5)
check("a clip rendered for Exports to show", st == "done", st)

RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def axe(page, where):
    if not page.evaluate("!!window.axe"):
        page.add_script_tag(path=AXE)
    res = page.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})", RULES)
    found = [{"where": where, "rule": v["id"], "impact": v["impact"], "help": v["help"],
              "nodes": [n["target"][0] if n["target"] else "" for n in v["nodes"]][:6], "count": len(v["nodes"])}
             for v in res["violations"]]
    report.extend(found)
    check(f"{where}: no WCAG A / AA problems (axe-core)", not found,
          "; ".join(f"{f['rule']} ({f['count']}): {', '.join(map(str, f['nodes'][:3]))}" for f in found))


def focus_shows(page, where, limit=80):
    """Tab through the screen: every control that takes the focus draws a ring (outline or box-shadow)."""
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.mouse.click(1, 1)
    bad, seen = [], 0
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null;
            const s = getComputedStyle(e);
            const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none');
            const r = e.getBoundingClientRect();
            return {ring, tag: e.tagName.toLowerCase(), text: (e.getAttribute('aria-label') || e.textContent || e.name || '').trim().slice(0, 40),
                    hidden: r.width === 0 && r.height === 0} }""")
        if not info:
            break
        seen += 1
        if not info["ring"] and not info["hidden"]:
            bad.append(f"{info['tag']} '{info['text']}'")
    report.extend({"where": where, "rule": "focus-visible", "nodes": [b]} for b in bad)
    check(f"{where}: the focus shows on every control Tab reaches ({seen})", not bad, bad[:6])


def targets(page, where):
    """On a touch screen: controls at least 44 x 44 px (a checkbox counts with its label)."""
    small = page.evaluate("""() => [...document.querySelectorAll('button, a[href], select, input:not([type=hidden]), textarea, [role=tab]')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden' })
        .map(e => { let r = e.getBoundingClientRect();
          if (e.type === 'checkbox' || e.type === 'radio') { const l = e.closest('label'); if (l) r = l.getBoundingClientRect() }
          if (e.type === 'range') r = { width: r.width, height: Math.max(r.height, 44) }   // the whole track row is the target
          return { r, t: (e.getAttribute('aria-label') || e.textContent || e.type || '').trim().slice(0, 30), tag: e.tagName.toLowerCase() } })
        .filter(x => x.r.height < 43.5 || x.r.width < 43.5)
        .map(x => `${x.tag} '${x.t}' ${Math.round(x.r.width)}x${Math.round(x.r.height)}`)""")
    report.extend({"where": where, "rule": "target-size-44", "nodes": [s]} for s in small)
    check(f"{where}: every control is 44 px or more on a touch screen", not small, f"{len(small)}: {small[:6]}")


with sync_playwright() as pw:
    browser = launch(pw)
    if not browser:
        print("SKIPPED: no Chromium-based browser (set CLIPRADAR_BROWSER)")
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        sys.exit(0)
    page = browser.new_page(viewport={"width": 1366, "height": 860})
    page.add_init_script("if (!localStorage.getItem('vc_opts')) localStorage.setItem('vc_opts', JSON.stringify({captions: false}))")

    def screen(hash_, where, ready):
        page.goto(url + "/next/" + hash_)
        page.wait_for_selector(ready, timeout=30000)
        page.wait_for_timeout(600)
        axe(page, where)
        focus_shows(page, where)

    screen("#home", "Home", "[data-testid=find-clips]")
    screen("#review", "Review: open videos", "[data-testid=review-picker]")
    screen(f"#review/{pid}", "Review: Clip tab", "[data-testid=rv-row]")
    for tab in ("Captions", "Text", "Layout"):
        page.get_by_role("tab", name=tab, exact=True).click()
        page.wait_for_timeout(400)
        axe(page, f"Review: {tab} tab")
    page.get_by_role("tab", name="Clip", exact=True).click()
    if page.get_by_role("tab", name="Flagged").count():
        page.get_by_role("tab", name="Flagged").click()
        page.wait_for_timeout(400)
        axe(page, "Review: Flagged tab")
        page.get_by_role("tab", name="To review").click()
    page.get_by_test_id("rv-detect-open").click()
    page.get_by_role("dialog").wait_for()
    page.wait_for_timeout(400)                # dialogs fade in: colours are only final after that
    axe(page, "Review: detection settings dialog")
    page.keyboard.press("Escape")
    page.get_by_test_id("batch-keep").click()
    page.get_by_role("alertdialog").wait_for()
    page.wait_for_timeout(400)                # dialogs fade in: colours are only final after that
    axe(page, "Review: a confirm dialog")
    page.get_by_test_id("confirm-no").click()
    screen("#exports", "Exports", "[data-testid=export-row]")
    screen("#settings", "Settings", "h1")
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    axe(page, "Settings, every section open")

    # a touch screen (pointer: coarse), phone width
    ctx = browser.new_context(viewport={"width": 420, "height": 860}, has_touch=True, is_mobile=True)
    tp = ctx.new_page()
    tp.add_init_script("if (!localStorage.getItem('vc_opts')) localStorage.setItem('vc_opts', JSON.stringify({captions: false}))")
    coarse = None
    for hash_, where, ready in (("#home", "Home", "[data-testid=find-clips]"), (f"#review/{pid}", "Review", "[data-testid=rv-row]"),
                                ("#exports", "Exports", "[data-testid=export-row]"), ("#settings", "Settings", "h1")):
        tp.goto(url + "/next/" + hash_)
        tp.wait_for_selector(ready, timeout=30000)
        tp.wait_for_timeout(500)
        if coarse is None:
            coarse = tp.evaluate("matchMedia('(pointer: coarse)').matches")
            check("the touch screen check runs with a coarse pointer", coarse)
        targets(tp, f"{where} (touch)")
    browser.close()

server.SHUTTING_DOWN[0] = True
srv.shutdown()
if os.environ.get("CLIPRADAR_A11Y_REPORT"):
    with open(os.environ["CLIPRADAR_A11Y_REPORT"], "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
