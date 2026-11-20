"""
Live mode's glue to the rest of the app (moved from server.py, hardening phase 5b–c; no changes): starting a live
session with the settings it was asked for, what happens to each moment it finds (a project per moment, rendered
in order of strength when hands-free), Home › Sources, and the Ctrl+Alt+C hotkey. The recording and moment
detection themselves are in live.py.

  start_live_session(src, body)   a LiveSession, started (body keeps every setting, so Keep watching can start the next)
  live_on_moment(sess, m)         a moment found: its project, and its render when hands-free
  live_active(), live_view(sess)  the session running now; what the UI shows of one
  PriorityGate, live_gate         moments listened to strongest first, one at a time

The sessions (server.live_sessions) and everything else shared stay in server.py and are read here as S.<name> at
call time (server.py's docstring says why). A test that swaps a name defined here swaps live_glue.<name>.
"""

from __future__ import annotations

import sys
if "server" not in sys.modules:
    # imported before server: server imports this module itself (at its end), so server is imported first
    # and this copy steps aside (the import then gives back the copy server loaded in full)
    del sys.modules[__name__]
    import server  # noqa: E402,F401
import functools
import os
import threading
import time
import uuid
import engine
import live
import hotkey
import detect
import emotes
import exports
import outcome
import sources
import learning
import locations
import render_queue

import autopilot  # noqa: E402
import server as S  # noqa: E402  shared state and helpers, read as S.<name> at call time


LIVE_ON = ("starting", "waiting", "recording", "reconnecting")


def live_active():
    return next((s for s in S.live_sessions.values() if s.status in LIVE_ON), None)


def live_list() -> list:
    return [s for s in S.live_sessions.values() if s.status in LIVE_ON]


def _same_source(a: str, b: str) -> bool:
    ca, cb = sources.classify(a), sources.classify(b)
    return (ca["platform"], ca["id"] or ca["url"].lower()) == (cb["platform"], cb["id"] or cb["url"].lower())


def sources_view() -> dict:
    """Everything going on, per source (Home › Sources): VODs being opened and streams being clipped."""
    out = []
    for j in sorted(S.jobs.values(), key=lambda j: j["started"]):
        if j["kind"] != "analyze" or (j["status"] in ("done", "cancelled") and time.time() - j["started"] > 1800):
            continue
        body = j.get("body") or {}
        c = sources.classify(body.get("source", ""))
        res = j.get("result") or {}
        pr = S.projects.get(res.get("pid", "")) or {}
        an = pr.get("analysis")
        out.append({"type": "vod", "id": j["id"], "status": j["status"], "step": j["step"], "progress": round(j["progress"], 3),
                    "eta": j.get("eta"), "source": body.get("source", ""), "label": c["label"],
                    "title": res.get("title") or (os.path.basename(body.get("source", "")) if c["platform"] == "local" else c["url"]),
                    "pid": res.get("pid", ""), "error": (j.get("error") or "").splitlines()[0] if j.get("error") else "",
                    "started": j["started"],
                    # for Review's list of open videos: what's left to review in each, and how long it is
                    "todo": sum(1 for d in pr.get("clips", []) if d.get("state", "todo") == "todo"),
                    "duration": round(float(getattr(an, "duration", 0) or 0), 1)})
    for sess in S.live_sessions.values():
        if sess.status not in LIVE_ON and time.time() - sess.started > 6 * 3600:
            continue
        out.append({"type": "live", "id": sess.id, "status": sess.status, "title": sess.title, "source": sess.source,
                    "label": sources.classify(sess.source)["label"] if not sess.channel else "Twitch livestream",
                    "recorded": sess.recorded, "moments": len(sess.moments), "chat_state": sess.chat_state(),
                    "error": sess.error, "started": sess.started,
                    # the full stream being saved (keeping), joined into one video (saving), saved, or not (5.5)
                    "vod": (getattr(sess, "vod", None) or {}).get("state", "off")})
    p = exports.load()
    return {"items": out, "limits": {"downloads": p["downloads"], "lives": p["lives"]}}


def on_hotkey():
    """Ctrl+Alt+C anywhere in Windows: clip the last 30 seconds of the live stream."""
    sess = live_active()
    if sess and sess.status in ("recording", "reconnecting"):
        sess.clip_now(30)
        sess.hotkey_clips = getattr(sess, "hotkey_clips", 0) + 1
        hotkey.beep(True)
    else:
        hotkey.beep(False)


class PriorityGate:
    """Lets one job at a time through, the highest priority first (live moments waiting for Whisper:
    the moment you clipped yourself, then the strongest)."""

    def __init__(self, slots: int = 1):
        self.cv = threading.Condition()
        self.slots, self.busy, self.waiting, self.n = slots, 0, [], 0

    def acquire(self, prio: float, check=None):
        with self.cv:
            self.n += 1
            key = (-float(prio), self.n)
            self.waiting.append(key)
            try:
                while self.busy >= self.slots or min(self.waiting) != key:
                    self.cv.wait(0.5)
                    if check:
                        check()
            except BaseException:
                self.waiting.remove(key)
                self.cv.notify_all()
                raise
            self.waiting.remove(key)
            self.busy += 1

    def release(self):
        with self.cv:
            self.busy -= 1
            self.cv.notify_all()

    def queued(self) -> int:
        with self.cv:
            return len(self.waiting)


live_gate = PriorityGate(1)


def _live_prio(d: dict) -> float:
    return 1000.0 if d.get("manual") else float(d.get("quality") or 0)


def _live_rendered(it: dict):
    """Hands-free: how long rendering took, and the whole way from the moment on stream to the file."""
    sess = S.live_sessions.get(it.get("live") or "")
    if not sess or it.get("status") != "done":
        return
    now = time.time()
    sess.note_latency("render", now - it.get("created", now))
    lat = it.get("lat") or {}
    if it.get("aired"):
        lat["render"] = round(now - it.get("created", now), 2)
        lat["total"] = round(now - it["aired"], 2)
        sess.note_latency("total", lat["total"])
        sess.last_lat = lat


def live_on_moment(sess, m: dict):
    """A live session found a moment: register it as a small project, title it, maybe render it."""
    mid = uuid.uuid4().hex[:8]
    pr = {"video": m["video"], "chat": m["chat"], "title": f"{sess.title}, moment {m['number']}",
          "workdir": sess.dir, "analysis": m["analysis"], "info": engine.probe(m["video"]), "clips": [],
          "chat_delay": sess.chat_delay, "source": sess.source, "clips_ver": 0, "live": sess.id,
          "thumb_prefix": f"m{m['number']}_", "streamer": sess.channel or "", "length": sess.length,
          "tx_lock": threading.Lock()}
    S.projects[mid] = pr
    clip = m["clip"]
    clip.id = m["number"]
    pr["clips"] = S.clip_dicts(mid, [clip])
    d = pr["clips"][0]
    d.update(moment=m["number"], manual=m["manual"], live=sess.id, created=time.time(),
             at=round(m["offset"] + clip.peak, 1), abs_start=round(m["offset"] + clip.start, 1),
             abs_end=round(m["offset"] + clip.end, 1), len_mode=sess.length, lat=dict(m.get("lat") or {}))
    sess.moments.append(mid)
    # more of the stream is known now: every moment's score again, so 90+ stays the session's best few
    ds = [S.projects[x]["clips"][0] for x in sess.moments if x in S.projects and S.projects[x].get("clips")]
    for c, q in zip(ds, sess.grades([float(c.get("score") or 0) for c in ds])):
        c["quality"] = q
    autopilot.moment_found(sess, mid, d, m.get("aired"))     # titled, then maybe rendered: autopilot_steps


def _live_delay(v):
    """Chat delay for live: a number of seconds, or "auto" (empty) to work it out from the stream."""
    if v in (None, "", "auto"):
        return "auto"
    try:
        return min(max(float(v), 0.0), 30.0)
    except (TypeError, ValueError):
        return "auto"


LIVE_VOD_CHECK = 3600       # Save the whole stream: room for at least this many seconds before it starts


def live_vod_setting(body: dict) -> tuple:
    """(save the whole stream?, why not): on unless turned off, and only with room for an hour of it on the disk
    it's saved to. The session keeps checking as it records (live.LiveSession._check_vod_room)."""
    if not body.get("save_vod", True):
        return False, ""
    try:
        render_queue._check_space(locations.path("downloads") or S.WORKROOT, LIVE_VOD_CHECK)
    except OSError as e:
        return False, f"Not saving the full stream: {e.strerror or e}. Moments are still clipped."
    return True, ""


def _live_outcome(start):
    """An Outcome line for each live session start (outcome.py)."""
    @functools.wraps(start)
    def wrapped(src: str, body: dict):
        try:
            sess = start(src, body)
        except Exception:
            outcome.log("live", "failed", platform=S._platform(src), step="start")
            raise
        outcome.log("live", "ok", platform=S._platform(src), keep_watching=bool(body.get("keep_watching")))
        return sess
    return wrapped


@_live_outcome
def start_live_session(src: str, body: dict):
    """Create and start a live session. body holds every setting so 'keep watching' can start the next one."""
    sid = uuid.uuid4().hex[:8]
    save_vod, vod_note = live_vod_setting(body)
    sess = live.LiveSession(
        sid, src, S.WORKROOT, temp_root=locations.path("temp"), length=float(body.get("length", 0) or 0), events=bool(body.get("events", True)),
        sensitivity=body.get("frequency") or body.get("sensitivity", "normal"),
        chat_delay=_live_delay(body.get("chat_delay", "auto")), min_quality=int(body.get("min_quality") or 0),
        chat_weight=float(body.get("chat_weight", 60)), max_height=int(body.get("max_height", 1080)),
        cookies=body.get("cookies") or "", warmup=float(body.get("warmup", 90)),
        buffer_minutes=float(body.get("buffer_minutes", 15)),
        wait_for_live=bool(body.get("wait_for_live", False)), save_vod=save_vod, save_vod_note=vod_note,
        on_moment=live_on_moment, on_end=autopilot.session_ended,      # Keep watching: autopilot_steps.keep_watching
        log=lambda m: print("  [live] " + m, flush=True))
    if body.get("chat_channel"):
        sess.channel = live.parse_channel(body["chat_channel"]) or sess.channel
        if sess.channel and sess.title == "stream (live)":
            sess.title = f"{sess.channel} (live)"
            engine._save_meta(sess.dir, title=sess.title, source=sess.source, live=True)
    sess.adjust = learning.adjuster(sess.channel or "")
    sess.weights = detect.preset(body.get("preset"))
    if sess.channel and body.get("emotes", True):
        def load_emotes(se=sess):
            # 7TV/BTTV/FFZ emotes make chat reactions count the same way they do for VODs
            es = emotes.for_channel(login=se.channel, log=se.log)
            if es:
                se.scorer.hype = detect.weigher(es)
                se.emote_set = es
        threading.Thread(target=load_emotes, daemon=True).start()
    hands_free = bool(body.get("hands_free", body.get("auto_render")))
    sess.extra = {"auto_render": hands_free, "opts": dict(body.get("opts") or {}),
                  "model": body.get("model") or "small", "language": body.get("language") or "",
                  "keep_watching": bool(body.get("keep_watching")),
                  "params": dict(body, source=src), "user_stopped": False}
    S.live_sessions[sid] = sess
    sess.start()
    return sess


def live_view(sess) -> dict:
    v = sess.public()
    v["clips"] = [S.projects[mid]["clips"][0] for mid in sess.moments if mid in S.projects and S.projects[mid]["clips"]]
    v["auto_render"] = bool(sess.extra.get("auto_render"))
    v["hands_free"] = v["auto_render"]
    v["keep_watching"] = bool(sess.extra.get("keep_watching"))
    v["held"] = sum(1 for c in v["clips"] if c.get("held"))
    v["hotkey_clips"] = getattr(sess, "hotkey_clips", 0)
    v["next"] = sess.extra.get("next", "")
    v["listening"] = any(j["status"] == "running" and j["kind"] == "listen" for j in
                         (S.jobs.get(S.projects[mid].get("listen_job", "")) or {"status": "", "kind": ""}
                          for mid in sess.moments if mid in S.projects))
    return v
