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


RUN_MIN = 180.0          # seconds a block may take before it counts as hung, at least (the CPU takes ~45 s on 4 cores)
RUN_TIMES = 8.0          # or this many times as long as a block usually takes here, if that's longer


class Hung(RuntimeError):
    """A run of the model that never came back."""


def timed_run(sess, spec: np.ndarray, limit: float) -> np.ndarray:
    """One run of the model, given up on after `limit` seconds (5.7.6). A run is one call into ONNX Runtime that can't
    be interrupted: a graphics driver that wedges partway (or a processor run stuck behind it) never returned, and the
    export sat at the same percent for good, Stop included. The run goes on in its own thread; this one stops waiting."""
    box: dict = {}

    def go():
        try:
            box["out"] = sess.run(None, {"mix_spec": spec})[0]
        except BaseException as e:      # noqa: BLE001  handed to the caller
            box["err"] = e
    th = threading.Thread(target=go, daemon=True, name="song-eraser-run")
    th.start()
    th.join(limit)
    if th.is_alive():
        raise Hung(f"the song eraser's model didn't finish a block in {int(limit)} seconds")
    if "err" in box:
        raise box["err"]
    return box["out"]


def _limit() -> float:
    usual = expected_block_seconds() or _cpu_block_seconds()
    return max(RUN_MIN, RUN_TIMES * float(usual or 0))


class _Model:
    def __init__(self, sess, where: str = "cpu", fallback=None):
        self.sess, self.where, self._fallback = sess, where, fallback
        self._run = threading.Lock()      # DirectML can't take two runs of one session at once; the CPU doesn't mind

    def run(self, spec: np.ndarray) -> np.ndarray:
        """[1, 2, 1025, 1034] (re, im) -> [1, 6, 1025, 1034]: dialogue, music, effects, each re and im."""
        lock = self._run
        with lock:
            try:
                out = timed_run(self.sess, spec, _limit())
                if self.where != "cpu" and not np.isfinite(out).all():
                    raise RuntimeError("the graphics card gave numbers that aren't numbers")
                return out
            except Exception as e:      # noqa: BLE001  5.7.5: the card failed partway (a driver reset): the processor goes on
                if self.where == "cpu" or self._fallback is None:
                    raise
                why = "it stopped responding" if isinstance(e, Hung) else (str(e).strip().splitlines()[-1][:160] if str(e).strip() else type(e).__name__)
                _note(f"The song eraser stopped working on the graphics card ({why}); it goes on on the processor.")
                _remember({"device": "cpu", "why": "failed on the graphics card", "card": _card_name()})
                _status.update(where="cpu", why="it failed on the graphics card" if not isinstance(e, Hung) else "it stopped responding on the graphics card")
                # a fresh lock with the fresh session: a run that never came back still holds the old one
                self.sess, self.where, self._run = self._fallback(), "cpu", threading.Lock()
        with self._run:
            return timed_run(self.sess, spec, _limit())


# ----------------------------------------------------------------- on the graphics card (5.7.5)
# The model ran on the processor only: 44 s for each 10 s of song on 4 cores, most of a long export, with the graphics
# card idle. DirectML (Windows' own way to run models on any DirectX 12 card: NVIDIA, AMD and Intel alike, as Whisper's
# Vulkan engine is for AMD and Intel) runs it on the card. It's tried once per card: one block timed and checked, and
# used only if it gives real numbers and beats the processor; the choice is kept (song_eraser_device.json) until the
# card changes. Settings › Performance › "Whisper runs on" is the song eraser's choice too: Automatic, the graphics
# card, or the CPU (and Automatic leaves the card alone while a game has it busy).

DML = "DmlExecutionProvider"
_status: dict = {"where": "", "why": "", "note": ""}


def _device_file() -> Path:
    return Path(paths.DATA_DIR) / "song_eraser_device.json"


def _card_name() -> str:
    try:
        import gpu
        c = gpu.card()
        return str(c.get("name") or "")
    except Exception:       # noqa: BLE001
        return ""


def _remembered() -> dict:
    try:
        import persist
        d = persist.read_json(str(_device_file()), {}) or {}
        return d if d.get("card") == _card_name() else {}
    except Exception:       # noqa: BLE001
        return {}


def _remember(d: dict) -> None:
    try:
        import persist
        persist.write_json(str(_device_file()), d)
    except Exception:       # noqa: BLE001
        pass


def _note(m: str) -> None:
    _status["note"] = m


def status() -> dict:
    """Where the song eraser runs and why, for Settings and the export's log: {"where": "gpu"/"cpu"/"", "why", "note"}."""
    return dict(_status)


def _want() -> str:
    try:
        import gpu
        return gpu.mode()
    except Exception:       # noqa: BLE001
        return "auto"


def _cpu_session(ort, p):
    so = ort.SessionOptions()
    so.intra_op_num_threads = CPU_THREADS[0] or max(1, os.cpu_count() or 2)
    # 5.7.5: every core, and the threads spin waiting for the model's next step: the processor really busy. Measured on 4
    # cores: 41.3 s a block with 3.9 cores busy, against 51.8 s with 2.2 (one core left out, threads asleep between
    # steps). Spinning was turned off in 5.4 because the PC crawled; since 5.4.2 Clip Radar's background work runs below
    # normal priority (Keep the PC responsive), so Windows gives those cores to whatever you're doing first.
    so.add_session_config_entry("session.intra_op.allow_spinning", "1")
    so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    # 5.7.2: no memory pool: the same speed (measured: 44.5 s against 43.9 s a block on 4 cores) with about 3 GB
    # held instead of 5, so a PC with 16 GB isn't pushed into swapping, which is what made it crawl
    so.enable_cpu_mem_arena = False
    so.log_severity_level = 3
    return ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])


CPU_THREADS = [0]       # 0: every core (5.7.5; it was one fewer, leaving a core idle through hours of erasing)


def _gpu_session(ort, p, device: int):
    """The model on card `device`, set up in its own thread and given up on after PROBE_MAX seconds (5.7.6: a driver
    that wedges while the model is set up on it held the export before it had started)."""
    box: dict = {}

    def go():
        try:
            box["s"] = _gpu_session_now(ort, p, device)
        except BaseException as e:      # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=go, daemon=True, name="song-eraser-setup")
    th.start()
    th.join(PROBE_MAX)
    if th.is_alive():
        raise Hung(f"setting the song eraser up on the graphics card took over {int(PROBE_MAX)} seconds")
    if "e" in box:
        raise box["e"]
    return box["s"]


def _gpu_session_now(ort, p, device: int):
    so = ort.SessionOptions()
    so.enable_mem_pattern = False                         # DirectML's own requirements
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.log_severity_level = 3
    return ort.InferenceSession(str(p), so, providers=[(DML, {"device_id": int(device)}), "CPUExecutionProvider"])


PROBE_MAX = 90.0         # seconds a card's first-use test may take before the card counts as unusable (5.7.6)


def _try_cards(ort, p) -> Optional[tuple]:
    """(session, device, seconds a block) on the fastest card that gives real numbers, or None."""
    import time as _t
    rng = np.random.default_rng(0)
    probe = (rng.standard_normal((1, 2, 1025, 1034)) * 0.05).astype(np.float32)
    try:
        import gpu
        n = max(1, len(gpu.cards()))
    except Exception:       # noqa: BLE001
        n = 1
    best = None
    for dev in range(min(n, 3)):
        try:
            sess = _gpu_session(ort, p, dev)
            timed_run(sess, probe, PROBE_MAX)                 # the first run sets the card up: not timed
            t0 = _t.perf_counter()
            out = timed_run(sess, probe, PROBE_MAX)
            took = _t.perf_counter() - t0
        except Exception:   # noqa: BLE001  this card can't run it
            continue
        if out.shape != (1, 6, 1025, 1034) or not np.isfinite(out).all() or not np.abs(out).max() > 0:
            continue
        if best is None or took < best[2]:
            best = (sess, dev, took)
    return best


def _cpu_block_seconds() -> float:
    """How long a block takes on this PC's processor: measured on an earlier export, or the typical speed for its cores."""
    try:
        import exporteta
        return GEN / SR / exporteta.rate("voderase")[0]
    except Exception:       # noqa: BLE001
        return 44.0 * 4 / max(1, os.cpu_count() or 4)


def _place(ort, p):
    """The model's session on the graphics card when that's allowed, works and is quicker; else on the processor."""
    want = _want()
    have_dml = DML in (ort.get_available_providers() or [])
    cpu = lambda: _cpu_session(ort, p)          # noqa: E731
    if want == "cpu" or not have_dml:
        _status.update(where="cpu", why="you chose the CPU" if want == "cpu" else "this copy of Clip Radar can't use the graphics card")
        return _Model(cpu())
    if want == "auto":
        try:
            import gpu
            if gpu.gpu_load(wait=False) >= gpu.GPU_BUSY:
                _status.update(where="cpu", why="the graphics card is busy (a game?)")
                return _Model(cpu())
        except Exception:   # noqa: BLE001
            pass
    seen = _remembered()
    if seen.get("device") == "cpu" and want == "auto":
        _status.update(where="cpu", why=seen.get("why") or "the processor was quicker on this PC")
        return _Model(cpu())
    if seen.get("device") == "gpu":
        try:
            sess = _gpu_session(ort, p, int(seen.get("id", 0)))
            _status.update(where="gpu", why=f"about {seen.get('block', 0):.1f} s a block on {seen.get('card') or 'the graphics card'}")
            return _Model(sess, "gpu", cpu)
        except Exception:   # noqa: BLE001  the card changed under it: tried again below
            pass
    got = _try_cards(ort, p)
    on_cpu = _cpu_block_seconds()
    if got and (want == "gpu" or got[2] < on_cpu * 0.9):
        sess, dev, took = got
        _remember({"device": "gpu", "id": dev, "block": round(took, 2), "cpu_block": round(on_cpu, 1), "card": _card_name()})
        _status.update(where="gpu", why=f"about {took:.1f} s a block on the graphics card, against {on_cpu:.0f} s on the processor")
        return _Model(sess, "gpu", cpu)
    why = ("the graphics card couldn't run it" if not got
           else f"the processor is quicker on this PC ({on_cpu:.0f} s a block against {got[2]:.0f} s)")
    _remember({"device": "cpu", "why": why, "card": _card_name()})
    _status.update(where="cpu", why=why)
    return _Model(cpu())


def needs_trial() -> bool:
    """The graphics card will be tried before the first block (once per card): the export says so, since setting the
    model up on a card can take a minute or two with the bar standing still."""
    if session_impl[0] is not None or _session is not None or _want() == "cpu" or _remembered().get("device"):
        return False
    try:
        import onnxruntime as ort
        return DML in (ort.get_available_providers() or [])
    except Exception:       # noqa: BLE001
        return False


def expected_block_seconds() -> Optional[float]:
    """Seconds a block should take where the eraser will run, when the graphics card was timed on this PC (for the
    export's time estimate); None to go by the processor's speed."""
    seen = _remembered()
    if seen.get("device") == "gpu" and _want() != "cpu" and seen.get("block"):
        return float(seen["block"])
    return None


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
            _session = _place(ort, p)
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
    from songref import overlap_add          # 5.7.2: whole-array adds, not a Python loop over 1034 frames
    out = overlap_add(fr, HOP)
    norm = overlap_add(np.broadcast_to(win * win, (frames, N_FFT)), HOP)
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


def erase_block(ctx: np.ndarray, strength: float = STRENGTH, whole: bool = False) -> np.ndarray:
    """ctx: [2, SEG] (1 s before, the 10 s block, 1 s after) -> the block with the song erased, [2, GEN].
    whole: all SEG of it, the seconds heard only on one side too (5.7.8: the YouTube check's 12 s samples, one run each)."""
    m = _load()
    spec = stft(ctx)                                     # [2, bins, frames]
    mono = spec.mean(axis=0)
    x = np.stack([mono.real, mono.imag])[None].astype(np.float32)
    stems = m.run(x)[0]
    keep = keep_mask(stems, strength)
    y = istft(spec * keep[None], SEG)
    return y if whole else y[:, CTX:CTX + GEN]


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


def _erase_unlocked(video: str, spans: list, out_path: str, start: float = 0.0, length: float = 0.0,
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
    # 5.7.2: the bar covers the whole sound track, the blocks through the model counting for most of it (they're
    # most of the time). Before, it only went as far as the last song and then sat at 99% while the rest of a long
    # VOD's sound was read and written.
    try:
        whole = int((length if length > 0 else max(0.0, float(engine.probe(video).get("duration") or 0) - start)) * SR)
    except Exception:       # noqa: BLE001
        whole = 0
    whole = max(whole, marks[-1][1] if marks else 0)
    expect = sum(1 for k in range(0, whole, GEN) if any(a < k + GEN and b > k for a, b in marks)) if whole else 0
    touched = 0
    ran, pos = 0, 0
    prev = np.zeros((2, CTX), dtype=np.float32)
    w = procjob.Watch([dec, enc], check)
    try:
        with w.waiting():
            cur = _read(dec.stdout, GEN)
        while cur.shape[1]:
            check()
            with w.waiting():
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
            touched += mix is not None
            with w.waiting():
                enc.stdin.write(np.ascontiguousarray(np.clip(out, -1.0, 1.0).T, dtype="<f4").tobytes())
            prev = np.concatenate([prev, cur], axis=1)[:, -CTX:]
            pos += n
            if whole:
                done = 0.85 * min(1.0, touched / expect) + 0.15 * min(1.0, pos / whole) if expect else pos / whole
                progress(min(0.999, done))
            cur = nxt
        w.raise_if()
        enc.stdin.close()
        with w.waiting():
            err = enc.stderr.read().decode("utf-8", "replace")
        w.raise_if()
        if enc.wait() != 0:
            raise RuntimeError("Couldn't write the sound with the songs erased: " + err.strip()[-300:])
    except Exception:
        w.raise_if()
        raise
    finally:
        w.close()
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
            p.wait()
    progress(1.0)
    return {"blocks": ran, "seconds": round(ran * GEN / SR, 1)}


_run_lock = threading.Lock()


def erase(*args, **kw) -> dict:
    """The song eraser over a sound track (_erase_unlocked), one at a time (5.7.3): Segments makes two
    stretches at once, and the model already uses every core and a few GB of memory, so a second one alongside would
    only add memory, not speed (measured: 40 s a block two at a time, 44 s one at a time). The other stretch's
    picture goes on being made meanwhile."""
    with _run_lock:
        return _erase_unlocked(*args, **kw)


if __name__ == "__main__":
    # python src/songerase.py --download [folder]
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--download":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target and (Path(target) / MODEL_NAME).is_file():
            print("song eraser already there")
        else:
            print("saved", download(print, target))

