"""
Server mode (5.2): Clip Radar opened from another device on your network, Safari on an iPhone, iPad or Mac included.

  - Off by default. Settings › Server mode turns it on: a second listener on every network address of this PC, on its
    own port (7870 unless changed), next to the usual one on 127.0.0.1, which nothing here changes.
  - Every request from the network needs signing in once with the access code shown in Settings (eight digits, made at
    random; a new one signs every device out). A device that signs in gets a cookie (HttpOnly, SameSite=Strict) that
    lasts 30 days. Five wrong codes in a minute from one address: that address waits a minute.
  - A request from the network must come from the page it was served by (Origin matches Host), so another site open
    in Safari can't use the signed-in cookie.
  - Plain HTTP: anyone on the same network who can watch its traffic could read it. For a home network.
  - Turning server mode on or off, and the code, only from this PC's own window (never from the network).

Kept in remote.json in the data folder.
"""

from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from typing import Optional

import paths
import persist

DEFAULT_PORT = 7870
COOKIE = "cr_session"
SESSION_DAYS = 30
TRIES = 5                     # wrong codes per address per minute before it waits

_lock = threading.Lock()
_server: list = [None]        # the listener while server mode is on
_fails: dict = {}             # address -> [times of wrong codes]
_bound: list = [0]            # the port it really listens on


def _file() -> str:
    return os.path.join(str(paths.DATA_DIR), "remote.json")


def new_code() -> str:
    return f"{secrets.randbelow(10 ** 8):08d}"


def load() -> dict:
    d = persist.read_json(_file(), {}) or {}
    out = {"on": d.get("on") is True, "port": int(d.get("port") or DEFAULT_PORT), "code": str(d.get("code") or ""),
           "sessions": {k: float(v) for k, v in (d.get("sessions") or {}).items() if isinstance(k, str)}}
    if not (1024 <= out["port"] <= 65535):
        out["port"] = DEFAULT_PORT
    if not out["code"]:
        out["code"] = new_code()
        _save(out)
    return out


def _save(d: dict) -> None:
    try:
        os.makedirs(str(paths.DATA_DIR), exist_ok=True)
        persist.write_json(_file(), d)
    except OSError:
        pass


def addresses() -> list:
    """This PC's addresses on the network (what to type in Safari), best first."""
    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))          # no packet is sent: it only picks the outgoing address
            out.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
    except OSError:
        pass
    return [ip for ip in out if not ip.startswith("127.")]


def view() -> dict:
    d = load()
    port = _bound[0] or d["port"]
    running = _server[0] is not None
    return {"on": d["on"], "running": running, "port": port, "code": d["code"],
            "urls": [f"http://{ip}:{port}" for ip in addresses()] if running else [],
            "devices": len(d["sessions"]), "error": state.get("error", "")}


state: dict = {}


# ---------------------------------------------------------------- signing in

def check_code(addr: str, code: str) -> Optional[str]:
    """A new session token when the code is right, None when it isn't. Raises PermissionError while the address waits."""
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(addr, []) if now - t < 60]
        _fails[addr] = recent
        if len(recent) >= TRIES:
            raise PermissionError("Too many wrong codes. Wait a minute and try again.")
        d = load()
        if not secrets.compare_digest(str(code or "").strip().replace(" ", ""), d["code"]):
            recent.append(now)
            return None
        token = secrets.token_urlsafe(32)
        d["sessions"] = {k: v for k, v in d["sessions"].items() if now - v < SESSION_DAYS * 86400}
        d["sessions"][token] = now
        _save(d)
        return token


def signed_in(cookie_header: str) -> bool:
    token = ""
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            token = v
    if not token:
        return False
    sessions = load()["sessions"]
    t = sessions.get(token)
    return t is not None and time.time() - t < SESSION_DAYS * 86400


# ---------------------------------------------------------------- the listener

def apply(make_server) -> dict:
    """Start or stop the network listener to match the setting. make_server(host, port) -> a server (not started)."""
    d = load()
    with _lock:
        cur = _server[0]
        if cur is not None and (not d["on"] or _bound[0] != d["port"]):
            try:
                cur.shutdown()
                cur.server_close()
            except Exception:
                pass
            _server[0], _bound[0] = None, 0
        if d["on"] and _server[0] is None:
            try:
                srv = make_server("0.0.0.0", d["port"])
            except OSError as e:
                state["error"] = (f"Port {d['port']} is taken by another program. Choose another port." if getattr(e, "errno", 0) in (98, 10048)
                                  else f"Couldn't start server mode: {e.strerror or e}")
                return view()
            srv.remote = True
            _server[0], _bound[0] = srv, d["port"]
            state["error"] = ""
            threading.Thread(target=srv.serve_forever, daemon=True, name="remote").start()
        elif not d["on"]:
            state["error"] = ""
    return view()


def save(patch: dict, make_server) -> dict:
    """From this PC's window only: turn it on or off, change the port, or make a new code (signing every device out)."""
    with _lock:
        d = load()
        if "on" in patch:
            d["on"] = patch["on"] is True
        if "port" in patch:
            try:
                p = int(patch["port"])
            except (TypeError, ValueError):
                p = 0
            if not (1024 <= p <= 65535):
                raise ValueError("Choose a port from 1024 to 65535.")
            d["port"] = p
        if patch.get("new_code"):
            d["code"], d["sessions"] = new_code(), {}
        _save(d)
    return apply(make_server)


LOGIN_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Clip Radar</title><style>
:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e0f11;color:#e9eaed;
font:16px/1.4 -apple-system,system-ui,Segoe UI,sans-serif}form{display:flex;flex-direction:column;gap:12px;width:min(320px,90vw)}
h1{font-size:22px;margin:0}p{margin:0;color:#90939c;font-size:14px}input{font:inherit;font-size:22px;letter-spacing:.2em;
text-align:center;padding:12px;border-radius:8px;border:1px solid #3a3d46;background:#16171a;color:inherit}
button{font:inherit;font-weight:600;padding:12px;border:0;border-radius:8px;background:#2fe36f;color:#0e0f11}
#err{color:#ff6b6b;min-height:1.4em}</style>
<form id=f><h1>Clip Radar</h1><p>Type the access code from Clip Radar on your PC: Settings › Server mode.</p>
<input id=c inputmode=numeric autocomplete=one-time-code maxlength=9 autofocus aria-label="Access code">
<button>Sign in</button><p id=err role=alert></p></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/remote/login',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({code:document.getElementById('c').value})});
if(r.ok){location.href='/'}else{document.getElementById('err').textContent=(await r.json().catch(()=>({}))).error||'That code is wrong.'}}</script>"""
