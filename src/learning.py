"""
Learns what you like, per streamer.

Every time you keep, skip or post a suggested clip, VodClipper remembers what
that moment's signals looked like (chat surge, loudness, laughter, screams,
cheering, speech, length, "clip it" requests, how far into the stream). Once
there are enough decisions it fits a small logistic model per streamer and
nudges the ranking: if you always keep the moments where the streamer laughs
and skip the loud-but-empty ones, laughter moves up and loudness moves down.

Stored in learning.json in VodClipper's data folder. Nothing leaves the PC.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Optional

import numpy as np

import paths

FEATURES = ("chat", "audio", "laugh", "scream", "cheer", "speech", "length", "clipreq", "pos", "emotes")
MAX_SAMPLES = 600
MIN_DECISIONS = 8          # before this many keeps + skips, ranking isn't changed
FULL_TRUST = 40            # decisions needed for the full effect
MAX_SHIFT = 2.5            # most a clip can move, in score points (strong moments score 3 to 8)
ALL = "_all"               # every streamer together, used until one streamer has enough

_lock = threading.RLock()     # re-entrant: summary() asks trim_offsets() while holding it
_cache: dict = {}


def _path() -> str:
    return os.path.join(str(paths.DATA_DIR), "learning.json")


def _load() -> dict:
    if _cache.get("data") is not None:
        return _cache["data"]
    data = {"enabled": True, "profiles": {}}
    try:
        with open(_path(), encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            data.update(got)
    except (OSError, ValueError):
        pass
    _cache["data"] = data
    return data


def _save(data: dict):
    tmp = _path() + ".tmp"
    try:
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _path())
    except OSError:
        pass


def enabled() -> bool:
    with _lock:
        return bool(_load().get("enabled", True))


def set_enabled(on: bool):
    with _lock:
        d = _load()
        d["enabled"] = bool(on)
        _save(d)


def _name(streamer: str) -> str:
    return (streamer or "").strip().lower() or "unknown"


def record(streamer: str, key: str, feat: Optional[dict], action: str):
    """action: keep | skip | post | undo (forget the decision for this clip)."""
    if not feat or not key:
        return
    with _lock:
        d = _load()
        for prof in {_name(streamer), ALL}:
            p = d["profiles"].setdefault(prof, {"samples": [], "updated": 0})
            p["samples"] = [s for s in p["samples"] if s.get("k") != key]
            if action in ("keep", "skip", "post"):
                y = 0.0 if action == "skip" else 1.0
                w = 2.0 if action == "post" else 1.0
                p["samples"].append({"k": key, "f": {k: float(feat.get(k, 0.0)) for k in FEATURES},
                                     "y": y, "w": w, "t": time.time()})
                p["samples"] = p["samples"][-MAX_SAMPLES:]
            p["updated"] = time.time()
            p.pop("model", None)
        _save(d)


def _fit(samples: list) -> Optional[dict]:
    ys = np.array([s["y"] for s in samples], dtype=np.float64)
    if len(samples) < MIN_DECISIONS or ys.sum() < 2 or (1 - ys).sum() < 2:
        return None
    X = np.array([[s["f"].get(k, 0.0) for k in FEATURES] for s in samples], dtype=np.float64)
    sw = np.array([s.get("w", 1.0) for s in samples], dtype=np.float64)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1])
    b = 0.0
    lam = 1.0                                   # keeps a handful of decisions from swinging things wildly
    lr = 0.5
    for _ in range(300):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = (p - ys) * sw
        gw = Z.T @ g / sw.sum() + lam * w / len(ys)
        gb = g.sum() / sw.sum()
        w -= lr * gw
        b -= lr * gb
    return {"w": w.tolist(), "b": float(b), "mu": mu.tolist(), "sd": sd.tolist(), "n": len(samples),
            "keep_rate": float(ys.mean())}


def _model(streamer: str) -> tuple:
    """(model, profile name actually used) or (None, '')."""
    d = _load()
    for prof in (_name(streamer), ALL):
        p = d["profiles"].get(prof)
        if not p:
            continue
        if "model" not in p:
            p["model"] = _fit(p["samples"])
        if p["model"]:
            return p["model"], prof
    return None, ""


def adjuster(streamer: str):
    """A function features -> score shifts for pick_clips, or None while there's nothing learned."""
    with _lock:
        if not _load().get("enabled", True):
            return None
        m, prof = _model(streamer)
    if not m:
        return None
    trust = min(1.0, m["n"] / FULL_TRUST) * (1.0 if prof != ALL else 0.6)
    w, b, mu, sd = np.array(m["w"]), m["b"], np.array(m["mu"]), np.array(m["sd"])

    def adjust(feats: list) -> list:
        X = np.array([[f.get(k, 0.0) for k in FEATURES] for f in feats], dtype=np.float64)
        z = ((X - mu) / sd) @ w + b
        p = 1 / (1 + np.exp(-z))
        # centre on this streamer's usual keep rate, so learning reorders rather than inflates
        base = min(0.9, max(0.1, m["keep_rate"]))
        return [round(float(trust * MAX_SHIFT * math.tanh(2.2 * (pi - base))), 3) for pi in p]
    return adjust


REASONS = {"boring": "Not interesting", "cut": "Bad start or end", "duplicate": "Same as another clip",
           "noise": "Just a loud noise", "other": "Something else"}
TRIM_MIN = 5          # trims needed before clips start shifting
TRIM_MAX_SHIFT = 6.0  # seconds


def _stats(p: dict) -> dict:
    return p.setdefault("stats", {"reasons": {}, "added": 0, "trims": []})


def record_reason(streamer: str, reason: str):
    """Why you skipped a clip (optional, from the buttons after Skip)."""
    if reason not in REASONS:
        return
    with _lock:
        d = _load()
        for prof in {_name(streamer), ALL}:
            st = _stats(d["profiles"].setdefault(prof, {"samples": [], "updated": 0}))
            st["reasons"][reason] = st["reasons"].get(reason, 0) + 1
        _save(d)


def record_added(streamer: str):
    """You made a clip by hand: a moment the finder missed."""
    with _lock:
        d = _load()
        for prof in {_name(streamer), ALL}:
            st = _stats(d["profiles"].setdefault(prof, {"samples": [], "updated": 0}))
            st["added"] = st.get("added", 0) + 1
        _save(d)


def record_trim(streamer: str, key: str, d_start: float, d_end: float):
    """How far you moved a clip's start and end before keeping it (negative = earlier)."""
    if abs(d_start) > 60 or abs(d_end) > 60:
        return
    with _lock:
        d = _load()
        for prof in {_name(streamer), ALL}:
            st = _stats(d["profiles"].setdefault(prof, {"samples": [], "updated": 0}))
            st["trims"] = [t for t in st.get("trims", []) if t.get("k") != key][-59:]
            st["trims"].append({"k": key, "s": round(float(d_start), 2), "e": round(float(d_end), 2)})
        _save(d)


def trim_offsets(streamer: str) -> dict:
    """The start/end shift learned from your trims ({start, end, n}); zero until there are enough."""
    with _lock:
        d = _load()
        if not d.get("enabled", True):
            return {"start": 0.0, "end": 0.0, "n": 0}
        p = d["profiles"].get(_name(streamer)) or {}
        tr = (p.get("stats") or {}).get("trims", [])[-30:]
    if len(tr) < TRIM_MIN:
        return {"start": 0.0, "end": 0.0, "n": len(tr)}
    ms = float(np.median([t["s"] for t in tr]))
    me = float(np.median([t["e"] for t in tr]))
    clip = lambda v: 0.0 if abs(v) < 0.5 else max(-TRIM_MAX_SHIFT, min(TRIM_MAX_SHIFT, round(v, 1)))  # noqa: E731
    return {"start": clip(ms), "end": clip(me), "n": len(tr)}


def describe(streamer: str) -> dict:
    """For the UI: is ranking tuned for this streamer, and from how many decisions."""
    with _lock:
        d = _load()
        on = bool(d.get("enabled", True))
        p = d["profiles"].get(_name(streamer)) or {}
        n = len(p.get("samples", []))
        m, prof = _model(streamer) if on else (None, "")
    return {"enabled": on, "streamer": _name(streamer) if streamer else "", "decisions": n,
            "active": bool(m), "using": "this streamer" if prof and prof != ALL else ("all your picks" if prof else ""),
            "needed": max(0, MIN_DECISIONS - n), "trim": trim_offsets(streamer)}


def summary() -> dict:
    with _lock:
        d = _load()
        profs = []
        for name, p in d["profiles"].items():
            if name == ALL:
                continue
            s = p.get("samples", [])
            st = p.get("stats") or {}
            kept = int(sum(1 for x in s if x["y"] > 0))
            reasons = sorted((st.get("reasons") or {}).items(), key=lambda kv: -kv[1])[:3]
            profs.append({"streamer": name, "decisions": len(s), "kept": kept,
                          "posted": int(sum(1 for x in s if x.get("w", 1) > 1)),
                          "accept": round(100 * kept / len(s)) if s else None,
                          "added": int(st.get("added", 0)), "trim": trim_offsets(name),
                          "reasons": [{"key": k, "label": REASONS.get(k, k), "n": v} for k, v in reasons],
                          "updated": p.get("updated", 0)})
        profs.sort(key=lambda x: -x["updated"])
        total = len(d["profiles"].get(ALL, {}).get("samples", []))
        return {"enabled": bool(d.get("enabled", True)), "profiles": profs, "total": total,
                "min_decisions": MIN_DECISIONS}


def forget(streamer: str = ""):
    """Forget one streamer (or everything when streamer is empty)."""
    with _lock:
        d = _load()
        if not streamer:
            d["profiles"] = {}
        else:
            name = _name(streamer)
            gone = {s["k"] for s in d["profiles"].pop(name, {}).get("samples", [])}
            allp = d["profiles"].get(ALL)
            if allp:
                allp["samples"] = [s for s in allp["samples"] if s["k"] not in gone]
                allp.pop("model", None)
        _save(d)
