"""
Erase song (5.0): takes a song out of a stretch of sound and keeps everything else, the way YouTube Studio's
Erase song does: people talking, the game, alerts. The song goes whole, its singing too.

Before 5.0, Export VOD's "Remove music" kept the voices (voicekeep.py: a vocal separation model). A song with
singing kept its singing, so on most real songs it barely did anything: measured on speech over a pop song, the
song came out 3.6 dB quieter, which you can hardly hear.

This uses a dialogue / music / effects separation model, the kind trained for film soundtracks: TIGER
(Xu, Li, Chen, Hu, Tsinghua University, ICLR 2025), its Divide and Remaster model, converted to ONNX with int8
weights by audiojs (Apache 2.0; credited in src/models/CREDITS-song-eraser.txt; 7.3 MB). It hears music as music,
sung or not, because its training music had singing in it.

  songerase.erase(video, [(start, end), ...], out_flac)

writes the video's whole sound track with the songs erased inside those stretches and everything outside them
untouched, sample for sample (the same shape as voicekeep.keep_voice, which it replaces).

How: the model hears one 12-second mono stretch at a time and says, for each frequency at each moment, how much
of it is dialogue, music and effects. That share (a soft mask) is put on the left and the right channel's own sound,
so stereo stays stereo. Each block is 10 s, with 1 s of what's before and after it heard too and then left out,
so blocks join without seams. `strength` above 1 takes out more of what's left of the song, at some cost to the
sound around it.
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
N_FFT = 2048
HOP = 512
SEG = 529200                 # 12 s: what the model takes at a time (1034 frames)
CTX = SR                     # 1 s heard on each side of a block and left out
GEN = SEG - 2 * CTX          # 10 s kept from each run
RAMP = 0.05                  # seconds over which a stretch fades between the original and the erased sound
QUIET = 1e-4                 # a block quieter than this has nothing to erase
STRENGTH = 1.5               # how hard the song is pushed down where the model is unsure (1 = as the model says)

MODEL_NAME = "song_eraser.onnx"
MODEL_URL = os.environ.get("CLIPRADAR_SONG_MODEL_URL",
                           "https://huggingface.co/audiojs/tiger-dnr/resolve/main/tiger.int8.onnx")
MODEL_SHA256 = "2189859ac7e89cf4d9d284c6f393e737aaa2b3233ed7ee424ad622012bce7bca"
MODEL_MB = 7

Log = Callable[[str], None]
_noop = lambda *a, **k: None  # noqa: E731

_session = None
_lock = threading.Lock()
session_impl: list = [None]       # tests: a stand-in for the model (an object with run(spec) -> stems)


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
    log(f"Downloading the song eraser (about {MODEL_MB} MB)...")
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
        raise RuntimeError("the song eraser download was damaged; try again")
    os.replace(tmp, dest)
    return dest


class _Model:
    def __init__(self, sess):
        self.sess = sess

    def run(self, spec: np.ndarray) -> np.ndarray:
        """[1, 2, 1025, 1034] (re, im) -> [1, 6, 1025, 1034]: dialogue, music, effects, each re and im."""
        return self.sess.run(None, {"mix_spec": spec})[0]


def _load():
    global _session
    if session_impl[0] is not None:
        return session_impl[0]
    with _lock:
        if _session is None:
            import onnxruntime as ort
            p = model_path()
            if p is None:
                raise RuntimeError("the song eraser model isn't installed")
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
    """Let go of the model once an export is done (it holds a few GB while it works)."""
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
    """[..., samples] -> complex [..., bins, frames]: centred, reflected at the ends, unnormalised (as torch.stft)."""
    pad = [(0, 0)] * (x.ndim - 1) + [(N_FFT // 2, N_FFT // 2)]
    x = np.pad(x, pad, mode="reflect")
    frames = 1 + (x.shape[-1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    return np.swapaxes(np.fft.rfft(x[..., idx] * _window(), axis=-1), -1, -2)


def istft(spec: np.ndarray, n: int) -> np.ndarray:
    """complex [..., bins, frames] -> [..., n], the inverse of stft."""
    win = _window()
    fr = (np.fft.irfft(np.swapaxes(spec, -1, -2), n=N_FFT, axis=-1) * win).astype(np.float32)
    frames = fr.shape[-2]
    out = np.zeros(fr.shape[:-2] + (N_FFT + HOP * (frames - 1),), dtype=np.float32)
    norm = np.zeros(out.shape[-1], dtype=np.float32)
    w2 = win * win
    for i in range(frames):
        out[..., i * HOP:i * HOP + N_FFT] += fr[..., i, :]
        norm[i * HOP:i * HOP + N_FFT] += w2
    out /= np.maximum(norm, 1e-8)
    return out[..., N_FFT // 2:N_FFT // 2 + n]


def keep_mask(stems: np.ndarray, strength: float = STRENGTH) -> np.ndarray:
    """How much of each frequency at each moment is kept: the part that isn't music.
    stems: [6, bins, frames] (dialogue, music, effects, re and im). A Wiener mask, the music's share
    weighted by `strength`, so where the model is unsure the song loses."""
    p = stems.astype(np.float32) ** 2
    dia, mus, fx = p[0] + p[1], p[2] + p[3], p[4] + p[5]
    rest = dia + fx
    return rest / np.maximum(rest + strength * mus, 1e-10)


def erase_block(ctx: np.ndarray, strength: float = STRENGTH) -> np.ndarray:
    """ctx: [2, SEG] (1 s before, the 10 s block, 1 s after) -> the block with the song erased, [2, GEN]."""
    m = _load()
    spec = stft(ctx)                                     # [2, bins, frames]
    mono = spec.mean(axis=0)
    x = np.stack([mono.real, mono.imag])[None].astype(np.float32)
    stems = m.run(x)[0]
    keep = keep_mask(stems, strength)
    return istft(spec * keep[None], SEG)[:, CTX:CTX + GEN]


# ----------------------------------------------------------------- a whole sound track

def blend(pos: int, n: int, spans: list, ramp: int) -> Optional[np.ndarray]:
    """How much of the erased sound each of samples [pos, pos + n) gets: 1 inside a stretch, 0 outside,
    fading over `ramp` samples at each edge. None when no stretch touches the block."""
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


def erase(video: str, spans: list, out_path: str, start: float = 0.0, length: float = 0.0,
          progress=_noop, check=_noop, strength: float = STRENGTH) -> dict:
    """The sound of `video` from `start` for `length` seconds (0 = to the end), written to out_path (FLAC,
    44.1 kHz stereo) with the songs erased inside spans [(start, end)] (seconds from `start`).
    Returns {"blocks": how many went through the model, "seconds": how much sound that was}."""
    import engine
    marks = sorted((int(max(0.0, a) * SR), int(b * SR)) for a, b in spans if b > a)
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if length > 0:
        cmd += ["-t", f"{length:.3f}"]
    # first_pts=0: sample 0 here is where the picture starts, so the two stay in step when put back together
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
    prev = np.zeros((2, CTX), dtype=np.float32)
    try:
        cur = _read(dec.stdout, GEN)
        while cur.shape[1]:
            check()
            nxt = _read(dec.stdout, GEN)
            n = cur.shape[1]
            mix = blend(pos, n, marks, int(RAMP * SR))
            out = cur
            if mix is not None and float(np.abs(cur).max()) > QUIET:
                ctx = np.zeros((2, SEG), dtype=np.float32)
                ctx[:, :CTX] = prev
                ctx[:, CTX:CTX + n] = cur
                tail = nxt[:, :CTX]
                if n == GEN:
                    ctx[:, CTX + GEN:CTX + GEN + tail.shape[1]] = tail
                erased = erase_block(ctx, strength)[:, :n]
                out = mix * erased + (1.0 - mix) * cur
                ran += 1
            elif mix is not None:
                out = cur * (1.0 - mix)
            enc.stdin.write(np.ascontiguousarray(np.clip(out, -1.0, 1.0).T, dtype="<f4").tobytes())
            prev = np.concatenate([prev, cur], axis=1)[:, -CTX:]
            pos += n
            if total:
                progress(min(0.999, pos / total))
            cur = nxt
        enc.stdin.close()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError("Couldn't write the sound with the songs erased: " + err.strip()[-300:])
    finally:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
            p.wait()
    progress(1.0)
    return {"blocks": ran, "seconds": round(ran * GEN / SR, 1)}


if __name__ == "__main__":
    # python src/songerase.py --download [folder]
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target and (Path(target) / MODEL_NAME).is_file():
            print("song eraser already there")
        else:
            print("saved", download(print, target))
