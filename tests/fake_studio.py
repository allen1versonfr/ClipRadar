"""
A stand-in for TikTok Studio's upload page, for tests of studio_auto (the real one needs a TikTok
account). It behaves like the parts Clip Radar relies on: a hidden video file box; once a file is in, a
rich text caption box that starts with the file's name; a Post button that's off while the video
"uploads"; a "Post now" confirmation; the content page after posting. ?login=1 asks to sign in first.
Modes (4.36): "leave" asks "Leave site?" once a video is in; "fails" says the upload failed; "short" keeps only
the first 120 characters of a caption (TikTok's limit, smaller); "es" is the page in Spanish, without the
Post button's data-e2e. "emoji" (4.40) draws the caption's emoji as pictures and puts invisible characters
around its hashtags, so the box's text isn't exactly what was typed. "suggest" picks a hashtag suggestion when
the box is left with the caret right after a hashtag; "lines" drops line breaks from typed text (pasting keeps
them); "nopaste" also ignores pasting, so only typing line by line works. "laggy" (4.50.2) learns about a select-all late, so
Backspace straight after Ctrl+A deletes one letter instead of the caption TikTok started with ("suggest" does too).

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
const FLAKY = location.search.includes('flaky=1');      // a page that ignores the first file and the first typing
const MODE = document.body.dataset.mode || '';
if (MODE === 'es') {
  document.getElementById('post').removeAttribute('data-e2e');
  document.getElementById('post').innerText = 'Publicar';
  document.getElementById('postnow').innerText = 'Publicar ahora';
  document.getElementById('draft').innerText = 'Guardar borrador';
}
if (MODE === 'short') {
  document.getElementById('cap').addEventListener('input', () => {
    const c = document.getElementById('cap');
    if (c.innerText.length > 120) c.innerText = c.innerText.slice(0, 120);
  });
}
if (MODE === 'emoji') {     // a rich editor: emoji drawn as pictures, hashtags with invisible characters around them
  document.getElementById('cap').addEventListener('input', () => setTimeout(() => {
    const c = document.getElementById('cap');
    c.innerHTML = c.innerText.replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[m])
      .replace(/\\p{Extended_Pictographic}\\ufe0f?/gu, m => `<img alt="${m}" width=16 height=16>`)
      .replace(/#\\w+/g, m => '\\u200b' + m + '\\u200b').replace(/\\n/g, '<br>');
  }, 50));
}
if (MODE === 'suggest') {  // TikTok's hashtag list: open while the caret is right after "#word"; leaving the box picks
  const cap = document.getElementById('cap');      // its top suggestion. Escape doesn't close it.
  cap.addEventListener('blur', () => {
    const t = cap.innerText;
    if (/#\\w+$/.test(t)) cap.innerText = t + 'viral';
  });
}
if (MODE === 'lines' || MODE === 'nopaste') {   // a rich editor that drops line breaks in typed text (pasting keeps them)
  const cap = document.getElementById('cap');
  cap.addEventListener('beforeinput', e => {
    if ((e.inputType === 'insertText' || e.inputType === 'insertCompositionText') && e.data && e.data.includes('\\n')) {
      e.preventDefault();
      document.execCommand('insertText', false, e.data.replace(/\\n/g, ''));
    }
  });
}
document.getElementById('cap').addEventListener('paste', e => {   // as Draft.js does: the pasted text, line breaks kept
  if (MODE === 'nopaste') return;
  const t = e.clipboardData && e.clipboardData.getData('text/plain');
  if (!t) return;
  e.preventDefault();
  t.split('\\n').forEach((ln, i) => { if (i) document.execCommand('insertParagraph'); if (ln) document.execCommand('insertText', false, ln); });
});
if (MODE === 'laggy' || MODE === 'suggest') {   // an editor (like Draft.js) that learns about a new selection a moment
  const cap = document.getElementById('cap');      // late: Backspace right after Ctrl+A deletes one letter, not all
  let selAt = -1e9;
  cap.addEventListener('keydown', e => {
    if (e.key === 'a' && (e.ctrlKey || e.metaKey)) selAt = performance.now();
    if (e.key === 'Backspace' && performance.now() - selAt < 60) {
      e.preventDefault();
      const t = cap.innerText;
      cap.innerText = t.slice(0, -1);
      const r = document.createRange(); r.selectNodeContents(cap); r.collapse(false);
      getSelection().removeAllRanges(); getSelection().addRange(r);
    }
  });
}
let ignoredFile = false, ignoredTyping = false;
document.getElementById('cap').addEventListener('input', () => {
  if (FLAKY && !ignoredTyping) { ignoredTyping = true; setTimeout(() => { document.getElementById('cap').innerText = file.name; }, 100); }
});
document.getElementById('f').addEventListener('change', e => {
  if (FLAKY && !ignoredFile) { ignoredFile = true; e.target.value = ''; return; }
  file = e.target.files[0];
  if (MODE === 'leave') window.addEventListener('beforeunload', ev => { ev.preventDefault(); ev.returnValue = ''; });
  document.getElementById('pick').hidden = true;
  document.getElementById('editor').hidden = false;
  document.getElementById('cap').innerText = file.name.replace(/\\.[^.]+$/, '');   // TikTok starts with the file name
  let pct = 0;
  if (MODE === 'fails') { setTimeout(() => { document.getElementById('status').innerText = "Couldn't upload. The video format isn't supported."; }, 400); return; }
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
            if srv.mode == "flaky" and "flaky=1" not in u.query:
                self.send_response(302)
                self.send_header("Location", "/tiktokstudio/upload?flaky=1")
                self.end_headers()
                return
            return self._send(UPLOAD.replace("<body>", f"<body data-mode={srv.mode or 'none'}>", 1))
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
