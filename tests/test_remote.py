"""
Server mode (5.2): Clip Radar opened from another device on the network (Safari on an iPhone or a Mac). Off by default;
on, every request from the network needs the access code once, a wrong code five times in a minute makes the address
wait, another site can't use a signed-in device's cookie, the switch and the code are only for this PC's own window,
and turning it off closes the door.

  python tests/test_remote.py
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_remote_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_remote_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


import remote  # noqa: E402
import server  # noqa: E402

srv, url = server.start(7981)
LOCAL = int(url.rsplit(":", 1)[1])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"192.168.1.20:{port}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        out = json.loads(raw or b"{}")
    except ValueError:
        out = {"_text": raw.decode("utf-8", "replace")}
    return r.status, dict(r.getheaders()), out


st, _, v = req(LOCAL, "GET", "/api/remote", host=f"127.0.0.1:{LOCAL}")
check("off by default, with a code ready", st == 200 and v["on"] is False and v["running"] is False and len(v["code"]) == 8, v)

port = free_port()
st, _, v = req(LOCAL, "POST", "/api/remote", {"on": True, "port": port}, host=f"127.0.0.1:{LOCAL}")
check("turned on from this PC: it listens on the network", st == 200 and v["running"] and v["port"] == port, v)
code = v["code"]

st, h, _ = req(port, "GET", "/")
check("from the network, the app sends you to sign in", st == 303 and h.get("Location") == "/remote-login", (st, h))
st, _, page = req(port, "GET", "/remote-login")
check("the sign-in page asks for the code", st == 200 and "access code" in page.get("_text", ""), st)
st, _, _ = req(port, "GET", "/api/status")
check("and nothing else answers without signing in", st == 401, st)
st, _, _ = req(port, "GET", "/vod/anything")
check("no video either", st == 401, st)

st, _, e = req(port, "POST", "/api/remote/login", {"code": "00000000" if code != "00000000" else "11111111"})
check("a wrong code is refused", st == 401 and "wrong" in e.get("error", ""), (st, e))
st, h, _ = req(port, "POST", "/api/remote/login", {"code": code[:4] + " " + code[4:]})
cookie = (h.get("Set-Cookie") or "").split(";")[0]
check("the right code signs the device in (spaces don't matter)", st == 200 and cookie.startswith(remote.COOKIE + "=")
      and "HttpOnly" in h.get("Set-Cookie", "") and "SameSite=Strict" in h.get("Set-Cookie", ""), h.get("Set-Cookie"))
st, _, s = req(port, "GET", "/api/status", headers={"Cookie": cookie})
check("then the app answers", st == 200 and "version" in s, st)
st, _, _ = req(port, "POST", "/api/titles", {}, headers={"Cookie": cookie, "Origin": "http://evil.example"})
check("another site can't use the signed-in cookie", st == 403, st)
st, _, _ = req(port, "POST", "/api/remote", {"on": False}, headers={"Cookie": cookie, "Origin": f"http://192.168.1.20:{port}"})
check("server mode can't be switched from the network", st == 403, st)
st, _, s = req(port, "GET", "/api/status", headers={"Cookie": cookie})
check("the page knows it's on another device (no Quit button there)", s.get("remote") is True, s.get("remote"))
st, _, _ = req(port, "POST", "/api/quit", {}, headers={"Cookie": cookie, "Origin": f"http://192.168.1.20:{port}"})
check("and can't quit Clip Radar on the PC", st == 403, st)
st, _, rv = req(port, "GET", "/api/remote", headers={"Cookie": cookie})
check("nor the code read from it", st == 200 and "code" not in rv and rv.get("here") is False, rv)

for _ in range(5):
    req(port, "POST", "/api/remote/login", {"code": "99999999" if code != "99999999" else "88888888"})
st, _, e = req(port, "POST", "/api/remote/login", {"code": code})
check("five wrong codes in a minute: the address waits, even with the right one", st == 429, (st, e))
remote._fails.clear()

st, _, v2 = req(LOCAL, "POST", "/api/remote", {"new_code": True}, host=f"127.0.0.1:{LOCAL}")
check("a new code", v2["code"] != code and len(v2["code"]) == 8)
st, _, _ = req(port, "GET", "/api/status", headers={"Cookie": cookie})
check("signs every device out", st == 401, st)

st, _, _ = req(LOCAL, "GET", "/api/status", host=f"127.0.0.1:{LOCAL}")
check("this PC's own window works as always, no code", st == 200, st)

st, _, v3 = req(LOCAL, "POST", "/api/remote", {"on": False}, host=f"127.0.0.1:{LOCAL}")
try:
    req(port, "GET", "/remote-login")
    closed = False
except OSError:
    closed = True
check("turned off: the network listener is gone", v3["running"] is False and closed, v3)
st, _, e = req(LOCAL, "POST", "/api/remote", {"port": 80}, host=f"127.0.0.1:{LOCAL}")
check("a port under 1024 is refused", st == 400 and "1024" in e.get("error", ""), e)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
os._exit(1 if failures else 0)
