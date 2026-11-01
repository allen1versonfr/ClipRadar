"""
Whisper on AMD and Intel graphics cards (4.37): whisper.cpp built with Vulkan, as a second engine beside faster-whisper.

faster-whisper only reaches the GPU through NVIDIA's CUDA. whisper.cpp doesn't publish a Vulkan build for Windows, so
.github/workflows/whisper-vulkan.yml builds an unmodified release of it and publishes one whisper-cli.exe on the
"whisper-vulkan-v1" pre-release. The program, the speech detector and the models are downloaded once into the data
folder, each checked against the SHA-256 pinned here, and can be removed. gpu.py decides when this engine runs.

Measured on an RX 7600 XT, 300 s of a real stream, large-v3 (issue 46): about 2x real time against 1.5x on the CPU, and
48 s of CPU time against 800 s, which is what a live session needs. Its words are a little less accurate and their
timing looser (about 220 ms against faster-whisper's 80 ms), so final captions stay on faster-whisper.

--mc 0 matches condition_on_previous_text=False, but it also drops the prompt (whisper.cpp caps the prompt at the same
number). A context big enough to keep the prompt scored worse on that test, so this engine runs without one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Callable

import numpy as np

import paths
import procjob

RELEASE = os.environ.get("CLIPRADAR_WHISPER_VULKAN_URL",
                         "https://github.com/allen1versonfr/ClipRadar/releases/download/whisper-vulkan-v1")
BIN_NAME = "whisper-cli.exe"
BIN_SHA256 = "297e7d443319285eb1f7259467d3db8ba80084c19d3846d7f9eac52ccdd4b2a3"
BIN_MB = 48
# pinned to a revision of each repo, so a changed file can't be swapped in under the same name
HF = os.environ.get("CLIPRADAR_WHISPER_MODELS_URL", "https://huggingface.co")
MODELS_AT = f"{HF}/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1"
VAD_AT = f"{HF}/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b"
VAD = ("ggml-silero-v6.2.0.bin", "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987")
MODELS = {   # Clip Radar's size -> (file, size in MB, SHA-256)
    "tiny": ("ggml-tiny.bin", 78, "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"),
    "base": ("ggml-base.bin", 148, "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "small": ("ggml-small.bin", 488, "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"),
    "medium": ("ggml-medium.bin", 1534, "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208"),
    "large-v3": ("ggml-large-v3.bin", 3095, "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2"),
}
FIRST_MODEL = "small"      # installed with the program; a bigger one comes when it's first asked for
DOWNLOAD_MB = BIN_MB + MODELS[FIRST_MODEL][1]
STRIKES = 2                # failures in a row (or runs slower than the sound) before the CPU takes over
RETRY_AFTER = 600          # seconds before the card gets another go: what stopped it may have been a game, now closed
_lock = threading.Lock()   # one run on the card at a time: a second model wouldn't fit on a small one
_fetching: set = set()
_said: set = set()
_state = {"strikes": 0, "off_at": 0.0}


class Failed(RuntimeError):
    """This run didn't work; Whisper on the CPU takes over."""


def folder() -> Path:
    return Path(paths.DATA_DIR) / "whisper-vulkan"


def exe() -> Path:
    return folder() / BIN_NAME


def model_file(size: str) -> Path | None:
    m = MODELS.get(size)
    return folder() / m[0] if m else None


def installed() -> bool:
    return sys.platform == "win32" and exe().is_file() and (folder() / VAD[0]).is_file()


def has_model(size: str) -> bool:
    p = model_file(size)
    return bool(p) and p.is_file()


def turned_off() -> bool:
    if _state["strikes"] < STRIKES:
        return False
    if time.time() - _state["off_at"] < RETRY_AFTER:
        return True
    _state["strikes"] = STRIKES - 1      # one more failure turns it off again
    return False


def forget() -> None:
    """Try again (after a new download, or a new driver)."""
    _state["strikes"] = 0


def _strike(why: str) -> None:
    _state["strikes"] += 1
    if _state["strikes"] == STRIKES:
        _state["off_at"] = time.time()
        print(f"  Whisper stopped on the graphics card ({why[:140]}). The CPU takes over; the card gets another try "
              f"in {RETRY_AFTER // 60} minutes.", flush=True)


def _download(url: str, dest: Path, sha256: str, progress: Callable[[float], None] = lambda f: None,
              check: Callable[[], None] = lambda: None) -> None:
    """Fetch a file next to where it goes; it takes its name only once its checksum matches."""
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "ClipRadar"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total, got = int(r.headers.get("Content-Length") or 0), 0
            while True:
                check()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
                got += len(chunk)
                if total:
                    progress(got / total)
        if h.hexdigest() != sha256:
            raise RuntimeError(f"{dest.name} didn't download correctly (its checksum doesn't match). Try again.")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def _room(mb: float) -> None:
    free = shutil.disk_usage(str(folder())).free
    if free < mb * 1e6 * 1.2:
        raise RuntimeError(f"Not enough free space: this needs about {mb / 1000:.1f} GB ({free / 1e9:.1f} GB free).")


def install(progress: Callable[[float], None] = lambda f: None, log: Callable[[str], None] = lambda m: None,
            check: Callable[[], None] = lambda: None) -> None:
    """The program, the speech detector and the first model, then a dry run on the card."""
    folder().mkdir(parents=True, exist_ok=True)
    _room(DOWNLOAD_MB)
    name, mb, sha = MODELS[FIRST_MODEL]
    log(f"Downloading Whisper for the graphics card ({BIN_MB} MB)...")
    if not exe().is_file():
        _download(f"{RELEASE}/{BIN_NAME}", exe(), BIN_SHA256, lambda f: progress(0.08 * f), check)
    if not (folder() / VAD[0]).is_file():
        _download(f"{VAD_AT}/{VAD[0]}", folder() / VAD[0], VAD[1], check=check)
    log(f"Downloading the Whisper model ({mb} MB)...")
    if not has_model(FIRST_MODEL):
        _download(f"{MODELS_AT}/{name}", folder() / name, sha, lambda f: progress(0.08 + 0.87 * f), check)
    log("Trying it on the graphics card...")
    forget()
    transcribe(np.zeros(16000, dtype=np.float32), FIRST_MODEL, "en", log)
    progress(1.0)


def remove() -> None:
    shutil.rmtree(folder(), ignore_errors=True)
    forget()


def fetch_later(size: str, log: Callable[[str], None]) -> None:
    """A model not here yet: get it in the background. Whatever asked for it carries on on the CPU, because a live
    title shouldn't wait for a 3 GB download."""
    if size not in MODELS or has_model(size) or size in _fetching:
        return
    _fetching.add(size)
    name, mb, sha = MODELS[size]

    def go():
        try:
            _room(mb)
            log(f"Getting Whisper '{size}' for the graphics card ({mb} MB) in the background; the CPU works until it's here.")
            _download(f"{MODELS_AT}/{name}", folder() / name, sha)
            log(f"Whisper '{size}' is ready on the graphics card.")
        except Exception as e:
            log(f"Couldn't get Whisper '{size}' for the graphics card: {str(e)[:160]}")
        finally:
            _fetching.discard(size)
    threading.Thread(target=go, daemon=True, name="whisper-model").start()


def _write_wav(path: str, audio: np.ndarray) -> None:
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


def command(size: str, language: str) -> list:
    """The same choices as engine._transcribe: beam 5, no carried-over text, the same silence and speech lengths."""
    return [str(exe()), "-m", str(model_file(size)), "-f", "a.wav", "-of", "a", "-oj", "-l", language or "auto", "-bs", "5",
            "-mc", "0", "-ml", "1", "-sow", "-pp", "-t", str(max(1, min(4, (os.cpu_count() or 2) - 1))),
            "--vad", "-vm", str(folder() / VAD[0]), "-vsd", "400", "-vspd", "0", "-vp", "400"]


def _words(data: dict, stop: re.Pattern) -> list:
    """whisper-cli's one-word segments as engine.transcribe's words, without made-up phrases ("thanks for watching")."""
    words = [{"start": s["offsets"]["from"] / 1000, "end": s["offsets"]["to"] / 1000, "word": s["text"].strip()}
             for s in data.get("transcription", []) if s.get("text", "").strip()]
    text, at = "", []
    for i, w in enumerate(words):                 # the phrases span words, so look at the joined text
        at += [i] * (len(w["word"]) + 1)
        text += w["word"] + " "
    drop = {at[j] for m in stop.finditer(text) for j in range(m.start(), m.end())}
    return [w for i, w in enumerate(words) if i not in drop]


def transcribe(audio: np.ndarray, size: str, language: str, log: Callable[[str], None], stop: re.Pattern = re.compile(r"$^"),
               progress: Callable[[float], None] = lambda f: None, check: Callable[[], None] = lambda: None) -> list:
    """16 kHz mono float32 samples -> [{"start", "end", "word"}]. Raises Failed for the CPU to take over."""
    with _lock:
        work = tempfile.mkdtemp(prefix="vk_")
        started = time.time()
        p = None
        try:
            _write_wav(os.path.join(work, "a.wav"), audio)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            p = subprocess.Popen(command(size, language), cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", creationflags=flags)
            procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
            on_card = False
            for line in p.stdout:
                on_card = on_card or "using Vulkan" in line
                m = re.search(r"progress =\s*(\d+)%", line)
                if m:
                    progress(int(m.group(1)) / 100)
                check()
            p.wait()
            if p.returncode != 0 or not os.path.isfile(os.path.join(work, "a.json")):
                raise Failed(f"whisper-cli stopped with code {p.returncode}")
            if not on_card:      # its speech detector says "no GPU found" on a working run: it's on the CPU on purpose
                raise Failed("Vulkan found no graphics card to use")
            with open(os.path.join(work, "a.json"), encoding="utf-8") as f:
                words = _words(json.load(f), stop)
        except Failed as e:
            _strike(str(e))
            raise
        except (OSError, ValueError, KeyError) as e:
            _strike(str(e))
            raise Failed(str(e)[:160]) from e
        finally:
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()
            shutil.rmtree(work, ignore_errors=True)
        took = time.time() - started
        length = audio.size / 16000
        if length >= 20 and took > length:
            # a card this slow (an old integrated one) is better left alone, unless the CPU has no room either: with a
            # game up (measured: Cyberpunk, large-v3) the card took 59 s for 45 s of sound, and the CPU 150 to 1000 s
            import gpu
            if gpu.cpu_load() < gpu.CPU_ROOM:
                _strike("slower than the sound")
        elif length >= 20:
            _state["strikes"] = 0
        return words
