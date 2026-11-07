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
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
