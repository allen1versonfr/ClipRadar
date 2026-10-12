"""
A local imitation of GitHub's "latest release" API, for testing VodClipper's updater.

  python mock_github.py PORT TAG [SETUP_EXE]

Serves /repos/<owner>/<repo>/releases/latest with one VodClipper-Setup.exe asset
(the given file, or a few bytes of filler), including its size and sha256 digest
the way GitHub reports them. POST /__set {"tag": "v1.2.3"} changes the release.
Point VodClipper at it with:
  VODCLIPPER_UPDATE_API=http://127.0.0.1:PORT/repos/ali1qbal/vodclipper/releases/latest
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"tag": "v99.0.0", "file": "", "data": b"MZ not really an installer\n", "hits": 0, "downloads": 0}


def _blob() -> bytes:
    if STATE["file"]:
        with open(STATE["file"], "rb") as f:
            return f.read()
    return STATE["data"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        host = self.headers.get("Host")
        if self.path.endswith("/releases/latest"):
            STATE["hits"] += 1
            if not self.headers.get("User-Agent"):
                return self._json({"message": "Request forbidden by administrative rules. Please make sure your request has a User-Agent header"}, 403)
            blob = _blob()
            ver = STATE["tag"].lstrip("v")
            return self._json({
                "tag_name": STATE["tag"], "name": f"VodClipper {ver}", "draft": False, "prerelease": False,
                "html_url": f"https://github.com/ali1qbal/vodclipper/releases/tag/{STATE['tag']}",
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "body": f"Test release {ver}.\n\n### Added\n- **Something new** for `testing`.\n\n---\n**Download:** the installer.",
                "assets": [{
                    "name": "VodClipper-Setup.exe", "size": len(blob),
                    "digest": "sha256:" + hashlib.sha256(blob).hexdigest(),
                    "browser_download_url": f"http://{host}/download/VodClipper-Setup.exe",
                }, {
                    "name": "VodClipper-portable.zip", "size": 3,
                    "browser_download_url": f"http://{host}/download/VodClipper-portable.zip",
                }],
            })
        if self.path == "/download/VodClipper-Setup.exe":
            STATE["downloads"] += 1
            blob = _blob()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            for i in range(0, len(blob), 1 << 20):
                self.wfile.write(blob[i:i + (1 << 20)])
            return
        if self.path == "/__state":
            return self._json({k: v for k, v in STATE.items() if k != "data"})
        self._json({"message": "Not Found"}, 404)

    def do_POST(self):
        if self.path == "/__set":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            STATE.update({k: v for k, v in body.items() if k in ("tag", "file")})
            return self._json({"ok": True})
        self._json({"message": "Not Found"}, 404)


def start(port: int, tag: str = "v99.0.0", file: str = "") -> ThreadingHTTPServer:
    STATE.update(tag=tag, file=file)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    start(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "v99.0.0",
          os.path.abspath(sys.argv[3]) if len(sys.argv) > 3 else "")
    print("mock GitHub running", flush=True)
    while True:
        time.sleep(3600)
