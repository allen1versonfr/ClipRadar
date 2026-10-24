"""
A stand-in for TikTok Studio's upload page, for tests of studio_auto (the real one needs a TikTok
account). It behaves like the parts Clip Radar relies on: a hidden video file box; once a file is in, a
rich text caption box that starts with the file's name; a Post button that's off while the video
"uploads"; a "Post now" confirmation; the content page after posting. ?login=1 asks to sign in first.

    srv = fake_studio.start()          # srv.url, srv.posted (what was posted), srv.shutdown()
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

UPLOAD = """<!doctype html><meta charset=utf-8><title>TikTok Studio</title>
<body><h1>Upload video</h1>
<div id=pick><button onclick="document.getElementById('f').click()">Select video</button>
<input id=f type=file accept="video/*" style="display:none"></div>
<div id=editor hidden>
  <div data-e2e=caption_container><div class="public-DraftEditor-content" contenteditable=true id=cap></div></div>
  <div class=upload-status id=status>Uploading 0%</div>
  <button id=draft>Save draft</button> <button data-e2e=post_video_button id=post disabled>Post</button>
</div>
<div id=confirm hidden>Your video may not be eligible for the For You feed. <button id=postnow>Post now</button></div>
<script>
let file = null;
document.getElementById('f').addEventListener('change', e => {
  file = e.target.files[0];
  document.getElementById('pick').hidden = true;
  document.getElementById('editor').hidden = false;
  document.getElementById('cap').innerText = file.name.replace(/\\.[^.]+$/, '');   // TikTok starts with the file name
  let pct = 0;
  const t = setInterval(() => { pct += 25; document.getElementById('status').innerText = pct < 100 ? 'Uploading ' + pct + '%' : 'Uploaded';
    if (pct >= 100) { clearInterval(t); document.getElementById('post').disabled = false; } }, 300);
});
document.getElementById('post').onclick = () => { document.getElementById('confirm').hidden = false; };
document.getElementById('postnow').onclick = async () => {
  await fetch('/posted', { method: 'POST', body: JSON.stringify({ name: file.name, size: file.size,
    caption: document.getElementById('cap').innerText }) });
  location.href = '/tiktokstudio/content';
};
</script>"""
LOGIN = """<!doctype html><meta charset=utf-8><title>Log in | TikTok</title>
<div id=login-modal><button id=signin onclick="document.cookie='signed=1; path=/'; location.href='/tiktokstudio/upload'">Log in</button></div>"""
CONTENT = "<!doctype html><meta charset=utf-8><title>Posts</title><p>Your video has been uploaded.</p>"
CHANGED = "<!doctype html><meta charset=utf-8><title>TikTok Studio</title><p>A redesigned page with no file box.</p>"


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        srv = self.server
        signed = "signed=1" in (self.headers.get("Cookie") or "")
        if u.path == "/tiktokstudio/upload":
            if srv.mode == "changed":
                return self._send(CHANGED)
            if (srv.mode == "login" or parse_qs(u.query).get("login")) and not signed:
                self.send_response(302)
                self.send_header("Location", "/login?redirect_url=/tiktokstudio/upload")
                self.end_headers()
                return
            return self._send(UPLOAD)
        if u.path == "/login":
            return self._send(LOGIN)
        if u.path == "/tiktokstudio/content":
            return self._send(CONTENT)
        self._send("not here", 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if self.path == "/posted":
            self.server.posted.append(json.loads(self.rfile.read(n) or b"{}"))
        self._send("ok")


def start(mode: str = "") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    srv.posted, srv.mode = [], mode
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/tiktokstudio/upload"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
