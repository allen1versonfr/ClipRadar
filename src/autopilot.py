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

import campaigns
import checkflow
import errorlog
import safety

MAX_LOG = 30                      # entries kept per clip; the oldest go first

MOMENT_FOUND = "moment_found"
CLIP_TITLED = "clip_titled"
RENDER_DONE = "render_done"
RENDER_FAILED = "render_failed"
CLIP_SKIPPED = "clip_skipped"
SESSION_ENDED = "session_ended"
APP_STARTED = "app_started"
EVENTS = (MOMENT_FOUND, CLIP_TITLED, RENDER_DONE, RENDER_FAILED, CLIP_SKIPPED, SESSION_ENDED, APP_STARTED)


@dataclass
class Ctx:
    """What a step gets: the event and what it's about."""
    event: str
    pid: str = ""
    d: Optional[dict] = None              # the clip dict; the record goes on it
    sess: object = None                   # the live session, for a live moment
    aired: Optional[float] = None         # when a live moment was on stream
    it: Optional[dict] = None             # the queue item, for render_done/render_failed/clip_skipped; the record goes on it
    extra: dict = field(default_factory=dict)

    @property
    def target(self) -> Optional[dict]:
        """What the record goes on: the queue item for the render-side events, else the clip; for session_ended
        (no clip), the session's extra; for app_started, nothing."""
        if self.it is not None:
            return self.it
        if self.d is not None:
            return self.d
        extra = getattr(self.sess, "extra", None)
        return extra if isinstance(extra, dict) else None


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
    rule: str          # "content_check" (a high-risk flag, hold on), "unreviewed" (checkflow.blocks_render),
                       # or "campaign" (outside the campaign's length; outward steps only)
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
find_clip: Callable[[dict], Optional[dict]] = lambda it: None   # a queue item's clip, when its project is loaded


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

def gate(d: dict, camp: Optional[dict] = None) -> Optional[Hold]:
    """Why this clip may not go further on its own (shown and recorded), or None. With camp (an outward step:
    the clip would leave the PC), the campaign's length rule applies too."""
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
    if camp and "start" in d and "end" in d:
        why = campaigns.length_warning(camp, float(d["end"]) - float(d["start"]))
        if why:
            return Hold("campaign", why)
    return None


def _open_only(sf: dict, still_open: Callable[[dict], bool]) -> dict:
    """The content check's result with only the flags nobody has decided on yet, its level from those."""
    flags = [f for f in sf.get("flags", []) if still_open(f)]
    level = safety.LEVELS[max([f.get("severity", 0) for f in flags], default=0)]
    return dict(sf, flags=flags, level=level, status="needs_review" if flags else (sf.get("status") or "clean"))


def _fixed(f: dict, it: dict) -> bool:
    """A spoken flag inside a fix you applied in Review (muted or bleeped in the render)."""
    if f.get("source") != "speech":
        return False
    a, b = it["start"] + f.get("start", 0), it["start"] + f.get("end", 0)
    return any(fa - 0.05 <= a and b <= fb + 0.05 for fa, fb, _kind in it.get("fix_spans") or [])


def item_subject(it: dict) -> dict:
    """What the gate looks at for a rendered queue item: the clip as it was rendered.

    Its length is always the item's (what was rendered). Its flags are the clip's in Review when its project is
    loaded, minus the ones you accepted, dismissed or fixed there; after a restart (the project not loaded), or
    for a clip never checked in Review, they're the item's own: the check the render ran (it["safety"]), minus
    the flags you left alone (it["keep_fps"]) and those inside a fix you applied (it["fix_spans"])."""
    d = find_clip(it)
    sub = {"start": float(it.get("start") or 0), "end": float(it.get("end") or 0), "title": it.get("title", "")}
    if d is not None and isinstance(d.get("safety"), dict):
        sub["check"] = d.get("check")                 # excluded from export
        sub["safety"] = _open_only(d["safety"], lambda f: f.get("status", "open") == "open")
    elif isinstance(it.get("safety"), dict):
        keep = set(it.get("keep_fps") or [])
        sub["safety"] = _open_only(dict(it["safety"], status=""), lambda f: (
            checkflow.fingerprint(f, {"start": sub["start"]}) not in keep and not _fixed(f, it)))
    return sub


def gate_item(it: dict) -> Optional[Hold]:
    """The gate for an outward step after a render: may this rendered clip leave the PC on its own?"""
    camp = campaigns.get((it.get("campaign") or {}).get("id", ""))
    return gate(item_subject(it), camp)


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
            hold = gate_item(ctx.it) if ctx.it is not None else gate(ctx.d or {})
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


def clip_skipped(pid: str, cid, it: Optional[dict] = None):
    """You skipped clip cid of project pid in Review; it is one of its queue items (fired once for each), or None
    when it has none."""
    emit(Ctx(CLIP_SKIPPED, pid=pid or "", it=it, extra={"cid": cid}))


def session_ended(sess):
    """A live session ended, was stopped or failed (status ended, stopped or error), and its recording thread has
    finished. LiveSession fires it once per session."""
    emit(Ctx(SESSION_ENDED, sess=sess))


def app_started():
    """Clip Radar started (server.start, once)."""
    emit(Ctx(APP_STARTED))
