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

FEATURES = ("chat", "audio", "laugh", "scream", "cheer", "speech", "length", "clipreq", "pos")
MAX_SAMPLES = 600
MIN_DECISIONS = 8          # before this many keeps + skips, ranking isn't changed
FULL_TRUST = 40            # decisions needed for the full effect
MAX_SHIFT = 2.5            # most a clip can move, in score points (strong moments score 3 to 8)
ALL = "_all"               # every streamer together, used until one streamer has enough

_lock = threading.Lock()
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
            "needed": max(0, MIN_DECISIONS - n)}


def summary() -> dict:
    with _lock:
        d = _load()
        profs = []
        for name, p in d["profiles"].items():
            if name == ALL:
                continue
            s = p.get("samples", [])
            profs.append({"streamer": name, "decisions": len(s), "kept": int(sum(1 for x in s if x["y"] > 0)),
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
