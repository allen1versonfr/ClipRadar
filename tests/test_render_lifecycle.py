"""
The render lifecycle through the real queue and the real FFmpeg: a render goes queued → validating →
preparing → rendering → verifying → done, each attempt is recorded, a percentage is only reported
while FFmpeg is really encoding, failures name their cause, only transient ones are retried by
themselves (once), cancelling stops FFmpeg and leaves nothing behind, a broken output is never shown
as a finished clip, and a clip that keeps crashing the app isn't restarted forever.

  python tests/test_render_lifecycle.py
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

import engine  # noqa: E402
import exports  # noqa: E402
import persist  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- pure parts first: these run without FFmpeg
k, msg, _ = server.classify_render_error(engine.RenderInputError("source_range", "This clip starts after the end."))
check("a clip outside the video is its own kind, not retried", k == "source_range" and k not in server.RETRYABLE, k)
k, msg, _ = server.classify_render_error(RuntimeError("ffmpeg render failed:\nFFmpeg made no progress for 120 seconds and was stopped."))
check("a stalled FFmpeg is a retryable stall", k == "stalled" and k in server.RETRYABLE, (k, msg))
for raw, want in (("[Parsed_crop_0 @ 0x1] Failed to configure input pad on Parsed_crop_0\nError reinitializing filters!", "layout"),
                  ("Task finished with error code: -22 (Invalid argument)\nError reinitializing filters!", "layout"),
                  ("[vost#0:0/libx264] Could not open encoder before EOF\nError while opening encoder", "encoder"),
                  ("moov atom not found", "source_damaged")):
    k = server.classify_render_error(RuntimeError(raw))[0]
    check(f"FFmpeg's '{raw.splitlines()[0][:40]}…' is classed as {want}", k == want, k)
check("deterministic failures aren't retryable", not ({"layout", "encoder", "source_damaged", "source_missing",
                                                      "captions", "disk_full"} & server.RETRYABLE))
red = server.redact("--twitch-token abc oauth:SECRET1 https://x/v?token=SECRET2&a=1 Authorization: Bearer SECRET3 "
                    + os.path.expanduser("~") + "/clips")
check("diagnostics never carry tokens", not any(t in red for t in ("SECRET1", "SECRET2", "SECRET3")), red)
check("and show the home folder as ~", os.path.expanduser("~") not in red, red)

# restarting after a crash mid-render: once it starts again, twice it waits for Retry
QV = persist.QUEUE_V
base = {"id": "c1", "pid": "p", "start": 0, "end": 5, "title": "t", "status": "rendering", "progress": 0.4,
        "history": [{"n": 1, "outcome": "running", "started": 1.0}]}
got = persist.restore_queue({"v": QV, "items": [dict(base)]})
check("a render cut off by a crash starts again after a restart", got and got[0]["status"] == "queued"
      and got[0]["interrupted"] == 1 and got[0]["history"][-1]["outcome"] == "interrupted", got)
again = dict(got[0], status="rendering")
got = persist.restore_queue({"v": QV, "items": [again]})
check("cut off twice in a row: it waits for Retry instead of crashing the app again",
      got and got[0]["status"] == "error" and got[0]["error_kind"] == "interrupted" and "Retry" in got[0]["error"], got)

if not shutil.which("ffmpeg"):
    print("PASSED (FFmpeg parts skipped: FFmpeg isn't installed)" if not failures else f"FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)

TMP = tempfile.mkdtemp(prefix="vc_life_")
SRC = os.path.join(TMP, "vod.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=40",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=40", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", SRC], check=True)
exports.save({"sounds": False})
# software encoding, no captions: the queue item's own options
OPTS = {"layout": "split", "captions": False, "hw_encode": False}

stages = []
real_set_stage = server.set_stage


def spy_stage(it, stage, step):
    stages.append((it["id"], stage, it.get("progress", 0.0)))
    real_set_stage(it, stage, step)


server.set_stage = spy_stage
srv, url = server.start(7993)


def api(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


def item(iid, start, end, **kw):
    it = {"id": iid, "pid": "life", "start": start, "end": end, "title": f"Clip {iid}", "status": "queued", "stage": "queued",
          "progress": 0.0, "step": "Waiting", "error": "", "cancel": False, "workdir": TMP, "video": SRC, "live": "",
          "opts": dict(OPTS), "created": time.time(), "words": [], "export": exports.settings(exports.load())}
    it.update(kw)
    with server.queue_lock:
        server.queue.append(it)
    server.queue_wake.set()
    return it


def wait(iid, want, timeout=120):
    end = time.time() + timeout
    it = None
    while time.time() < end:
        it = next((x for x in api("/api/queue")["items"] if x["id"] == iid), None)
        if it is None or it["status"] in want:
            return it
        time.sleep(0.1)
    return it


def ffmpeg_children():
    """FFmpeg processes started by this test (they write into TMP)."""
    if sys.platform == "win32":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
              "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        return [ln.split()[0] for ln in p.stdout.splitlines() if ln.strip() and os.path.basename(TMP) in ln]
    out = []
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
        except OSError:
            continue
        if "ffmpeg" in cmd and TMP in cmd:
            out.append(pid)
    return out


# 1. a good render, every stage in order, a percentage only while encoding
seen = []
it = item("ok1", 5.0, 13.0)
end = time.time() + 120
while time.time() < end:
    q = next((x for x in api("/api/queue")["items"] if x["id"] == "ok1"), {})
    seen.append((q.get("stage"), q.get("progress")))
    if q.get("status") in ("done", "error"):
        break
    time.sleep(0.05)
q = wait("ok1", ("done", "error"))
check("a good render finishes", q and q["status"] == "done" and q.get("stage") == "done", q and (q.get("error"), q.get("detail", "")[-300:]))
order = [s for i, s, _ in stages if i == "ok1"]
check("through validating, preparing, rendering, verifying, done", order[:1] == ["validating"] and "preparing" in order
      and order.index("rendering") < order.index("verifying") < order.index("done"), order)
check("no percentage outside encoding", all((p or 0) == 0 for s, p in seen if s in ("validating", "preparing")), seen[:20])
check("the percentage climbs while encoding", any(0 < (p or 0) < 1 for s, p in seen if s == "rendering"), seen[-20:])
h = (q or {}).get("history") or []
check("one attempt recorded, done, with its encoder", len(h) == 1 and h[0]["outcome"] == "done" and h[0].get("encoder") == "libx264"
      and h[0]["ended"] >= h[0]["started"], h)
check("the file is a real clip", q and os.path.isfile(os.path.join(q["folder"], q["name"])))
check("no FFmpeg left running", not ffmpeg_children(), ffmpeg_children())

# 2. a clip outside the video: fails at once, names why, isn't retried
q = wait(item("past", 100.0, 110.0)["id"], ("done", "error"))
check("a clip past the end fails in validation", q and q["status"] == "error" and q.get("error_kind") == "source_range"
      and q.get("retryable") is False and (q.get("history") or [{}])[-1].get("stage") == "validating", q)
check("with one attempt, not retried by itself", q and len(q.get("history") or []) == 1, q and q.get("history"))
check("and no file", q and not q.get("path"))

# 3. the recording was deleted
gone = os.path.join(TMP, "gone.mp4")
shutil.copy(SRC, gone)
it = item("gone", 1.0, 5.0, video=gone, pid="gone")
os.remove(gone)
q = wait("gone", ("done", "error"))
check("a deleted recording is named as such", q and q.get("error_kind") == "source_missing" and q.get("retryable") is False, q)

# 4. a real FFmpeg filter failure: classed as a layout problem, with FFmpeg's command and words kept
real_bf = engine.build_filter
engine.build_filter = lambda info, o, path=None, face=None: ("[0:v]crop=4000:4000:0:0,scale=1080:1920,format=yuv420p[v]", 960.0)
q = wait(item("bad", 2.0, 6.0)["id"], ("done", "error"))
engine.build_filter = real_bf
h = (q or {}).get("history") or []
check("a filter that can't be built fails as a layout problem", q and q.get("error_kind") == "layout"
      and "Layout" in q.get("error", ""), q and (q.get("error_kind"), q.get("error")))
check("not retried by itself (it would fail the same way)", len(h) == 1, h)
check("the attempt keeps FFmpeg's command, exit code and reason", h and "ffmpeg" in h[-1].get("cmd", "")
      and h[-1].get("exit") not in (None, 0) and "crop" in h[-1].get("stderr", "").lower(), h)
check("no temporary files left in the clips folder", q and not [f for f in os.listdir(q.get("folder") or TMP)
                                                               if f.startswith("vc_")] if q and q.get("folder") else True)
r = api("/api/queue/retry", {"id": "bad"})
q = wait("bad", ("done", "error"))
check("Retry after the cause is gone renders it", r.get("retried") == 1 and q and q["status"] == "done"
      and [a["outcome"] for a in q["history"]] == ["error", "done"], q and q.get("history"))
check("and clears the old failure", q and not q.get("error") and "error_kind" not in q and "retryable" not in q, q)

# 5. a stall is retried once by itself; a second stall stops there (no endless loop)
real_rc = engine.render_clip
calls = {"n": 0}


def stall_once(*a, **k):
    calls["n"] += 1
    if calls["n"] == 1:
        raise RuntimeError("ffmpeg render failed:\nFFmpeg made no progress for 120 seconds and was stopped.")
    return real_rc(*a, **k)


engine.render_clip = stall_once
q = wait(item("stall", 2.0, 6.0)["id"], ("done", "error"))
check("a stalled render is tried once more by itself and finishes", q and q["status"] == "done"
      and [a["outcome"] for a in q["history"]] == ["error", "done"] and q["history"][0].get("kind") == "stalled",
      q and q.get("history"))


def always_stall(*a, **k):
    calls["n"] += 1
    raise RuntimeError("ffmpeg render failed:\nFFmpeg made no progress for 120 seconds and was stopped.")


calls["n"] = 0
engine.render_clip = always_stall
q = wait(item("stall2", 2.0, 6.0)["id"], ("done", "error"))
time.sleep(1.0)
check("stalling again: it stops after two tries", q and q["status"] == "error" and calls["n"] == 2
      and q.get("retryable") is True, (calls["n"], q and q.get("status")))
engine.render_clip = real_rc

# 6. cancelling a render stops FFmpeg and leaves nothing behind
it = item("cancel", 0.0, 38.0, export=dict(exports.settings(exports.load()), speed="high"))
end = time.time() + 60
while time.time() < end:
    q = next((x for x in api("/api/queue")["items"] if x["id"] == "cancel"), {})
    if q.get("stage") == "rendering" and q.get("progress", 0) > 0.02:
        break
    time.sleep(0.05)
check("the long render got going", q.get("stage") == "rendering", q.get("stage"))
had_ffmpeg = bool(ffmpeg_children())
api("/api/queue/remove", {"id": "cancel"})
q = wait("cancel", ("done", "error"), timeout=20)
end = time.time() + 10
while ffmpeg_children() and time.time() < end:
    time.sleep(0.1)
check("cancelled: gone from the queue", q is None, q)
check("its FFmpeg stopped", had_ffmpeg and not ffmpeg_children(), (had_ffmpeg, ffmpeg_children()))
folder = it.get("folder") or ""
check("and no partial file left", folder and not [f for f in os.listdir(folder) if f.startswith("vc_") or f == it.get("name")],
      folder and os.listdir(folder))

# 7. a broken output is never a finished clip
probe = os.path.join(TMP, "probe_out.mp4")
shutil.copy(SRC, probe)
with open(probe, "r+b") as f:
    f.truncate(os.path.getsize(probe) // 2)
try:
    server._check_output(probe, 40.0)
    check("a file cut short is refused", False)
except server.BrokenOutput:
    check("a file cut short is refused, and deleted", not os.path.exists(probe))
mute = os.path.join(TMP, "mute_out.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SRC, "-t", "4", "-an", "-c", "copy", mute], check=True)
try:
    server._check_output(mute, 4.0, want_audio=True)
    check("a render that lost its sound is refused", False)
except server.BrokenOutput as e:
    check("a render that lost its sound is refused", "no sound" in str(e), str(e))
good = os.path.join(TMP, "good_out.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SRC, "-t", "4", "-c", "copy", good], check=True)
try:
    server._check_output(good, 4.0, want_audio=True)
    check("a good file passes", os.path.exists(good))
except server.BrokenOutput as e:
    check("a good file passes", False, str(e))

# 8. what's saved for a restart has no internal state, and comes back as it was
server.save_queue()
saved = persist.read_json(server.QUEUE_FILE)
check("the saved queue has no FFmpeg scratch state", all("_diag" not in x for x in saved["items"]))
back = {x["id"]: x for x in persist.restore_queue(saved)}
check("a finished clip comes back with its history", back.get("ok1", {}).get("history", [{}])[0].get("outcome") == "done", back.get("ok1"))
check("a failed one comes back failed, with its kind", back.get("past", {}).get("error_kind") == "source_range", back.get("past"))

server.SHUTTING_DOWN[0] = True
srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
