"""
Hears laughter, screams and cheering in a stream.

A small AudioSet sound-event model (EfficientAT MobileNetV3 "mn10_as", MIT
licence, about 20 MB as ONNX) runs on onnxruntime, which VodClipper already
ships for Whisper's voice detection. The log-mel front end is computed here
with numpy so the model file stays small and nothing else needs installing.

  events = audio_events.scan_file(video, duration)    # per-second probabilities
  events["laugh"][s], events["scream"][s], events["cheer"][s]
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import paths

SR = 32000
N_FFT = 1024
WIN = 800
HOP = 320                     # 100 mel frames per second
N_MELS = 128
FPS = SR // HOP
WINDOW_S = 3                  # each second is judged from the 3 seconds around it
BATCH = 32

MODEL_NAME = "audio_events.onnx"
MODEL_URL = os.environ.get("VODCLIPPER_MODEL_URL",
                           "https://github.com/allen1versonfr/vodclipper/releases/download/models-v1/" + MODEL_NAME)
MODEL_SHA256 = ""             # filled in once the model release exists (checked when set)

# AudioSet class indices (see metadata/class_labels_indices.csv)
GROUPS = {
    "laugh": [16, 17, 18, 19, 20, 21],        # Laughter, Baby laughter, Giggle, Snicker, Belly laugh, Chuckle
    "scream": [8, 9, 10, 11, 12, 14],         # Shout, Bellow, Whoop, Yell, Battle cry, Screaming
    "cheer": [66, 67, 69],                    # Cheering, Applause, Crowd
    "speech": [0],
    "music": [137],
}

Log = Callable[[str], None]
_noop = lambda *a, **k: None  # noqa: E731

_session = None
_lock = threading.Lock()


# ----------------------------------------------------------------- front end

def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def mel_basis(n_mels: int = N_MELS, n_fft: int = N_FFT, sr: int = SR,
              fmin: float = 0.0, fmax: float = 15000.0) -> np.ndarray:
    """Kaldi-style triangular mel filters, [n_mels, n_fft//2 + 1] (last column zero), as EfficientAT uses."""
    n_bins = n_fft // 2
    fft_bin_width = sr / n_fft
    mel_low, mel_high = _mel(fmin), _mel(fmax)
    delta = (mel_high - mel_low) / (n_mels + 1)
    b = np.arange(n_mels, dtype=np.float64)[:, None]
    left = mel_low + b * delta
    center = mel_low + (b + 1.0) * delta
    right = mel_low + (b + 2.0) * delta
    m = _mel(fft_bin_width * np.arange(n_bins, dtype=np.float64))[None, :]
    up = (m - left) / (center - left)
    down = (right - m) / (right - center)
    banks = np.maximum(0.0, np.minimum(up, down))
    return np.pad(banks, ((0, 0), (0, 1))).astype(np.float32)


_BASIS = None
_WINDOW = None


def logmel(wave: np.ndarray) -> np.ndarray:
    """32 kHz mono float32 audio -> normalised log-mel [128, frames] (100 frames per second)."""
    global _BASIS, _WINDOW
    if _BASIS is None:
        _BASIS = mel_basis()
        w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN) / (WIN - 1))     # hann, symmetric
        lpad = (N_FFT - WIN) // 2
        _WINDOW = np.pad(w, (lpad, N_FFT - WIN - lpad)).astype(np.float32)
    x = np.asarray(wave, dtype=np.float32)
    if x.size < 2:
        x = np.zeros(2, dtype=np.float32)
    x = x[1:] - 0.97 * x[:-1]                                               # pre-emphasis
    pad = N_FFT // 2
    if x.size > pad:
        x = np.pad(x, (pad, pad), mode="reflect")
    else:
        x = np.pad(x, (pad, pad))
    n_frames = 1 + (x.size - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, N_FFT), strides=(x.strides[0] * HOP, x.strides[0]), writeable=False)
    spec = np.fft.rfft(frames * _WINDOW, axis=1)
    power = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)           # [frames, 513]
    mel = _BASIS @ power.T                                                  # [128, frames]
    return ((np.log(mel + 1e-5) + 4.5) / 5.0).astype(np.float32)


# ----------------------------------------------------------------- model

def model_path() -> Optional[Path]:
    for d in (Path(paths.RES_DIR) / "models", Path(paths.DATA_DIR) / "models"):
        p = d / MODEL_NAME
        if p.is_file() and p.stat().st_size > 1_000_000:
            return p
    return None


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return model_path() is not None


def can_download() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def download(log: Log = _noop) -> Path:
    """Fetch the model into the data folder (only needed when it isn't bundled)."""
    dest = Path(paths.DATA_DIR) / "models" / MODEL_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log("Downloading the laughter detector (about 20 MB)...")
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "VodClipper"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
    if MODEL_SHA256 and h.hexdigest() != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("the laughter detector download was damaged; try again")
    os.replace(tmp, dest)
    return dest


def _load():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            p = model_path()
            if p is None:
                raise RuntimeError("the laughter detector model isn't installed")
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            so.log_severity_level = 3
            _session = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
        return _session


def classify(mel_windows: np.ndarray) -> np.ndarray:
    """[B, 128, T] log-mel windows -> [B, 527] probabilities."""
    sess = _load()
    x = mel_windows[:, None, :, :].astype(np.float32)
    name = sess.get_inputs()[0].name
    return sess.run(None, {name: x})[0]


def _groups(probs: np.ndarray) -> dict:
    return {k: probs[:, idx].max(axis=1) for k, idx in GROUPS.items()}


class Scanner:
    """Turns a stream of 32 kHz audio into per-second event probabilities.
    Feed audio in any chunk size; results come out one value per second."""

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0                         # absolute sample index of buf[0]
        self.done = 0                           # seconds already scored
        self.out = {k: [] for k in GROUPS}

    def feed(self, wave: np.ndarray, final: bool = False):
        self.buf = np.concatenate([self.buf, np.asarray(wave, dtype=np.float32)])
        total_s = (self.offset + self.buf.size) // SR
        half = WINDOW_S / 2.0
        # second s is scored from [s + 0.5 - half, s + 0.5 + half)
        last = total_s if final else int(math.floor(total_s - half + 0.5))
        if last <= self.done:
            return
        a = int(max(0, (self.done + 0.5 - half) * SR)) - self.offset
        b = int(min(self.offset + self.buf.size, (last + 0.5 + half) * SR)) - self.offset
        a = max(0, a)
        mel = logmel(self.buf[a:b])
        base_frame = (self.offset + a) / HOP
        wf = WINDOW_S * FPS
        wins = []
        for s in range(self.done, last):
            f0 = int(round((s + 0.5 - half) * FPS - base_frame))
            f0 = min(max(0, f0), max(0, mel.shape[1] - wf))
            w = mel[:, f0:f0 + wf]
            if w.shape[1] < wf:
                w = np.pad(w, ((0, 0), (0, wf - w.shape[1])), constant_values=(np.log(1e-5) + 4.5) / 5.0)
            wins.append(w)
        for i in range(0, len(wins), BATCH):
            g = _groups(classify(np.stack(wins[i:i + BATCH])))
            for k in GROUPS:
                self.out[k].extend(g[k].tolist())
        self.done = last
        # keep only what the next windows still need
        keep_from = int(max(0, (self.done + 0.5 - half - 1) * SR)) - self.offset
        if keep_from > 0:
            self.buf = self.buf[keep_from:]
            self.offset += keep_from

    def result(self, n: Optional[int] = None) -> dict:
        out = {}
        for k, v in self.out.items():
            a = np.asarray(v, dtype=np.float32)
            if n is not None:
                a = np.pad(a[:n], (0, max(0, n - len(a))))
            out[k] = a
        return out


def scan_file(video: str, duration: float, start: float = 0.0, progress=_noop, check=_noop) -> dict:
    """Per-second event probabilities for a whole file (streamed, light on memory)."""
    import engine
    n = int(math.ceil(duration)) + 1
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", video, "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=engine.CREATE_NO_WINDOW)
    sc = Scanner()
    got = 0
    try:
        while True:
            chunk = p.stdout.read(SR * 4 * 30)          # 30 s at a time
            if not chunk:
                break
            check()
            arr = np.frombuffer(chunk[: len(chunk) // 4 * 4], dtype=np.float32)
            got += arr.size
            sc.feed(arr)
            if duration:
                progress(min(0.999, got / SR / duration))
        sc.feed(np.zeros(0, dtype=np.float32), final=True)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    progress(1.0)
    return sc.result(n)
