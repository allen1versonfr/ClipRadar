"""
Targets (5.5): streamers Clip Radar watches on its own and starts clipping as soon as they go live.

  - Home › Targets: a list of Twitch channels and YouTube channels, each with its own live clipping settings (hands-free
    or review, how many clips, stream type, lowest score, clip length, save the whole stream) and an on/off switch.
  - A watcher checks whether they're live: every Twitch target in one request to Twitch's public GQL each minute, each
    YouTube target's /live page every few minutes. A target that isn't live costs nothing else: no live session, no
    slot under Settings › Performance › Live streams at once.
  - When one goes live, an ordinary live session starts for it (live_glue.start_live_session) with its settings, and
    shows under Home › Running now like any other. When the stream ends, the target goes back to being watched, and
    its next stream starts a new session.
  - Stop on a target's session stops it for that stream only: it's started again on the streamer's next stream.
  - No free slot when one goes live: it says so and starts as soon as a slot frees up, while the stream is still on.

Kept in targets.json in the data folder.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import uuid
from typing import Callable, Optional

import paths
import persist

TWITCH_EVERY = 60.0        # seconds between checks of the Twitch targets (one request for all of them)
YOUTUBE_EVERY = 180.0      # and of each YouTube target
COOLDOWN = 300.0           # after a target's stream ended: not started again from a live answer that's out of date
MAX_TARGETS = 50
MODES = ("hands", "review")
FREQS = ("fewer", "normal", "more", "lots")
DEFAULTS = {"mode": "hands", "frequency": "normal", "preset": "any", "min_quality": 0, "length": 0, "save_vod": True,
            "prep_songs": True}

_lock = threading.RLock()
state: dict = {}           # target id -> {"live", "stream", "checked", "note", "session", "ended", "stopped_stream"}
_thread: list = [None]
_stop = threading.Event()
_wake = threading.Event()


def _file() -> str:
    return os.path.join(str(paths.DATA_DIR), "targets.json")


# ---------------------------------------------------------------- the list

def parse(source: str) -> Optional[dict]:
    """{"platform", "channel", "source", "name"} for a Twitch name or link, or a YouTube channel link; None otherwise."""
    import sources
    t = (source or "").strip()
    if not t:
        return None
    c = sources.classify(t)
    if c["platform"] == "twitch" and c["kind"] == "channel" and c["id"]:
        return {"platform": "twitch", "channel": c["id"], "source": f"https://www.twitch.tv/{c['id']}", "name": c["id"]}
    if c["platform"] == "youtube" and c["kind"] in ("channel", "live") and c["id"] and not re.fullmatch(r"[\w-]{11}", c["id"]):
        handle = c["id"]
        return {"platform": "youtube", "channel": handle, "source": f"https://www.youtube.com/{handle}/live",
                "name": handle.lstrip("@").split("/")[-1]}
    return None


def clean_settings(s: Optional[dict]) -> dict:
    s = s or {}
    out = dict(DEFAULTS)
    if s.get("mode") in MODES:
        out["mode"] = s["mode"]
    if s.get("frequency") in FREQS:
        out["frequency"] = s["frequency"]
    if isinstance(s.get("preset"), str) and re.fullmatch(r"[a-z_]{1,30}", s["preset"]):
        out["preset"] = s["preset"]
    for k, lo, hi in (("min_quality", 0, 100), ("length", 0, 300)):
        try:
            out[k] = int(min(max(float(s.get(k, out[k]) or 0), lo), hi))
        except (TypeError, ValueError):
            pass
    out["save_vod"] = s.get("save_vod", True) is not False
    out["prep_songs"] = s.get("prep_songs", True) is not False      # Recognise songs while recording (5.8)
    return out


def _clean(t: dict) -> Optional[dict]:
    p = parse(t.get("source", ""))
    if not p:
        return None
    return {"id": str(t.get("id") or uuid.uuid4().hex[:8])[:16], **p, "on": t.get("on", True) is not False,
            "settings": clean_settings(t.get("settings")),
            # the rest of what Live clipping sends (render style, captions model, cookies...), kept as the screen sent it
            "params": {k: v for k, v in (t.get("params") or {}).items() if k in PARAM_KEYS},
            "added": float(t.get("added") or time.time())}


PARAM_KEYS = ("opts", "model", "language", "max_height", "cookies", "chat_delay", "emotes", "events")


def load() -> list:
    d = persist.read_json(_file(), {}) or {}
    out = []
    for t in d.get("targets") or []:
        if isinstance(t, dict):
            c = _clean(t)
            if c:
                out.append(c)
    return out[:MAX_TARGETS]


def _save(ts: list) -> None:
    os.makedirs(str(paths.DATA_DIR), exist_ok=True)
    persist.write_json(_file(), {"targets": ts})


def add(source: str, settings: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    p = parse(source)
    if not p:
        raise ValueError("Type a Twitch channel (a name or twitch.tv/name) or a YouTube channel link (youtube.com/@name).")
    with _lock:
        ts = load()
        if any(t["platform"] == p["platform"] and t["channel"].lower() == p["channel"].lower() for t in ts):
            raise ValueError(f"{p['name']} is already a target.")
        if len(ts) >= MAX_TARGETS:
            raise ValueError(f"That's {MAX_TARGETS} targets already. Remove one first.")
        t = _clean({"source": p["source"], "settings": settings, "params": params})
        ts.append(t)
        _save(ts)
    _wake.set()
    return t


def update(tid: str, patch: dict) -> dict:
    with _lock:
        ts = load()
        t = next((x for x in ts if x["id"] == tid), None)
        if not t:
            raise KeyError("That target is gone.")
        if "on" in patch:
            t["on"] = patch["on"] is True
        if "settings" in patch:
            t["settings"] = clean_settings(dict(t["settings"], **(patch.get("settings") or {})))
        if "params" in patch:
            t["params"] = {k: v for k, v in (patch.get("params") or {}).items() if k in PARAM_KEYS}
        _save(ts)
    _wake.set()
    return t


def set_params(params: dict) -> None:
    """What Live clipping would send now (render style, captions model, cookies...), for every target: Home sends it
    when it opens, so a target clips with the style you use now, not the one it was added with."""
    with _lock:
        ts = load()
        p = {k: v for k, v in (params or {}).items() if k in PARAM_KEYS}
        for t in ts:
            t["params"] = p
        _save(ts)


def remove(tid: str) -> None:
    with _lock:
        ts = [x for x in load() if x["id"] != tid]
        _save(ts)
        state.pop(tid, None)


# ---------------------------------------------------------------- are they live?

def twitch_live(logins: list, gql: Optional[Callable] = None) -> dict:
    """login -> stream id (live) or "" (not live), for up to 100 logins in one request. Raises when Twitch can't be
    asked (the targets keep what they had)."""
    if not logins:
        return {}
    import twitchclips
    q = "query{users(logins:[%s]){login stream{id}}}" % ",".join(json.dumps(x.lower()) for x in logins[:100])
    d = (gql or twitchclips._gql)(q)
    out = {x.lower(): "" for x in logins}
    for u in ((d or {}).get("data") or {}).get("users") or []:
        if u and u.get("login"):
            out[u["login"].lower()] = str((u.get("stream") or {}).get("id") or "")
    return out


def youtube_live(handle: str, fetch: Optional[Callable] = None) -> str:
    """The id of what a YouTube channel is streaming now, or "" when it isn't live."""
    url = f"https://www.youtube.com/{handle}/live"

    def get(u):
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(2_000_000).decode("utf-8", "replace")
    html = (fetch or get)(url)
    if '"isLiveNow":true' not in html and '"isLive":true' not in html:
        return ""
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', html) or \
        re.search(r'"videoId":"([\w-]{11})"', html)
    return m.group(1) if m else "live"


# ---------------------------------------------------------------- the watcher

def _session_for(S, t: dict):
    """The live session clipping this target's channel now, if any (started here or by hand)."""
    import live_glue
    for sess in live_glue.live_list():
        if live_glue._same_source(sess.source, t["source"]):
            return sess
    return None


def _start(S, t: dict, stream: str) -> str:
    """Start clipping a target that just went live. Returns what happened, for the screen."""
    import exports
    import live_glue
    st = state.setdefault(t["id"], {})
    on = live_glue.live_list()
    limit = exports.load()["lives"]
    if len(on) >= limit:
        return (f"Live, but {len(on)} stream{'s are' if len(on) != 1 else ' is'} already being clipped (Settings › "
                "Performance › Live streams at once). Starts as soon as one stops.")
    s = t["settings"]
    body = dict(t.get("params") or {}, source=t["source"], hands_free=s["mode"] == "hands", frequency=s["frequency"],
                preset=s["preset"], min_quality=s["min_quality"], length=s["length"], save_vod=s["save_vod"],
                prep_songs=s["prep_songs"],
                wait_for_live=True, keep_watching=False, target=t["id"])
    sess = live_glue.start_live_session(t["source"], body)
    st.update(session=sess.id, stream=stream, started=time.time())
    return f"Live: clipping since {time.strftime('%H:%M')}."


def check(S, now: Optional[float] = None, twitch: Optional[Callable] = None, youtube: Optional[Callable] = None,
          force: bool = False) -> None:
    """One round: ask whether the targets that are due are live, and start clipping the ones that went live."""
    now = now or time.time()
    ts = [t for t in load() if t["on"]]
    with _lock:
        for t in load():
            if not t["on"]:
                state.setdefault(t["id"], {}).update(live=None, note="Off")
        tw = [t for t in ts if t["platform"] == "twitch"
              and (force or now - state.get(t["id"], {}).get("checked", 0) >= TWITCH_EVERY - 1)]
        if tw:
            try:
                live = twitch_live([t["channel"] for t in tw], twitch)
                for t in tw:
                    state.setdefault(t["id"], {}).update(live=bool(live.get(t["channel"].lower())),
                                                         stream=live.get(t["channel"].lower(), ""), checked=now, error="")
            except Exception as e:      # noqa: BLE001  Twitch didn't answer: asked again next round
                for t in tw:
                    state.setdefault(t["id"], {}).update(error=f"Couldn't ask Twitch: {str(e)[:120]}", checked=now)
        for t in ts:
            if t["platform"] != "youtube" or (not force and now - state.get(t["id"], {}).get("checked", 0) < YOUTUBE_EVERY - 1):
                continue
            try:
                vid = youtube_live(t["channel"], youtube)
                state.setdefault(t["id"], {}).update(live=bool(vid), stream=vid, checked=now, error="")
            except Exception as e:      # noqa: BLE001
                state.setdefault(t["id"], {}).update(error=f"Couldn't ask YouTube: {str(e)[:120]}", checked=now)
        for t in ts:
            st = state.setdefault(t["id"], {})
            sess = _session_for(S, t)
            if sess is not None:
                st["session"] = sess.id
                st["note"] = "Live: clipping." if sess.status in ("recording", "reconnecting") else "Starting…"
                continue
            # its session ended: remember how, so a stopped stream isn't started again and an ended one waits a bit
            prev = S.live_sessions.get(st.get("session") or "")
            if prev is not None and st.get("session"):
                if prev.extra.get("user_stopped"):
                    st["stopped_stream"] = st.get("started_stream") or st.get("stream", "")
                st["ended"] = now
                st["session"] = ""
            if not st.get("live"):
                st["note"] = st.get("error") or "Offline. Starts clipping when they go live."
                st.pop("stopped_stream", None)
                continue
            if st.get("stopped_stream") and st.get("stopped_stream") == st.get("stream"):
                st["note"] = "Live. You stopped clipping this stream: it starts again on their next one."
                continue
            if now - st.get("ended", 0) < COOLDOWN:
                st["note"] = "The stream just ended."
                continue
            try:
                st["started_stream"] = st.get("stream", "")
                st["note"] = _start(S, t, st.get("stream", ""))
            except Exception as e:      # noqa: BLE001  said on the target, tried again next round
                st["note"] = f"Couldn't start clipping: {str(e)[:160]}"


def view(S) -> dict:
    out = []
    with _lock:
        for t in load():
            st = state.get(t["id"], {})
            sess = _session_for(S, t)
            out.append(dict(t, params=None, live=bool(st.get("live")) if t["on"] else None,
                            session=sess.id if sess is not None else "", note=st.get("note") or ("Checking…" if t["on"] else "Off"),
                            checked=st.get("checked", 0),
                            # its session saving the full stream (shown, so you know the recording is being kept)
                            vod=(getattr(sess, "vod", None) or {}).get("state", "off") if sess is not None else "off",
                            recording=sess is not None and getattr(sess, "status", "") in ("recording", "reconnecting")))
    return {"targets": out, "watching": bool(_thread[0] and _thread[0].is_alive())}


def _loop(S) -> None:
    while not _stop.is_set():
        try:
            if load():
                check(S)
        except Exception:               # noqa: BLE001  never let the watcher die
            import errorlog
            errorlog.exc("targets")
        _wake.wait(15)
        if _wake.is_set():
            _wake.clear()
            try:
                check(S, force=True)
            except Exception:           # noqa: BLE001
                pass


def start(S) -> None:
    if _thread[0] is None or not _thread[0].is_alive():
        _stop.clear()
        _thread[0] = threading.Thread(target=_loop, args=(S,), daemon=True, name="targets")
        _thread[0].start()
