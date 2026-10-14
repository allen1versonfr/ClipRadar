"""
Hears laughter, screams and cheering in a stream.

A small AudioSet sound-event model (EfficientAT MobileNetV3 "mn10_as", MIT
licence, about 20 MB as ONNX) runs on onnxruntime, which Clip Radar already
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

MODEL_NAME = "audio_events.onnx"
MODEL_URL = os.environ.get("CLIPRADAR_MODEL_URL",
                           "https://github.com/allen1versonfr/vodclipper/releases/download/models-v1/" + MODEL_NAME)
MODEL_SHA256 = "197920fa51524d0a8078a6a999d97ccbeff76fe61a86034a07d31d236b24803e"

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


def download(log: Log = _noop, folder: Optional[str] = None, progress=_noop) -> Path:
    """Fetch the model into the data folder (only needed when it isn't bundled)."""
    dest = Path(folder or (Path(paths.DATA_DIR) / "models")) / MODEL_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log("Downloading the laughter detector (about 20 MB)...")
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "ClipRadar"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
            got += len(chunk)
            if total:
                progress(got / total)
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


def classify(mel: np.ndarray) -> np.ndarray:
    """[B, 128, frames] log-mel -> [B, steps, 527] probabilities, one step every ~0.32 s.
    Each step describes the ~3 seconds around it."""
    sess = _load()
    x = mel[:, None, :, :].astype(np.float32)
    name = sess.get_inputs()[0].name
    out = sess.run(None, {name: x})[0]
    if out.ndim == 2:                       # an older clip-level model: one answer per window
        out = out[:, None, :]
    return out


def _groups(probs: np.ndarray) -> dict:
    """[..., 527] -> {group: [...]} (the strongest class in each group)."""
    return {k: probs[..., idx].max(axis=-1) for k, idx in GROUPS.items()}


CHUNK_S = 12      # the model was trained on 10-second clips, so it listens about that long at a time
LEAD_S = 1.0      # and trusts only the middle: its first second lacks context,
TAIL_S = 2.5      # and its last 2.5 s are unreliable (edge effects at the end of the input)
CORE_S = CHUNK_S - LEAD_S - TAIL_S


class Scanner:
    """Turns a stream of 32 kHz audio into per-second event probabilities.
    Feed audio in any chunk size; call result(n) for one value per second."""

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0                         # absolute sample index of buf[0] (= next chunk start)
        self.times: list = []                   # centre time (s) of every kept step
        self.vals = {k: [] for k in GROUPS}
        self.first = True

    def _run(self, starts: list, chunks: list, final_tail: bool = False):
        mels = [logmel(c) for c in chunks]
        width = max(m.shape[1] for m in mels)
        pad = (np.log(1e-5) + 4.5) / 5.0
        batch = np.stack([np.pad(m, ((0, 0), (0, width - m.shape[1])), constant_values=pad) for m in mels])
        out = classify(batch)                                   # [B, steps, 527]
        g = _groups(out)
        fps_step = width / out.shape[1]                         # mel frames per step
        for i, (t0, m) in enumerate(zip(starts, mels)):
            real_steps = int(math.ceil(m.shape[1] / fps_step))
            centres = t0 + (np.arange(real_steps) + 0.5) * fps_step / FPS
            lo = t0 + (0.0 if (self.first and i == 0 and t0 == 0) else LEAD_S)
            hi = t0 + (max(LEAD_S, m.shape[1] / FPS - TAIL_S) if final_tail else LEAD_S + CORE_S)
            keep = (centres >= lo) & (centres < hi)
            self.times.extend(centres[keep].tolist())
            for k in GROUPS:
                self.vals[k].extend(g[k][i, :real_steps][keep].tolist())
        self.first = False

    def feed(self, wave: np.ndarray, final: bool = False):
        self.buf = np.concatenate([self.buf, np.asarray(wave, dtype=np.float32)])
        need, hop = CHUNK_S * SR, int(CORE_S * SR)
        starts, chunks = [], []
        while self.buf.size >= need:
            starts.append(self.offset / SR)
            chunks.append(self.buf[:need].copy())
            self.buf = self.buf[hop:]
            self.offset += hop
            if len(chunks) == 2:
                self._run(starts, chunks)
                starts, chunks = [], []
        if chunks:
            self._run(starts, chunks)
        if final and self.buf.size > SR // 2:
            self._run([self.offset / SR], [self.buf.copy()], final_tail=True)
            self.offset += self.buf.size
            self.buf = np.zeros(0, dtype=np.float32)

    @property
    def seconds_done(self) -> float:
        return self.times[-1] if self.times else 0.0

    def result(self, n: Optional[int] = None) -> dict:
        """Per-second values: the average of the steps centred in that second."""
        t = np.asarray(self.times, dtype=np.float64)
        if n is None:
            n = int(math.ceil(t[-1])) + 1 if len(t) else 0
        out = {}
        sec = np.clip(np.floor(t).astype(np.int64), 0, max(0, n - 1)) if len(t) else np.zeros(0, dtype=np.int64)
        cnt = np.bincount(sec, minlength=n)[:n].astype(np.float64) if n else np.zeros(0)
        for k in GROUPS:
            v = np.asarray(self.vals[k], dtype=np.float64)
            tot = np.bincount(sec, weights=v, minlength=n)[:n] if n else np.zeros(0)
            with np.errstate(invalid="ignore", divide="ignore"):
                a = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
            # a second with no step of its own (rare) takes its neighbours' value
            if n and np.isnan(a).any() and (~np.isnan(a)).any():
                idx = np.arange(n)
                ok = ~np.isnan(a)
                last = int(idx[ok][-1])
                a[:last + 1] = np.interp(idx[:last + 1], idx[ok], a[ok])
            out[k] = np.nan_to_num(a, nan=0.0).astype(np.float32)
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


if __name__ == "__main__":
    # python src/audio_events.py --download [folder]   (used by build_exe.bat and install.bat)
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target and (Path(target) / MODEL_NAME).is_file():
            print("laughter detector already there")
        else:
            print("saved", download(print, target))
