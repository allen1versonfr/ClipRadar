"""
A small Chrome DevTools Protocol client (Edge and Chrome speak it), with only the standard library.

Clip Radar uses it to drive TikTok Studio in a browser window of its own (studio_auto.py). It only
ever talks to a browser on this PC (127.0.0.1), over a plain WebSocket: no TLS, no extensions, no
fragmented frames, which is all a local DevTools endpoint needs.

  page = cdp.Page.connect(port)          # the first page tab of the browser listening on port
  page.call("Page.navigate", url="...")
  page.eval("document.title")
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from typing import Optional
from urllib.parse import urlparse


BROWSER_PAGES = ("edge://", "chrome://", "devtools://", "chrome-extension://", "extension://")


class CDPError(RuntimeError):
    pass


def targets(port: int, timeout: float = 3.0) -> list:
    """The browser's tabs and workers (/json/list)."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as r:
        return json.loads(r.read().decode())


def new_tab(port: int, url: str = "about:blank") -> dict:
    """Open a tab (/json/new; newer browsers want PUT)."""
    from urllib.parse import quote
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/?=&')}", method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def alive(port: int) -> bool:
    try:
        targets(port, timeout=1.0)
        return True
    except OSError:
        return False


class _Socket:
    """One WebSocket connection: text frames out (masked, as a client must), text frames in."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        if u.hostname not in ("127.0.0.1", "localhost"):
            raise CDPError("Only a browser on this PC can be driven.")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("The browser closed the connection.")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise CDPError(f"The browser refused the connection ({status.decode(errors='replace')}).")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise CDPError("The browser window was closed.")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str):
        data = text.encode()
        n = len(data)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 1 << 16:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> Optional[str]:
        """The next text message (None for a control frame)."""
        b0, b1 = self._read(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if b1 & 0x80 else b""
        data = self._read(n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        op = b0 & 0x0F
        if op == 0x8:
            raise CDPError("The browser window was closed.")
        if op == 0x9:                                   # ping: answer it
            self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            return None
        return data.decode(errors="replace") if op == 0x1 else None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Page:
    """One browser tab, driven over DevTools."""

    def __init__(self, ws_url: str, target_id: str = ""):
        self.ws = _Socket(ws_url)
        self.id = target_id
        self.next = 0
        self.events: list = []

    @classmethod
    def connect(cls, port: int, url_prefix: str = "") -> "Page":
        """The first page tab (whose URL starts with url_prefix, when given). The browser's own pages don't count:
        Edge signed in to an account lists its sync dialog (edge://sync-confirmation-dialog/) as a page, and a page
        like that can't be sent anywhere (4.38)."""
        pages = [t for t in targets(port) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                 and not t.get("url", "").startswith(BROWSER_PAGES)]
        pick = next((t for t in pages if url_prefix and t.get("url", "").startswith(url_prefix)), pages[0] if pages else None)
        if not pick:
            raise CDPError("The browser has no tab to use.")
        return cls(pick["webSocketDebuggerUrl"], pick.get("id", ""))

    def call(self, method: str, timeout: float = 20.0, **params) -> dict:
        self.next += 1
        mine = self.next
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        end = time.time() + timeout
        while time.time() < end:
            self.ws.sock.settimeout(max(0.1, end - time.time()))
            try:
                msg = self.ws.recv()
            except socket.timeout:
                break
            if msg is None:
                continue
            m = json.loads(msg)
            if m.get("id") == mine:
                if "error" in m:
                    raise CDPError(f"{method}: {m['error'].get('message', m['error'])}")
                return m.get("result", {})
            if "method" in m:
                self.events = (self.events + [m])[-200:]
        raise CDPError(f"The browser didn't answer {method} in time.")

    def eval(self, expression: str, timeout: float = 20.0):
        """Run JavaScript in the page and return its (JSON) value."""
        r = self.call("Runtime.evaluate", timeout=timeout, expression=expression, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise CDPError(f"Page script failed: {d.get('exception', {}).get('description') or d.get('text')}")
        return r.get("result", {}).get("value")

    def close(self):
        self.ws.close()
