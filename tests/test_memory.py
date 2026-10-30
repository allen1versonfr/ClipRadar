"""
Unit tests for memory held between jobs: at most two Whisper models are kept, it's let go after being
idle, never while a transcription is running, and one timer does the watching however many
transcriptions run.

  python tests/test_memory.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

loaded = []


class FakeModel:
    """Stands in for faster_whisper.WhisperModel (no download, no GPU)."""
    def __init__(self, size, device="cpu", compute_type="int8"):
        if device == "cuda":
            raise RuntimeError("no GPU here")
        self.size = size
        loaded.append(size)

    def transcribe(self, audio, **kw):
        time.sleep(float(getattr(audio, "hold", 0) or 0))
        n = int(getattr(audio, "segments", 0) or 0)
        total = len(audio) / 16000
        word = types.SimpleNamespace(start=0.0, end=0.1, word="hi")
        return iter([types.SimpleNamespace(end=total * (i + 1) / n, text="hi", words=[word]) for i in range(n)]), None


sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeModel)

import numpy as np  # noqa: E402
import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


quiet = np.zeros(1600, dtype=np.float32)
log = lambda m: None  # noqa: E731

engine.transcribe(quiet, "tiny", "en", log)
check("a model is loaded on first use", list(engine._whisper_cache) == ["tiny"])
engine.transcribe(quiet, "tiny", "en", log)
check("and reused", loaded == ["tiny"], loaded)
engine.transcribe(quiet, "base", "en", log)
check("two sizes side by side stay loaded", list(engine._whisper_cache) == ["tiny", "base"], list(engine._whisper_cache))
for _ in range(3):
    engine.transcribe(quiet, "tiny", "en", log)
    engine.transcribe(quiet, "base", "en", log)
check("and alternating between them doesn't reload", loaded == ["tiny", "base"], loaded)
engine.transcribe(quiet, "small", "en", log)
check("a third size pushes out the one used longest ago", list(engine._whisper_cache) == ["base", "small"], list(engine._whisper_cache))

check("not let go while recently used", engine.release_whisper(engine.WHISPER_IDLE) is False)
check("let go once idle", engine.release_whisper(0) is True and not engine._whisper_cache)
check("nothing to let go twice", engine.release_whisper(0) is False)
engine.transcribe(quiet, "base", "en", log)
check("loads again when needed", list(engine._whisper_cache) == ["base"])


class Segmented(np.ndarray):
    segments = 4


seen = []
engine.transcribe(np.zeros(16000 * 8, dtype=np.float32).view(Segmented), "base", "en", log, progress=seen.append)
check("transcription reports how far it has got (a render's bar moves while captions are made)",
      [round(f, 2) for f in seen] == [0.25, 0.5, 0.75, 1.0], seen)


class Held(np.ndarray):
    hold = 0.6


slow = np.zeros(1600, dtype=np.float32).view(Held)
t = threading.Thread(target=engine.transcribe, args=(slow, "base", "en", log))
t.start()
time.sleep(0.2)
check("a running transcription counts as busy", engine._whisper_busy[0] == 1, engine._whisper_busy)
t0 = time.time()
engine.release_whisper(0)
check("never let go mid-transcription (waits for it to finish)", time.time() - t0 > 0.25 and not t.is_alive())
t.join()

timers_before = sum(1 for th in threading.enumerate() if isinstance(th, threading.Timer))
for _ in range(50):
    engine.transcribe(quiet, "tiny", "en", log)
timers = sum(1 for th in threading.enumerate() if isinstance(th, threading.Timer))
check("one watching timer however many transcriptions", timers <= max(1, timers_before), f"{timers_before} -> {timers}")

engine.WHISPER_IDLE = 0.4
engine._release_timer[0] and engine._release_timer[0].cancel()
engine._release_timer[0] = None
engine.transcribe(quiet, "tiny", "en", log)
deadline = time.time() + 5
while engine._whisper_cache and time.time() < deadline:
    time.sleep(0.05)
check("the timer lets an idle model go by itself", not engine._whisper_cache)

# a file path is decoded by the bundled FFmpeg, never by PyAV (the packaged app has none: src/stubs/av)
import shutil  # noqa: E402
import subprocess  # noqa: E402
if shutil.which("ffmpeg") and shutil.which("ffprobe"):
    got = []
    FakeModel.transcribe = lambda self, audio, **kw: (got.append(type(audio).__name__), (iter([]), None))[1]
    wav = os.path.join(tempfile.mkdtemp(), "speech.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "mpeg4", "-c:a", "pcm_s16le", wav], check=True)
    engine.transcribe(wav, "tiny", "en", log)
    check("a file is decoded to samples before Whisper sees it", got and set(got) == {"ndarray"}, got)
else:
    print("[skip] no ffmpeg here: file decoding not checked")

# ---------------------------------------------------------------- 4.36: a project's chat, jobs, live sessions
import bisect  # noqa: E402
import gc  # noqa: E402
import tracemalloc  # noqa: E402

sys.path.insert(0, HERE)
os.environ.setdefault("CLIPRADAR_WORK", tempfile.mkdtemp(prefix="vc_test_"))
os.environ["CLIPRADAR_NO_BROWSER"] = "1"
import server  # noqa: E402

msgs = [(i * 0.5, f"message {i} KEKW", f"user{i % 300}", "") for i in range(20000)]
folder = tempfile.mkdtemp()
open(os.path.join(folder, server.ChatStore.FILE), "w").write("[[1, \"stale\", \"x\", \"\"]]")
st = server.ChatStore(msgs, folder)
check("a stale chat file from the last run is removed", not os.path.exists(os.path.join(folder, server.ChatStore.FILE)))
same = lambda: (len(st) == 20000 and bool(st) and st[5] == msgs[5] and st[100:103] == msgs[100:103]  # noqa: E731
                and list(st)[-1] == msgs[-1] and bisect.bisect_left(st, 2500.0, key=lambda m: m[0]) == 5000)
check("a project's chat reads like a list", same())
check("it goes to disk", st.unload() and not st.loaded and os.path.exists(os.path.join(folder, server.ChatStore.FILE)))
check("its length is known without reading it back", len(st) == 20000 and bool(st) and not st.loaded)
check("and anything that reads it gets it back, the same", same() and st.loaded)
mtime = os.path.getmtime(os.path.join(folder, server.ChatStore.FILE))
time.sleep(0.05)
st.unload()
check("written once (chat doesn't change)", os.path.getmtime(os.path.join(folder, server.ChatStore.FILE)) == mtime)
st._get()
os.remove(os.path.join(folder, server.ChatStore.FILE))
st.unload()
check("its file deleted while it was in memory: written again", os.path.exists(os.path.join(folder, server.ChatStore.FILE)))
os.remove(os.path.join(folder, server.ChatStore.FILE))
try:
    st[0]
    check("its file deleted while set aside: says so", False)
except RuntimeError as e:
    check("its file deleted while set aside: says so", "Open the video again" in str(e), str(e))

tracemalloc.start()
gc.collect()
before = tracemalloc.get_traced_memory()[0]
big = server.ChatStore([(i * 0.1, f"text number {i}", f"u{i}", "") for i in range(200000)], tempfile.mkdtemp())
gc.collect()
held = tracemalloc.get_traced_memory()[0] - before
big.unload()
gc.collect()
after = tracemalloc.get_traced_memory()[0] - before
check("setting chat aside frees its memory", after < held * 0.05, (round(held / 1e6, 1), round(after / 1e6, 2)))

# trim_memory: idle projects' chat, finished jobs, render-only projects
now = time.time()
server.projects.clear()
for k in range(5):
    store = server.ChatStore(msgs[:100], tempfile.mkdtemp())
    store.used = now - 3600 - k            # p0 used last
    server.projects[f"p{k}"] = {"chat": store, "workdir": "", "video": ""}
server.jobs["busy"] = {"id": "busy", "status": "running", "started": now - 99999, "pid": "p4"}
server.jobs["old"] = {"id": "old", "status": "done", "started": now - 2 * 3600}
server.jobs["new"] = {"id": "new", "status": "done", "started": now - 60}
server._render_projects.update(r1={"video": "a"}, r2={"video": "b"})
server.queue[:] = [{"pid": "r2", "status": "queued"}, {"pid": "r1", "status": "done"}]
got = server.trim_memory(now)
loaded = {k for k, pr in server.projects.items() if pr["chat"].loaded}
check("idle projects' chat goes to disk, but not the two used last or one being worked on", loaded == {"p0", "p1", "p4"}, loaded)
check("a project used a moment ago keeps its chat", (server.projects["p2"]["chat"]._get(), server.trim_memory(now))[0]
      and server.projects["p2"]["chat"].loaded)
check("finished jobs go after an hour, running ones stay", set(server.jobs) == {"busy", "new"}, set(server.jobs))
check("a project rebuilt only to render a clip goes once nothing queued needs it", set(server._render_projects) == {"r2"},
      set(server._render_projects))
server.projects.clear()
server.jobs.clear()
server.queue.clear()

# an ended live session lets go of its chat, loudness and chatters, and its page still shows what it did
import numpy as np  # noqa: E402
import live_replay as lr  # noqa: E402
rng = np.random.default_rng(1)
N = 1800
loud = (-30 + rng.normal(0, 1.5, N)).astype(np.float32)
chat = sorted((t + rng.random(), ["hello chat", "KEKW", "nice"][rng.integers(3)], f"u{rng.integers(20000)}")
              for t in range(N) for _ in range(rng.poisson(8)))
for t in (300, 900, 1500):
    loud[t:t + 3] = -6
sess = lr.replay(loud, chat=chat)
gc.collect()
page = sess.public()
before = tracemalloc.get_traced_memory()[0]
sess.release_memory()
gc.collect()
freed = before - tracemalloc.get_traced_memory()[0]
check("an ended live session lets go of its chat, loudness and chatters (they stayed until Clip Radar closed)",
      freed > 2e6 and not sess.chat and not sess.loud and not sess.fine, round(freed / 1e6, 1))
after = sess.public()
check("and its page still shows the stream: length, curve, chat totals",
      all(after[k] == page[k] for k in ("recorded", "curve", "chat_total", "chatters", "chat_ignored", "laughter", "moments")),
      {k: (page[k], after[k]) for k in ("recorded", "chat_total", "chatters") if page[k] != after[k]})
check("its moments keep their scores", sess.grades([5.0, 3.0]) == sess.grades([5.0, 3.0]) and sess._graded_an is not None)
tracemalloc.stop()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
