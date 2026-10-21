"""
Unit tests for render failure and retry: a real FFmpeg crop failure gets a plain-words message
that points at the layout, the item lands in a failed state (not stuck at 0%), Retry renders it
again under the same file name, and nothing is duplicated or lost.

  python tests/test_retry.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
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
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# what FFmpeg really says when a crop doesn't fit (asked from the installed FFmpeg when there is one)
SAMPLE = ("[Parsed_crop_0 @ 0x1] Invalid too big or non positive size for width '0' or height '0'\n"
          "[Parsed_crop_0 @ 0x1] Failed to configure input pad on Parsed_crop_0\n"
          "Error reinitializing filters!\nFailed to inject frame into filter network: Invalid argument")
crop_err = SAMPLE
if shutil.which("ffmpeg"):
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
                        "-vf", "crop=0:0:400:300", "-f", "null", "-"], capture_output=True, text=True)
    crop_err = (p.stderr or SAMPLE).strip()
msg, detail = server.friendly_render_error(RuntimeError("ffmpeg failed:\n" + crop_err))
check("a crop that doesn't fit is explained as a layout problem", "layout" in msg.lower() and "retry" in msg.lower(), msg)
check("with FFmpeg's own words kept as details", "crop" in detail.lower(), detail)
enc, _ = server.friendly_render_error(RuntimeError("Error while opening encoder for output stream #0:0 - h264_nvenc"))
check("encoder failures keep their own advice", "GPU" in enc, enc)

attempts = []


def fake_render(it):
    attempts.append(it["id"])
    it["progress"] = 0.4
    if len(attempts) == 1:
        raise RuntimeError("ffmpeg failed:\n" + crop_err)
    os.makedirs(it["folder"], exist_ok=True)
    with open(os.path.join(it["folder"], it["name"]), "wb") as f:
        f.write(b"ok")
    it["path"] = os.path.join(it["folder"], it["name"])


server._render_item = fake_render
srv, url = server.start(7991)


def api(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


def wait_status(iid, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        it = next((x for x in api("/api/queue")["items"] if x["id"] == iid), None)
        if it and it["status"] in want:
            return it
        time.sleep(0.1)
    return it


folder = tempfile.mkdtemp(prefix="vc_out_")
item = {"id": "r1", "pid": "p1", "start": 0, "end": 5, "title": "Crop at the edge", "status": "queued",
        "progress": 0.0, "folder": folder, "name": "Crop at the edge.mp4", "workdir": folder, "video": "",
        "opts": {}, "created": time.time()}
with server.queue_lock:
    server.queue.append(item)
server.queue_wake.set()

it = wait_status("r1", ("error",))
check("a failed render ends as failed, not stuck", it and it["status"] == "error", it)
check("with the plain-words reason", it and "layout" in it.get("error", "").lower(), it and it.get("error"))
check("and one attempt counted", it and it.get("attempts") == 1, it and it.get("attempts"))

r = api("/api/queue/retry", {"id": "r1"})
check("retry accepts it", r.get("retried") == 1, r)
it = wait_status("r1", ("done",))
check("the retry renders", it and it["status"] == "done", it)
check("under the same file name", it and it["name"] == "Crop at the edge.mp4" and os.path.isfile(os.path.join(folder, it["name"])), it)
check("old error and progress cleared", it and not it.get("error") and it.get("progress") == 1.0, it)
check("rendered exactly twice (once failed, once on retry)", attempts == ["r1", "r1"], attempts)
check("still one item in Exports", sum(1 for x in api("/api/queue")["items"] if x["id"] == "r1") == 1)
r = api("/api/queue/retry", {"id": "r1"})
check("retrying a finished clip does nothing", r.get("status") == 404, r)

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
