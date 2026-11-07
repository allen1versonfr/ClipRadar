"""
The autopilot core on its own (issue 50): no server, no live session.

  1. steps run in the order they were registered, only for their event
  2. a step that fails is recorded and the next one still runs; the error log has it
  3. background steps go to the executor (inline here, injected), in order
  4. a switch that's off: recorded as skipped, not run; a step that isn't concerned: nothing recorded
  5. the record keeps the last 30 entries
  6. shutting down: nothing runs, every step recorded as skipped
  7. the gate: a high-risk flag with hold on, unreviewed flags that block renders, or nothing
  8. an outward step passes the gate first
  9. clip_titled gets the live session and air time moment_found was given, once
 10. render_done / render_failed: recorded on the queue item, in order; a failing step doesn't stop the next
     and doesn't raise into the render loop; shutting down skips them; the after-render steps are registered
     in queue_worker's old order

  python tests/test_autopilot.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import autopilot  # noqa: E402
import errorlog  # noqa: E402
import safety  # noqa: E402
from autopilot import CLIP_TITLED, MOMENT_FOUND, RENDER_DONE, RENDER_FAILED, Ctx, Step  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


check("autopilot doesn't import server", "server" not in sys.modules)

RAN: list = []


def step(name, on=CLIP_TITLED, enabled=True, fail=False, out=None, **kw):
    def run(ctx):
        RAN.append(name)
        if fail:
            raise ValueError("boom")
        return out
    return Step(name, on, enabled=lambda ctx: enabled, run=run, **kw)


def fresh(*ss):
    autopilot.reset()
    RAN.clear()
    for s in ss:
        autopilot.register(s)


def outcomes(d):
    return [(e["step"], e["outcome"]) for e in d.get("auto_log", [])]


autopilot.EXECUTOR = autopilot.InlineExecutor()

# 1. order --------------------------------------------------------------------------------------------
fresh(step("a"), step("other", on=MOMENT_FOUND), step("b"), step("c"))
d = {"title": "t"}
autopilot.emit(Ctx(CLIP_TITLED, pid="p", d=d))
check("1: in registration order, only this event's steps", RAN == ["a", "b", "c"], RAN)
check("1: each recorded as ran", outcomes(d) == [("a", "ran"), ("b", "ran"), ("c", "ran")], d.get("auto_log"))
e = d["auto_log"][0]
check("1: an entry has time, event, step, outcome, ms", set(e) == {"t", "event", "step", "outcome", "ms"}
      and e["event"] == CLIP_TITLED, e)
autopilot.register(step("a"))
check("1: registering the same name again does nothing", [s.name for s in autopilot.steps(CLIP_TITLED)] == ["a", "b", "c"])
try:
    autopilot.register(step("x", on="nope"))
    check("1: an unknown event is refused", False)
except ValueError:
    check("1: an unknown event is refused", True)

# 2. a failing step -----------------------------------------------------------------------------------
before = len(errorlog.recent())
fresh(step("a"), step("bad", fail=True), step("c"))
d = {"title": "My clip"}
autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("2: the next step still runs", RAN == ["a", "bad", "c"], RAN)
check("2: recorded as failed, with why", outcomes(d) == [("a", "ran"), ("bad", "failed"), ("c", "ran")]
      and d["auto_log"][1].get("why") == "ValueError: boom", d.get("auto_log"))
errs = errorlog.recent()
check("2: in the error log, with the step and the clip", len(errs) > before and any(
    x.get("where") == "autopilot:bad" and (x.get("context") or {}).get("clip") == "My clip" for x in errs), errs[-1:])

# a switch that raises is recorded too
fresh(Step("odd", CLIP_TITLED, enabled=lambda ctx: 1 / 0, run=lambda ctx: None), step("c"))
d = {}
autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("2: a switch that fails: recorded, the next still runs", outcomes(d) == [("odd", "failed"), ("c", "ran")], d)

# 3. background steps on the executor -----------------------------------------------------------------
SUBMITTED: list = []


class Recording(autopilot.InlineExecutor):
    def submit(self, fn, *a, **k):
        SUBMITTED.append(a[0].name)
        super().submit(fn, *a, **k)


autopilot.EXECUTOR = Recording()
fresh(step("a"), step("bg", background=True), step("c"))
d = {}
autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("3: only the background step goes to the executor", SUBMITTED == ["bg"], SUBMITTED)
check("3: inline executor: still in order", RAN == ["a", "bg", "c"], RAN)
autopilot.EXECUTOR = autopilot.InlineExecutor()

# 4. switches and concern ------------------------------------------------------------------------------
fresh(step("off", enabled=False), step("not_mine", concerns=lambda ctx: False), step("on", out=("held", "because")))
d = {}
autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("4: a switch that's off doesn't run", RAN == ["on"], RAN)
check("4: recorded as skipped; not concerned: nothing recorded; an outcome a step returns is kept",
      outcomes(d) == [("off", "skipped"), ("on", "held")] and d["auto_log"][1].get("why") == "because", d.get("auto_log"))
autopilot.emit(Ctx(CLIP_TITLED, d=None))
check("4: no clip: steps still run, nothing to record on", RAN == ["on", "on"], RAN)

# 5. the record's cap -----------------------------------------------------------------------------------
fresh(step("a"))
d = {}
for _ in range(45):
    autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("5: at most 30 entries", len(d["auto_log"]) == autopilot.MAX_LOG == 30, len(d["auto_log"]))
d = {"auto_log": [{"step": "old"}] * 30}
autopilot.record(d, CLIP_TITLED, "new", "ran")
check("5: the oldest goes first", len(d["auto_log"]) == 30 and d["auto_log"][-1]["step"] == "new")

# 6. shutting down --------------------------------------------------------------------------------------
fresh(step("a"), step("bg", background=True))
autopilot.shutting_down = lambda: True
try:
    d = {}
    autopilot.emit(Ctx(CLIP_TITLED, d=d))
finally:
    autopilot.shutting_down = lambda: False
check("6: nothing runs", RAN == [], RAN)
check("6: each recorded as skipped, shutting down", outcomes(d) == [("a", "skipped"), ("bg", "skipped")]
      and all(e.get("why") == "shutting down" for e in d["auto_log"]), d.get("auto_log"))

# 7. the gate -------------------------------------------------------------------------------------------
SLUR = {"severity": 3, "label": "Hate slur", "text": "xx", "status": "open", "fp": "a"}
risky = {"start": 0, "end": 10, "safety": {"level": "high", "status": "needs_review", "flags": [SLUR]}}
safety.save_config({"enabled": True, "hold": True, "export_flagged": "ask"})
h = autopilot.gate(risky)
check("7: high risk, hold on: held with the flag's label and text",
      h is not None and h.rule == "content_check" and h.why == "Held for review: Hate slur (xx)", h)
no_text = dict(risky, safety={"level": "high", "flags": [dict(SLUR, text="")]})
check("7: no text: just the label", str(autopilot.gate(no_text)) == "Held for review: Hate slur", autopilot.gate(no_text))
check("7: a clean clip goes", autopilot.gate({"start": 0, "end": 10, "safety": {"level": "clean", "flags": []}}) is None)
check("7: no check run: goes", autopilot.gate({"start": 0, "end": 10}) is None)
safety.save_config({"enabled": True, "hold": False, "export_flagged": "ask"})
check("7: hold off: goes", autopilot.gate(risky) is None, autopilot.gate(risky))
safety.save_config({"enabled": False, "hold": True, "export_flagged": "ask"})
check("7: content check off: goes", autopilot.gate(risky) is None)
safety.save_config({"enabled": True, "hold": False, "export_flagged": "block"})
h = autopilot.gate(risky)
check("7: unreviewed flags block renders: held, with checkflow's reason",
      h is not None and h.rule == "unreviewed" and "haven't reviewed" in h.why, h)

# 8. an outward step passes the gate --------------------------------------------------------------------
safety.save_config({"enabled": True, "hold": True, "export_flagged": "ask"})
fresh(step("send", outward=True), step("local"))
d = dict(risky, title="r")
autopilot.emit(Ctx(CLIP_TITLED, d=d))
check("8: held: the outward step doesn't run, the next does", RAN == ["local"], RAN)
check("8: recorded as held, with why", outcomes(d) == [("send", "held"), ("local", "ran")]
      and d["auto_log"][0].get("why") == "Held for review: Hate slur (xx)", d.get("auto_log"))

# 9. moment_found -> clip_titled carry the session ------------------------------------------------------
SEEN: list = []
autopilot.reset()
autopilot.register(Step("seen1", MOMENT_FOUND, enabled=lambda ctx: True, run=lambda ctx: SEEN.append((ctx.event, ctx.sess, ctx.aired))))
autopilot.register(Step("seen2", CLIP_TITLED, enabled=lambda ctx: True, run=lambda ctx: SEEN.append((ctx.event, ctx.sess, ctx.aired))))
sess = object()
autopilot.moment_found(sess, "m1", {})
autopilot.clip_titled("m1", {})
autopilot.clip_titled("m1", {})
autopilot.clip_titled("other", {})
check("9: the session and air time reach clip_titled once; other clips get none",
      SEEN == [(MOMENT_FOUND, sess, None), (CLIP_TITLED, sess, None), (CLIP_TITLED, None, None), (CLIP_TITLED, None, None)], SEEN)
SEEN.clear()
autopilot.moment_found(sess, "m2", {}, 123.0)
autopilot.clip_titled("m2", {})
check("9: with its air time", SEEN == [(MOMENT_FOUND, sess, 123.0), (CLIP_TITLED, sess, 123.0)], SEEN)

# 10. render_done / render_failed: the record goes on the queue item ------------------------------------
GOT: list = []
fresh(step("r1", on=RENDER_DONE), step("rbad", on=RENDER_DONE, fail=True), step("rbg", on=RENDER_DONE, background=True),
      step("r3", on=RENDER_DONE), step("f1", on=RENDER_FAILED),
      Step("fk", RENDER_FAILED, enabled=lambda ctx: True, run=lambda ctx: GOT.append((ctx.pid, ctx.it, ctx.extra.get("kind")))))
it = {"id": "q1", "pid": "p1", "title": "Queued clip"}
try:
    autopilot.render_done(it)
    raised = None
except Exception as e:      # noqa: BLE001
    raised = e
check("10: a failing step doesn't raise into the render loop", raised is None, raised)
check("10: render_done: only its steps, in order, the failing one doesn't stop the next",
      RAN == ["r1", "rbad", "rbg", "r3"], RAN)
check("10: recorded on the queue item", outcomes(it) == [("r1", "ran"), ("rbad", "failed"), ("rbg", "ran"), ("r3", "ran")]
      and all(e["event"] == RENDER_DONE for e in it["auto_log"]), it.get("auto_log"))
check("10: the failure is in the error log with the item's title", any(
    x.get("where") == "autopilot:rbad" and (x.get("context") or {}).get("clip") == "Queued clip"
    for x in errorlog.recent()), errorlog.recent()[-1:])
RAN.clear()
it2 = {"id": "q2", "pid": "p2"}
autopilot.render_failed(it2, "stalled")
check("10: render_failed: only its steps, with the item and the kind", RAN == ["f1"] and GOT == [("p2", it2, "stalled")], (RAN, GOT))
check("10: render_failed recorded on the item", outcomes(it2) == [("f1", "ran"), ("fk", "ran")]
      and all(e["event"] == RENDER_FAILED for e in it2["auto_log"]), it2.get("auto_log"))
d = {"title": "clip"}
RAN.clear()
autopilot.emit(Ctx(RENDER_DONE, d=d, it=it2))
check("10: with both a clip and an item, the item gets the record", "auto_log" not in d and len(it2["auto_log"]) == 6, d)

RAN.clear()
autopilot.shutting_down = lambda: True
try:
    it3 = {"id": "q3"}
    autopilot.render_done(it3)
    autopilot.render_failed(it3, "ffmpeg")
finally:
    autopilot.shutting_down = lambda: False
check("10: shutting down: nothing runs after a render", RAN == [] and GOT == [("p2", it2, "stalled")], RAN)
check("10: each recorded on the item as skipped, shutting down",
      outcomes(it3) == [("r1", "skipped"), ("rbad", "skipped"), ("rbg", "skipped"), ("r3", "skipped"),
                        ("f1", "skipped"), ("fk", "skipped")]
      and all(e.get("why") == "shutting down" for e in it3["auto_log"]), it3.get("auto_log"))

import autopilot_steps  # noqa: E402
autopilot.reset()
autopilot_steps.register()
check("10: the after-render steps, in queue_worker's old order",
      [s.name for s in autopilot.steps(RENDER_DONE)] == ["ready_sound", "discord", "cleanup_vods", "latency_render"],
      [s.name for s in autopilot.steps(RENDER_DONE)])
check("10: none outward or background yet (Discord sends on its own thread, as before)",
      not any(s.outward or s.background for s in autopilot.steps(RENDER_DONE)))
check("10: no steps on render_failed yet", autopilot.steps(RENDER_FAILED) == [])

check("still no server import", "server" not in sys.modules)
autopilot.reset()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
