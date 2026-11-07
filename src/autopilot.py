"""
What Clip Radar does on its own, in one place (issue 50).

A few named events, fired from the one place each thing happens; the steps that listen to them, in the
order they were registered; one gate that says why a clip may not go further on its own; and a short
record on each clip of what automation did with it (d["auto_log"]; for the render-side events, on the
queue item: it["auto_log"]).

The steps themselves are in autopilot_steps.py. This module imports nothing from server, so there's no
import cycle: the steps import server inside their functions.

    docs/plans/issue-50-automation-core.md, section 4
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import checkflow
import errorlog
import safety

MAX_LOG = 30                      # entries kept per clip; the oldest go first

MOMENT_FOUND = "moment_found"
CLIP_TITLED = "clip_titled"
RENDER_DONE = "render_done"
RENDER_FAILED = "render_failed"
EVENTS = (MOMENT_FOUND, CLIP_TITLED, RENDER_DONE, RENDER_FAILED)


@dataclass
class Ctx:
    """What a step gets: the event and what it's about."""
    event: str
    pid: str = ""
    d: Optional[dict] = None              # the clip dict; the record goes on it
    sess: object = None                   # the live session, for a live moment
    aired: Optional[float] = None         # when a live moment was on stream
    it: Optional[dict] = None             # the queue item, for render_done/render_failed; the record goes on it
    extra: dict = field(default_factory=dict)

    @property
    def target(self) -> Optional[dict]:
        """What the record goes on: the queue item for the render-side events, else the clip."""
        return self.it if self.it is not None else self.d


@dataclass(frozen=True)
class Step:
    name: str                                     # "hands_free_render", ...
    on: str                                       # the event it listens to
    enabled: Callable[[Ctx], bool]                # reads the switch where it lives today
    run: Callable[[Ctx], Optional[str]]           # returns an outcome ("ran", "held", ...) or None for "ran"
    outward: bool = False                         # True: passes gate() before it runs
    background: bool = False                      # True: runs on the executor, else inline
    concerns: Optional[Callable[[Ctx], bool]] = None   # False: not this step's business; nothing run or recorded


@dataclass(frozen=True)
class Hold:
    """Why a clip may not go further on its own. rule is which check said so; why is shown and recorded."""
    rule: str          # "content_check" (a high-risk flag, hold on) or "unreviewed" (checkflow.blocks_render)
    why: str

    def __str__(self):
        return self.why


class InlineExecutor:
    """Runs background steps right away, in the caller's thread (tests)."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


_steps: list = []
_lock = threading.Lock()
EXECUTOR = None                       # created lazily; tests set InlineExecutor()
shutting_down: Callable[[], bool] = lambda: False     # server points this at SHUTTING_DOWN


def _executor():
    global EXECUTOR
    with _lock:
        if EXECUTOR is None:
            EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="autopilot")
        return EXECUTOR


def register(step: Step):
    if step.on not in EVENTS:
        raise ValueError(f"unknown event: {step.on}")
    with _lock:
        if any(s.name == step.name for s in _steps):
            return
        _steps.append(step)


def steps(event: Optional[str] = None) -> list:
    with _lock:
        return [s for s in _steps if event is None or s.on == event]


def reset():
    """Forget every step (tests)."""
    with _lock:
        _steps.clear()


# ---------------------------------------------------------------- the gate

def gate(d: dict) -> Optional[Hold]:
    """Why this clip may not go further on its own (shown and recorded), or None."""
    cfg = safety.load_config()
    sf = d.get("safety") or {}
    if cfg.get("enabled", True) and cfg.get("hold", True) and sf.get("level") == "high":
        # nothing that could get the account in trouble goes ahead on its own: it waits for you
        worst = next((f for f in sf.get("flags", []) if f["severity"] == 3), {})
        return Hold("content_check", f"Held for review: {worst.get('label', 'flagged')}" + (
            f" ({worst.get('text')})" if worst.get("text") else ""))
    why = checkflow.blocks_render(d, cfg)
    if why:
        return Hold("unreviewed", why)
    return None


# ---------------------------------------------------------------- the record

def record(target: Optional[dict], event: str, step: str, outcome: str, why: str = "", ms: float = 0.0):
    """Add one line to a clip's (or queue item's) record of what automation did; at most MAX_LOG kept."""
    if target is None:
        return
    e = {"t": round(time.time(), 1), "event": event, "step": step, "outcome": outcome, "ms": int(ms)}
    if why:
        e["why"] = str(why)[:300]
    log = target.get("auto_log")
    if not isinstance(log, list):
        log = target["auto_log"] = []
    log.append(e)
    del log[:-MAX_LOG]
    if outcome not in ("ran", "skipped"):
        print(f"  Autopilot: {step} {outcome}" + (f": {why}" if why else "")
              + (f" ({target.get('title')})" if target.get("title") else ""), flush=True)


# ---------------------------------------------------------------- running steps

def _run(step: Step, ctx: Ctx):
    t0 = time.time()
    try:
        if step.outward:
            hold = gate(ctx.d or {})
            if hold:
                record(ctx.target, ctx.event, step.name, "held", hold.why)
                return
        outcome = step.run(ctx) or "ran"
        why = ""
        if isinstance(outcome, tuple):
            outcome, why = outcome
        record(ctx.target, ctx.event, step.name, outcome, why, (time.time() - t0) * 1000)
    except Exception as e:
        # nothing an automation step does may break the titling job or the render loop
        record(ctx.target, ctx.event, step.name, "failed", f"{type(e).__name__}: {e}", (time.time() - t0) * 1000)
        errorlog.record(f"autopilot:{step.name}", str(e) or type(e).__name__, exc=e,
                        clip=(ctx.target or {}).get("title", ""), step=step.name)


def emit(ctx: Ctx):
    """Run every step listening to ctx.event, in registration order. Inline steps run in this thread;
    background ones on the executor. A step that fails is recorded and the next one still runs."""
    for step in steps(ctx.event):
        try:
            if step.concerns and not step.concerns(ctx):
                continue
            if shutting_down():
                record(ctx.target, ctx.event, step.name, "skipped", "shutting down")
                continue
            if not step.enabled(ctx):
                record(ctx.target, ctx.event, step.name, "skipped")
                continue
        except Exception as e:
            record(ctx.target, ctx.event, step.name, "failed", f"{type(e).__name__}: {e}")
            errorlog.record(f"autopilot:{step.name}", str(e) or type(e).__name__, exc=e, step=step.name)
            continue
        if step.background:
            try:
                _executor().submit(_run, step, ctx)
            except RuntimeError:          # the executor is shut down: the app is closing
                record(ctx.target, ctx.event, step.name, "skipped", "shutting down")
        else:
            _run(step, ctx)


# ---------------------------------------------------------------- events

_moments: dict = {}       # pid -> (sess, aired): a live moment waiting to be titled


def moment_found(sess, pid: str, d: dict, aired: Optional[float] = None):
    """A live session cut a moment and the server registered it as project pid with clip d."""
    _moments[pid] = (sess, aired)
    emit(Ctx(MOMENT_FOUND, pid=pid, d=d, sess=sess, aired=aired))


def clip_titled(pid: str, d: dict):
    """A clip has its words and its first title (every titling, live or not)."""
    sess, aired = _moments.pop(pid, (None, None))
    emit(Ctx(CLIP_TITLED, pid=pid, d=d, sess=sess, aired=aired))


def render_done(it: dict):
    """A queue item finished rendering: its file is ready."""
    emit(Ctx(RENDER_DONE, pid=it.get("pid") or "", it=it))


def render_failed(it: dict, kind: str):
    """A queue item's render failed for good (not a retry), with the error kind classify_render_error gave."""
    emit(Ctx(RENDER_FAILED, pid=it.get("pid") or "", it=it, extra={"kind": kind}))
