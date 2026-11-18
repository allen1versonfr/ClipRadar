"""
Uploading an exported VOD to YouTube Studio for you (studio_auto.upload_youtube), against a stand-in for YouTube
Studio's upload dialog in a real browser: Edge or Chrome when installed, else Playwright's Chromium.

The video that goes in is the exported file; the title replaces the one YouTube starts with (the file's name) and
the description is typed. It stops there: audience, visibility and Publish are never pressed. The first time, it
waits for you to sign in. A page without the upload box (YouTube asking something first) is a named step, recorded in
the error log with a fingerprint of the page that has none of the title, description, file name or account.

  python tests/test_youtube_auto.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_STUDIO_HEADLESS"] = "1"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import studio_auto  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


UPLOAD = """<!doctype html><meta charset=utf-8><title>Channel content - YouTube Studio</title>
<body><h1>Upload videos</h1>
<ytcp-uploads-file-picker><button onclick="document.getElementById('f').click()">Select files</button>
<input id=f type=file name=Filedata style="display:none"></ytcp-uploads-file-picker>
<div id=details hidden>
  <div id=title-textarea><div id=textbox contenteditable=true aria-label="Add a title that describes your video"></div></div>
  <div id=description-textarea><div id=textbox contenteditable=true aria-label="Tell viewers about your video"></div></div>
  <div id=audience>Is this video made for kids? <input type=radio name=kids id=kids-yes> <input type=radio name=kids id=kids-no></div>
  <button id=publish onclick="window.__published = true">Publish</button>
</div>
<script>
window.__published = false;
const f = document.getElementById('f');
f.addEventListener('change', () => {
  if (MODE === 'nodetails') return;
  setTimeout(() => {
    document.getElementById('details').hidden = false;
    document.querySelector('#title-textarea #textbox').innerText = f.files[0].name.replace(/\\.[^.]+$/, '');
  }, 300);
});
</script>"""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        mode = self.server.mode
        if u.path == "/signin":
            self.server.signed = True
            return self._send("<!doctype html><title>Signed in</title><script>location.href='/upload'</script>")
        if u.path.startswith("/upload"):
            if mode == "login" and not self.server.signed:
                return self._send("<!doctype html><title>Sign in</title><body>Sign in to continue to YouTube"
                                  "<script>history.replaceState(null, '', '/signin-page'); window.__login = 1</script>")
            if mode == "channel":
                return self._send("<!doctype html><html lang=en><title>Create your channel</title><body>"
                                  "<ytd-channel-name>Mira Okonkwo</ytd-channel-name> Create a channel first"
                                  "<button>Create channel</button><button aria-label='Account'>Mira Okonkwo</button>")
            page = UPLOAD.replace("<script>", f"<script>const MODE = {mode!r};", 1)
            return self._send(page)
        self._send("not found", 404)

    def _send(self, html, code=200):
        b = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def start(mode=""):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.mode, srv.signed = mode, False
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/upload"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if not studio_auto.find_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        if os.path.isfile(exe):
            os.environ["CLIPRADAR_STUDIO_BROWSER"] = exe
    except Exception:
        pass
if not studio_auto.find_browser():
    print("SKIPPED: no Edge, Chrome or Playwright Chromium here")
    sys.exit(0)
print("browser:", studio_auto.find_browser())

# the sign-in check looks for Google's sign-in page; the stand-in signs in on its own address
studio_auto.YT["login"] = "location.pathname === '/signin-page'"
tmp = tempfile.mkdtemp(prefix="vc_yt_")
video = os.path.join(tmp, "Friday stream (full VOD).mp4")
with open(video, "wb") as f:
    f.write(b"\0" * 2048)
steps: list = []

try:
    srv = start()
    r = studio_auto.upload_youtube(video, "Friday <stream> highlights", "Full stream from Friday.\nTimestamps below.",
                                   srv.url, steps.append)
    page = studio_auto._yt_tab(studio_auto._state["port"], srv.url)[0]
    try:
        title = page.eval("document.querySelector('#title-textarea #textbox').innerText")
        desc = page.eval("document.querySelector('#description-textarea #textbox').innerText")
        got_file = page.eval("document.getElementById('f').files[0]?.name || ''")
        published = page.eval("window.__published")
        kids = page.eval("document.getElementById('kids-yes').checked || document.getElementById('kids-no').checked")
    finally:
        page.close()
    check("the exported file goes in", got_file == os.path.basename(video), got_file)
    check("the title replaces the file's name, without angle brackets", title == "Friday stream highlights" and r["title"], title)
    check("the description is typed", "Full stream from Friday." in desc and r["description"], desc)
    check("Publish is never pressed", published is False)
    check("the audience question is left for you", kids is False)
    check("each step is said", steps[:3] == ["Opening YouTube Studio", "Putting the video in", "Typing the title"], steps)
    srv.shutdown()

    srv = start("login")
    del steps[:]
    done: dict = {}

    def run():
        try:
            done["r"] = studio_auto.upload_youtube(video, "T", "", srv.url, steps.append)
        except Exception as e:      # noqa: BLE001  said in the check below
            done["error"] = repr(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    import time
    end = time.time() + 30
    while time.time() < end and not any("Sign in" in s for s in steps):
        time.sleep(0.2)
    check("asks you to sign in when YouTube does", any("Sign in to YouTube" in s for s in steps), steps)
    # sign in on the tab that's waiting (it's on the sign-in page now, not the upload page)
    import cdp
    tab = next(x for x in cdp.targets(studio_auto._state["port"]) if "/signin-page" in x.get("url", ""))
    page = cdp.Page(tab["webSocketDebuggerUrl"])
    page.eval("setTimeout(() => location.href = '/signin', 0), true")
    page.close()
    t.join(60)
    check("and carries on once you're in", (done.get("r") or {}).get("ready") is True, (done, steps))
    srv.shutdown()

    srv = start("channel")
    try:
        studio_auto.upload_youtube(video, "Zanzibar marathon recap", "Quokka giveaway tonight", srv.url, lambda s: None)
        check("a page without the upload box is a named step", False)
    except studio_auto.StudioError as e:
        check("a page without the upload box is a named step", e.step == "file box" and "answer it" in str(e), (e.step, str(e)))
    srv.shutdown()
    # recorded in errors.jsonl with the page's fingerprint, and nothing of the title, description, file or account
    import json
    import errorlog
    rec = next((x for x in errorlog.recent(20) if x.get("where") == "upload"), {})
    fp = rec.get("fingerprint") or {}
    check("the failure is recorded with YouTube's fingerprint", rec.get("context", {}).get("site") == "youtube"
          and rec.get("context", {}).get("step") == "file box" and fp.get("site") == "youtube", rec)
    check("which shows no upload box", fp.get("selectors", {}).get("file") == {"match": False}
          and set(studio_auto.YT) <= set(fp.get("selectors", {})), fp.get("selectors"))
    check("and the page's buttons", "Create channel" in fp.get("buttons", []), fp.get("buttons"))
    text = json.dumps(rec, ensure_ascii=False).lower()
    check("no title, description, file name or account in it",
          not [w for w in ("zanzibar", "quokka", "friday", "okonkwo", "mira") if w in text], text[:500])

    try:
        studio_auto.upload_youtube(os.path.join(tmp, "gone.mp4"), "T", "", srv.url, lambda s: None)
        check("a file that isn't there is refused", False)
    except studio_auto.StudioError as e:
        check("a file that isn't there is refused", e.step == "file", e.step)
finally:
    studio_auto._close_profile_browser()

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
