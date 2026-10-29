"""
The contract between the app's local server and whichever UI it serves (the original page in
src/ui, or the React build in src/web). The React rewrite leans on this: it must be able to treat
the API as stable, and the server must serve a Vite build correctly.

  - every API path a UI's source builds exists on the server (scripts/api_routes.py --ui)
  - every GET route answers JSON without crashing, with an id that doesn't exist too
  - POST bodies must be JSON, bounded in size, from this PC (Host and Origin checks)
  - /api/status reports the API version and which UI is served
  - a React build in WEB_DIR is served at /, with hashed /assets/ cached for good, client-side routes
    answered with index.html, module scripts with a JavaScript type, and the old page still at /legacy/

  python tests/test_api_contract.py
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_NO_BROWSER"] = "1"
os.environ.pop("CLIPRADAR_UI", None)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import api_routes  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


PORT = 7991
srv, url = server.start(PORT)


def req(method, path, body=None, headers=None, host=f"127.0.0.1:{PORT}"):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Host": host}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def post(path, obj, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    return req("POST", path, json.dumps(obj).encode(), h)


try:
    # ---------------------------------------------------------------- UI ↔ server paths
    routes = api_routes.server_routes()
    check("the route list was read from routes.py", len(routes) > 60, len(routes))
    calls = sorted(set(api_routes.ui_calls()))
    check("UI sources were scanned", len(calls) > 40, len(calls))
    missing = [(f, p) for f, p in calls if not api_routes.route_for(p, routes)]
    check("every API path the UI calls exists on the server", not missing, missing[:5])

    # ---------------------------------------------------------------- GET routes answer JSON
    skip = {"/api/update/versions", "/api/ping"}           # network / side effects
    crashed, not_json = [], []
    for r in routes:
        if r["method"] != "GET" or not r["path"].startswith("/api/") or r["path"] in skip:
            continue
        path = r["path"] if r["pattern"] is None else r["path"].replace("{id}", "nosuchid0")
        code, h, data = req("GET", path)
        if code >= 500:
            crashed.append((path, code))
        if not h.get("content-type", "").startswith("application/json"):
            not_json.append((path, h.get("content-type")))
        elif r["pattern"] is not None:
            try:
                ok = code == 404 and "error" in json.loads(data)
            except ValueError:
                ok = False
            if not ok and not path.startswith("/api/frame/"):
                crashed.append((path, code))
    check("no GET route fails with a server error", not crashed, crashed[:5])
    check("every GET API route answers JSON", not not_json, not_json[:5])
    code, h, _ = req("GET", "/api/status")
    check("API answers aren't cached by the window", h.get("cache-control") == "no-store", h.get("cache-control"))
    code, h, data = req("GET", "/api/nosuchroute")
    check("an unknown API route is a JSON 404", code == 404 and json.loads(data).get("error"), (code, data[:80]))

    st = json.loads(req("GET", "/api/status")[2])
    check("status reports the API version", st.get("api") == server.API_VERSION, st.get("api"))
    check("status reports which UI is served", st.get("ui") == server.ui_mode() and st.get("ui") in ("web", "legacy"), st.get("ui"))

    # ---------------------------------------------------------------- jobs: only their public fields
    code, _, data = post("/api/analyze", {"source": os.path.join(tempfile.gettempdir(), "no_such_video_x.mp4")})
    jid = json.loads(data).get("job", "")
    code, _, data = req("GET", f"/api/job/{jid}")
    j = json.loads(data) if code == 200 else {}
    check("a job comes back with exactly its public fields", set(j) == set(server.JOB_PUBLIC), sorted(j))
    check("and not the request it was started with or its cancel flag", "body" not in j and "cancel" not in j, sorted(j))

    # ---------------------------------------------------------------- POST hardening
    code, _, data = post("/api/nosuchroute", {})
    check("an unknown POST route is a JSON 404", code == 404 and b"error" in data, (code, data[:80]))
    code, _, data = req("POST", "/api/hotkey", b'{"on": false}', {"Content-Type": "text/plain"})
    check("a POST body that isn't JSON is refused (415)", code == 415, (code, data[:80]))
    code, _, data = req("POST", "/api/hotkey", b"", {"Content-Type": "text/plain"})
    check("an empty POST needs no content type", code != 415, (code, data[:80]))
    code, _, data = req("POST", "/api/hotkey", b"x" * 10,
                        {"Content-Type": "application/json", "Content-Length": str(server.MAX_BODY + 1)})
    check("an oversized POST is refused without reading it (413)", code == 413, (code, data[:80]))
    code, _, _ = post("/api/hotkey", {}, {"Origin": "https://evil.example"})
    check("a POST from another site is refused", code == 403, code)
    code, _, _ = post("/api/hotkey", {}, {"Origin": "null"})
    check("a POST from a sandboxed page (Origin: null) is refused", code == 403, code)
    code, _, _ = post("/api/hotkey", {}, {"Origin": "http://localhost:5173"})
    check("a POST from the Vite dev server (localhost, any port) is allowed", code != 403, code)
    code, _, _ = req("GET", "/api/status", host="evil.example")
    check("a request for another host name is refused (DNS rebinding)", code == 403, code)
    code, _, data = req("POST", "/api/hotkey", b"[1,2]", {"Content-Type": "application/json"})
    check("a JSON body that isn't an object is a 400", code == 400, (code, data[:80]))

    # ---------------------------------------------------------------- serving a React build
    old_web = server.WEB_DIR
    server.WEB_DIR = Path(tempfile.mkdtemp(prefix="vc_noweb_"))       # first, as if it was never built
    code, h, data = req("GET", "/")
    check("with no React build, / is the original page", code == 200 and b"/ui/app.js" in data, code)
    code, _, data = req("GET", "/review")
    check("and client-side paths don't get the page (legacy has no routes)", code == 404, code)

    web = Path(tempfile.mkdtemp(prefix="vc_web_"))
    (web / "assets").mkdir()
    (web / "index.html").write_text('<!doctype html><div id="root"></div>'
                                    '<script type="module" src="/assets/index-a1b2c3.js"></script>', encoding="utf-8")
    (web / "assets" / "index-a1b2c3.js").write_text("console.log('hi')", encoding="utf-8")
    (web / "assets" / "index-a1b2c3.css").write_text("body{}", encoding="utf-8")
    (web / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    server.WEB_DIR = web
    try:
        # with a build, the new UI is at / (4.25.0); /next/, its old address, still works
        code, h, data = req("GET", "/")
        check("with a React build, / is the new UI by default", code == 200 and b'id="root"' in data, (code, data[:60]))
        code, h, data = req("GET", "/next/")
        check("and /next/ still opens it", code == 200 and b'id="root"' in data, (code, data[:60]))
        code, h, _ = req("GET", "/assets/index-a1b2c3.js")
        check("with its bundles", code == 200 and "immutable" in h.get("cache-control", ""), code)
        st = json.loads(req("GET", "/api/status")[2])
        check("status says the new UI is at /", st.get("ui") == "web" and st.get("next") is True, st)
        code, _, data = req("GET", "/legacy/")
        check("the previous UI stays at /legacy/", code == 200 and b"/ui/app.js" in data, code)

        os.environ["CLIPRADAR_UI"] = "web"
        code, h, data = req("GET", "/")
        check("with CLIPRADAR_UI=web, / is the new app", code == 200 and b'id="root"' in data, (code, data[:60]))
        check("its index.html is never cached (updates show up)", h.get("cache-control") == "no-cache", h.get("cache-control"))
        code, h, data = req("GET", "/assets/index-a1b2c3.js")
        check("a hashed bundle is served", code == 200 and data == b"console.log('hi')", code)
        check("as JavaScript (module scripts need it)", h.get("content-type", "").startswith("text/javascript"),
              h.get("content-type"))
        check("and cached for good", "immutable" in h.get("cache-control", ""), h.get("cache-control"))
        code, h, _ = req("GET", "/assets/index-a1b2c3.css")
        check("stylesheets are text/css", h.get("content-type", "").startswith("text/css"), h.get("content-type"))
        code, _, data = req("GET", "/robots.txt")
        check("other files in the build are served", code == 200 and data.startswith(b"User-agent"), code)
        for route in ("/review", "/settings/campaigns", "/exports/"):
            code, h, data = req("GET", route)
            check(f"client-side route {route} gets the app", code == 200 and b'id="root"' in data, code)
        code, _, _ = req("GET", "/assets/missing-zzz.js")
        check("a missing bundle is a 404, not the app page", code == 404, code)
        code, _, _ = req("GET", "/nope.png")
        check("a missing top-level file is a 404", code == 404, code)
        code, _, _ = req("GET", "/assets/..%2F..%2Fserver.py")
        check("paths can't climb out of the build folder", code in (403, 404), code)
        code, _, _ = req("GET", "/..%2Fserver.py")
        check("nor out of the build root", code in (403, 404), code)
        code, _, data = req("GET", "/legacy/")
        check("the original page is still at /legacy/", code == 200 and b"/ui/app.js" in data, code)
        code, h, _ = req("GET", "/ui/app.js")
        check("and its files still load", code == 200 and h.get("content-type", "").startswith("text/javascript"),
              (code, h.get("content-type")))
        code, h, _ = req("GET", "/ui/fonts/captions/Anton-Regular.ttf")
        check("caption fonts stay at /ui/fonts/ for either UI", code == 200, code)
        st = json.loads(req("GET", "/api/status")[2])
        check("status says the new UI is served", st.get("ui") == "web", st.get("ui"))
        code, h, _ = req("GET", "/api/nosuchroute")
        check("API 404s stay JSON with the new UI", code == 404 and h.get("content-type") == "application/json", code)

        os.environ["CLIPRADAR_UI"] = "legacy"
        code, _, data = req("GET", "/")
        check("CLIPRADAR_UI=legacy brings the original page back", code == 200 and b"/ui/app.js" in data, code)
        code, _, _ = req("GET", "/assets/index-a1b2c3.js")
        check("and the build's bundles aren't served then", code == 404, code)
        code, _, _ = req("GET", "/next/")
        check("nor the new UI at /next/", code == 404, code)
    finally:
        server.WEB_DIR = old_web
        os.environ.pop("CLIPRADAR_UI", None)

    os.environ["CLIPRADAR_UI"] = "web"
    server.WEB_DIR = Path(tempfile.mkdtemp(prefix="vc_noweb_"))       # no build there, whatever src/web holds
    code, _, data = req("GET", "/")
    check("CLIPRADAR_UI=web with no build says so instead of showing the old page", code == 503, (code, data[:80]))
    server.WEB_DIR = old_web
    os.environ.pop("CLIPRADAR_UI", None)
finally:
    server.SHUTTING_DOWN[0] = True
    srv.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
