"""
How long each step of a job took (issue 47): one line in clipradar.log per job, so a real user's log shows where the
time went, and the recent ones at GET /api/timings. Only measures; nothing here changes what runs.

A Timer holds one job. mark() ends the step in progress and starts the next, so a job's existing step() calls can drive
it; add() is for a stretch timed elsewhere. A step seen twice (a render done again, a clip of several) adds up.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Callable

KEEP = 200
_recent: deque = deque(maxlen=KEEP)
_lock = threading.Lock()
_VARIES = re.compile(r"[\d,(%].*$")      # "Putting the video together, 40%" is one step, not a hundred


class Timer:
    def __init__(self, what: str, name: str = ""):
        self.what, self.name, self.steps = what, name, {}
        self.started = time.time()
        self.now, self.since = "", self.started

    def add(self, step: str, seconds: float) -> None:
        self.steps[step] = self.steps.get(step, 0.0) + max(0.0, float(seconds))

    def mark(self, step: str) -> None:
        at = time.time()
        if self.now:
            self.add(self.now, at - self.since)
        self.now, self.since = step, at

    def marking(self, then: Callable[[str], None]) -> Callable[[str], None]:
        """A step() for a job that also times it."""
        def step(text: str):
            self.mark(_VARIES.sub("", text).strip() or text)
            then(text)
        return step

    def done(self) -> dict:
        self.mark("")
        out = {"what": self.what, "name": self.name, "at": round(self.started), "total": round(time.time() - self.started, 2),
               "steps": {k: round(v, 2) for k, v in self.steps.items()}}
        with _lock:
            _recent.append(out)
        bits = ", ".join(f"{k} {v:.1f} s" for k, v in out["steps"].items())
        print(f"  Timing, {self.what}" + (f" \"{self.name}\"" if self.name else "") + f": {bits} (all {out['total']:.1f} s)", flush=True)
        return out


def recent() -> list:
    with _lock:
        return list(_recent)
