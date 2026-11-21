"""
Erase song with the song itself (5.3): the way YouTube Studio's Erase song can work so well. YouTube knows which song it
is (Content ID) and has the recording; so can Clip Radar, when you say which song it is (a YouTube link, or a file).

The song as it plays in the stream is the recording put through the streamer's setup: a delay, an EQ, a volume that
moves (a fader, a compressor) and the stream's own compression. So, a stretch at a time (20 s pieces, so a song played
from the middle, skipped or paused is followed):

  1. find where in the recording this piece is (cross-correlation, at 4 kHz to find it, then to the sample);
  2. fit how the stream changed it: one EQ (a complex gain for each frequency) and one volume for each moment, the
     moments where the song is louder than the talking counting most (so the talking isn't taken for the song);
  3. take that out. What's left is the talking, the game and the room, nearly untouched.

A piece where the recording isn't found (another song, a different version, a remix: its spike in the correlation
doesn't stand out, see _z) is left exactly as it was and said so. The model-based eraser (songerase.py) isn't run on
top: measured, it took nothing more out.

Measured on speech over a song with vocals, the song delayed, EQ'd, compressed and AAC-encoded as a stream does
(2026-11-19): the song 29 dB quieter between sentences and 17 dB quieter under talking, speech SI-SDR 25.6 dB, against
11 dB, 6 dB and 14 dB with the model alone.

  fetch(source, folder) -> path of the recording (a YouTube or other link through yt-dlp, or a file on this PC)
  clean(mix [2, n], ref [2, m]) -> (cleaned [2, n], report)
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from typing import Callable, Optional

import numpy as np

SR = 44100
N_FFT = 2048
HOP = 512
PIECE = 20.0          # seconds aligned and fitted at a time
OVERLAP = 2.0         # seconds the pieces share, crossfaded
LOW = 11              # 44.1 kHz / 11 = about 4 kHz for finding where the piece is
G_SMOOTH = 9          # frames the moment-to-moment volume is smoothed over (about 0.1 s)
MIN_DROP = 4.0        # dB the song's part must drop by for the piece to count as found
MIN_Z = 12.0          # and its spike in the correlation must stand out this far (see _z)
REF_MAX = 15 * 60.0   # seconds of a recording read (a song, not an album)
READ_TIMEOUT = 90.0   # seconds a short read of a video's sound may take before it's given up on (plus a share of its length)

_noop = lambda *a, **k: None  # noqa: E731


# ---------------------------------------------------------------- the recording

def _key(source: str) -> str:
    return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()[:16]


def fetch(source: str, folder: str, log: Callable = _noop) -> str:
    """The recording as a 44.1 kHz stereo FLAC in folder: a file on this PC as it is (converted), or a link fetched with
    yt-dlp (audio only). Kept by the link, so the same song isn't fetched twice."""
    import engine
    source = (source or "").strip().strip('"')
    if not source:
        raise RuntimeError("Say which song it is: a YouTube link, or a song file on this PC.")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"song_{_key(source)}.flac")
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        return out
    src = source
    if re.match(r"^https?://", source, re.I):
        log("Getting the song from " + re.sub(r"^https?://(www\.)?", "", source).split("/")[0] + "...")
        tmpl = os.path.join(folder, f"dl_{_key(source)}.%(ext)s")
        try:
            import yt_dlp               # the copy Clip Radar downloads VODs with
            opts = {"format": "bestaudio/best", "outtmpl": tmpl, "noplaylist": True, "quiet": True, "no_warnings": True,
                    "max_filesize": 200 * 1024 * 1024, "nopart": True}
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([source])
        except Exception as e:          # noqa: BLE001  yt-dlp's own errors: said in words
            msg = str(e).replace("ERROR: ", "").strip().splitlines()[-1:] or ["unknown error"]
            raise RuntimeError("Couldn't get the song from that link: " + msg[0][:200]) from None
        got = [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith(f"dl_{_key(source)}.")]
        if not got:
            raise RuntimeError("Couldn't get the song from that link: nothing came down.")
        src = got[0]
    elif not os.path.isfile(source):
        raise RuntimeError("That song file isn't there. Paste a YouTube link, or choose the file again.")
    p = subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-t", str(REF_MAX), "-i", src, "-vn", "-ac", "2",
                        "-ar", str(SR), "-c:a", "flac", out], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=engine.CREATE_NO_WINDOW)
    if src != source:
        try:
            os.remove(src)
        except OSError:
            pass
    if p.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError("Couldn't read that song: " + (p.stderr or "").strip()[-200:])
    return out


def read(path: str, start: float = 0.0, length: float = 0.0) -> np.ndarray:
    """Stereo 44.1 kHz float32 [2, n] of a file (or a video's sound), from start for length seconds (0: to the end)."""
    import engine
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if length > 0:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-vn", "-af", "aresample=async=1:first_pts=0", "-ac", "2", "-ar", str(SR),
            "-f", "f32le", "-"]
    try:
        # 5.7.2: never waits for good on an FFmpeg that stalled (nothing read is no sound, which every caller handles)
        raw = subprocess.run(cmd, capture_output=True, creationflags=engine.CREATE_NO_WINDOW,
                             timeout=READ_TIMEOUT + (length or REF_MAX) / 4).stdout
    except subprocess.TimeoutExpired:
        raw = b""
    a = np.frombuffer(raw[: len(raw) // 8 * 8], dtype="<f4")
    return a.reshape(-1, 2).T.copy()


# ---------------------------------------------------------------- the maths

_W = None


def _win() -> np.ndarray:
    global _W
    if _W is None:
        _W = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)
    return _W


def stft(x: np.ndarray) -> np.ndarray:
    x = np.pad(np.asarray(x, dtype=np.float32), ((0, 0), (N_FFT // 2, N_FFT // 2)), mode="reflect")
    f = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None] + HOP * np.arange(f)[:, None]
    return np.fft.rfft(x[:, idx] * _win(), axis=2).astype(np.complex64, copy=False).transpose(0, 2, 1)


def overlap_add(fr: np.ndarray, hop: int) -> np.ndarray:
    """Frames [..., f, n_fft] laid end to end every `hop` samples and summed: [..., n_fft + hop * (f - 1)].
    A few whole-array adds (n_fft / hop of them) instead of a Python loop over every frame (5.7.2: the loop was a
    good part of each block's time)."""
    *lead, f, n = fr.shape
    k = n // hop
    out = np.zeros((*lead, n + hop * (f - 1)), dtype=fr.dtype)
    for j in range(k):
        out[..., j * hop:j * hop + f * hop] += fr[..., j * hop:(j + 1) * hop].reshape(*lead, f * hop)
    return out


def istft(s: np.ndarray, n: int) -> np.ndarray:
    w = _win()
    fr = (np.fft.irfft(s.transpose(0, 2, 1), n=N_FFT, axis=2) * w).astype(np.float32)
    f = fr.shape[1]
    out = overlap_add(fr, HOP)
    nm = overlap_add(np.broadcast_to(w * w, (f, N_FFT)), HOP)
    out /= np.maximum(nm, 1e-8)
    return out[:, N_FFT // 2:N_FFT // 2 + n]


def _low(x: np.ndarray) -> np.ndarray:
    """Mono at about 4 kHz (averaged in blocks of LOW samples), with its mean taken out."""
    m = x.mean(0)
    m = m[: len(m) // LOW * LOW].reshape(-1, LOW).mean(1)
    return (m - m.mean()).astype(np.float32)


_spec_cache: dict = {}


def _xcorr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """c[lag] = sum a[t] b[t - lag] for every lag, lags from -(len(b)-1) to len(a)-1 (index lag + len(b) - 1).
    b's transform is kept for the next call with the same b and size (5.7.2): it's the recording, the same for every
    piece of the video it's compared with, and was transformed again each time."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    key = (id(b), len(b), n)
    fb = _spec_cache.get(key)
    if fb is None or fb[0] is not b:
        if len(_spec_cache) >= 4:          # a recording at most 15 min: a few of these at a time
            _spec_cache.clear()
        fb = (b, np.conj(np.fft.rfft(b, n)))
        _spec_cache[key] = fb
    c = np.fft.irfft(np.fft.rfft(a, n) * fb[1], n)
    return np.concatenate([c[n - len(b) + 1:], c[:len(a)]])


def _z(c: np.ndarray) -> float:
    """How far the correlation's peak stands out from the rest (robust z-score). The recording that's really there gives
    a spike far above everything else (30 to 70 measured with the song 8 dB under the talking); another song, even one
    in the same key and tempo, only bumps (under 10)."""
    m = float(np.median(c))
    spread = 1.4826 * float(np.median(np.abs(c - m))) + 1e-12
    return (float(c.max()) - m) / spread


def locate(piece: np.ndarray, ref: np.ndarray, ref_low: Optional[np.ndarray] = None) -> tuple:
    """Where the recording starts relative to the piece, in samples: piece[t] ~ ref[t - lag] (lag < 0: the piece is
    from inside the recording), and how sure that is (_z). Found at 4 kHz, then to the sample around it, on 4 s of the
    piece where the recording plays (not where the piece starts before it)."""
    lo_p, lo_r = _low(piece), ref_low if ref_low is not None else _low(ref)
    c = _xcorr(lo_p, lo_r)
    lag_low = int(np.argmax(c)) - (len(lo_r) - 1)
    z = _z(c)
    guess = lag_low * LOW
    n, m = piece.shape[1], ref.shape[1]
    t0, t1 = max(0, guess) + 2 * LOW, min(n, m + guess) - 2 * LOW       # the piece's samples the recording covers
    span = min(4 * SR, t1 - t0)
    if span < SR // 2:
        return guess, z
    t0 = t0 + (t1 - t0 - span) // 2                                      # its middle
    seg = piece[:, t0:t0 + span].mean(0)
    best, lag = -np.inf, guess
    for d in range(-2 * LOW, 2 * LOW + 1):
        r0 = t0 - (guess + d)
        if r0 < 0 or r0 + span > m:
            continue
        v = float(seg @ ref[:, r0:r0 + span].mean(0))
        if v > best:
            best, lag = v, guess + d
    return lag, z


def fit(X: np.ndarray, R: np.ndarray, iters: int = 3, smooth: int = G_SMOOTH) -> np.ndarray:
    """The song as it is in the mix: R (the recording, lined up) through one EQ (a complex gain per frequency and
    channel) and one volume per frame, fitted in turn; cells where the song is louder than the rest count most.

    5.7.2: the same fit, worked out from X·conj(R) and |R|² once (they don't change between rounds) with the sums as
    matrix products, in single precision: about 4 times faster, the same result to within rounding."""
    X = X.astype(np.complex64, copy=False)
    R = R.astype(np.complex64, copy=False)
    T = X.shape[2]
    XR = X * np.conj(R)                                   # [c, f, t]
    Rp = (R.real ** 2 + R.imag ** 2)                      # |R|²
    Xp = (X.real ** 2 + X.imag ** 2)                      # |X|²
    G = np.ones(T, dtype=np.float32)
    w = None
    k = np.ones(smooth, dtype=np.float32) / smooth
    E = None
    for _ in range(iters):
        wXR = XR if w is None else w * XR
        wRp = Rp if w is None else w * Rp
        E = (wXR @ G) / (wRp @ (G * G) + 1e-9)            # [c, f]
        Ec = np.conj(E)[..., None]
        Ep = (E.real ** 2 + E.imag ** 2)[..., None]
        cross = (XR * Ec).real                            # Re(X conj(R E)) per cell
        num = (cross if w is None else w * cross).sum((0, 1))
        den = ((Rp * Ep) if w is None else w * (Rp * Ep)).sum((0, 1))
        g = num / (den + 1e-9)
        G = np.maximum(np.convolve(g, k, "same"), 0.0).astype(np.float32)
        est_p = Rp * Ep * (G * G)                         # |est|²
        res_p = np.maximum(Xp - 2 * cross * G + est_p, 0.0)  # |X - est|²
        w = est_p / (est_p + res_p + 1e-12)
    return R * (E[..., None] * G).astype(np.complex64)


def clean(mix: np.ndarray, ref: np.ndarray, progress: Callable = _noop, check: Callable = _noop) -> tuple:
    """mix [2, n] with the recording ref [2, m] taken out of it. Returns (cleaned [2, n], {"found": seconds where the
    song was found and taken out, "missed": [(start, end)] seconds where it wasn't (left as they were)})."""
    n = mix.shape[1]
    out = np.zeros_like(mix)
    weight = np.zeros(n, dtype=np.float32)
    piece, ov = int(PIECE * SR), int(OVERLAP * SR)
    step = piece - ov
    ref_low = _low(ref)
    found, missed = 0.0, []
    starts = list(range(0, max(1, n - ov), step)) or [0]

    def one(a: int):
        b = min(n, a + piece)
        x = mix[:, a:b]
        lag, z = locate(x, ref, ref_low)
        r = np.zeros_like(x)
        s0 = -lag                                     # where in the recording this piece starts
        lo, hi = max(0, s0), min(ref.shape[1], s0 + x.shape[1])
        if hi > lo:
            r[:, lo - s0:hi - s0] = ref[:, lo:hi]
        X, R = stft(x), stft(r)
        est = fit(X, R)
        y = istft(X - est, x.shape[1])
        # found: the song's share drops clearly where it was loud (else this isn't the recording here: leave it)
        e_x = (np.abs(X) ** 2).sum((0, 1))
        e_y = (np.abs(X - est) ** 2).sum((0, 1))
        loud = (np.abs(est) ** 2).sum((0, 1)) > 0.5 * e_x
        drop = 10 * np.log10((e_x[loud].sum() + 1e-12) / (e_y[loud].sum() + 1e-12)) if loud.sum() > 10 else 0.0
        return y, z >= MIN_Z and drop >= MIN_DROP and hi - lo > SR

    # 5.7.2: two pieces worked on at once (they don't depend on each other; NumPy does its sums outside Python's lock),
    # the results put together in order as before. Two, not more: each holds a few hundred MB while it's worked on.
    from concurrent.futures import ThreadPoolExecutor
    workers = 2 if (os.cpu_count() or 1) >= 4 and len(starts) > 1 else 1
    with ThreadPoolExecutor(workers) as pool:
        futs = {}
        for i, a in enumerate(starts):
            check()
            for j in range(i, min(len(starts), i + workers)):
                if j not in futs:
                    futs[j] = pool.submit(one, starts[j])
            y, ok = futs.pop(i).result()
            b = min(n, a + piece)
            x = mix[:, a:b]
            if not ok:
                y = x
                missed.append((round(a / SR, 2), round(b / SR, 2)))
            else:
                found += (b - a) / SR
            # crossfade the shared seconds
            wgt = np.ones(b - a, dtype=np.float32)
            if a > 0:
                wgt[:min(ov, b - a)] = np.linspace(0, 1, min(ov, b - a), dtype=np.float32)
            if b < n:
                k = min(ov, b - a)
                wgt[-k:] = np.minimum(wgt[-k:], np.linspace(1, 0, k, dtype=np.float32))
            out[:, a:b] += y * wgt
            weight[a:b] += wgt
            progress((i + 1) / len(starts))
    out /= np.maximum(weight, 1e-6)
    missed = _merge(missed)
    if missed and found == 0:
        return mix.copy(), {"found": 0.0, "missed": missed}
    return out, {"found": round(found - OVERLAP * max(0, len(starts) - 1 - len(missed)), 1), "missed": missed}


def _merge(spans: list) -> list:
    out: list = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ---------------------------------------------------------------- in an export

PAD = 1.0        # seconds read either side of a stretch, so its edges are fitted too
BLOCK = 13 * (PIECE - OVERLAP)   # 234 s of a stretch cleaned at a time (never all of it in memory), on the
                                 # pieces' own grid, so the result is the same as cleaning it in one go


class _Raw:
    """Cleaned sound kept on disk (float32, frames of 2 channels), read back a range at a time. Plain reads, no
    memory-mapping: Windows won't delete a file that's still mapped."""

    def __init__(self, path: str, n: int):
        self.path, self.n = path, n

    @property
    def shape(self) -> tuple:
        return (2, self.n)

    def read(self, lo: int, hi: int) -> np.ndarray:
        lo, hi = max(0, lo), min(self.n, hi)
        if hi <= lo:
            return np.zeros((2, 0), dtype=np.float32)
        with open(self.path, "rb") as f:
            f.seek(lo * 8)
            a = np.frombuffer(f.read((hi - lo) * 8), dtype="<f4")
        return a[: a.size // 2 * 2].reshape(-1, 2).T


def _clean_stretch(video: str, x0: float, x1: float, ref: np.ndarray, raw: str, check: Callable, progress: Callable) -> tuple:
    """The video's sound from x0 to x1 with ref taken out, BLOCK seconds at a time, written to raw on disk: about
    200 MB of memory at most, however long the stretch (5.4: a 30-minute stretch had needed gigabytes, and the PC
    ground to a halt). Returns (_Raw, {"found", "missed"} in the video's seconds)."""
    total = x1 - x0
    n = int(round(total * SR))
    ovn = int(OVERLAP * SR)
    found, missed, pos, written = 0.0, [], 0.0, 0
    tail = None                         # the seconds the next block shares, held back to be crossfaded
    with open(raw, "wb") as f:
        while pos < total - 1e-6 and written < n:
            check()
            span = min(BLOCK + OVERLAP, total - pos)
            mix = read(video, x0 + pos, span)
            cleaned, rep = clean(mix, ref, check=check, progress=lambda q, pos=pos: progress(min(1.0, (pos + q * span) / total)))
            cleaned = cleaned[:, :n - written]          # it starts where what's written ends
            if tail is not None:
                k = min(tail.shape[1], cleaned.shape[1])
                w = np.linspace(0, 1, k, dtype=np.float32)
                cleaned[:, :k] = tail[:, :k] * (1 - w) + cleaned[:, :k] * w
            last = pos + BLOCK >= total - 1e-6
            keep = cleaned if last else cleaned[:, :max(0, cleaned.shape[1] - ovn)]
            tail = None if last else cleaned[:, keep.shape[1]:].copy()
            f.write(np.ascontiguousarray(keep.T, dtype="<f4").tobytes())
            written += keep.shape[1]
            found += rep["found"]
            missed += [(round(x0 + pos + p, 2), round(x0 + pos + q, 2)) for p, q in rep["missed"]]
            del mix, cleaned, keep
            pos += BLOCK
        if tail is not None:
            f.write(np.ascontiguousarray(tail.T, dtype="<f4").tobytes())
            written += tail.shape[1]
    return _Raw(raw, written), {"found": found, "missed": _merge(missed)}


def erase_known(video: str, songs: list, out_path: str, start: float, length: float, folder: str,
                progress: Callable = _noop, check: Callable = _noop, log: Callable = _noop) -> dict:
    """The sound of video from start for length seconds (FLAC, 44.1 kHz stereo) with each named song taken out of its
    stretch: songs [(a, b, source)] in the video's own seconds. Everything else is the video's sound, sample for sample.
    Returns {"found": seconds taken out, "missed": [(a, b)] in the video's seconds where the song wasn't found}."""
    import engine
    import procjob
    pieces, found, missed, raws = [], 0.0, [], []
    for i, (a, b, source) in enumerate(songs):
        check()
        x0 = max(start, a - PAD)
        x1 = min(start + length, b + PAD)
        try:
            ref = read(fetch(source, folder, log))
            if ref.shape[1] < SR:
                raise RuntimeError("that song file has no sound in it")
        except RuntimeError as e:       # no network, the video gone: that stretch is left to the eraser model
            log(f"Couldn't get the song for {engine.fmt_ts(a)} to {engine.fmt_ts(b)}: {e}")
            missed.append((round(max(a, x0), 2), round(min(b, x1), 2)))
            continue
        raw = os.path.join(os.path.dirname(os.path.abspath(out_path)), f"known_{os.getpid()}_{i}.f32")
        raws.append(raw)
        cleaned, rep = _clean_stretch(video, x0, x1, ref, raw, check,
                                      lambda f, i=i: progress((i + f) / len(songs) * 0.9))
        del ref
        # only the stretch itself is replaced; the padding was there to fit its edges
        s, e = int(round((a - x0) * SR)), int(round((min(b, x1) - x0) * SR))
        s = max(0, s)
        pieces.append((int(round((max(a, x0) - start) * SR)), cleaned, s, max(0, min(e, cleaned.n) - s)))
        found += rep["found"]
        missed += rep["missed"]
        log(f"Song taken out of {engine.fmt_ts(a)} to {engine.fmt_ts(b)}"
            + (f", not found from {', '.join(engine.fmt_ts(p) + '-' + engine.fmt_ts(q) for p, q in rep['missed'])}" if rep["missed"] else "") + ".")
    # the video's sound, the pieces put in where they go (a 50 ms crossfade at each edge, so nothing clicks)
    dec = subprocess.Popen([engine.ffmpeg(), "-v", "error", "-nostdin", *(["-ss", f"{start:.3f}"] if start > 0 else []),
                            "-t", f"{length:.3f}", "-i", video, "-map", "0:a:0", "-vn", "-af", "aresample=async=1:first_pts=0",
                            "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           creationflags=engine.CREATE_NO_WINDOW)
    enc = subprocess.Popen([engine.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                            "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "0", out_path],
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=engine.CREATE_NO_WINDOW)
    for p in (dec, enc):
        procjob.adopt(p)
    ramp = int(0.05 * SR)
    pos = 0
    whole = max(1, int(length * SR))
    watch = procjob.Watch([dec, enc], check)       # 5.7.2: Stop works, and a stalled FFmpeg can't hold the bar
    try:
        while True:
            check()
            with watch.waiting():
                raw = dec.stdout.read(SR * 8 * 10)
            if not raw:
                break
            blk = np.frombuffer(raw[: len(raw) // 8 * 8], dtype="<f4").reshape(-1, 2).T.copy()
            n = blk.shape[1]
            for p0, src, off, ln in pieces:
                p1 = p0 + ln
                lo, hi = max(p0, pos), min(p1, pos + n)
                if hi <= lo:
                    continue
                t = np.arange(lo, hi)
                w = np.minimum(np.clip((t - p0) / ramp, 0, 1), np.clip((p1 - t) / ramp, 0, 1)).astype(np.float32)
                got = src.read(off + lo - p0, off + hi - p0)
                m = got.shape[1]
                blk[:, lo - pos:lo - pos + m] = w[:m] * got + (1 - w[:m]) * blk[:, lo - pos:lo - pos + m]
            with watch.waiting():
                enc.stdin.write(np.ascontiguousarray(np.clip(blk, -1, 1).T, dtype="<f4").tobytes())
            pos += n
            # 5.7.2: this pass over the whole sound track moves the bar too (it sat at 90% through all of it)
            progress(min(0.999, 0.9 + 0.1 * pos / whole))
        watch.raise_if()
        enc.stdin.close()
        with watch.waiting():
            err = enc.stderr.read().decode("utf-8", "replace")
        watch.raise_if()
        if enc.wait() != 0:
            raise RuntimeError("Couldn't write the sound with the songs taken out: " + err.strip()[-300:])
    except Exception:
        watch.raise_if()
        raise
    finally:
        watch.close()
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
            p.wait()
        for f in raws:
            try:
                os.remove(f)
            except OSError:
                pass
    progress(1.0)
    return {"found": round(found, 1), "missed": missed}


# ---------------------------------------------------------------- everywhere a song plays (5.7)

SWEEP_PIECE = 60.0          # seconds of the video compared with the recording at a time
SWEEP_STEP = 50.0           # how far apart they start (so a song that starts at a piece's edge isn't missed)
LOW_SR = SR / LOW           # the rate _low works at


def _low_stream(video: str, check: Callable = _noop):
    """The video's whole sound, mono at LOW_SR, read once from start to end: yields (start second, samples) in
    SWEEP_STEP blocks. One FFmpeg reading straight through, instead of a new one seeking to each minute (5.7.2)."""
    import engine
    import procjob
    rate = int(round(LOW_SR))
    p = subprocess.Popen([engine.ffmpeg(), "-v", "error", "-nostdin", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1",
                          "-ar", str(rate), "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=engine.CREATE_NO_WINDOW)
    procjob.adopt(p)
    w = procjob.Watch([p], check)
    step = int(SWEEP_STEP * rate) * 4
    at = 0
    try:
        while True:
            buf = bytearray()
            while len(buf) < step:
                with w.waiting():
                    chunk = p.stdout.read(step - len(buf))
                if not chunk:
                    break
                buf += chunk
            w.raise_if()
            if not buf:
                break
            a = np.frombuffer(bytes(buf[: len(buf) // 4 * 4]), dtype="<f4")
            yield at / rate, a
            at += a.size
            if len(buf) < step:
                break
    except GeneratorExit:
        raise
    except Exception:
        w.raise_if()
        raise
    finally:
        w.close()
        if p.poll() is None:
            p.kill()
        p.wait()


def sweep(video: str, duration: float, refs: dict, progress: Callable = _noop, check: Callable = _noop) -> dict:
    """Everywhere in the video each recording plays: refs {key: recording [2, m]} -> {key: [(start, end)]}, in the
    video's seconds, a minute or so at a time (the export cleans each 20 s piece exactly, and leaves a piece the song
    isn't in as it was). Finds a song where the music scan didn't: under talking, past a stretch's edges, played again
    later.

    5.7.2: the sound is read once straight through (it was a new FFmpeg seeking to every 50 s), each recording's
    transform is worked out once (_xcorr), and the pieces are compared on several cores at once."""
    if not refs or duration <= 0:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    lows = {k: _low(r) for k, r in refs.items() if r.shape[1] >= SR}
    out: dict = {k: [] for k in lows}
    if not lows:
        return {}
    piece_n = int(SWEEP_PIECE * LOW_SR)
    starts = list(np.arange(0.0, max(duration - 5.0, 0.0) + 1e-6, SWEEP_STEP)) or [0.0]
    total = len(starts)

    def look(t: float, x: np.ndarray) -> list:
        if x.size < LOW_SR * 5 or float(np.abs(x).max()) < 1e-4:
            return []
        x = (x - x.mean()).astype(np.float32)
        return [k for k, rl in lows.items() if _z(_xcorr(x[:piece_n], rl)) >= MIN_Z]

    workers = max(1, min(3, (os.cpu_count() or 2) - 1))
    done = 0
    buf = np.zeros(0, dtype=np.float32)
    buf_at = 0.0
    pending: list = []
    with ThreadPoolExecutor(workers) as pool:
        def drain(limit: int):
            nonlocal done
            while len(pending) > limit:
                t, fut = pending.pop(0)
                for k in fut.result():
                    out[k].append((round(float(t), 2), round(float(min(duration, t + SWEEP_PIECE)), 2)))
                done += 1
                progress(min(0.999, done / total))
        si = 0
        for _, a in _low_stream(video, check):
            check()
            buf = np.concatenate([buf, a]) if buf.size else a
            # every piece that's all here now (or the last ones, which run to the end)
            while si < len(starts):
                t = starts[si]
                lo = int(round((t - buf_at) * LOW_SR))
                hi = lo + piece_n
                if hi > buf.size:
                    break
                pending.append((t, pool.submit(look, t, buf[lo:hi].copy())))
                si += 1
                drain(workers * 2)
            keep = int(round((starts[si] - buf_at) * LOW_SR)) if si < len(starts) else buf.size
            if keep > 0:
                buf, buf_at = buf[keep:], buf_at + keep / LOW_SR
        while si < len(starts):             # the pieces at the very end, shorter than a whole minute
            t = starts[si]
            lo = max(0, int(round((t - buf_at) * LOW_SR)))
            pending.append((t, pool.submit(look, t, buf[lo:lo + piece_n].copy())))
            si += 1
        drain(0)
    progress(1.0)
    return {k: _merge(v) for k, v in out.items() if v}
