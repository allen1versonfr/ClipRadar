"""
Takes the music out of a stretch of sound and keeps the voices (Export VOD's copyright scan).

A vocal separation model (MDX-Net "UVR-MDX-NET-Voc_FT" from Ultimate Vocal Remover, MIT licence, credited in
src/models/CREDITS-music-remover.txt; about 64 MB as ONNX) runs on onnxruntime,
which Clip Radar already ships. The model works on a spectrogram: the sound's frequencies over time
go in, the voice's come out. The transform both ways is done here with numpy.

  voicekeep.keep_voice(video, [(start, end), ...], out_flac)

writes the video's whole sound track with the music removed inside those stretches and everything
outside them untouched, sample for sample. The model hears voices, so singing stays too: a song with
vocals still has its vocals afterwards. Muting the whole stretch is the fix for those.

Measured on a speech + instrumental mix (2026-11-16, this model, CPU): music 47 dB quieter, speech
within 0.1 dB of the original, about twice as fast as real time.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import paths
import procjob

SR = 44100
N_FFT = 7680
HOP = 1024
GAIN = 1.021            # the model's output is this much too quiet (its published compensation)
RAMP = 0.03             # seconds over which a stretch fades between the original and the voice
QUIET = 1e-4            # a block quieter than this has nothing to separate

MODEL_NAME = "voice_keep.onnx"
MODEL_URL = os.environ.get("CLIPRADAR_VOICE_MODEL_URL",
                           "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/"
                           "UVR-MDX-NET-Voc_FT.onnx")
MODEL_SHA256 = "534b2070fcc7df514b13ef660dc8cbb328679c2374d04354a5c42bb14ecce111"
MODEL_MB = 64

Log = Callable[[str], None]
_noop = lambda *a, **k: None  # noqa: E731

_session = None
_lock = threading.Lock()
session_impl: list = [None]       # tests: a stand-in for the model (an object with run(), dim_f, dim_t)


# ----------------------------------------------------------------- model

def model_path() -> Optional[Path]:
    for d in (Path(paths.RES_DIR) / "models", Path(paths.DATA_DIR) / "models"):
        p = d / MODEL_NAME
        if p.is_file() and p.stat().st_size > 1_000_000:
            return p
    return None


def available() -> bool:
    if session_impl[0] is not None:
        return True
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return model_path() is not None


def download(log: Log = _noop, folder: Optional[str] = None, progress=_noop) -> Path:
    """Fetch the model into the data folder, checked against its known hash."""
    dest = Path(folder or (Path(paths.DATA_DIR) / "models")) / MODEL_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log(f"Downloading the music remover (about {MODEL_MB} MB)...")
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
        raise RuntimeError("the music remover download was damaged; try again")
    os.replace(tmp, dest)
    return dest


class _Model:
    """The ONNX session with the sizes it was built for: dim_f frequency bins, dim_t frames at a time."""

    def __init__(self, sess):
        self.sess = sess
        inp = sess.get_inputs()[0]
        self.name = inp.name
        self.dim_f, self.dim_t = int(inp.shape[2]), int(inp.shape[3])

    def run(self, x: np.ndarray) -> np.ndarray:
        return self.sess.run(None, {self.name: x})[0]


def _load():
    global _session
    if session_impl[0] is not None:
        return session_impl[0]
    with _lock:
        if _session is None:
            import onnxruntime as ort
            p = model_path()
            if p is None:
                raise RuntimeError("the music remover model isn't installed")
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            # threads wait for work without spinning: spinning kept every core but one at 100% even between blocks,
            # and the PC crawled through a long Erase song (5.4)
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            so.add_session_config_entry("session.inter_op.allow_spinning", "0")
            so.log_severity_level = 3
            _session = _Model(ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"]))
        return _session


def release() -> None:
    """Let go of the model (about 300 MB while loaded) once an export is done."""
    global _session
    with _lock:
        _session = None


# ----------------------------------------------------------------- spectrogram, both ways

_WIN = None


def _window() -> np.ndarray:
    global _WIN
    if _WIN is None:
        _WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)   # hann, periodic
    return _WIN


def stft(x: np.ndarray) -> np.ndarray:
    """[channels, samples] -> complex [channels, bins, frames], centred, reflected at the ends."""
    x = np.pad(x, ((0, 0), (N_FFT // 2, N_FFT // 2)), mode="reflect")
    frames = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    return np.fft.rfft(x[:, idx] * _window(), axis=2).transpose(0, 2, 1)


def istft(spec: np.ndarray, n: int) -> np.ndarray:
    """complex [channels, bins, frames] -> [channels, n], the inverse of stft."""
    win = _window()
    fr = (np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=2) * win).astype(np.float32)
    frames = fr.shape[1]
    out = np.zeros((spec.shape[0], N_FFT + HOP * (frames - 1)), dtype=np.float32)
    norm = np.zeros(out.shape[1], dtype=np.float32)
    w2 = win * win
    for i in range(frames):
        out[:, i * HOP:i * HOP + N_FFT] += fr[:, i]
        norm[i * HOP:i * HOP + N_FFT] += w2
    out /= np.maximum(norm, 1e-8)
    return out[:, N_FFT // 2:N_FFT // 2 + n]


class Separator:
    """Voice from sound, a block at a time. The model takes `chunk` samples and its edges come out
    smeared, so each block is given `trim` samples of what's before and after it and only the middle
    (`gen` samples) is kept."""

    def __init__(self):
        self.m = _load()
        self.chunk = HOP * (self.m.dim_t - 1)
        self.trim = N_FFT // 2
        self.gen = self.chunk - 2 * self.trim

    def block(self, ctx: np.ndarray) -> np.ndarray:
        """ctx: [2, chunk] (trim before, gen, trim after) -> the voice in the middle, [2, gen]."""
        f, t = self.m.dim_f, self.m.dim_t
        s = stft(ctx)[:, :f, :t]
        x = np.stack([s[0].real, s[0].imag, s[1].real, s[1].imag])[None].astype(np.float32)
        y = self.m.run(x)[0]
        full = np.zeros((2, N_FFT // 2 + 1, t), dtype=np.complex64)
        full[0, :f] = y[0] + 1j * y[1]
        full[1, :f] = y[2] + 1j * y[3]
        return istft(full, self.chunk)[:, self.trim:self.trim + self.gen] * GAIN


# ----------------------------------------------------------------- a whole sound track

def blend(pos: int, n: int, spans: list, ramp: int) -> Optional[np.ndarray]:
    """How much of the voice-only sound each of samples [pos, pos + n) gets: 1 inside a stretch, 0 outside,
    fading over `ramp` samples at each edge so the switch can't click. None when no stretch touches the block.
    spans: [(first sample, last sample)]."""
    out = None
    for a, b in spans:
        if b <= pos or a >= pos + n:
            continue
        if out is None:
            out = np.zeros(n, dtype=np.float32)
        t = np.arange(pos, pos + n, dtype=np.float64)
        up = np.clip((t - a) / max(1, ramp), 0.0, 1.0)
        down = np.clip((b - t) / max(1, ramp), 0.0, 1.0)
        out = np.maximum(out, np.minimum(up, down).astype(np.float32))
    return out


def _read(pipe, n: int) -> np.ndarray:
    """Up to n stereo samples from a pipe of f32le, as [2, got]."""
    want = n * 8
    buf = bytearray()
    while len(buf) < want:
        chunk = pipe.read(want - len(buf))
        if not chunk:
            break
        buf += chunk
    a = np.frombuffer(bytes(buf[: len(buf) // 8 * 8]), dtype="<f4")
    return a.reshape(-1, 2).T.copy()


def keep_voice(video: str, spans: list, out_path: str, start: float = 0.0, length: float = 0.0,
               progress=_noop, check=_noop) -> dict:
    """The sound of `video` from `start` for `length` seconds (0 = to the end), written to out_path (FLAC,
    44.1 kHz stereo) with the music taken out inside spans [(start, end)] (seconds from `start`).
    Returns {"blocks": how many went through the model, "seconds": how much sound that was}."""
    import engine
    sep = Separator()
    gen, trim = sep.gen, sep.trim
    marks = sorted((int(max(0.0, a) * SR), int(b * SR)) for a, b in spans if b > a)
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if length > 0:
        cmd += ["-t", f"{length:.3f}"]
    # first_pts=0: sound that starts a moment after the picture gets that moment of silence in front, so
    # sample 0 here is where the picture starts and the two stay in step when they're put back together
    cmd += ["-i", video, "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", "aresample=async=1:first_pts=0",
            "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           creationflags=engine.CREATE_NO_WINDOW)
    enc = subprocess.Popen([engine.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                            "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "0", out_path],
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=engine.CREATE_NO_WINDOW)
    for p in (dec, enc):
        procjob.adopt(p)
    total = marks[-1][1] if marks else 0
    ran, pos = 0, 0
    prev = np.zeros((2, trim), dtype=np.float32)
    try:
        cur = _read(dec.stdout, gen)
        while cur.shape[1]:
            check()
            nxt = _read(dec.stdout, gen)
            n = cur.shape[1]
            mix = blend(pos, n, marks, int(RAMP * SR))
            out = cur
            if mix is not None and float(np.abs(cur).max()) > QUIET:
                ctx = np.zeros((2, sep.chunk), dtype=np.float32)
                ctx[:, :trim] = prev
                ctx[:, trim:trim + n] = cur
                tail = nxt[:, :trim]
                ctx[:, trim + gen:trim + gen + tail.shape[1]] = tail if n == gen else 0
                voice = sep.block(ctx)[:, :n]
                out = mix * voice + (1.0 - mix) * cur
                ran += 1
            elif mix is not None:
                out = cur * (1.0 - mix)
            enc.stdin.write(np.ascontiguousarray(np.clip(out, -1.0, 1.0).T, dtype="<f4").tobytes())
            prev = np.concatenate([prev, cur], axis=1)[:, -trim:]
            pos += n
            if total:
                progress(min(0.999, pos / total))
            cur = nxt
        enc.stdin.close()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError("Couldn't write the sound with the music removed: " + err.strip()[-300:])
    finally:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
            p.wait()
    progress(1.0)
    return {"blocks": ran, "seconds": round(ran * gen / SR, 1)}


if __name__ == "__main__":
    # python src/voicekeep.py --download [folder]
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target and (Path(target) / MODEL_NAME).is_file():
            print("music remover already there")
        else:
            print("saved", download(print, target))
