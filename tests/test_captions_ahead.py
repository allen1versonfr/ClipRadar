"""
Captions heard ahead (4.49): while one clip renders, the clips queued behind it have their captions heard, in order, one at
a time, so their renders don't stop to listen (efficiency audit 2026-11-08, F1 and F4). A clip with nothing ahead of it
isn't touched, a render waits for its own clip's hearing instead of hearing it twice, and a clip removed from the queue
stops being heard. Whisper is stood in for, so this runs anywhere.

  python tests/test_captions_ahead.py
"""

from __future__ import annotations

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

import engine  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


PR = {"info": {"has_audio": True}, "tx_lock": threading.Lock()}
heard: list = []
hold = {"s": 0.0}
started = threading.Event()


def fake_ensure_words(pr, a, b, model, lang, check=lambda: None, stats=None, task="captions", timer=None):
    heard.append((a, b))
    started.set()
    end = time.time() + hold["s"]
    while time.time() < end:
        check()
        time.sleep(0.02)
    return []


server.ensure_words = fake_ensure_words
server._render_project = lambda it: PR
engine.whisper_available = lambda: True


def item(i, status="queued", **kw):
    return dict({"id": f"c{i}", "status": status, "start": 10.0 * i, "end": 10.0 * i + 8, "title": f"Clip {i}",
                 "opts": {"captions": True}, "words": None, "cancel": False}, **kw)


def reset(items):
    with server.queue_lock:
        server.queue[:] = items
    server._prehear.clear()
    heard.clear()
    started.clear()
    hold["s"] = 0.0


# 1. nothing rendering: the next clip renders at once and hears its own captions, as before
reset([item(1), item(2)])
check("with nothing rendering, nothing is heard ahead", server._prehear_one() is False and not heard, heard)

# 2. one rendering, two waiting: both heard, in queue order, then nothing more to do
reset([item(1, "rendering"), item(2), item(3)])
r = [server._prehear_one(), server._prehear_one(), server._prehear_one()]
check("the clips waiting behind a render are heard, in order", r == [True, True, False] and heard == [(20.0, 28.0), (30.0, 38.0)],
      (r, heard))
check("and marked done, so their renders don't wait", all(server._prehear[k].is_set() for k in ("c2", "c3")))
check("the clip already rendering isn't heard ahead", "c1" not in server._prehear)

# 3. clips that won't hear captions aren't heard
reset([item(1, "rendering"), item(2, words=[{"start": 20.0, "end": 20.5, "word": "fixed"}]),
       item(3, opts={"captions": False})])
server.safety.load_config = lambda: {"enabled": True, "censor": "off"}
server._prehear_one()
server._prehear_one()
check("a clip with captions fixed by hand, or captions off and no censoring, isn't heard", heard == [], heard)
check("and doesn't hold its render", all(server._prehear[k].is_set() for k in ("c2", "c3")))

# 4. a render that reaches a clip still being heard waits for it, instead of hearing it a second time
reset([item(1, "rendering"), item(2)])
hold["s"] = 0.6
t = threading.Thread(target=server._prehear_one)
t.start()
started.wait(5)
t0 = time.time()
timer = server.timing.Timer("render", "test")
server._wait_prehear(server.queue[1], lambda: None, timer)
waited = time.time() - t0
t.join()
check("the render waits for the clip's hearing to finish", 0.3 < waited < 3 and len(heard) == 1, (waited, heard))
check("and its timing says so", timer.steps.get("waiting for captions heard ahead", 0) > 0.3, timer.steps)

# 5. a cancelled render stops waiting
reset([item(1, "rendering"), item(2)])
hold["s"] = 2.0
t = threading.Thread(target=server._prehear_one)
t.start()
started.wait(5)


def cancelled():
    raise engine.Cancelled()


try:
    server._wait_prehear(server.queue[1], cancelled, None)
    check("a render cancelled while waiting stops waiting", False)
except engine.Cancelled:
    check("a render cancelled while waiting stops waiting", True)

# 6. a clip removed from the queue while it's being heard stops being heard
with server.queue_lock:
    server.queue.pop(1)
t.join(5)
check("a clip removed from the queue stops being heard", not t.is_alive())
check("and the next pass forgets it", server._prehear_one() is False and "c2" not in server._prehear, list(server._prehear))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
