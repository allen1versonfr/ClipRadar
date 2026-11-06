"""
What Clip Radar does on its own today, pinned before it moves into one place (issue 50, phase 1).

The real server module, in process: a live moment is handed to server.live_on_moment, titled by the real
start_listening (Whisper stubbed: fixed words), held or queued by the real hands-free rules, and rendered by
the real queue_worker (the render itself stubbed). Discord, sounds and the cleanup sweep are stubbed too.
Every stub writes to one list, so each case checks the order things happen in as well as the flags the UI
and the saved files read (docs/plans/issue-50-automation-core.md, section 2.4):

  1. hands-free on, a clean clip: queued once (auto, prio = its score, lat); rendered: sound, Discord, cleanup, latency
  2. hands-free on, a high-risk flag, hold on: held with the flag's label and text, nothing queued, a session log line
  3. hold off: the same clip is queued
  4. "unreviewed flags block renders": held, not queued
  5. hands-free off: titled, not queued, the whole way's latency set when titled
  6. a moment you clipped yourself (hotkey) goes first: prio 1000
  7. a render that fails: no Discord, no cleanup, latency untouched
  8. campaign not set to send by itself, or no webhook: nothing sent
  9. skipping a hands-free render deletes its file only when Storage says so, and never one you rendered yourself
 10. Keep watching: a stream ending by itself starts the next session with the same settings; stopping it doesn't
 11. shutting down mid-render: no Discord, no cleanup

Background threads (the titling job, the Discord send, the cleanup sweep) run inline here, so the order
is the order the code starts them in.

  python tests/test_autopilot_pin.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import campaigns  # noqa: E402
import cleanup  # noqa: E402
import discordhook  # noqa: E402
import engine  # noqa: E402
import framing  # noqa: E402
import safety  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


EVENTS: list = []          # every side effect, in order


def names(kinds=None) -> list:
    return [e[0] for e in EVENTS if kinds is None or e[0] in kinds]


# ---------------------------------------------------------------- stubs

class InlineThread:
    """threading.Thread for server.py only: runs its target when started, so the order is fixed."""

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}
        self.daemon, self.name = daemon, name

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class _Threading:
    def __getattr__(self, k):
        return getattr(threading, k)


inline_threading = _Threading()
inline_threading.Thread = InlineThread
server.threading = inline_threading     # Locks, Events and Timers stay real

WORDS = ["that", "was", "the", "craziest", "play", "i", "have", "ever", "seen"]
RISKY = ["oh", "no", "pineapple", "that", "was", "wild", "chat"]
SAY = {"words": WORDS}


def fake_words(pr, a, b, model, lang, check=lambda: None, stats=None, task="captions", timer=None):
    EVENTS.append(("whisper", task))
    t = max(0.0, a + 8.5)
    return [{"start": round(t + i * 0.4, 2), "end": round(t + i * 0.4 + 0.35, 2), "word": w} for i, w in enumerate(SAY["words"])]


server.ensure_words = fake_words
engine.whisper_available = lambda: True
engine.probe = lambda path: {"duration": 60.0, "has_audio": True, "fps": 30.0, "width": 1920, "height": 1080}
engine.make_thumb = lambda *a, **k: None
framing.available = lambda: False

SENT: list = []


def fake_send(webhook, content, file_path="", limit=0, timeout=0.0):
    EVENTS.append(("discord", file_path))
    SENT.append((webhook, content, file_path))
    return {"ok": True, "attached": bool(file_path), "note": ""}


discordhook.send = fake_send
server.play_sound = lambda event, it: EVENTS.append(("sound", event))
cleanup.sweep = lambda *a, **k: EVENTS.append(("cleanup",))

_real_live_rendered = server._live_rendered


def live_rendered(it):
    EVENTS.append(("latency", it.get("id")))
    _real_live_rendered(it)


server._live_rendered = live_rendered

OUT = tempfile.mkdtemp(prefix="vc_out_")
RENDER = {"mode": "ok"}


def fake_render(it):
    EVENTS.append(("render", it.get("id")))
    if RENDER["mode"] == "fail":
        raise RuntimeError("FFmpeg exited with code 1")
    if RENDER["mode"] == "shutdown":
        server.SHUTTING_DOWN[0] = True             # the app is closing while this clip renders
        raise engine.Cancelled()
    path = os.path.join(OUT, f"{it['id']}.mp4")
    with open(path, "wb") as f:
        f.write(b"x" * 1000)
    it["path"], it["name"], it["folder"] = path, os.path.basename(path), OUT


server._render_item = fake_render


class _StopWorker(Exception):
    pass


class OneRound:
    """queue_wake for one pass of queue_worker: it works through what's queued, then stops when it would wait."""

    def __init__(self):
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits > 1:
            raise _StopWorker()
        return True

    def set(self):
        pass

    def clear(self):
        pass

    def is_set(self):
        return False


def run_worker():
    real = server.queue_wake
    server.queue_wake = OneRound()
    try:
        server.queue_worker(0)
    except _StopWorker:
        pass
    finally:
        server.queue_wake = real


# ---------------------------------------------------------------- a live session, as live.LiveSession looks to the server

class FakeSession:
    def __init__(self, hands_free: bool, **extra):
        self.id = "s" + os.urandom(3).hex()
        self.title, self.channel, self.source = "kai (live)", "kai", "https://www.twitch.tv/kai"
        self.dir = tempfile.mkdtemp(prefix="vc_live_")
        self.seg_dir = os.path.join(self.dir, "segments")
        self.chat_delay, self.length, self.moments = 6.0, 0, []
        self.status, self.frequency, self.min_quality = "recording", "normal", 0
        self.extra = {"auto_render": hands_free, "opts": {}, "model": "small", "language": "",
                      "keep_watching": False, "params": {"source": self.source}, "user_stopped": False, **extra}
        self.lat = {"title": [], "render": [], "total": []}
        self.last_lat: dict = {}
        self.logged: list = []
        server.live_sessions[self.id] = self

    def grades(self, ranks):
        return [77 for _ in ranks]

    def note_latency(self, stage, seconds):
        EVENTS.append(("note_latency", stage))
        self.lat[stage].append(seconds)

    def log(self, msg):
        EVENTS.append(("log", msg))
        self.logged.append(msg)


def moment(sess, number=1, manual=False, aired=True, words=WORDS):
    SAY["words"] = words
    video = os.path.join(sess.dir, f"moment_{number}.mp4")
    with open(video, "wb") as f:
        f.write(b"\0" * 100)
    clip = engine.Clip(id=0, start=10.0, end=40.0, peak=20.0, score=0.8, feat={"chat": 1.0, "loud": 0.5})
    m = {"video": video, "chat": [], "number": number, "manual": manual, "offset": 600.0, "clip": clip,
         "analysis": types.SimpleNamespace(duration=60.0), "lat": {"cut": 4.0},
         "aired": time.time() - 30 if aired else None}
    before = set(server.projects)
    server.live_on_moment(sess, m)
    mid = next(p for p in server.projects if p not in before)
    return mid, server.projects[mid]["clips"][0]


def items_for(mid) -> list:
    return [x for x in server.queue if x.get("pid") == mid]


def reset(**cfg):
    EVENTS.clear()
    SENT.clear()
    server.queue.clear()
    RENDER["mode"] = "ok"
    safety.save_config(dict({"hold": True, "export_flagged": "ask", "custom": ["pineapple"]}, **cfg))


HOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEFG-123"


def campaign(auto=True, hook=HOOK):
    campaigns.save({"id": "kai", "name": "Kai clipping", "discord": {"webhook": hook, "auto": auto, "file": True}})
    campaigns.set_active("kai")


cleanup.save({"vods": True, "skipped": False})
campaign()

# 1. hands-free on, a clean clip ------------------------------------------------------------------
reset()
sess = FakeSession(hands_free=True)
mid, d = moment(sess)
its = items_for(mid)
check("1: titled from what was said", d.get("listened") is True and names(["whisper"]) == ["whisper"], (d.get("listened"), EVENTS))
check("1: a clean clip has no flags", (d.get("safety") or {}).get("level") == "clean", d.get("safety"))
check("1: queued once", len(its) == 1, len(its))
it = its[0] if its else {}
check("1: the item is marked as rendered on its own", it.get("auto") is True, it.get("auto"))
check("1: it goes in at the moment's score", it.get("prio") == d.get("quality") == 77, (it.get("prio"), d.get("quality")))
check("1: the clip says it was queued by itself, and isn't held", d.get("auto_queued") is True and not d.get("held"))
check("1: time to title is on the clip", isinstance(d["lat"].get("title"), float), d.get("lat"))
check("1: the cut's latency from the session stays", d["lat"].get("cut") == 4.0, d.get("lat"))
check("1: hands-free: the whole way is timed at render, not at title", "total" not in d["lat"], d.get("lat"))
check("1: the item carries the latency and when it aired", it.get("lat", {}).get("title") == d["lat"]["title"]
      and isinstance(it.get("aired"), float), (it.get("lat"), it.get("aired")))
check("1: the session noted the title time", [e for e in EVENTS if e[0] == "note_latency"] == [("note_latency", "title")], EVENTS)
check("1: the item carries the active campaign", (it.get("campaign") or {}).get("id") == "kai", it.get("campaign"))
EVENTS.clear()
run_worker()
check("1: rendered", it.get("status") == "done" and os.path.isfile(it.get("path", "")), it.get("status"))
check("1: then sound, Discord, cleanup, latency, in that order",
      names(["render", "sound", "discord", "cleanup", "latency"]) == ["render", "sound", "discord", "cleanup", "latency"], EVENTS)
check("1: the ready sound", ("sound", "ready") in EVENTS, EVENTS)
check("1: Discord got the file (the campaign attaches it)", SENT and SENT[0][2] == it.get("path"), SENT)
check("1: the send is recorded on the item", isinstance(it.get("discord"), dict) and it["discord"].get("attached") is True
      and "at" in it["discord"] and it["discord"].get("post_url") is False, it.get("discord"))
check("1: render and total latency on the item", isinstance(it["lat"].get("render"), float) and isinstance(it["lat"].get("total"), float),
      it.get("lat"))
check("1: the session noted render, then total", [e[1] for e in EVENTS if e[0] == "note_latency"] == ["render", "total"], EVENTS)
check("1: and shows it as the last one", sess.last_lat.get("total") == it["lat"]["total"], sess.last_lat)
check("1: the clip's own latency isn't touched by the render", "render" not in d["lat"], d["lat"])

# 2. a high-risk flag, hold on --------------------------------------------------------------------
reset()
sess = FakeSession(hands_free=True)
mid, d = moment(sess, words=RISKY)
check("2: the flag is high risk", (d.get("safety") or {}).get("level") == "high", d.get("safety"))
check("2: held with the flag's label and what was said", d.get("held") == "Held for review: Your blocked word (pineapple)", d.get("held"))
check("2: nothing queued", items_for(mid) == [] and not d.get("auto_queued"))
check("2: the session says so", ("log", "Moment 1 held back by the content check") in EVENTS, EVENTS)
check("2: titled first, then held", names(["whisper", "note_latency", "log"]) == ["whisper", "note_latency", "log"], EVENTS)
check("2: time to title still set", isinstance(d["lat"].get("title"), float), d.get("lat"))

# 3. hold off: the same clip renders --------------------------------------------------------------
reset(hold=False)
sess = FakeSession(hands_free=True)
mid, d = moment(sess, words=RISKY)
check("3: hold off: a high-risk clip is queued", (d.get("safety") or {}).get("level") == "high"
      and len(items_for(mid)) == 1 and d.get("auto_queued") is True and not d.get("held"), (d.get("held"), len(items_for(mid))))

# 4. unreviewed flags block renders ---------------------------------------------------------------
reset(hold=False, export_flagged="block")
sess = FakeSession(hands_free=True)
mid, d = moment(sess, words=RISKY)
check("4: the flags aren't reviewed", (d.get("safety") or {}).get("status") == "needs_review", d.get("safety"))
check("4: held until they are", d.get("held") == "Held for review: flags not reviewed yet", d.get("held"))
check("4: not queued", items_for(mid) == [] and not d.get("auto_queued"))
check("4: the session says why", any(e[0] == "log" and e[1].startswith("Moment 1 held back: This clip has flags you haven't reviewed")
                                     for e in EVENTS), EVENTS)

# 5. hands-free off -------------------------------------------------------------------------------
reset()
sess = FakeSession(hands_free=False)
mid, d = moment(sess)
check("5: titled", d.get("listened") is True and names(["whisper"]) == ["whisper"])
check("5: not queued", items_for(mid) == [] and not d.get("auto_queued") and not d.get("held"))
check("5: the whole way is timed when it's ready to look at", isinstance(d["lat"].get("total"), float)
      and isinstance(d["lat"].get("title"), float), d.get("lat"))
check("5: the session noted title, then total", [e[1] for e in EVENTS if e[0] == "note_latency"] == ["title", "total"], EVENTS)
check("5: and shows it as the last one", sess.last_lat.get("total") == d["lat"]["total"], sess.last_lat)
reset()
sess = FakeSession(hands_free=False)
mid, d = moment(sess, aired=False)
check("5: no air time: no total", "total" not in d["lat"] and isinstance(d["lat"].get("title"), float), d.get("lat"))

# 6. a moment you clipped yourself ----------------------------------------------------------------
reset()
sess = FakeSession(hands_free=True)
mid, d = moment(sess, manual=True)
its = items_for(mid)
check("6: a hotkey moment goes in first (prio 1000)", len(its) == 1 and its[0].get("prio") == 1000.0, its and its[0].get("prio"))
mid2, d2 = moment(sess, number=2)
its2 = items_for(mid2)
check("6: the next one in at its score", len(its2) == 1 and its2[0].get("prio") == 77, its2 and its2[0].get("prio"))
EVENTS.clear()
run_worker()
check("6: the hotkey moment renders first", [e[1] for e in EVENTS if e[0] == "render"] == [its[0]["id"], its2[0]["id"]], EVENTS)

# 7. a render that fails --------------------------------------------------------------------------
reset()
sess = FakeSession(hands_free=True)
mid, d = moment(sess)
it = items_for(mid)[0]
lat_before = dict(it["lat"])
EVENTS.clear()
RENDER["mode"] = "fail"
run_worker()
check("7: the item failed", it.get("status") == "error" and it.get("error"), (it.get("status"), it.get("error")))
check("7: no sound, no Discord, no cleanup, no latency", names() == ["render"], EVENTS)
check("7: latency untouched", it["lat"] == lat_before and sess.lat["render"] == [] and not sess.last_lat, (it["lat"], sess.lat))
check("7: nothing about Discord on the item", "discord" not in it)

# 8. the campaign doesn't send by itself, or has no webhook ---------------------------------------
for label, kw in (("not set to send by itself", {"auto": False}), ("no webhook", {"hook": ""})):
    campaign(**kw)
    reset()
    sess = FakeSession(hands_free=True)
    mid, d = moment(sess)
    it = items_for(mid)[0]
    EVENTS.clear()
    run_worker()
    check(f"8: {label}: rendered, nothing sent", it.get("status") == "done" and not SENT and "discord" not in it, (SENT, it.get("status")))
    check(f"8: {label}: the rest still runs, in order", names(["sound", "discord", "cleanup", "latency"]) == ["sound", "cleanup", "latency"],
          EVENTS)
campaign()

# 9. skipping a hands-free render -----------------------------------------------------------------
for skipped_on, auto, kept in ((True, True, False), (False, True, True), (True, False, True)):
    cleanup.save({"skipped": skipped_on})
    reset()
    sess = FakeSession(hands_free=True)
    mid, d = moment(sess)
    it = items_for(mid)[0]
    it["auto"] = auto
    run_worker()
    path = it.get("path", "")
    ok_render = it.get("status") == "done" and os.path.isfile(path)
    server.record_feedback(mid, d["id"], "skip")
    label = ("Storage › skipped on" if skipped_on else "Storage › skipped off") + (", hands-free render" if auto else ", a render you made")
    if kept:
        check(f"9: {label}: the file is kept", ok_render and os.path.isfile(path) and it in server.queue, (ok_render, path))
    else:
        check(f"9: {label}: the file is deleted and the item goes", ok_render and not os.path.exists(path) and it not in server.queue,
              (ok_render, path, it in server.queue))
cleanup.save({"skipped": False})

# 10. Keep watching -------------------------------------------------------------------------------
STARTED: list = []
_real_start = server.start_live_session


def fake_start(src, body):
    STARTED.append((src, dict(body)))
    return types.SimpleNamespace(id="next1")


server.start_live_session = fake_start
try:
    reset()
    sess = FakeSession(hands_free=True, keep_watching=True, opts={"layout": "full"}, model="medium", language="en",
                       params={"source": "https://www.twitch.tv/kai", "chat_weight": 70, "preset": "gaming"})
    sess.status = "ended"
    server._watch_for_next(sess)
    src, body = STARTED[0] if STARTED else ("", {})
    check("10: a stream that ends by itself: the next session starts", len(STARTED) == 1 and src == "https://www.twitch.tv/kai", STARTED)
    check("10: with the same settings, waiting for the stream to go live",
          body.get("chat_weight") == 70 and body.get("preset") == "gaming" and body.get("wait_for_live") is True
          and body.get("hands_free") is True and body.get("opts") == {"layout": "full"} and body.get("model") == "medium"
          and body.get("language") == "en" and body.get("frequency") == "normal" and body.get("min_quality") == 0, body)
    check("10: the session points at the next one and says so", sess.extra.get("next") == "next1"
          and ("log", "Waiting for the next stream.") in EVENTS, (sess.extra.get("next"), EVENTS))
    for label, change in (("stopped by you", {"user_stopped": True}), ("Keep watching turned off", {"keep_watching": False})):
        STARTED.clear()
        sess = FakeSession(hands_free=True, keep_watching=True)
        sess.extra.update(change)
        sess.status = "ended"
        server._watch_for_next(sess)
        check(f"10: {label}: no next session", STARTED == [] and "next" not in sess.extra, STARTED)
    for status in ("stopped", "error"):
        STARTED.clear()
        sess = FakeSession(hands_free=True, keep_watching=True)
        sess.status = status
        server._watch_for_next(sess)
        check(f"10: a session that {'was stopped' if status == 'stopped' else 'failed'}: no next session", STARTED == [], STARTED)
finally:
    server.start_live_session = _real_start

# 11. shutting down mid-render --------------------------------------------------------------------
reset()
sess = FakeSession(hands_free=True)
mid, d = moment(sess)
it = items_for(mid)[0]
EVENTS.clear()
RENDER["mode"] = "shutdown"
try:
    server.queue_worker(0)                       # it stops by itself: the app is closing
finally:
    server.SHUTTING_DOWN[0] = False
check("11: no sound, no Discord, no cleanup, no latency", names() == ["render"], EVENTS)
check("11: the clip waits in the queue for next time", it.get("status") == "queued" and it in server.queue, it.get("status"))
check("11: the try is marked interrupted", [a.get("outcome") for a in it.get("history", [])] == ["interrupted"], it.get("history"))
check("11: nothing sent", not SENT and "discord" not in it)

server.queue.clear()
server.flush_saves()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
