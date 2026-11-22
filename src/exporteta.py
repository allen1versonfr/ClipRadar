"""
How long an export will take, and what's holding it back (5.7.4).

Export VOD and Segments said "about N left" only from how fast the bar had been moving, and the bar's parts didn't match
where the time goes (erasing an hour of songs is most of an export, but was a fixed share of the bar), so the time left
was off by hours and jumped between steps. Now:

  plan(...)        what the export will do, step by step, and how long each step should take on this PC: from the
                   speeds measured here before (kept like the clips' render speeds, in export_speeds.json), or until there
                   are some, from the speeds measured while making Clip Radar, scaled to this PC's cores. With a short
                   explainer of what the estimate rests on, and anything notable that makes it slower than it could be.
  Tracker          follows the steps as they run: each one's time left from its own speed so far (blended with the plan
                   until there's enough to go on), steps that run side by side counted once (the longest), and the bar
                   moved as the share of the time that's gone, so it moves at an even pace. It notices a step going much
                   slower than this PC usually does and says so.
  learn(...)       what each step took, kept for the next estimate.

Speeds are seconds of video (or of song) done per second of waiting.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import paths
import persist

# measured while making Clip Radar, on 4 cores (2026-11-22): the starting point until this PC has done one itself
BASE_CORES = 4
DEFAULTS = {
    "voderase": 10.0 / 44.0,      # the song eraser model: 44 s for each 10 s block of song
    "vodknown": 360.0 / 22.0,     # a recognised song taken out with its recording: 6 min of song in 22 s
    "vodcopy": 100.0,             # the picture copied, the sound made again: limited by the drive more than the CPU
    "vodrender": 90.0 / 42.0,     # 1080p60 made at 720p, "balanced", on the processor
    "vodclaim": 12.0 / 1.4 * 3,   # the YouTube check, as it is: a 12 s sample a request, three at a time
}
GPU_GAIN = 3.0                    # a graphics card encoder, until one has been timed here
SLOW = 1.7                        # a step this many times slower than planned is said to be going slow
MIN_SAY = 0.12                    # a reason is only said when it's at least this share of the time


_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "export_speeds.json")  # noqa: E731


def _speed(key: str) -> Optional[float]:
    v = (persist.read_json(_FILE(), {}) or {}).get(key)
    return float(v) if v else None


def _keep(key: str, factor: float) -> None:
    """A step's speed (seconds done per second), blended with what was kept before. Kept apart from the clips' render
    speeds (exports.record_speed): a copied picture runs hundreds of times faster than it plays, past their bounds."""
    if not (1e-3 < factor < 1e5):
        return
    with _lock:
        d = persist.read_json(_FILE(), {}) or {}
        for k in (key, key.split("|")[0]):
            old = d.get(k)
            d[k] = round(factor if not old else 0.6 * float(old) + 0.4 * factor, 4)
        try:
            persist.write_json(_FILE(), d)
        except OSError:
            pass


def _cores() -> int:
    return max(1, os.cpu_count() or BASE_CORES)


def rate(kind: str, key: str = "", scale: float = 1.0) -> tuple:
    """(speed, measured here?) for a step. key: the finer kind (encoder, size), tried before the step's own kind."""
    for k in ([f"{kind}|{key}"] if key else []) + [kind]:
        got = _speed(k)
        if got:
            return float(got), True
    # not timed here yet: the measured speed, scaled for this PC's cores (sublinear: the model and FFmpeg don't use
    # every core fully)
    return DEFAULTS[kind] * scale * (_cores() / BASE_CORES) ** 0.8, False


def learn(kind: str, key: str, seconds_done: float, took: float) -> None:
    """A step of `seconds_done` (of video or song) took `took` seconds: kept for the next estimate."""
    if took >= 3 and seconds_done > 0:
        _keep(f"{kind}|{key}" if key else kind, seconds_done / took)


def _fmt(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{max(1, m)} min" if sec >= 60 else f"{max(1, sec)} s"


def render_key(enc: str, out_h: int, fps: float, quality: str) -> str:
    return f"{enc}|{out_h}|{int(round(fps))}|{quality}"


def _pixel_rate(out_h: int, fps: float) -> float:
    """How much heavier than 720p60 a picture is to make (by the pixels each second)."""
    return max(0.1, (out_h / 720.0) ** 2 * (fps / 60.0))


def plan(*, length: float, info: dict, copying: bool, size: str, quality: str, encoder: str, out_h: int, fps: float,
         erase_s: float = 0.0, known_s: float = 0.0, claim_s: float = 0.0, separate: bool = False,
         pieces: int = 1, together: bool = False, copyable: bool = True) -> dict:
    """The steps of making `length` seconds of video and how long each should take here:
    {"steps": {name: seconds}, "seconds": total, "together": picture made alongside the sound, "basis": [what the
    estimate rests on], "held": [what's notable about why it takes this long]}."""
    steps: dict = {}
    measured: list = []
    if known_s > 0:
        r, m = rate("vodknown")
        steps["known"] = known_s / r
        measured.append(m)
    if erase_s > 0:
        r, m = rate("voderase")
        steps["erase"] = erase_s / r
        measured.append(m)
    if claim_s > 0:
        r, m = rate("vodclaim")
        t = claim_s / r
        if separate:                        # what's left of the music pulled out with the model too
            t += claim_s / rate("voderase")[0]
        steps["claim"] = t
    hw = bool(encoder) and encoder not in ("libx264", "libx265", "libvpx-vp9", "CPU", "")
    if copying:
        r, m = rate("vodcopy")
        steps["picture"] = length / r
        measured.append(m)
    else:
        key = render_key(encoder or "cpu", out_h, fps, quality)
        r, m = rate("vodrender", key, (GPU_GAIN if hw else 1.0) / _pixel_rate(out_h, fps))
        q = {"fast": 1.6, "balanced": 1.0, "high": 0.45}.get(quality, 1.0)     # x264's presets, relative to balanced
        if not m:
            r *= q
        if pieces > 1 and not _speed(f"vodrender|{key}"):
            r *= 1.0 + 0.2 * (pieces - 1)  # pieces side by side (measured: 2 pieces 1.2 times as fast on 4 cores)
        steps["picture"] = length / r
        measured.append(m)
    sound = sum(steps.get(k, 0.0) for k in ("known", "erase", "claim"))
    if together and not copying:
        # side by side: the longer of the two, and a part of the shorter (they share the processor)
        total = max(sound, steps["picture"]) + 0.3 * min(sound, steps["picture"])
    else:
        total = sound + steps["picture"]
    total += 5 + length / 2000.0            # starting, checking the file, putting the picture and sound together
    basis = ["this PC's own speed on earlier exports" if measured and all(measured)
             else "speeds measured for a PC like this one until this PC has exported something itself"
             if not any(measured) else "this PC's speed on earlier exports where it has some, typical speeds for the rest"]
    held = []
    if not copying:
        share = steps["picture"] / max(1.0, total)
        if size != "source" and copyable and share >= MIN_SAY:
            held.append(f"The picture is made again at {size}p ({_fmt(steps['picture'])}). At the video's own size "
                        f"(Size: Source) it would be copied as it is, in about {_fmt(length / rate('vodcopy')[0])}.")
        elif not copyable and share >= MIN_SAY:
            held.append(f"This video's picture ({info.get('vcodec') or 'its kind'}) can't be copied into an MP4, so every frame "
                        f"is made again ({_fmt(steps['picture'])}).")
        if not hw and share >= MIN_SAY:
            held.append("No graphics card encoder was found, so the picture is made on the processor.")
    if steps.get("erase", 0) / max(1.0, total) >= MIN_SAY:
        held.append(f"{_fmt(erase_s)} of song goes through the song eraser model ({_fmt(steps['erase'])}), the slowest part: it "
                    "runs on the processor. Recognised songs are much quicker (their recording is taken out).")
    if steps.get("claim", 0) / max(1.0, total) >= MIN_SAY:
        held.append(f"Checking YouTube won't still hear the songs ({_fmt(steps['claim'])}). It can be turned off under the scan.")
    return {"steps": {k: round(v, 1) for k, v in steps.items()}, "seconds": round(total, 1), "together": bool(together),
            "basis": basis, "held": held,
            "inputs": dict(length=length, info=info, copying=copying, size=size, quality=quality, encoder=encoder, out_h=out_h,
                           fps=fps, erase_s=erase_s, known_s=known_s, claim_s=claim_s, separate=separate, pieces=pieces,
                           together=together, copyable=copyable)}


def combine(plans: list, workers: int = 1) -> dict:
    """Several stretches' plans as one export (Segments: `workers` of them made at a time): {"seconds", "basis", "held",
    "parts": [each stretch's seconds]}. What's holding it back is said once, for the whole export."""
    if not plans:
        return {"seconds": 0.0, "basis": [""], "held": [], "parts": []}
    parts = [p["seconds"] for p in plans]
    total = max(max(parts), sum(parts) / max(1, workers)) + 3 * len(plans) / max(1, workers)
    ins = [p["inputs"] for p in plans]
    whole = dict(ins[0], **{k: sum(i[k] for i in ins) for k in ("length", "erase_s", "known_s", "claim_s")},
                 copying=all(i["copying"] for i in ins), separate=any(i["separate"] for i in ins))
    one = plan(**whole)
    return {"seconds": round(total, 1), "basis": one["basis"], "held": one["held"], "parts": parts}


def summary(p: dict) -> dict:
    """What the screen shows before an export starts: {"seconds", "note", "held"}."""
    return {"seconds": p["seconds"], "note": f"About {_fmt(p['seconds'])}, from {p['basis'][0]}.", "held": p["held"]}


class Tracker:
    """Time left for an export of steps that run one after another, or side by side.

        t = Tracker(job, plan["steps"], together, to_bar)
        t.step("erase")(0.4)        # a step 40% done
        t.done("erase")

    job["eta"] (seconds left), job["eta_note"] (what it rests on) and job["held"] (what's notable) are kept up to date,
    and to_bar(share of the time gone) moves the bar."""

    def __init__(self, job: dict, steps: dict, together: bool, to_bar: Callable, basis: str = "", held: Optional[list] = None,
                 set_eta: bool = True, workers: int = 1):
        self.job, self.plan, self.together, self.to_bar = job, dict(steps), together, to_bar
        self.workers = max(1, workers)        # steps named "p:..." are made this many at a time (Segments)
        self.frac = {k: 0.0 for k in steps}
        self.t0: dict = {}
        self.t1: dict = {}
        self.start = time.monotonic()
        self.top = 0.0
        self.slow_said: set = set()
        self.set_eta = set_eta
        self.lock = threading.Lock()
        self.held = list(held or [])
        if set_eta:
            job["eta_note"] = basis
            job["held"] = list(self.held)
            job["eta"] = round(sum(self.plan.values()), 1)

    def left(self, k: str) -> float:
        p, f = self.plan[k], self.frac[k]
        if k in self.t1:
            return 0.0
        if k not in self.t0 or f <= 0:
            return p
        el = time.monotonic() - self.t0[k]
        if el < 5 or f < 0.02:
            return max(0.0, p * (1 - f))
        seen = el * (1 - f) / f
        w = min(1.0, f / 0.15)              # trust what's been seen more as more of the step is done
        return max(0.0, w * seen + (1 - w) * p * (1 - f))

    def eta(self) -> float:
        r = {k: self.left(k) for k in self.plan}
        pool = [v for k, v in r.items() if k.startswith("p:")]
        if pool:
            rest = sum(v for k, v in r.items() if not k.startswith("p:"))
            return max(max(pool), sum(pool) / self.workers) + rest
        sound = sum(v for k, v in r.items() if k in ("known", "erase", "claim"))
        rest = sum(v for k, v in r.items() if k not in ("known", "erase", "claim", "picture"))
        if self.together and "picture" in r:
            return max(sound, r["picture"]) + rest
        return sum(r.values())

    def _update(self):
        e = self.eta()
        el = time.monotonic() - self.start
        self.top = max(self.top, el / max(1e-6, el + e))
        self.to_bar(min(0.999, self.top))
        if self.set_eta:
            self.job["eta"] = round(e, 1)
        for k in self.plan:                 # a step going much slower than this PC usually does
            if k in self.t0 and k not in self.slow_said and self.frac[k] >= 0.05:
                el_k = time.monotonic() - self.t0[k]
                if el_k > 60 and el_k / self.frac[k] > SLOW * max(30.0, self.plan[k]):
                    self.slow_said.add(k)
                    what = {"erase": "The song eraser", "known": "Taking the recognised songs out", "claim": "The YouTube check",
                            "picture": "Making the picture"}.get(k, "This export")
                    self.held.append(f"{what} is going slower than this PC usually does: another program may be using the "
                                     "processor, the graphics card or the drive, or the PC may be short of memory.")
                    if self.set_eta:
                        self.job["held"] = list(self.held)

    def step(self, k: str) -> Callable:
        if k not in self.plan:
            self.plan[k] = 0.0
            self.frac[k] = 0.0

        def f(v: float):
            with self.lock:
                self.t0.setdefault(k, time.monotonic())
                self.frac[k] = max(self.frac[k], max(0.0, min(1.0, v)))
                self._update()
        return f

    def done(self, k: str):
        with self.lock:
            if k in self.plan:
                self.t0.setdefault(k, time.monotonic())
                self.frac[k] = 1.0
                self.t1[k] = time.monotonic()
                self._update()

    def skip(self, k: str):
        """A step that won't run after all (the picture copied after all, a song that needs no eraser)."""
        with self.lock:
            if k in self.plan:
                self.plan[k] = 0.0
                self.frac[k] = 1.0
                self.t1[k] = time.monotonic()
                self._update()

    def restart(self, k: str, seconds: float):
        """A step started over another way (the picture made again after a copy that didn't work)."""
        with self.lock:
            self.plan[k] = seconds
            self.frac[k] = 0.0
            self.t0.pop(k, None)
            self.t1.pop(k, None)
            self._update()

    def took(self, k: str) -> float:
        return (self.t1.get(k, time.monotonic()) - self.t0[k]) if k in self.t0 else 0.0



