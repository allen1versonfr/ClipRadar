"""
Targets (5.5): streamers Clip Radar starts clipping on its own as soon as they go live. Twitch's and YouTube's answers
are made up here, and so is starting a live session; the rest is real (the list, the watcher's rounds, the routes).

  python tests/test_targets.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_targets_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_targets_w_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


import exports  # noqa: E402
import live_glue  # noqa: E402
import server  # noqa: E402
import targets  # noqa: E402

# ---- what counts as a target
p = targets.parse("twitch.tv/Shroud")
check("a Twitch link is a target", p and p["platform"] == "twitch" and p["channel"] == "shroud", p)
check("so is a bare Twitch name", (targets.parse("@xQc") or {}).get("channel") == "xqc")
p = targets.parse("https://www.youtube.com/@LudwigAhgren")
check("and a YouTube channel, watched on its /live page", p and p["platform"] == "youtube"
      and p["source"] == "https://www.youtube.com/@LudwigAhgren/live", p)
check("a single video or a VOD isn't", targets.parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None
      and targets.parse("https://www.twitch.tv/videos/123456789") is None)
s = targets.clean_settings({"mode": "review", "frequency": "lots", "min_quality": 500, "length": "45", "save_vod": False, "x": 1})
check("settings are checked", s == {"mode": "review", "frequency": "lots", "preset": "any", "min_quality": 100, "length": 45,
                                    "save_vod": False}, s)

# ---- the list
a = targets.add("shroud", {"mode": "hands", "save_vod": False}, {"opts": {"layout": "split"}, "model": "small", "junk": 1})
targets.add("https://www.youtube.com/@somechannel")
try:
    targets.add("twitch.tv/SHROUD")
    dup = False
except ValueError:
    dup = True
check("the same streamer can't be added twice", dup)
try:
    targets.add("not a channel at all!")
    bad = False
except ValueError as e:
    bad = "Twitch channel" in str(e)
check("something that isn't a channel says what to type", bad)
ts = targets.load()
check("kept with their settings and what Live clipping sends (and nothing else)", len(ts) == 2 and ts[0]["settings"]["save_vod"] is False
      and ts[0]["params"] == {"opts": {"layout": "split"}, "model": "small"}, ts[0])

# ---- the watcher: live sessions made up
started = []


class Sess:
    n = 0

    def __init__(self, src, body):
        self.id = f"s{Sess.n}"
        Sess.n += 1
        self.source, self.body, self.status, self.extra = src, body, "recording", {}
        self.seg_dir = os.path.join(os.environ["CLIPRADAR_WORK"], self.id, "segments")
        server.live_sessions[self.id] = self


def fake_start(src, body):
    s = Sess(src, body)
    started.append(s)
    return s


live_glue.start_live_session = fake_start
TW = {"shroud": ""}
YT = {"html": "<html>offline</html>"}
gql = lambda q: {"data": {"users": [{"login": k, "stream": {"id": v} if v else None} for k, v in TW.items()]}}  # noqa: E731
yt = lambda url: YT["html"]  # noqa: E731
now = [1_000_000.0]


def round_(**k):
    now[0] += 61
    targets.check(server, now=now[0], twitch=gql, youtube=yt, force=True, **k)


round_()
v = {t["channel"]: t for t in targets.view(server)["targets"]}
check("offline targets start nothing and say they're waiting", not started and "Offline" in v["shroud"]["note"], v["shroud"]["note"])

TW["shroud"] = "111"
round_()
check("a target that goes live starts clipping with its own settings", len(started) == 1
      and started[0].source == "https://www.twitch.tv/shroud" and started[0].body["hands_free"] is True
      and started[0].body["save_vod"] is False and started[0].body["opts"] == {"layout": "split"} and started[0].body["target"] == a["id"],
      started[0].body if started else None)
round_()
check("and only once while it's being clipped", len(started) == 1)
v = {t["channel"]: t for t in targets.view(server)["targets"]}
check("the screen shows it live, with its session", v["shroud"]["live"] is True and v["shroud"]["session"] == started[0].id, v["shroud"])

# you stop it: not started again for this stream, but for the next one
started[0].extra["user_stopped"] = True
started[0].status = "stopped"
round_()
check("stopped by you: not started again during that stream", len(started) == 1)
v = {t["channel"]: t for t in targets.view(server)["targets"]}
check("and it says so", "stopped" in v["shroud"]["note"], v["shroud"]["note"])
TW["shroud"] = ""
round_()
TW["shroud"] = "222"
now[0] += targets.COOLDOWN
round_()
check("their next stream starts clipping again", len(started) == 2 and started[1].source == "https://www.twitch.tv/shroud")

# the stream ends on its own: Twitch may still say live for a moment; it's not started again straight away
started[1].status = "ended"
round_()
check("a stream that just ended isn't started again from an out-of-date answer", len(started) == 2)

# no free slot
started[1].status = "ended"
for s in started:
    s.status = "ended"
TW["shroud"] = ""
round_()
blockers = [Sess("https://www.twitch.tv/other1", {}), Sess("https://www.twitch.tv/other2", {}), Sess("https://www.twitch.tv/other3", {})]
exports_load = exports.load
exports.load = lambda: dict(exports_load(), lives=3)
TW["shroud"] = "333"
now[0] += targets.COOLDOWN
round_()
v = {t["channel"]: t for t in targets.view(server)["targets"]}
check("live with no free slot: says so and starts nothing", len(started) == 2 and "already being clipped" in v["shroud"]["note"],
      v["shroud"]["note"])
for b in blockers:
    b.status = "stopped"
round_()
for b in blockers:
    server.live_sessions.pop(b.id, None)
check("and starts as soon as a slot frees up", len(started) == 3)
exports.load = exports_load

# switched off, and YouTube
for s in started:
    s.status = "ended"
targets.update(a["id"], {"on": False})
TW["shroud"] = "444"
now[0] += targets.COOLDOWN
round_()
check("a target switched off isn't started", len(started) == 3)
YT["html"] = '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">..."isLiveNow":true...'
round_()
check("a YouTube target that goes live starts clipping its live page", len(started) == 4
      and started[3].source == "https://www.youtube.com/@somechannel/live", started[-1].source if started else None)
check("YouTube's live page read for the video it's streaming", targets.youtube_live("@x", yt) == "abcdefghijk")
check("Twitch asked once for every target", targets.twitch_live(["A", "b"], lambda q: {"data": {"users": [
    {"login": "a", "stream": {"id": "9"}}, {"login": "b", "stream": None}]}}) == {"a": "9", "b": ""})

# ---- through the app
srv, url = server.start(7997)


def api(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


r = api("/api/targets/add", {"source": "twitch.tv/pokimane", "settings": {"frequency": "more"}})
check("Home › Targets adds one", any(t["channel"] == "pokimane" and t["settings"]["frequency"] == "more" for t in r.get("targets", [])), r)
check("its list comes back without what Live clipping sends", all(t.get("params") is None for t in api("/api/targets")["targets"]))
tid = next(t["id"] for t in api("/api/targets")["targets"] if t["channel"] == "pokimane")
r = api("/api/targets/update", {"id": tid, "settings": {"mode": "review"}})
check("changes its settings", next(t for t in r["targets"] if t["id"] == tid)["settings"]["mode"] == "review")
check("a bad one says what to type", api("/api/targets/add", {"source": "??"}).get("status") == 400)
api("/api/targets/update", {"id": "*", "params": {"opts": {"layout": "full"}, "model": "medium"}})
check("Home brings every target up to date with the style you use now", all(
    t["params"] == {"opts": {"layout": "full"}, "model": "medium"} for t in targets.load()), [t["params"] for t in targets.load()])
r = api("/api/targets/remove", {"id": tid})
check("and removes it", all(t["id"] != tid for t in r["targets"]))
check("the watcher runs with the app", api("/api/targets").get("watching") is True)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
os._exit(1 if failures else 0)
