"""
Framing (3.9): find faces in the video, follow the streamer smoothly in vertical crops, spot a
facecam in a corner, and suggest a layout.

Faces come from a small face detector (UltraFace RFB-320, MIT licence, about 1 MB as ONNX) run
on onnxruntime, which Clip Radar already ships for the laughter detector. Frames are read with
ffmpeg at a few per second, small, so a 60 s clip takes a few seconds.

Everything degrades gracefully: no model, no faces or low confidence means the crop stays where
you put it (Layout › Focus).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

import engine
import paths

MODEL = "ultraface-rfb-320.onnx"
IN_W, IN_H = 320, 240
MIN_SCORE = 0.75
SAMPLE_FPS = 4.0              # frames looked at per second while following a face
HOLD_S = 2.0                  # a face that disappears (turned away, covered) is held this long
MAX_SPEED = 0.22              # fastest pan, in source widths per second
DEADZONE = 0.035              # the crop doesn't move for wiggles smaller than this (source widths)
MIN_COVERAGE = 0.3            # under this share of frames with the face, don't follow (fall back)
GROUP_SIZE = 0.4              # another face at least this big (vs the biggest) counts as a second person
GROUP_FIT = 0.92             # people are framed together when they span under this share of the crop
SWITCH_S = 1.5               # someone else is followed only after the person followed is gone this long
TARGETS = ("auto", "largest", "left", "right", "point")
KEEP_SCORE = 0.6              # a weaker detection still counts when it's the face already followed (a turned head)
FINE_BELOW = 0.9              # the first look found the face in fewer frames than this: look again at twice the size
CHUNK = 32                    # frames read and looked at together (a long clip never sits in memory whole)
CUT_DIFF = 28.0               # a new shot: the picture changes this much (mean, 0-255) ...
CUT_HIST = 0.12               # ... and its colours this much (0-2; within a shot, 0.01 to 0.03), from one look to the next
CUT_FPS = 30.0                # how finely a cut's moment is found
MAX_CUTS = 24

_lock = threading.Lock()
_session = [None, False]      # (session, tried)


def model_path() -> Optional[Path]:
    for d in (Path(paths.RES_DIR) / "models" / "face", Path(__file__).resolve().parent / "models" / "face"):
        p = d / MODEL
        if p.is_file():
            return p
    return None


def available() -> bool:
    if model_path() is None:
        return False
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _sess():
    with _lock:
        if not _session[1]:
            _session[1] = True
            try:
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.log_severity_level = 3
                so.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
                _session[0] = ort.InferenceSession(str(model_path()), so, providers=["CPUExecutionProvider"])
            except Exception:
                _session[0] = None
        return _session[0]


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.3) -> list:
    order = np.argsort(-scores)
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        r = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[r, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[r, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[r, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[r, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        o = inter / (area(boxes[i]) + area(boxes[r]) - inter + 1e-9)
        order = r[o <= iou]
    return keep


def detect(frames: np.ndarray, min_score: float = 0.0) -> list:
    """Faces in frames (N x 240 x 320 x 3, RGB uint8). Per frame: [(x1, y1, x2, y2, score)] as
    fractions of the frame, scored at least min_score (MIN_SCORE when not given)."""
    s = _sess()
    if s is None or not len(frames):
        return [[] for _ in range(len(frames))]
    min_score = min_score or MIN_SCORE
    out = []
    for frame in frames:
        # one frame at a time, as the model takes them (a whole clip turned to float32 at once was 4x its size)
        one = ((frame.astype(np.float32) - 127.0) / 128.0).transpose(2, 0, 1)[None]
        sc, bx = s.run(None, {"input": one})
        sc, bx = sc[0, :, 1], bx[0]
        m = sc >= min_score
        if not m.any():
            out.append([])
            continue
        b, c = np.clip(bx[m], 0, 1), sc[m]
        out.append([(*map(float, b[k]), float(c[k])) for k in _nms(b, c)])
    return out


def stream_frames(video: str, start: float, end: float, fps: float = SAMPLE_FPS, w: int = IN_W, h: int = IN_H,
                  chunk: int = CHUNK):
    """Frames of start..end at fps, stretched to w x h (the boxes are fractions, so they map back), as arrays of up
    to `chunk` frames: a long clip is looked at a piece at a time instead of held in memory whole."""
    n_want = max(1, int((end - start) * fps) + 1)
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, end - start):.3f}",
           "-i", video, "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=bilinear", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             creationflags=engine.CREATE_NO_WINDOW)
    except OSError:
        return
    try:
        import procjob
        procjob.adopt(p)
    except Exception:
        pass
    # a stuck read (a damaged file, a network drive gone) ends after as long as the old one-shot read allowed
    timer = threading.Timer(max(60.0, (end - start) * 3 * (w * h) / (IN_W * IN_H)), p.kill)
    timer.daemon = True
    timer.start()
    size, got, buf = w * h * 3, 0, []
    try:
        while got < n_want:
            raw = p.stdout.read(size)
            if len(raw) < size:
                break
            buf.append(np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3))
            got += 1
            if len(buf) >= chunk:
                yield np.stack(buf)
                buf = []
        if buf:
            yield np.stack(buf)
    finally:
        timer.cancel()
        try:
            p.kill()
        except OSError:
            pass
        p.wait()


def read_frames(video: str, start: float, end: float, fps: float = SAMPLE_FPS) -> np.ndarray:
    """Small frames for face finding (stretched to 320x240; the boxes are fractions, so they map back)."""
    parts = list(stream_frames(video, start, end, fps))
    return np.concatenate(parts) if parts else np.zeros((0, IN_H, IN_W, 3), dtype=np.uint8)


SPREAD_WORKERS = 4


def _spread_frame(video: str, t: float, fast: bool = True) -> Optional[np.ndarray]:
    """One double-size frame near t. Fast: the keyframe at or before t, decoded alone (the moment doesn't have to
    be exact for spotting a facecam); else the frame at t, decoded up from that keyframe (a file whose keyframes
    aren't marked gives nothing the fast way)."""
    seek = ["-noaccurate_seek", "-skip_frame", "nokey"] if fast else []
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", *seek, "-ss", f"{float(t):.3f}", "-i", video, "-frames:v", "1",
           "-an", "-sn", "-dn", "-vf", f"scale={IN_W * 2}:{IN_H * 2}:flags=bilinear", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                             creationflags=engine.CREATE_NO_WINDOW).stdout
    except Exception:
        raw = b""
    if len(raw) < IN_W * IN_H * 12:
        return _spread_frame(video, t, False) if fast else None
    return np.frombuffer(raw[: IN_W * IN_H * 12], dtype=np.uint8).reshape(IN_H * 2, IN_W * 2, 3)


def read_spread(video: str, duration: float, count: int = 24) -> np.ndarray:
    """A few frames spread across a whole video, at twice the detector's size (for spotting a facecam:
    webcam faces are small). Each FFmpeg spends most of its time opening the video (a 9-hour VOD's index is tens
    of MB) and, seeking exactly, decoding up to 2 s of frames it throws away; so a few run at once, and each
    decodes only the keyframe it lands on."""
    from concurrent.futures import ThreadPoolExecutor
    ts = [float(t) for t in np.linspace(duration * 0.05, duration * 0.95, count)]
    with ThreadPoolExecutor(SPREAD_WORKERS) as ex:
        frames = [f for f in ex.map(lambda t: _spread_frame(video, t), ts) if f is not None]
    return np.stack(frames) if frames else np.zeros((0, IN_H * 2, IN_W * 2, 3), dtype=np.uint8)


def detect_fine(big: np.ndarray, min_score: float = 0.0) -> list:
    """Faces in double-size frames: the whole frame plus each quarter, so small faces are found too."""
    whole = big[:, ::2, ::2]
    out = [list(f) for f in detect(np.ascontiguousarray(whole), min_score)]
    for qy in (0, 1):
        for qx in (0, 1):
            tile = np.ascontiguousarray(big[:, qy * IN_H:(qy + 1) * IN_H, qx * IN_W:(qx + 1) * IN_W])
            for i, faces in enumerate(detect(tile, min_score)):
                for x1, y1, x2, y2, sc in faces:
                    out[i].append(((x1 + qx) / 2, (y1 + qy) / 2, (x2 + qx) / 2, (y2 + qy) / 2, sc))
    res = []
    for faces in out:                            # the same face found twice: keep one
        if not faces:
            res.append([])
            continue
        b = np.array([f[:4] for f in faces])
        c = np.array([f[4] for f in faces])
        res.append([faces[k] for k in _nms(b, c, 0.3)])
    return res


# ---------------------------------------------------------------- following a face

def _group(faces: list, crop_frac: float) -> Optional[tuple]:
    """Two or more people of similar size who fit in the crop together: one box around them all."""
    size = lambda f: (f[2] - f[0]) * (f[3] - f[1])  # noqa: E731
    big = max(size(f) for f in faces)
    people = [f for f in faces if size(f) >= GROUP_SIZE * big]
    if len(people) < 2:
        return None
    x1, x2 = min(f[0] for f in people), max(f[2] for f in people)
    if x2 - x1 > crop_frac * GROUP_FIT:
        return None
    return (x1, min(f[1] for f in people), x2, max(f[3] for f in people), min(f[4] for f in people), "group")


def _pick(faces: list, prev: Optional[float], target: str, point: tuple, crop_frac: float = 0.0) -> Optional[tuple]:
    """The face to follow in one frame. In automatic mode, people sitting together (a duo stream,
    a guest) are framed as a group when they fit in the crop."""
    if not faces:
        return None
    cx = lambda f: (f[0] + f[2]) / 2  # noqa: E731
    size = lambda f: (f[2] - f[0]) * (f[3] - f[1])  # noqa: E731
    if target == "auto" and len(faces) > 1 and 0 < crop_frac < 1:
        g = _group(faces, crop_frac)
        if g is not None:
            return g
    if prev is not None and target in ("auto", "point"):
        near = min(faces, key=lambda f: abs(cx(f) - prev))
        if abs(cx(near) - prev) < 0.12:
            return near                          # keep following the same person
    if target == "left":
        return min(faces, key=cx)
    if target == "right":
        return max(faces, key=cx)
    if target == "point":
        return min(faces, key=lambda f: (cx(f) - point[0]) ** 2 + ((f[1] + f[3]) / 2 - point[1]) ** 2)
    return max(faces, key=size)                  # auto / largest: the biggest face is usually the streamer


def thumbs(frames: np.ndarray) -> list:
    """What cut finding keeps of each frame: a tiny grey picture and a colour histogram (a few KB, not the frame)."""
    out = []
    for f in frames:
        small = f[::8, ::8].astype(np.float32)
        hist = np.concatenate([np.histogram(small[..., c], bins=16, range=(0, 256))[0] for c in range(3)])
        out.append((small.mean(axis=2), hist / max(1.0, hist.sum() / 3)))
    return out


def _change(a, b) -> tuple:
    return float(np.abs(a[0] - b[0]).mean()), float(np.abs(a[1] - b[1]).sum() / 3)


def find_cuts(th: list) -> list:
    """Frame numbers where a new shot starts (an edited video's cuts, a stream switching scenes): the picture and
    its colours both change at once. A camera that moves or turns changes the picture, but keeps its colours."""
    if len(th) < 2:
        return []
    ch = [_change(a, b) for a, b in zip(th, th[1:])]
    pix = np.array([c[0] for c in ch])
    cuts = []
    for k, (d, hd) in enumerate(ch):
        near = np.delete(pix[max(0, k - 4):k + 5], min(k, 4))
        if d >= CUT_DIFF and hd >= CUT_HIST and d >= 2.5 * max(4.0, float(np.median(near)) if near.size else 0.0):
            cuts.append(k + 1)
    return cuts[:MAX_CUTS]


def cut_time(video: str, start: float, i: int, fps: float = SAMPLE_FPS) -> float:
    """When the cut found between looks i-1 and i really happens (seconds from start), to a frame or so: the
    crop jumps there, not up to a quarter second early or late."""
    a, b = start + (i - 1) / fps, start + i / fps
    w, h = 64, 36
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, a):.3f}", "-t", f"{b - a + 1 / CUT_FPS:.3f}",
           "-i", video, "-an", "-vf", f"fps={CUT_FPS},scale={w}:{h}:flags=bilinear", "-pix_fmt", "rgb24",
           "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                             creationflags=engine.CREATE_NO_WINDOW).stdout
    except Exception:
        raw = b""
    k = len(raw) // (w * h * 3)
    if k < 2:
        return (i - 0.5) / fps
    f = np.frombuffer(raw[: k * w * h * 3], dtype=np.uint8).reshape(k, h, w, 3).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3))
    # the new shot's first frame is read at most a step after the cut: its middle is closest
    return round(min(b, max(a, a + (int(np.argmax(d)) + 1.5) / CUT_FPS)) - start, 3)


def _steady(x: np.ndarray, fps: float) -> np.ndarray:
    """One shot's face positions as a camera would move: no single-frame jumps, no chasing small wiggles,
    smooth both ways (no lag), and never faster than MAX_SPEED."""
    n = len(x)
    k = 5
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    x = np.array([np.median(pad[i:i + k]) for i in range(n)])
    cam = np.empty(n)
    cam[0] = x[0]
    for i in range(1, n):
        d = x[i] - cam[i - 1]
        cam[i] = cam[i - 1] + (d - np.sign(d) * DEADZONE if abs(d) > DEADZONE else 0.0)
    a = 0.25
    for rng in (range(1, n), range(n - 2, -1, -1)):
        for i in rng:
            j = i - 1 if rng.step == 1 else i + 1
            cam[i] = cam[j] + a * (cam[i] - cam[j])
    step = MAX_SPEED / fps
    for i in range(1, n):
        cam[i] = cam[i - 1] + float(np.clip(cam[i] - cam[i - 1], -step, step))
    return cam


def follow(detections: list, fps: float = SAMPLE_FPS, target: str = "auto", point: tuple = (0.5, 0.4),
           fallback: float = 0.5, crop_frac: float = 0.3164, cuts: tuple = ()) -> dict:
    """Turn per-frame faces into a smooth path for the crop's centre.
    crop_frac: crop width as a fraction of the source width (how much of the frame shows).
    cuts: seconds where a new shot starts. The crop jumps there, as the video does, instead of panning across
    (a face in the new shot was out of the crop for a second or two), and each shot is framed on its own.
    Returns {"path": [(t, x)], "confidence": 0-1, "coverage", "face_y", "face_h", "reason", "cuts"}."""
    n = len(detections)
    cx = lambda f: (f[0] + f[2]) / 2  # noqa: E731
    cuts = sorted({round(float(t), 3) for t in cuts if 0 < float(t) * fps < n - 0.5})
    first_of = {min(n - 1, max(1, int(np.ceil(t * fps - 1e-6)))): t for t in cuts}
    starts = sorted({0, *first_of})
    raw = np.full(n, np.nan)
    ys, hs, ws = [], [], []
    prev = None
    away, switch, groups = 0, max(1, int(SWITCH_S * fps)), 0
    for i, faces in enumerate(detections):
        if i in first_of:
            prev, away = None, 0             # a new shot: whoever is in it, from its first frame
        # a weak detection only keeps the face already followed (a turned head, a hand in front)
        faces = [f for f in faces if f[4] >= MIN_SCORE or (prev is not None and abs(cx(f) - prev) < 0.12)]
        f = _pick(faces, prev, target, point, crop_frac)
        if f is not None and prev is not None and abs(cx(f) - prev) >= 0.12:
            # someone else: only move over once the person followed has really gone (not when
            # they're missed for a moment while a second person is in view)
            away += 1
            f = f if away >= switch else None
        if f is not None:
            away = 0
            groups += len(f) > 5
            raw[i] = prev = cx(f)
            ys.append((f[1] + f[3]) / 2)
            hs.append(f[3] - f[1])
            ws.append(f[2] - f[0])
    have = ~np.isnan(raw)
    cov = float(have.mean()) if n else 0.0
    out = {"coverage": round(cov, 2), "face_y": round(float(np.median(ys)), 3) if ys else None,
           "face_h": round(float(np.median(hs)), 3) if hs else None, "cuts": len(starts) - 1}
    if n == 0 or cov < MIN_COVERAGE:
        return dict(out, path=[], confidence=round(cov, 2),
                    reason="No face found" if cov == 0 else "Face only seen now and then, so the crop stays put")
    if np.median(ws) < 0.07 and float(np.ptp(raw[have])) < 0.06:
        # a small face that never moves is a facecam overlay: following it would crop to the corner
        return dict(out, path=[], confidence=round(cov, 2), reason="Only a facecam face, so the crop stays put")
    half = crop_frac / 2
    hold = int(HOLD_S * fps)
    every = max(1, int(round(fps / 2)))       # keyframes every half second: the crop moves in straight lines between
    path, jit = [], []
    for s, a in enumerate(starts):
        b = starts[s + 1] if s + 1 < len(starts) else n
        seg, got = raw[a:b], have[a:b]
        if got.any():
            # fill gaps: hold the last position for a while, then ease back to your focus point
            x = seg.copy()
            last, gap, first = None, 0, float(seg[got][0])
            for i in range(len(x)):
                if got[i]:
                    last, gap = seg[i], 0
                    continue
                gap += 1
                base = last if last is not None else first
                x[i] = base if gap <= hold else base + (fallback - base) * min(1.0, (gap - hold) / (fps * 2))
            cam = _steady(x, fps)
            if got.sum() > 2:
                jit.extend(np.abs(np.diff(seg[got])).tolist())
        else:
            cam = np.full(b - a, fallback)    # a shot with nobody in it: your focus point
        cam = np.clip(cam, half, 1 - half) if crop_frac < 1 else np.full(b - a, 0.5)
        if s and path:
            t = first_of[a]
            if t - 0.02 > path[-1][0]:
                path.append((round(t - 0.02, 3), path[-1][1]))     # the old shot's framing up to the cut ...
            path.append((round(t, 3), round(float(cam[0]), 4)))   # ... and the new one's from it
        idx = list(range(a, b, every)) + ([b - 1] if (b - 1 - a) % every else [])
        for i in idx:
            t = round(i / fps, 3)
            if not path or t > path[-1][0]:
                path.append((t, round(float(cam[i - a]), 4)))
    jitter = float(np.median(jit)) if jit else 0.0
    conf = cov * float(np.clip(1 - jitter * 10, 0.5, 1.0))     # a face that jumps around is less certain
    reason = "Keeping both people in frame" if groups > 0.5 * have.sum() else "Following the face"
    if len(starts) > 1:
        reason += f", cutting with the video ({len(starts) - 1} cut{'s' if len(starts) > 2 else ''})"
    return dict(out, path=path, confidence=round(conf, 2), reason=reason)


# FFmpeg's expression parser refuses expressions nested deeper than about 100 levels (FFmpeg 8 and later;
# 6.1 had no limit), and the crop that uses one then fails to configure. One term per keyframe in a flat
# sum is one level per keyframe: a face path over about 48 s (a keyframe every 0.5 s) went past it, and the
# clip fell back to blur-fit. x_expr picks the segment with a balanced if() tree instead: log2(keyframes)
# levels, so even an hour's path stays far below the limit.
EXPR_MAX_DEPTH = 40


def clean_path(path) -> list:
    """The keyframes of a face path that can be used: (t, centre) pairs of finite numbers, in time order,
    one per time (a repeated time keeps the later keyframe), centres 0..1. Anything else (a stale or
    hand-edited cache, None, NaN, a short entry) is dropped."""
    pts = {}
    for p in path or []:
        try:
            t, c = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if np.isfinite(t) and np.isfinite(c):
            pts[round(t, 3)] = min(max(c, 0.0), 1.0)
    return sorted(pts.items())


def x_expr(path: list, sw: int, cw: int) -> Optional[str]:
    """ffmpeg expression for the crop's left edge over time, from (t, centre) keyframes: straight lines
    between keyframes, held before the first and after the last, always inside 0..sw-cw.
    None when the path has no usable keyframe."""
    hi = max(0, sw - cw)
    pts = [(t, round(min(max(c * sw - cw / 2, 0.0), hi), 1)) for t, c in clean_path(path)]
    if not pts:
        return None
    if len(pts) == 1 or all(x == pts[0][1] for _, x in pts):
        return str(int(pts[0][1]))

    def seg(i: int) -> str:
        (t0, a), (t1, b) = pts[i], pts[i + 1]
        return f"{a:.1f}" if a == b else f"{a:.1f}+{(b - a) / (t1 - t0):.4f}*(t-{t0:.3f})"

    def pick(lo: int, n: int) -> str:
        """The segment for t among segments lo..lo+n-1 (t is already inside their span)."""
        if n == 1:
            return seg(lo)
        mid = lo + n // 2
        return f"if(lt(t,{pts[mid][0]:.3f}),{pick(lo, mid - lo)},{pick(mid, lo + n - mid)})"

    body = (f"if(lt(t,{pts[0][0]:.3f}),{pts[0][1]:.1f},if(gte(t,{pts[-1][0]:.3f}),{pts[-1][1]:.1f},"
            f"{pick(0, len(pts) - 1)}))")
    return f"'clip({body},0,{hi})'"


def expr_depth(expr: str) -> int:
    """How deeply one of x_expr's expressions nests: its brackets (its sums are of two terms, so they add no
    levels of their own the way a long flat sum does)."""
    d = top = 0
    for ch in expr:
        d += ch == "("
        d -= ch == ")"
        top = max(top, d)
    return top


def path_at(path: list, t: float) -> Optional[float]:
    if not path:
        return None
    if t <= path[0][0]:
        return path[0][1]
    for (t0, a), (t1, b) in zip(path, path[1:]):
        if t0 <= t < t1:
            return a + (b - a) * (t - t0) / (t1 - t0)
    return path[-1][1]


def shift(path: list, offset: float, length: float) -> list:
    """The part of a path from offset to offset + length, with times starting at 0."""
    path = clean_path(path)
    if not path:
        return []
    ts = [0.0] + [t - offset for t, _ in path if 0 < t - offset < length] + [length]
    return [(round(t, 3), round(float(path_at(path, t + offset)), 4)) for t in ts]


def crop_frac(info: dict, o) -> float:
    """How much of the source width a full-frame crop shows, for this shape and zoom."""
    W, H = engine.out_size(o)
    sw, sh = info["width"], info["height"]
    return min(1.0, min(sw, sh * W / H) / max(1.0, float(getattr(o, "game_zoom", 1.0))) / sw)


def track(video: str, start: float, end: float, info: dict, o, cache_dir: str = "", log=lambda m: None) -> dict:
    """Follow the face through start..end for a full-frame crop. Cached next to the video."""
    if not available():
        return {"path": [], "confidence": 0.0, "reason": "Face finding isn't available"}
    target = getattr(o, "track_target", "auto")
    point = (float(getattr(o, "game_x", 0.5)), float(getattr(o, "focus_y", 0.4)))
    cf = crop_frac(info, o)
    key = hashlib.sha1(json.dumps([os.path.basename(video), os.path.getsize(video) if os.path.exists(video) else 0,
                                   round(start, 2), round(end, 2), target, point, round(cf, 3), 4]).encode()).hexdigest()[:16]
    cpath = os.path.join(cache_dir, f"track_{key}.json") if cache_dir else ""
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    dets, th = [], []
    for frames in stream_frames(video, start, end):
        dets += detect(frames, KEEP_SCORE)
        th += thumbs(frames)
    cuts = [cut_time(video, start, i) for i in find_cuts(th)]
    seen = lambda ds: sum(1 for d in ds if any(f[4] >= MIN_SCORE for f in d)) / max(1, len(ds))  # noqa: E731
    looks = "first look"
    if dets and seen(dets) < FINE_BELOW:
        # Faces under about an eighth of the picture's height (someone standing back, a wide shot) are too small at
        # 320x240 (measured: 1 to 3 of 16 frames with a face 90 px tall in 1080p, 15 to 16 looking at each quarter at
        # twice the size). That's about 5x the work, so only when the first look missed the face in some frames.
        fine = []
        for big in stream_frames(video, start, end, w=IN_W * 2, h=IN_H * 2):
            fine += detect_fine(big, KEEP_SCORE)
        if len(fine) == len(dets) and seen(fine) > seen(dets):
            dets, looks = fine, "looked again closer"
    res = follow(dets, SAMPLE_FPS, target, point, fallback=point[0], crop_frac=cf, cuts=cuts)
    log(f"Framing: {res['reason'].lower()} ({int(res['coverage'] * 100)}% of frames, {looks})")
    if cpath:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            from persist import write_json
            write_json(cpath, res)
        except OSError:
            pass
    return res


# ---------------------------------------------------------------- facecam and layout

def find_facecam(detections: list) -> Optional[dict]:
    """A webcam overlay: a smallish face that sits in the same place across the video."""
    pts = [max(fs, key=lambda f: f[4]) for fs in detections if fs]
    if len(pts) < max(4, len(detections) * 0.4):
        return None
    b = np.array([p[:4] for p in pts])
    cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
    w, h = b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]
    mx, my = float(np.median(cx)), float(np.median(cy))
    still = (np.abs(cx - mx) < 0.04) & (np.abs(cy - my) < 0.05)
    if still.mean() < 0.7 or float(np.median(w)) > 0.2:
        return None
    fw, fh = float(np.median(w[still])), float(np.median(h[still]))
    # a webcam frame shows head and shoulders: about 3x the face across, 2.6x down
    cw, ch = min(1.0, fw * 3.0), min(1.0, fh * 2.6)
    x, y = mx - cw / 2, my - ch * 0.42
    # overlays sit against an edge: snap when close
    x = 0.0 if x < 0.03 else (1 - cw if x + cw > 0.97 else x)
    y = 0.0 if y < 0.03 else (1 - ch if y + ch > 0.97 else y)
    x, y = min(max(x, 0.0), 1 - cw), min(max(y, 0.0), 1 - ch)
    return {"cam": [round(x, 4), round(y, 4), round(cw, 4), round(ch, 4)], "seen": round(len(pts) / len(detections), 2),
            "steady": round(float(still.mean()), 2)}


_suggest_locks: dict = {}


def suggest(video: str, duration: float, cache_dir: str = "") -> dict:
    """What layout fits this video: a facecam in a corner (Cam + game), a person filling the frame
    (follow the face), or neither (whole frame). Cached next to the video. Opening a video starts this in the
    background (warm), so pressing Auto-frame usually finds it done; one look per video at a time."""
    cpath = os.path.join(cache_dir, "framing.json") if cache_dir else ""
    with _lock:
        lk = _suggest_locks.setdefault(cpath or video, threading.Lock())
    with lk:
        return _suggest(video, duration, cpath)


def _suggest(video: str, duration: float, cpath: str) -> dict:
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("v") == 2:
                return d
        except (OSError, ValueError):
            pass
    if not available():
        return {"v": 2, "layout": "", "reason": "Face finding isn't available", "confidence": 0.0}
    dets = detect_fine(read_spread(video, duration))
    seen = sum(1 for d in dets if d) / max(1, len(dets))
    cam = find_facecam(dets)
    if cam:
        res = {"v": 2, "layout": "split", "cam": cam["cam"], "track": "off", "confidence": cam["steady"],
               "reason": "Found a facecam: camera on top, gameplay below"}
    elif seen >= 0.5:
        big = [max(d, key=lambda f: (f[2] - f[0])) for d in dets if d]
        res = {"v": 2, "layout": "full", "track": "auto", "confidence": round(seen, 2),
               "face_w": round(float(np.median([f[2] - f[0] for f in big])), 3),
               "face_x": round(float(np.median([(f[0] + f[2]) / 2 for f in big])), 3),
               "face_y": round(float(np.median([(f[1] + f[3]) / 2 for f in big])), 3),
               "reason": "A person fills the frame: following their face"}
    else:
        res = {"v": 2, "layout": "full", "track": "off", "confidence": round(1 - seen, 2),
               "reason": "No face on screen: showing the middle of the frame"}
    res["faces_seen"] = round(seen, 2)
    if cpath:
        try:
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            from persist import write_json
            write_json(cpath, res)
        except OSError:
            pass
    return res


DEFAULT_CAM = (0.0, 0.0, 0.25, 0.25)       # RenderOpts.cam: the box nobody has moved


def _is_default_cam(cam) -> bool:
    try:
        return len(cam) == 4 and all(abs(float(a) - b) < 1e-6 for a, b in zip(cam, DEFAULT_CAM))
    except (TypeError, ValueError):
        return False


def untouched(layout: str, cam=None, layout_auto: bool = False) -> bool:
    """Nobody chose the layout: Auto-frame picked it before, or it's still Cam + game with the box in its
    default corner (which on a stream without a facecam shows the chat or a corner of the game)."""
    return bool(layout_auto) or (layout == "split" and _is_default_cam(cam))


def fit(r: dict, layout: str, cam=None, picked: bool = True, auto: bool = False, aspect: str = "9:16") -> dict:
    """What Auto-frame changes, for the layout you're on: {"set": {option: value}, "note": what it did}.
    picked: you chose this layout (Layout › Cam + game, Whole frame, ...); otherwise Auto-frame picks the layout too.
    auto: run by itself on opening a video, not by pressing the button. Then it only fills in what nobody set:
    a facecam box still in its default corner. A box you drew, Follow face turned off, or a focus point you
    moved stay as they are.
    Before 4.42 Auto-frame always switched to Cam + game or Whole frame, so on Cam only, Blur fit or Square blur
    it threw your layout away, and opening a new video only framed it when you were on Cam + game."""
    if not r.get("layout"):
        return {"set": {}, "note": r.get("reason") or "Nothing to suggest."}
    sure = "" if float(r.get("confidence", 1)) >= 0.6 else " (not sure: check it)"
    found_cam, person = r.get("cam"), r.get("layout") == "full" and r.get("track") == "auto"
    if not picked:
        out = {"layout": r["layout"], "layout_auto": True}
        if found_cam:
            out.update(cam=found_cam, cams=[])
        if r.get("track"):
            out["track"] = r["track"]
        if r["layout"] == "split" and aspect == "16:9":
            out["aspect"] = "9:16"           # Cam + game needs a tall frame
        return {"set": out, "note": r.get("reason", "") + sure}
    if layout in ("split", "cam"):
        if found_cam:
            if auto and not _is_default_cam(cam):
                return {"set": {}, "note": "Kept the facecam box you drew."}
            return {"set": {"cam": found_cam, "cams": []}, "note": "Found the facecam: boxed it" + sure}
        tip = ": Whole frame can follow the person instead" if person else ""
        return {"set": {}, "note": "No facecam found, so the box stays where it is" + tip}
    if layout == "full":
        if person:
            return {"set": {} if auto else {"track": "auto"}, "note": "Following the person's face" + sure}
        if found_cam:
            return {"set": {}, "note": "Only a facecam on screen, so the crop stays at the focus point: Cam + game shows it on top"}
        return {"set": {}, "note": "No face on screen, so the crop stays at the focus point"}
    if layout in ("blur", "square"):
        what = "square" if layout == "square" else "picture"
        if person and r.get("face_x") is not None:
            if auto:
                return {"set": {}, "note": ""}
            return {"set": {"game_x": round(min(max(float(r["face_x"]), 0.0), 1.0), 3)},
                    "note": f"Centred the {what} on the person" + sure}
        if found_cam:
            return {"set": {}, "note": "Found a facecam: Cam + game or Cam only show it bigger"}
        return {"set": {}, "note": f"No person on screen, so the {what} stays at the focus point"}
    return {"set": {}, "note": r.get("reason", "")}


def caption_y(face_y: Optional[float], face_h: Optional[float], H: int, default: float) -> float:
    """Captions that would cover the face move below it, or above it if there's no room."""
    if face_y is None or face_h is None:
        return default
    top, bot = (face_y - face_h * 0.7) * H, (face_y + face_h * 0.9) * H
    if top - 140 <= default <= bot + 140:
        below = bot + 230
        return below if below <= H * 0.86 else max(260.0, top - 230)
    return default
