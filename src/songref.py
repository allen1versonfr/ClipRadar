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
    raw = subprocess.run(cmd, capture_output=True, creationflags=engine.CREATE_NO_WINDOW).stdout
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
    x = np.pad(x, ((0, 0), (N_FFT // 2, N_FFT // 2)), mode="reflect")
    f = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None] + HOP * np.arange(f)[:, None]
    return np.fft.rfft(x[:, idx] * _win(), axis=2).transpose(0, 2, 1)


def istft(s: np.ndarray, n: int) -> np.ndarray:
    w = _win()
    fr = (np.fft.irfft(s.transpose(0, 2, 1), n=N_FFT, axis=2) * w).astype(np.float32)
    f = fr.shape[1]
    out = np.zeros((s.shape[0], N_FFT + HOP * (f - 1)), dtype=np.float32)
    nm = np.zeros(out.shape[1], dtype=np.float32)
    for i in range(f):
        out[:, i * HOP:i * HOP + N_FFT] += fr[:, i]
        nm[i * HOP:i * HOP + N_FFT] += w * w
    out /= np.maximum(nm, 1e-8)
    return out[:, N_FFT // 2:N_FFT // 2 + n]


def _low(x: np.ndarray) -> np.ndarray:
    """Mono at about 4 kHz (averaged in blocks of LOW samples), with its mean taken out."""
    m = x.mean(0)
    m = m[: len(m) // LOW * LOW].reshape(-1, LOW).mean(1)
    return (m - m.mean()).astype(np.float32)


def _xcorr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """c[lag] = sum a[t] b[t - lag] for every lag, lags from -(len(b)-1) to len(a)-1 (index lag + len(b) - 1)."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
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
    channel) and one volume per frame, fitted in turn; cells where the song is louder than the rest count most."""
    T = X.shape[2]
    G = np.ones(T)
    w = np.ones(X.shape)
    est = R
    k = np.ones(smooth) / smooth
    for _ in range(iters):
        RG = R * G[None, None, :]
        E = (w * X * np.conj(RG)).sum(2) / ((w * np.abs(RG) ** 2).sum(2) + 1e-9)
        RE = R * E[..., None]
        g = (w * (X * np.conj(RE)).real).sum((0, 1)) / ((w * np.abs(RE) ** 2).sum((0, 1)) + 1e-9)
        G = np.maximum(np.convolve(g, k, "same"), 0.0)
        est = RE * G[None, None, :]
        res = X - est
        w = np.abs(est) ** 2 / (np.abs(est) ** 2 + np.abs(res) ** 2 + 1e-12)
    return est


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
    for i, a in enumerate(starts):
        check()
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
        ok = z >= MIN_Z and drop >= MIN_DROP and hi - lo > SR
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
    try:
        while True:
            check()
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
            enc.stdin.write(np.ascontiguousarray(np.clip(blk, -1, 1).T, dtype="<f4").tobytes())
            pos += n
        enc.stdin.close()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError("Couldn't write the sound with the songs taken out: " + err.strip()[-300:])
    finally:
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
