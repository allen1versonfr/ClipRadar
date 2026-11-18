"""
Whisper: hearing what's said, for captions and titles (moved from engine.py, hardening phase 4c; no changes).
faster-whisper on the CPU or the graphics card, one transcription at a time, models let go after WHISPER_IDLE
seconds unused; a stretch the card heard too few words in is heard again on the CPU.

  whisper_available()                         faster-whisper is installed
  transcribe(audio, model_size, language, …)  the words heard, with their times
  extract_audio(video, start, length)         16 kHz mono samples of a stretch, for transcribe
  release_whisper(idle)                       let loaded models go (memory)

engine.py re-exports every name here, so engine.transcribe and the rest still work. Code that calls these, and a
test that replaces one (whisper.transcribe = fake), uses this module: a fake put on engine wouldn't reach it. The
helpers (ffmpeg, probe…) stay in engine.py and are imported from it (imported on its own, this module has engine
import it first).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from typing import Optional

import numpy as np

if "engine" not in sys.modules:
    # imported before engine: engine imports this module itself once the helpers below are defined, so engine is
    # imported first and this copy steps aside (the import then gives back the copy engine loaded in full)
    del sys.modules[__name__]
    import engine  # noqa: E402,F401
from engine import (  # noqa: E402  engine imports this module after these are defined
    CREATE_NO_WINDOW, Log, Progress, _noop, ffmpeg, probe,
)


_whisper_cache: dict = {}
_whisper_lock = threading.RLock()   # one transcription at a time (titles + renders share the model)
_whisper_used = [0.0]               # when the model last finished a job
_whisper_busy = [0]                 # transcriptions running or waiting for the model
_busy_lock = threading.Lock()
WHISPER_IDLE = 600                  # seconds unused before models are let go (each holds 0.3-3 GB)
WHISPER_KEEP = 2                    # models kept loaded at most


def whisper_cpu_threads() -> int:
    """Threads for faster-whisper on the CPU: one per physical core. Its default is 4, so a 6-core Ryzen heard captions
    on two thirds of its cores (efficiency audit 2026-11-08, F1: the CPU caption pass was 79% of render time). A PC with
    8 or more logical processors is taken to have two per core (SMT); hyper-threads don't speed up its int8 matrix work."""
    n = os.cpu_count() or 4
    return max(4, n // 2 if n >= 8 else n)


def release_whisper(idle: float = 0.0) -> bool:
    """Let go of the loaded Whisper model if it hasn't been used for `idle` seconds.
    Returns True when one was released. The next transcription loads it again (a few seconds)."""
    with _whisper_lock:
        if not _whisper_cache or _whisper_busy[0] or time.time() - _whisper_used[0] < idle:
            return False
        _whisper_cache.clear()
    import gc
    gc.collect()
    return True


_release_timer = [None]


def _release_later():
    """One timer at a time: it lets the model go once it's been idle long enough, or checks again."""
    if _release_timer[0] is not None:
        return

    def tick():
        _release_timer[0] = None
        if not release_whisper(WHISPER_IDLE) and _whisper_cache:
            _release_later()
    t = threading.Timer(WHISPER_IDLE / 4, tick)
    t.daemon = True
    _release_timer[0] = t
    t.start()


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _load_whisper(model_size: str, log: Log):
    if model_size in _whisper_cache:
        _whisper_cache[model_size] = _whisper_cache.pop(model_size)     # most recently used last
        return _whisper_cache[model_size]
    while len(_whisper_cache) >= WHISPER_KEEP:
        # Deep Scan and title listening may use two sizes side by side; a third (switching
        # small -> medium in Settings) pushes out the one used longest ago
        _whisper_cache.pop(next(iter(_whisper_cache)))
        import gc
        gc.collect()
    import gpu
    gpu.setup_dll_path()           # GPU support downloaded in Settings › Performance (4.36)
    from faster_whisper import WhisperModel
    silence = np.zeros(16000, dtype=np.float32)
    errors = []
    tries = [("cpu", "int8")]
    if gpu.nvidia_gpu() and time.time() < _card_rest[0]:
        pass                       # the card ran out of memory mid-clip a moment ago: the CPU for now
    elif gpu.nvidia_gpu():
        if gpu.cuda_libs():
            # float16 first; int8_float16 takes about half the graphics memory, for cards float16 doesn't fit on
            # (4.36: before, a card that ran out of memory went straight to the CPU)
            tries[:0] = [("cuda", "float16"), ("cuda", "int8_float16")]
        elif not _whisper_said.get("libs"):
            _whisper_said["libs"] = True
            log(f"Whisper is on the CPU: your {gpu.nvidia_gpu()} needs NVIDIA's CUDA support files to run it. "
                "Settings › Performance › GPU support installs them (about 1.3 GB, once).")
    for device, ctype in tries:
        try:
            log(f"Loading Whisper '{model_size}' on {'GPU' if device == 'cuda' else 'CPU'} "
                f"(the first time downloads the model)...")
            model = WhisperModel(model_size, device=device, compute_type=ctype,
                                 cpu_threads=whisper_cpu_threads() if device == "cpu" else 0)
            # tiny dry run so missing CUDA libraries fail here, not mid-render
            list(model.transcribe(silence, beam_size=1, language="en", without_timestamps=True)[0])
            _whisper_cache[model_size] = model
            _whisper_device[model_size] = device
            return model
        except Exception as e:
            msg = str(e).splitlines()[0][:140] if str(e) else type(e).__name__
            errors.append(f"{device} {ctype}: {msg}")
            if device == "cuda" and ctype == "int8_float16":
                log(f"  The GPU couldn't run Whisper ({msg}); using the CPU.")
    raise RuntimeError("Couldn't load Whisper. " + " | ".join(errors))


_whisper_device: dict = {}         # model size -> "cuda" or "cpu", for the ones loaded
_whisper_said: dict = {}
# The card can run out of memory part-way through a clip (a game, the GPU encoder, titles and Deep scan share it), not
# just while loading. That went to the render as "Your PC ran out of memory" with RAM to spare; now that clip is heard
# again on the CPU, and Whisper stays on the CPU this long before the card gets another try.
CARD_REST = 600
_card_rest = [0.0]
_CARD_ERROR = re.compile(r"cuda|cublas|cudnn|out of memory|device memory", re.I)


def whisper_device() -> str:
    """Where Whisper last ran: "cuda", "cpu", or "" before it has been loaded."""
    for size in reversed(list(_whisper_cache)):
        if size in _whisper_device:
            return _whisper_device[size]
    return next(reversed(list(_whisper_device.values())), "") if _whisper_device else ""


FEW_WORDS = 1.2                    # words a second: a caption's stretch with fewer is heard on the card as well
CARD_GAIN = 1.5                    # and the card's words are used when it heard this many times as many
REHEAR_FROM = 20                   # seconds: a shorter stretch can simply be quiet
_HALLUCINATION = re.compile(r"thank(s| you) (so much )?for watching|please subscribe|like and subscribe|"
                            r"subtitles by|amara\.org|♪|\[music\]|\(music\)", re.I)


def transcribe(audio, model_size: str, language: str, log: Log, prompt: str = "",
               progress: Progress = _noop, pick: Optional[dict] = None, check=_noop) -> list:
    """audio: path or 16 kHz mono float32 numpy array. prompt: names and words to expect (the
    streamer, emotes, your own vocabulary), so Whisper spells them right. progress gets 0-1 as
    the transcript reaches further into the audio. pick: gpu.choose()'s answer for this task;
    without one this is a caption, the task that stays on faster-whisper unless you chose otherwise."""
    if isinstance(audio, (str, os.PathLike)):
        # decode with the bundled FFmpeg: the packaged app has no PyAV (stubs/av)
        audio = extract_audio(str(audio), 0.0, probe(str(audio))["duration"] or 0.1)
        if audio is None:
            raise RuntimeError("Couldn't read the sound in this file.")
    import gpu
    import whispercpp
    pick = pick or gpu.choose("captions", model_size, log)
    if pick["engine"] == "vulkan":
        try:
            return whispercpp.transcribe(audio, model_size, language, log, _HALLUCINATION, progress, check)
        except whispercpp.Failed as e:
            log(f"Whisper on the graphics card stopped ({str(e)[:140]}); this one runs on the CPU.")
    with _busy_lock:
        _whisper_busy[0] += 1
    try:
        words = _transcribe(audio, model_size, language, log, prompt, progress)
    finally:
        with _busy_lock:
            _whisper_used[0] = time.time()
            _whisper_busy[0] -= 1
        _release_later()
    # faster-whisper's speech detector can take talking on a loud street for noise, and Whisper never hears it (measured:
    # 21 words of a 73 s clip against 87 on the card, 23 of a 24 s clip against 60). How much it kept doesn't show this
    # (77% on the second), and a lower threshold lost a different clip and took 200 s. The words themselves do: talking
    # ran at 2 to 3 a second. A clip that really is quiet gets about the same from the card, and stays on the CPU's.
    seconds = len(audio) / 16000
    if (pick.get("task") == "captions" and seconds >= REHEAR_FROM and len(words) / seconds < FEW_WORDS
            and gpu.can_rehear(model_size)):
        try:
            again = whispercpp.transcribe(audio, model_size, language, log, _HALLUCINATION, progress, check)
            if len(again) >= len(words) * CARD_GAIN:
                print(f"  Whisper heard only {len(words)} words in {seconds:.0f} s on the CPU, so these captions use the "
                      f"{len(again)} heard on the graphics card.", flush=True)
                return again
        except whispercpp.Failed as e:
            log(f"Whisper on the graphics card stopped ({str(e)[:140]}); these captions keep what the CPU heard.")
    return words


def _transcribe(audio, model_size: str, language: str, log: Log, prompt: str, progress: Progress = _noop) -> list:
    with _whisper_lock:
        model = _load_whisper(model_size, log)
        try:
            return _hear(model, audio, language, prompt, progress)
        except Exception as e:
            if _whisper_device.get(model_size) != "cuda" or not _CARD_ERROR.search(str(e)):
                raise
            why = str(e).splitlines()[0][:140] if str(e) else type(e).__name__
            log(f"The graphics card ran out of room for Whisper ({why}); hearing this on the CPU.")
            print(f"  Whisper on the GPU stopped ({why}); this and the next {CARD_REST // 60} minutes on the CPU.", flush=True)
            _card_rest[0] = time.time() + CARD_REST
            _whisper_cache.pop(model_size, None)
            import gc
            gc.collect()
            return _hear(_load_whisper(model_size, log), audio, language, prompt, progress)


def _hear(model, audio, language: str, prompt: str, progress: Progress) -> list:
    kw = dict(language=language or None, word_timestamps=True, vad_filter=True, beam_size=5,
              vad_parameters={"min_silence_duration_ms": 400}, condition_on_previous_text=False)
    if prompt:
        kw["initial_prompt"] = prompt[:400]
    try:
        segs, _ = model.transcribe(audio, **kw)
    except TypeError:                      # an older faster-whisper without some of these options
        kw.pop("condition_on_previous_text", None)
        segs, _ = model.transcribe(audio, **kw)
    words = []
    total = max(0.1, len(audio) / 16000)
    for seg in segs:
        progress(min(1.0, float(getattr(seg, "end", 0) or 0) / total))
        # Whisper's own check for text made up during silence or music
        if getattr(seg, "no_speech_prob", 0) > 0.6 and getattr(seg, "avg_logprob", 0) < -1.0:
            continue
        if _HALLUCINATION.search(getattr(seg, "text", "") or ""):
            continue
        for w in seg.words or []:
            txt = w.word.strip()
            if txt:
                words.append({"start": float(w.start), "end": float(w.end), "word": txt})
    return words


def extract_audio(video: str, start: float, length: float) -> Optional[np.ndarray]:
    """16 kHz mono float32 samples of a stretch of the video's audio (None if unreadable)."""
    try:
        # a damaged file or a stuck network drive mustn't hold Deep Scan or a render forever
        p = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}",
                            "-t", f"{max(0.1, length):.3f}", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1",
                            "-ar", "16000", "-f", "f32le", "-"], capture_output=True,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                           timeout=max(60.0, length * 2))
    except (subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0:
        return None
    return np.frombuffer(p.stdout, dtype=np.float32).copy()
