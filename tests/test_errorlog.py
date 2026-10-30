"""
Unit tests for the error log (src/errorlog.py): failures from jobs, the UI and crashes on any thread are
written to errors.jsonl with what was going on, secrets blanked; the newest come back first; the file
starts over (keeping one older file) when it gets big; a UI stuck in a loop can't fill it; and the
Exports › Source "Retry failed" only retries that video's clips, and Clear queue empties the list. Also: Downloads at once has no limit
unless you chose one.

  python tests/test_errorlog.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

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

import errorlog  # noqa: E402
import exports  # noqa: E402
import paths  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def lines():
    try:
        with open(errorlog.path(), encoding="utf-8") as f:
            return [json.loads(x) for x in f]
    except OSError:
        return []


# ---------------------------------------------------------------- recording
check("the log sits next to clipradar.log", os.path.dirname(errorlog.path()) == str(paths.LOG_FILE.parent))
try:
    raise ValueError("bad thing with oauth:SECRETTOKEN in it")
except ValueError as e:
    errorlog.record("test", str(e), exc=e, clip="Clip 1")
e = lines()[-1]
check("a failure is recorded with where, the version and the traceback",
      e["where"] == "test" and e["version"] == server.VERSION and "ValueError" in e.get("detail", "") and e["type"] == "ValueError", e)
check("with what was going on", e.get("context") == {"clip": "Clip 1"}, e.get("context"))
check("secrets are blanked", "SECRETTOKEN" not in json.dumps(e), e["message"])

# a job that fails is recorded with its last steps
job = server.new_job("analyze")
server.job_logger(job)("Downloading the VOD")


def boom():
    raise RuntimeError("The VOD download stopped for 3 minutes with nothing written.")


server.run_job(job, boom)
for _ in range(100):
    if job["status"] != "running":
        break
    time.sleep(0.05)
time.sleep(0.1)
e = lines()[-1]
check("a failed job is recorded", e["where"] == "job:analyze" and "download stopped" in e["message"], e)
check("with its last log lines", "Downloading the VOD" in e.get("context", {}).get("log", ""), e.get("context"))

# a crash on another thread
errorlog.install()
t = threading.Thread(target=lambda: 1 / 0, name="worker-x")
t.start()
t.join()
e = lines()[-1]
check("a crash on any thread is recorded", e["where"] == "crash" and e.get("type") == "ZeroDivisionError"
      and e.get("context", {}).get("thread") == "worker-x", e)

# newest first
check("recent() is newest first", errorlog.recent(2)[0]["where"] == "crash" and len(errorlog.recent(2)) == 2)
v = errorlog.view()
check("the view says where the file is and how many this session", v["path"] == errorlog.path() and v["session"] >= 3, v["session"])

# the file starts over when big, keeping one older file
old = errorlog.MAX_BYTES
errorlog.MAX_BYTES = 200
for i in range(5):
    errorlog.record("test", f"fill {i}", "x" * 100)
errorlog.MAX_BYTES = old
check("a big log starts over and keeps one older file",
      os.path.exists(errorlog.path()[:-6] + ".old.jsonl") and os.path.getsize(errorlog.path()) < 1000)
check("recent() reads the older file too", [x["message"] for x in errorlog.recent(2)] == ["fill 4", "fill 3"])

# recording never raises, even with nowhere to write
real = paths.LOG_FILE
paths.LOG_FILE = paths.Path(os.devnull) / "nope" / "clipradar.log"
try:
    errorlog.record("test", "nowhere to write")
    check("recording an error never raises", True)
except Exception as ex:
    check("recording an error never raises", False, ex)
paths.LOG_FILE = real

# ---------------------------------------------------------------- through the app's HTTP routes
import routes  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
import urllib.request  # noqa: E402

httpd = ThreadingHTTPServer(("127.0.0.1", 0), routes.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def post(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read() or b"{}")


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


st, _ = post("/api/client_error", {"message": "Cannot read properties of undefined", "detail": "at ReviewView", "page": "#review", "ui": "web"})
e = lines()[-1]
check("a UI error is recorded", st == 200 and e["where"] == "ui" and e.get("context", {}).get("page") == "#review", e)
for i in range(40):
    post("/api/client_error", {"message": f"loop {i}"})
check("a UI stuck in a loop records at most 30 a minute", sum(1 for x in lines() if x["where"] == "ui") <= 30)
got = get("/api/errors")
check("/api/errors lists them, newest first", got["items"] and got["items"][0]["where"] == "ui" and got["path"], got.get("path"))

# Exports › Source › Retry failed: only that video's clips
with server.queue_lock:
    server.queue[:] = [{"id": "a1", "pid": "p1", "status": "error", "history": []},
                       {"id": "b1", "pid": "p2", "status": "error", "history": []}]
server.queue_wake = threading.Event()        # no render worker in this test
st, r = post("/api/queue/retry", {"id": "all", "pid": "p1"})
check("Retry failed for one source retries only its clips", st == 200 and r.get("retried") == 1
      and [x["status"] for x in server.queue] == ["queued", "error"], (st, r, [x["status"] for x in server.queue]))
# Exports › Clear queue: everything but what's rendering comes off, per source when one is picked
with server.queue_lock:
    server.queue[:] = [{"id": "d1", "pid": "p1", "status": "done", "history": []},
                       {"id": "q1", "pid": "p1", "status": "queued", "history": []},
                       {"id": "r1", "pid": "p1", "status": "rendering", "history": []},
                       {"id": "e2", "pid": "p2", "status": "error", "history": []}]
st, r = post("/api/queue/clear", {"pid": "p1"})
check("Clear queue for one source: its finished and waiting clips go, the one rendering stays, others untouched",
      st == 200 and r == {"ok": True, "removed": 2, "rendering": 1} and [x["id"] for x in server.queue] == ["r1", "e2"],
      (r, [x["id"] for x in server.queue]))
st, r = post("/api/queue/clear", {})
check("Clear queue: everything but what's rendering", r.get("removed") == 1 and [x["id"] for x in server.queue] == ["r1"],
      (r, [x["id"] for x in server.queue]))
with server.queue_lock:
    server.queue[:] = []
httpd.shutdown()

# ---------------------------------------------------------------- downloads at once
with open(os.path.join(str(paths.DATA_DIR), "export.json"), "w") as f:
    json.dump({"downloads": 1, "renders": 2}, f)
check("an old saved 1 download at once (the old default) becomes no limit", exports.load()["downloads"] == 0)
exports.save({"downloads": 2})
check("a number you choose now is kept", exports.load()["downloads"] == 2)
exports.save({"downloads": 0})
check("and no limit is a choice too", exports.load()["downloads"] == 0)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
