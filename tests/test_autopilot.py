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
 11. the gate for a rendered clip (4.46.0): the campaign's length; the clip in Review when its project is loaded
     (flags you decided on don't hold it), the item's own check after a restart (keep_fps, fix_spans)
 12. with the server: the Discord step is gated (held by the content check, held by the campaign's length,
     sent when clean, never gated when you send it by hand) and GET /api/autopilot/<pid>/<cid>
 13. clip_skipped, session_ended, app_started (issue 50, phase 5): only their steps, in order; the record on the
     queue item (clip_skipped) or the session (session_ended); a failing step doesn't stop the next or raise;
     shutting down skips them; the steps are registered: delete_skipped_render, keep_watching, cleanup_start
 14. the steps with the server: a skipped hands-free render (Storage on, off, a render you made, no item);
     Keep watching (ended, stopped by you, turned off, stopped, failed, couldn't start); the start-up sweep
 15. a real LiveSession tells its end exactly once, after its recording thread has finished, on every way it
     can end (stream over, not live, failed to start, crashed, lost then stopped, stopped while waiting)

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
from autopilot import (APP_STARTED, CLIP_SKIPPED, CLIP_TITLED, MOMENT_FOUND, RENDER_DONE, RENDER_FAILED,  # noqa: E402
                       SESSION_ENDED, Ctx, Step)

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
check("10: only Discord is outward (4.46.0); none background (Discord sends on its own thread, as before)",
      [s.name for s in autopilot.steps(RENDER_DONE) if s.outward] == ["discord"]
      and not any(s.background for s in autopilot.steps(RENDER_DONE)))
check("10: no steps on render_failed yet", autopilot.steps(RENDER_FAILED) == [])

# 11. the gate for a rendered clip --------------------------------------------------------------------------
import campaigns  # noqa: E402
import checkflow  # noqa: E402

safety.save_config({"enabled": True, "hold": True, "export_flagged": "ask"})
HOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEFG-123"
campaigns.save({"id": "c1", "name": "Kai clipping", "min_len": 15, "max_len": 40,
                "discord": {"webhook": HOOK, "auto": True, "file": False}})
camp = campaigns.get("c1")
h = autopilot.gate({"start": 0, "end": 10}, camp)
check("11: shorter than the campaign wants: held, with the campaign's warning",
      h is not None and h.rule == "campaign" and "at least 15 s" in h.why, h)
h = autopilot.gate({"start": 0, "end": 50}, camp)
check("11: longer: held", h is not None and h.rule == "campaign" and "at most 40 s" in h.why, h)
check("11: inside it: goes", autopilot.gate({"start": 0, "end": 30}, camp) is None)
check("11: no campaign given (hands-free rendering): length doesn't hold it", autopilot.gate({"start": 0, "end": 10}) is None)
h = autopilot.gate(dict(risky, end=50), camp)
check("11: the content check comes first", h is not None and h.rule == "content_check", h)

LOADED: dict = {}
autopilot.find_clip = lambda it: LOADED.get(it.get("clip_id"))
SPEECH = {"severity": 3, "label": "Hate slur", "text": "xx", "source": "speech", "cat": "slur", "term": "xx",
          "start": 4.0, "end": 4.5}


def item(**kw):
    return dict({"id": "i", "pid": "p", "clip_id": 1, "start": 100.0, "end": 130.0, "title": "t",
                 "campaign": {"id": "c1", "name": "Kai clipping"}}, **kw)


LOADED[1] = {"id": 1, "start": 100.0, "end": 130.0, "safety": {"level": "high", "status": "needs_review",
                                                                "flags": [dict(SLUR, status="open")]}}
h = autopilot.gate_item(item())
check("11: loaded: an open high-risk flag in Review holds it", h is not None and h.rule == "content_check", h)
LOADED[1]["safety"] = {"level": "high", "status": "reviewed", "flags": [dict(SLUR, status="accepted")]}
check("11: loaded: a flag you accepted doesn't", autopilot.gate_item(item()) is None, autopilot.gate_item(item()))
check("11: the item's length counts (what was rendered), not the clip's",
      (autopilot.gate_item(item(end=145.0)) or autopilot.Hold("", "")).rule == "campaign")
check("11: a campaign that's gone: no length rule", autopilot.gate_item(item(end=145.0, campaign={"id": "gone"})) is None)
LOADED[1] = {"id": 1, "start": 100.0, "end": 130.0}
check("11: loaded but never checked, the render's check is clean: goes",
      autopilot.gate_item(item(safety={"level": "clean", "flags": []})) is None)
h = autopilot.gate_item(item(safety={"level": "high", "flags": [SPEECH]}))
check("11: loaded but never checked: the render's check holds it", h is not None and h.rule == "content_check", h)

LOADED.clear()                                     # after a restart: the project isn't loaded
h = autopilot.gate_item(item(safety={"level": "high", "flags": [SPEECH]}))
check("11: not loaded: the render's check holds it", h is not None and h.why == "Held for review: Hate slur (xx)", h)
fp = checkflow.fingerprint(SPEECH, {"start": 100.0})
check("11: not loaded: a flag you left alone in Review (keep_fps) doesn't",
      autopilot.gate_item(item(safety={"level": "high", "flags": [SPEECH]}, keep_fps=[fp])) is None)
check("11: not loaded: nor one inside a fix you applied (fix_spans)",
      autopilot.gate_item(item(safety={"level": "high", "flags": [SPEECH]}, fix_spans=[[103.9, 104.6, "bleep"]])) is None)
check("11: not loaded: a fix elsewhere doesn't cover it",
      autopilot.gate_item(item(safety={"level": "high", "flags": [SPEECH]}, fix_spans=[[110.0, 111.0, "mute"]])) is not None)
check("11: not loaded, no check on it: only the length", autopilot.gate_item(item()) is None
      and autopilot.gate_item(item(end=105.0)).rule == "campaign")
safety.save_config({"enabled": True, "hold": False, "export_flagged": "block"})
h = autopilot.gate_item(item(safety={"level": "low", "flags": [dict(SPEECH, severity=1)]}))
check("11: not loaded, unreviewed flags block renders: held", h is not None and h.rule == "unreviewed", h)
safety.save_config({"enabled": True, "hold": True, "export_flagged": "ask"})

check("still no server import", "server" not in sys.modules)

# 12. with the server: the Discord step and the API -----------------------------------------------------------
import http.client  # noqa: E402
import json  # noqa: E402

import discordhook  # noqa: E402
import server  # noqa: E402

autopilot.reset()
autopilot_steps.register()
check("12: the server points the gate at its projects", autopilot.find_clip is autopilot_steps.find_clip)
SENT: list = []
discordhook.send = lambda hook, text, path="", *a, **k: (SENT.append(text), {"attached": False, "note": ""})[1]
server.play_sound = lambda *a, **k: None


class InlineThread:
    def __init__(self, target=None, daemon=None, **k):
        self.target = target

    def start(self):
        self.target()


class _Threading:
    def __getattr__(self, k):
        return getattr(server.__dict__["_real_threading"], k)


server._real_threading = server.threading
inline = _Threading()
inline.Thread = InlineThread
server.threading = inline
srv = None
try:
    pid = "ap" + os.urandom(3).hex()
    clean = {"id": 1, "start": 100.0, "end": 130.0, "title": "Clean one", "safety": {"level": "clean", "status": "clean", "flags": []}}
    flagged = {"id": 2, "start": 200.0, "end": 230.0, "title": "Flagged one", "auto_log": [
        {"t": 1.0, "event": "clip_titled", "step": "hands_free_render", "outcome": "ran", "ms": 0}],
        "safety": {"level": "high", "status": "needs_review", "flags": [dict(SLUR, status="open")]}}
    server.projects[pid] = {"clips": [clean, flagged], "gems": []}

    def rendered(cid, start, end, **kw):
        it = dict({"id": f"q{cid}{int(end)}", "pid": pid, "clip_id": cid, "start": start, "end": end, "title": f"clip {cid}",
                   "status": "done", "campaign": {"id": "c1", "name": "Kai clipping"}}, **kw)
        server.queue.append(it)
        autopilot.render_done(it)
        return it

    def discord_log(it):
        return [(e["outcome"], e.get("why", "")) for e in it.get("auto_log", []) if e["step"] == "discord"]

    it = rendered(2, 200.0, 230.0)
    check("12: held by the content check: not sent", SENT == [] and "discord" not in it, SENT)
    check("12: its record says held, and why", discord_log(it) == [("held", "Held for review: Hate slur (xx)")], it.get("auto_log"))
    it_long = rendered(1, 100.0, 160.0)
    check("12: outside the campaign's length: not sent", SENT == [] and "discord" not in it_long, SENT)
    check("12: its record says why", len(discord_log(it_long)) == 1 and discord_log(it_long)[0][0] == "held"
          and "at most 40 s" in discord_log(it_long)[0][1], it_long.get("auto_log"))
    it_ok = rendered(1, 100.0, 130.0)
    check("12: clean and inside the length: sent", len(SENT) == 1 and isinstance(it_ok.get("discord"), dict)
          and not it_ok["discord"].get("error"), (SENT, it_ok.get("discord")))
    check("12: recorded as ran", discord_log(it_ok) == [("ran", "")], it_ok.get("auto_log"))
    server.send_to_discord(it)
    check("12: sent by hand (Exports' Discord button): never gated", len(SENT) == 2 and isinstance(it.get("discord"), dict))
    campaigns.save(dict(campaigns.get("c1"), discord={"webhook": HOOK, "auto": False, "file": False}))
    it_off = rendered(1, 100.0, 125.0)
    check("12: campaign not set to send by itself: skipped, not held", discord_log(it_off) == [("skipped", "")]
          and len(SENT) == 2, it_off.get("auto_log"))
    check("12: the queue's answer carries the record", server.queue_public(it).get("auto_log") == it["auto_log"])

    # the API
    rec = server.autopilot_record(pid, 2)
    check("12: the clip's record and its item's, merged by time", [e["step"] for e in rec][0] == "hands_free_render"
          and [e["t"] for e in rec] == sorted(e["t"] for e in rec) and ("discord", "held") in [(e["step"], e["outcome"]) for e in rec],
          rec)
    check("12: each entry has t, event, step, outcome, ms (why when there is one)",
          all({"t", "event", "step", "outcome", "ms"} <= set(e) for e in rec)
          and all(e.get("why") for e in rec if e["outcome"] == "held"), rec)
    del server.projects[pid]
    rec = server.autopilot_record(pid, 2)
    check("12: project not loaded (after a restart): the item's record still", rec is not None and len(rec) == len(it["auto_log"]), rec)
    check("12: no such clip: None", server.autopilot_record(pid, 9) is None and server.autopilot_record("nope", 1) is None)
    server.projects[pid] = {"clips": [clean, flagged], "gems": []}
    server.threading = server._real_threading          # the HTTP server runs on its own thread
    PORT = 7993
    srv, _url = server.start(PORT)

    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{PORT}"})
        r = c.getresponse()
        body = json.loads(r.read() or b"null")
        c.close()
        return r.status, body

    code, body = get(f"/api/autopilot/{pid}/2")
    check("12: GET /api/autopilot/<pid>/<cid>: a list, merged by time", code == 200 and isinstance(body, list)
          and body == server.autopilot_record(pid, 2), (code, body))
    code, body = get(f"/api/autopilot/{pid}/x")
    check("12: a bad clip id: 404 with an error", code == 404 and "error" in body, (code, body))
    code, body = get("/api/queue")
    check("12: /api/queue carries each item's record", code == 200 and any(
        x.get("id") == it["id"] and x.get("auto_log") == it["auto_log"] for x in body.get("items", [])), code)
finally:
    server.threading = server._real_threading
    server.queue.clear()
    server.SHUTTING_DOWN[0] = True
    if srv:
        srv.shutdown()

autopilot.reset()
autopilot.shutting_down = lambda: False
server.SHUTTING_DOWN[0] = False

# 13. clip_skipped, session_ended, app_started: the core ---------------------------------------------------------
import types  # noqa: E402

GOT.clear()
fresh(step("k1", on=CLIP_SKIPPED), step("kbad", on=CLIP_SKIPPED, fail=True), step("kbg", on=CLIP_SKIPPED, background=True),
      Step("kseen", CLIP_SKIPPED, enabled=lambda ctx: True, run=lambda ctx: GOT.append((ctx.pid, ctx.it, ctx.extra.get("cid")))),
      step("e1", on=SESSION_ENDED), step("ebad", on=SESSION_ENDED, fail=True),
      Step("eseen", SESSION_ENDED, enabled=lambda ctx: True, run=lambda ctx: GOT.append(ctx.sess)),
      step("a1", on=APP_STARTED), step("abad", on=APP_STARTED, fail=True), step("a2", on=APP_STARTED))
it = {"id": "q9", "pid": "p9", "clip_id": 3, "title": "Skipped clip"}
try:
    autopilot.clip_skipped("p9", 3, it)
    raised = None
except Exception as e:      # noqa: BLE001
    raised = e
check("13: clip_skipped: a failing step doesn't raise into Review's feedback", raised is None, raised)
check("13: clip_skipped: only its steps, in order", RAN == ["k1", "kbad", "kbg"], RAN)
check("13: clip_skipped: with the ids and the item", GOT == [("p9", it, 3)], GOT)
check("13: clip_skipped: recorded on the queue item", outcomes(it) == [("k1", "ran"), ("kbad", "failed"), ("kbg", "ran"), ("kseen", "ran")]
      and all(e["event"] == CLIP_SKIPPED for e in it["auto_log"]), it.get("auto_log"))
RAN.clear()
GOT.clear()
autopilot.clip_skipped("p9", 4, None)
check("13: clip_skipped with no item: the steps still run, nothing to record on", RAN == ["k1", "kbad", "kbg"]
      and GOT == [("p9", None, 4)], (RAN, GOT))

RAN.clear()
GOT.clear()
sess = types.SimpleNamespace(extra={"keep_watching": True}, status="ended")
autopilot.session_ended(sess)
check("13: session_ended: only its steps, in order, with the session", RAN == ["e1", "ebad"] and GOT == [sess], (RAN, GOT))
check("13: session_ended: recorded on the session", outcomes(sess.extra) == [("e1", "ran"), ("ebad", "failed"), ("eseen", "ran")]
      and all(e["event"] == SESSION_ENDED for e in sess.extra["auto_log"]), sess.extra.get("auto_log"))
check("13: the session's settings are untouched", sess.extra["keep_watching"] is True and set(sess.extra) == {"keep_watching", "auto_log"})

RAN.clear()
try:
    autopilot.app_started()
    raised = None
except Exception as e:      # noqa: BLE001
    raised = e
check("13: app_started: its steps in order, a failing one doesn't stop the next or raise into start()",
      raised is None and RAN == ["a1", "abad", "a2"], (raised, RAN))
check("13: app_started: the failure is in the error log", any(x.get("where") == "autopilot:abad" for x in errorlog.recent()))

RAN.clear()
GOT.clear()
autopilot.shutting_down = lambda: True
try:
    it4 = {"id": "q4"}
    sess4 = types.SimpleNamespace(extra={}, status="ended")
    autopilot.clip_skipped("p", 1, it4)
    autopilot.session_ended(sess4)
    autopilot.app_started()
finally:
    autopilot.shutting_down = lambda: False
check("13: shutting down: none of them runs", RAN == [] and GOT == [], (RAN, GOT))
check("13: each recorded as skipped, shutting down", [o for _s, o in outcomes(it4)] == ["skipped"] * 4
      and [o for _s, o in outcomes(sess4.extra)] == ["skipped"] * 3
      and all(e.get("why") == "shutting down" for e in it4["auto_log"] + sess4.extra["auto_log"]),
      (it4.get("auto_log"), sess4.extra.get("auto_log")))

autopilot.reset()
autopilot_steps.register()
check("13: the steps, one per new event", [s.name for s in autopilot.steps(CLIP_SKIPPED)] == ["delete_skipped_render"]
      and [s.name for s in autopilot.steps(SESSION_ENDED)] == ["keep_watching"]
      and [s.name for s in autopilot.steps(APP_STARTED)] == ["cleanup_start"])
check("13: none outward, none background (each ran where it does today)",
      not any(s.outward or s.background for e in (CLIP_SKIPPED, SESSION_ENDED, APP_STARTED) for s in autopilot.steps(e)))
check("13: the old Keep watching thread is gone", not hasattr(server, "_watch_for_next"))

# 14. the steps with the server -----------------------------------------------------------------------------------
import cleanup  # noqa: E402
import inspect  # noqa: E402

OUTDIR = tempfile.mkdtemp(prefix="vc_out_")


def done_item(pid, cid, auto=True):
    path = os.path.join(OUTDIR, f"{pid}_{cid}_{int(auto)}.mp4")
    with open(path, "wb") as f:
        f.write(b"x" * 100)
    it = {"id": os.urandom(4).hex(), "pid": pid, "clip_id": cid, "status": "done", "auto": auto, "path": path, "title": "c"}
    server.queue.append(it)
    return it


SAVED: list = []
_real_save_soon = server.save_queue_soon
server.save_queue_soon = lambda *a, **k: SAVED.append(a)
try:
    cleanup.save({"skipped": True})
    pid = "sk" + os.urandom(3).hex()
    mine, yours = done_item(pid, 1), done_item(pid, 1, auto=False)
    autopilot.clip_skipped(pid, 1, mine)
    autopilot.clip_skipped(pid, 1, yours)
    check("14: Storage › skipped on: a hands-free render's file goes, and the item", not os.path.exists(mine["path"])
          and mine not in server.queue and SAVED == [(0,)], (mine["path"], SAVED))
    check("14: its record says so", outcomes(mine) == [("delete_skipped_render", "ran")], mine.get("auto_log"))
    check("14: a render you made yourself: kept, nothing recorded", os.path.isfile(yours["path"]) and yours in server.queue
          and "auto_log" not in yours, yours.get("auto_log"))
    cleanup.save({"skipped": False})
    other = done_item(pid, 2)
    autopilot.clip_skipped(pid, 2, other)
    check("14: Storage › skipped off: kept, recorded as skipped", os.path.isfile(other["path"]) and other in server.queue
          and outcomes(other) == [("delete_skipped_render", "skipped")], other.get("auto_log"))
    cleanup.save({"skipped": True})
    queued = dict(done_item(pid, 3), status="queued")
    autopilot.clip_skipped(pid, 3, queued)
    check("14: not rendered yet: nothing to delete", outcomes(queued) == [("delete_skipped_render", "skipped")]
          and queued["auto_log"][0].get("why") == "nothing to delete", queued.get("auto_log"))

    # record_feedback fires it once for each of the clip's items
    FIRED: list = []
    _real_skipped = autopilot.clip_skipped
    autopilot.clip_skipped = lambda p, c, i: FIRED.append((p, c, i and i["id"]))
    try:
        server.projects[pid] = {"clips": [{"id": 2, "feat": {"chat": 1.0}, "at": 10.0}, {"id": 5, "feat": {"chat": 1.0}, "at": 20.0}],
                                "gems": [], "streamer": "kai"}
        server.record_feedback(pid, 2, "skip")
        server.record_feedback(pid, 5, "skip")
        server.record_feedback(pid, 2, "keep")
    finally:
        autopilot.clip_skipped = _real_skipped
        del server.projects[pid]
    check("14: record_feedback: skip fires clip_skipped for each of the clip's items, once with none when it has none",
          FIRED == [(pid, 2, other["id"]), (pid, 5, None)], FIRED)
finally:
    server.save_queue_soon = _real_save_soon
    cleanup.save({"skipped": False})
    server.queue.clear()

STARTED: list = []
_real_start = server.start_live_session


def fake_start(src, body):
    STARTED.append((src, dict(body)))
    if body.get("boom"):
        raise RuntimeError("no room")
    return types.SimpleNamespace(id="next1")


class Sess:
    def __init__(self, status="ended", **extra):
        self.status, self.frequency, self.min_quality, self.logged = status, "normal", 0, []
        self.extra = dict({"auto_render": True, "opts": {}, "model": "small", "language": "", "keep_watching": True,
                           "params": {"source": "https://www.twitch.tv/kai"}, "user_stopped": False}, **extra)

    def log(self, m):
        self.logged.append(m)


def kw(sess):
    return [(e["step"], e["outcome"]) for e in sess.extra.get("auto_log", [])]


server.start_live_session = fake_start
try:
    sess = Sess()
    autopilot.session_ended(sess)
    check("14: Keep watching: ended by itself: the next session, recorded on the session", len(STARTED) == 1
          and sess.extra.get("next") == "next1" and kw(sess) == [("keep_watching", "ran")], (STARTED, sess.extra))
    for label, s2 in (("stopped by you", Sess(user_stopped=True)), ("turned off", Sess(keep_watching=False)),
                      ("stopped", Sess("stopped")), ("failed", Sess("error"))):
        STARTED.clear()
        autopilot.session_ended(s2)
        check(f"14: Keep watching: {label}: no next session, recorded as skipped", STARTED == [] and "next" not in s2.extra
              and kw(s2) == [("keep_watching", "skipped")], (STARTED, s2.extra))
    STARTED.clear()
    s3 = Sess(params={"source": "https://www.twitch.tv/kai", "boom": True})
    autopilot.session_ended(s3)
    check("14: Keep watching couldn't start the next one: said in the session, recorded as failed",
          s3.logged == ["Couldn't keep watching: no room"] and kw(s3) == [("keep_watching", "failed")], (s3.logged, kw(s3)))
finally:
    server.start_live_session = _real_start

SWEPT: list = []
_real_run_cleanup = server.run_cleanup
server.run_cleanup = lambda *a, **k: SWEPT.append((a, k))
try:
    autopilot.app_started()
finally:
    server.run_cleanup = _real_run_cleanup
check("14: app_started: the start-up sweep, as server.start ran it (no wait)", SWEPT == [((), {})], SWEPT)
src = inspect.getsource(server.start)
check("14: server.start fires app_started instead of sweeping itself", "autopilot.app_started()" in src and "run_cleanup" not in src)

# 15. a real LiveSession tells its end once -----------------------------------------------------------------------
import live  # noqa: E402

check("15: only _set_status changes a session's status", inspect.getsource(live).count(".status = ") == 2
      and "self.status = s\n" in inspect.getsource(live.LiveSession._set_status)
      and ".status = " not in inspect.getsource(sys.modules["routes"]), inspect.getsource(live).count(".status = "))


class FastFlag:
    """stop_flag without the waiting."""

    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set

    def wait(self, timeout=None):
        return self._set


WORKDIR = tempfile.mkdtemp(prefix="vc_live_")


def session(resolve, run_hls=None, wait=False, on_end=None):
    """A LiveSession whose network is stubbed: resolve() and run_hls() play out one way of ending."""
    told: list = []
    s = live.LiveSession("t" + os.urandom(3).hex(), "https://www.twitch.tv/kai", WORKDIR, events=False, wait_for_live=wait,
                         on_end=on_end or (lambda se: told.append((se.status, se._finished, list(se.trace)))))
    s.trace = []
    s.stop_flag = FastFlag()
    s._resolve = lambda: resolve(s)
    s._run_hls = lambda url: run_hls(s) if run_hls else "ended"
    s._ingest_new = lambda final=False: s.trace.append("ingest")
    s._safe_detect = lambda final=False: s.trace.append("detect")
    s.release_memory = lambda: s.trace.append("released")
    s.log = lambda m: None
    return s, told


def raise_(e):
    raise e


def lost_then_stopped(s):
    s.n = getattr(s, "n", 0) + 1
    if s.n > 1:
        s.stop_flag.set()
        return "stopped"
    return "lost"


def stop_while_waiting(s):
    s.n = getattr(s, "n", 0) + 1
    if s.n == 2:                                   # /api/live/<id>/stop while it waits for the stream
        s._set_status("stopped")
        s.trace.append(("stopped by you", s.status))
        s.stop_flag.set()
    raise live.NotLive("kai is offline")


def start_failed(s):
    s.started -= 1000                              # trying for longer than 3 minutes
    raise OSError("connection refused")


AFTER = ["ingest", "detect", "released"]           # the last moments cut, the memory let go: the thread's end
CASES = (
    ("the stream ended (playlist end)", "ended", lambda s: "https://x/hls.m3u8", None, False),
    ("the stream ended (offline after recording)", "ended",
     lambda s: (s.segments.append({"start": 0, "dur": 2}), raise_(live.NotLive("offline")))[1], None, False),
    ("not live, not waiting", "error", lambda s: raise_(live.NotLive("kai is offline")), None, False),
    ("couldn't start recording", "error", start_failed, None, False),
    ("lost, then stopped", "stopped", lambda s: "https://x/hls.m3u8", lost_then_stopped, False),
    ("stopped while waiting", "stopped", stop_while_waiting, None, True),
)
for label, status, resolve, run_hls, wait in CASES:
    s, told = session(resolve, run_hls, wait)
    s._record_loop()
    check(f"15: {label}: status {status}", s.status == status, s.status)
    check(f"15: {label}: told once, after the recording thread finished",
          len(told) == 1 and told[0][0] == status and told[0][1] is True and told[0][2][-3:] == AFTER, told)
    s._set_status("stopped")                       # stopping a session that's already over
    s._set_status("error")
    check(f"15: {label}: never told twice", len(told) == 1, told)
check("15: stopped while waiting: not told when you stopped it, only when the thread finished",
      ("stopped by you", "stopped") in s.trace and s.trace.index(("stopped by you", "stopped")) < s.trace.index("released"), s.trace)

s, told = session(lambda s: "https://x/hls.m3u8", lambda s: raise_(RuntimeError("disk full")))
s._record_loop()
check("15: crashed: status error, told once with its error set", s.status == "error" and len(told) == 1
      and told[0][0] == "error" and "disk full" in s.error, (s.status, told))

s, told = session(lambda s: "https://x/hls.m3u8", on_end=lambda se: raise_(ValueError("bad callback")))
s._record_loop()
check("15: a callback that fails doesn't break the session; the error log has it", s.status == "ended"
      and any(x.get("where") == "live:end" for x in errorlog.recent()), s.status)
s, told = session(lambda s: "https://x/hls.m3u8")
s.on_end = live.LiveSession("x", "https://www.twitch.tv/kai", WORKDIR, events=False).on_end
s._record_loop()
check("15: no callback given: nothing to tell", s.status == "ended")

# the whole way: a real session ending on its own starts the next one, once, through autopilot
STARTED.clear()
server.start_live_session = fake_start
try:
    s, _told = session(lambda s: "https://x/hls.m3u8", on_end=autopilot.session_ended)
    s.extra = {"auto_render": False, "opts": {}, "model": "small", "language": "", "keep_watching": True,
               "params": {"source": s.source}, "user_stopped": False}
    s._record_loop()
    s._set_status("stopped")
    check("15: Keep watching through autopilot: the next session starts once", len(STARTED) == 1 and s.extra.get("next") == "next1"
          and [e["step"] for e in s.extra.get("auto_log", [])] == ["keep_watching"], (STARTED, s.extra))
    check("15: server passes autopilot.session_ended as the session's on_end",
          "on_end=autopilot.session_ended" in inspect.getsource(_real_start))
finally:
    server.start_live_session = _real_start

autopilot.reset()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
