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


def detect(frames: np.ndarray) -> list:
    """Faces in frames (N x 240 x 320 x 3, RGB uint8). Per frame: [(x1, y1, x2, y2, score)] as
    fractions of the frame."""
    s = _sess()
    if s is None or not len(frames):
        return [[] for _ in range(len(frames))]
    x = ((frames.astype(np.float32) - 127.0) / 128.0).transpose(0, 3, 1, 2)
    out = []
    for i in range(0, len(x), 8):
        batch = x[i:i + 8]
        for one in batch:                       # the model takes one frame at a time
            sc, bx = s.run(None, {"input": one[None]})
            sc, bx = sc[0, :, 1], bx[0]
            m = sc >= MIN_SCORE
            if not m.any():
                out.append([])
                continue
            b, c = np.clip(bx[m], 0, 1), sc[m]
            out.append([(*map(float, b[k]), float(c[k])) for k in _nms(b, c)])
    return out


def read_frames(video: str, start: float, end: float, fps: float = SAMPLE_FPS) -> np.ndarray:
    """Small frames for face finding (stretched to 320x240; the boxes are fractions, so they map back)."""
    n_want = max(1, int((end - start) * fps) + 1)
    cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, end - start):.3f}",
           "-i", video, "-an", "-vf", f"fps={fps},scale={IN_W}:{IN_H}:flags=bilinear", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=max(60, (end - start) * 3), stdin=subprocess.DEVNULL,
                           creationflags=engine.CREATE_NO_WINDOW)
        raw = p.stdout
    except Exception:
        raw = b""
    k = len(raw) // (IN_W * IN_H * 3)
    return np.frombuffer(raw[: k * IN_W * IN_H * 3], dtype=np.uint8).reshape(k, IN_H, IN_W, 3)[:n_want]


def read_spread(video: str, duration: float, count: int = 24) -> np.ndarray:
    """A few frames spread across a whole video, at twice the detector's size (for spotting a facecam:
    webcam faces are small)."""
    frames = []
    for t in np.linspace(duration * 0.05, duration * 0.95, count):
        cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{float(t):.3f}", "-i", video, "-frames:v", "1", "-an",
               "-vf", f"scale={IN_W * 2}:{IN_H * 2}:flags=bilinear", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        try:
            raw = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                                 creationflags=engine.CREATE_NO_WINDOW).stdout
        except Exception:
            raw = b""
        if len(raw) >= IN_W * IN_H * 12:
            frames.append(np.frombuffer(raw[: IN_W * IN_H * 12], dtype=np.uint8).reshape(IN_H * 2, IN_W * 2, 3))
    return np.stack(frames) if frames else np.zeros((0, IN_H * 2, IN_W * 2, 3), dtype=np.uint8)


def detect_fine(big: np.ndarray) -> list:
    """Faces in double-size frames: the whole frame plus each quarter, so small faces are found too."""
    whole = big[:, ::2, ::2]
    out = [list(f) for f in detect(np.ascontiguousarray(whole))]
    for qy in (0, 1):
        for qx in (0, 1):
            tile = np.ascontiguousarray(big[:, qy * IN_H:(qy + 1) * IN_H, qx * IN_W:(qx + 1) * IN_W])
            for i, faces in enumerate(detect(tile)):
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


def follow(detections: list, fps: float = SAMPLE_FPS, target: str = "auto", point: tuple = (0.5, 0.4),
           fallback: float = 0.5, crop_frac: float = 0.3164) -> dict:
    """Turn per-frame faces into a smooth path for the crop's centre.
    crop_frac: crop width as a fraction of the source width (how much of the frame shows).
    Returns {"path": [(t, x)], "confidence": 0-1, "coverage", "face_y", "face_h", "reason"}."""
    n = len(detections)
    raw = np.full(n, np.nan)
    ys, hs, ws = [], [], []
    prev = None
    away, switch, groups = 0, max(1, int(SWITCH_S * fps)), 0
    for i, faces in enumerate(detections):
        f = _pick(faces, prev, target, point, crop_frac)
        if f is not None and prev is not None and abs((f[0] + f[2]) / 2 - prev) >= 0.12:
            # someone else: only move over once the person followed has really gone (not when
            # they're missed for a moment while a second person is in view)
            away += 1
            f = f if away >= switch else None
        if f is not None:
            away = 0
            groups += len(f) > 5
            raw[i] = prev = (f[0] + f[2]) / 2
            ys.append((f[1] + f[3]) / 2)
            hs.append(f[3] - f[1])
            ws.append(f[2] - f[0])
    have = ~np.isnan(raw)
    cov = float(have.mean()) if n else 0.0
    out = {"coverage": round(cov, 2), "face_y": round(float(np.median(ys)), 3) if ys else None,
           "face_h": round(float(np.median(hs)), 3) if hs else None}
    if n == 0 or cov < MIN_COVERAGE:
        return dict(out, path=[], confidence=round(cov, 2),
                    reason="No face found" if cov == 0 else "Face only seen now and then, so the crop stays put")
    if np.median(ws) < 0.07 and float(np.ptp(raw[have])) < 0.06:
        # a small face that never moves is a facecam overlay: following it would crop to the corner
        return dict(out, path=[], confidence=round(cov, 2), reason="Only a facecam face, so the crop stays put")
    # fill gaps: hold the last position for a while, then ease back to your focus point
    hold = int(HOLD_S * fps)
    x = raw.copy()
    last, gap = None, 0
    first = float(raw[have][0])
    for i in range(n):
        if have[i]:
            last, gap = raw[i], 0
            continue
        gap += 1
        base = last if last is not None else first
        x[i] = base if gap <= hold else base + (fallback - base) * min(1.0, (gap - hold) / (fps * 2))
    # steady: a median knocks out single-frame jumps (a second face, a false detection)
    k = 5
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    x = np.array([np.median(pad[i:i + k]) for i in range(n)])
    # don't chase small wiggles: the crop only moves once the face drifts out of a middle band
    cam = np.empty(n)
    cam[0] = x[0]
    for i in range(1, n):
        d = x[i] - cam[i - 1]
        cam[i] = cam[i - 1] + (d - np.sign(d) * DEADZONE if abs(d) > DEADZONE else 0.0)
    # smooth both ways (no lag), then cap the speed so it never whips across
    a = 0.25
    for rng in (range(1, n), range(n - 2, -1, -1)):
        for i in rng:
            j = i - 1 if rng.step == 1 else i + 1
            cam[i] = cam[j] + a * (cam[i] - cam[j])
    step = MAX_SPEED / fps
    for i in range(1, n):
        cam[i] = cam[i - 1] + float(np.clip(cam[i] - cam[i - 1], -step, step))
    half = crop_frac / 2
    cam = np.clip(cam, half, 1 - half) if crop_frac < 1 else np.full(n, 0.5)
    # keyframes every half second are plenty: the crop moves in straight lines between them
    every = max(1, int(round(fps / 2)))
    idx = list(range(0, n, every)) + ([n - 1] if (n - 1) % every else [])
    path = [(round(i / fps, 2), round(float(cam[i]), 4)) for i in idx]
    jitter = float(np.median(np.abs(np.diff(raw[have])))) if have.sum() > 2 else 0.0
    conf = cov * float(np.clip(1 - jitter * 10, 0.5, 1.0))     # a face that jumps around is less certain
    reason = "Keeping both people in frame" if groups > 0.5 * have.sum() else "Following the face"
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
    return [(round(t, 2), round(float(path_at(path, t + offset)), 4)) for t in ts]


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
                                   round(start, 2), round(end, 2), target, point, round(cf, 3), 3]).encode()).hexdigest()[:16]
    cpath = os.path.join(cache_dir, f"track_{key}.json") if cache_dir else ""
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    frames = read_frames(video, start, end)
    res = follow(detect(frames), SAMPLE_FPS, target, point, fallback=point[0], crop_frac=cf)
    log(f"Framing: {res['reason'].lower()} ({int(res['coverage'] * 100)}% of frames)")
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


def suggest(video: str, duration: float, cache_dir: str = "") -> dict:
    """What layout fits this video: a facecam in a corner (Cam + game), a person filling the frame
    (follow the face), or neither (whole frame). Cached next to the video."""
    cpath = os.path.join(cache_dir, "framing.json") if cache_dir else ""
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("v") == 1:
                return d
        except (OSError, ValueError):
            pass
    if not available():
        return {"v": 1, "layout": "", "reason": "Face finding isn't available", "confidence": 0.0}
    dets = detect_fine(read_spread(video, duration))
    seen = sum(1 for d in dets if d) / max(1, len(dets))
    cam = find_facecam(dets)
    if cam:
        res = {"v": 1, "layout": "split", "cam": cam["cam"], "track": "off", "confidence": cam["steady"],
               "reason": "Found a facecam: camera on top, gameplay below"}
    elif seen >= 0.5:
        big = [max(d, key=lambda f: (f[2] - f[0])) for d in dets if d]
        res = {"v": 1, "layout": "full", "track": "auto", "confidence": round(seen, 2),
               "face_w": round(float(np.median([f[2] - f[0] for f in big])), 3),
               "reason": "A person fills the frame: following their face"}
    else:
        res = {"v": 1, "layout": "full", "track": "off", "confidence": round(1 - seen, 2),
               "reason": "No face on screen: showing the middle of the frame"}
    res["faces_seen"] = round(seen, 2)
    if cpath:
        try:
            from persist import write_json
            write_json(cpath, res)
        except OSError:
            pass
    return res


def caption_y(face_y: Optional[float], face_h: Optional[float], H: int, default: float) -> float:
    """Captions that would cover the face move below it, or above it if there's no room."""
    if face_y is None or face_h is None:
        return default
    top, bot = (face_y - face_h * 0.7) * H, (face_y + face_h * 0.9) * H
    if top - 140 <= default <= bot + 140:
        below = bot + 230
        return below if below <= H * 0.86 else max(260.0, top - 230)
    return default
