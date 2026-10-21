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
        return iter([]), None


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

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
