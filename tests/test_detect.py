"""
Unit tests for 3.5 detection: chat delay, the extra signals, late reactions, merging, the
0-100 score and its breakdown, presets, 7TV/BTTV/FFZ emotes and learning from trims.

  python tests/test_detect.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")

# a tiny stand-in for the three emote services
HITS = {"n": 0, "down": False}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS["n"] += 1
        if HITS["down"]:
            self.send_response(503)
            self.end_headers()
            return
        p = self.path
        body = {
            "/7tv/v3/emote-sets/global": {"emotes": [{"name": "EZ"}, {"name": "Clap"}]},
            "/7tv/v3/users/twitch/77": {"emote_set": {"emotes": [{"name": "ICANT"}, {"name": "Aware"}]}},
            "/bttv/3/cached/emotes/global": [{"code": "LULW"}],
            "/bttv/3/cached/users/twitch/77": {"channelEmotes": [{"code": "catJAM"}], "sharedEmotes": [{"code": "KEKW"}]},
            "/ffz/v1/set/global": {"default_sets": [3], "sets": {"3": {"emoticons": [{"name": "OMEGALUL"}]}}},
        }.get(p)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["CLIPRADAR_EMOTE_API"] = f"http://127.0.0.1:{srv.server_address[1]}"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import detect  # noqa: E402
import emotes  # noqa: E402
import engine  # noqa: E402
import learning  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


rng = np.random.default_rng(5)
N = 1800

# ---------------------------------------------------------------- chat delay
audio = rng.normal(0, 0.5, N).astype(np.float32)
rate0 = rng.poisson(2, N).astype(np.float32)
for t in rng.choice(np.arange(60, N - 60), 30, replace=False):
    audio[t:t + 3] += 5
    rate0[t + 7:t + 12] += 25                       # chat reacts 7 s later
lag, sure = detect.estimate_delay(rate0, audio, None)
check("works out how late chat reacts", sure and lag == 7, (lag, sure))
lag2, sure2 = detect.estimate_delay(rng.poisson(2, N).astype(np.float32), rng.normal(0, 1, N).astype(np.float32), None)
check("no clear answer: keeps 6 s", lag2 == 6.0 and not sure2, (lag2, sure2))
check("shifting chat by the delay", list(detect.shift(np.arange(10, dtype=np.float32), 3)[:3]) == [3, 4, 5])


def make_an(n=600, chat_spikes=(), audio_spikes=(), quiet=()):
    loud = np.full(n, -30.0, dtype=np.float32) + rng.normal(0, 1.0, n).astype(np.float32)
    rate = rng.poisson(2, n).astype(np.float32)
    for a, b in quiet:
        loud[a:b] = -55
    for t, d, db in audio_spikes:
        loud[t:t + d] = db
    for t, d, amount in chat_spikes:
        rate[t:t + d] += amount
    an = engine.score_signals(loud, [], n, float(n), 6.0, 60, rate=rate, stats={})
    detect.enrich(an, [], 6.0, None, rate=rate)
    an.combine(60, detect.preset("any"))
    return an


# ---------------------------------------------------------------- isolated noise, sudden reactions
an = make_an(audio_spikes=[(200, 1, -5)])
check("a loud blip nobody reacted to counts for less", float(an.noise[200]) < 0.7, float(an.noise[200]))
an = make_an(audio_spikes=[(200, 1, -5)], chat_spikes=[(203, 8, 30)])
check("a loud blip chat reacted to counts fully", float(an.noise[200]) == 1.0, float(an.noise[200]))
an = make_an(audio_spikes=[(300, 4, -3)], quiet=[(285, 300)])
check("silence, then a sudden reaction, gets a bonus", float(an.react[300:304].max()) > 0.5, float(an.react[300:304].max()))
an2 = make_an(audio_spikes=[(300, 4, -3)])
check("no bonus without the quiet stretch", float(an2.react[300:304].max()) == 0.0)

# ---------------------------------------------------------------- late reactions
an = make_an(audio_spikes=[(250, 2, -8)], chat_spikes=[(268, 10, 40)])
peak = int(np.argmax(an.score[255:300])) + 255
trig = detect.find_trigger(an, peak, 25)
check("a late chat reaction finds what it reacted to", trig is not None and 248 <= trig <= 253, (peak, trig))
st, en, _ = engine.smart_bounds(an, peak, trigger=trig)
check("the clip starts before the trigger", st <= trig - 4, (st, trig))
st2, en2, _ = engine.smart_bounds(an, peak, trigger=trig, max_len=25)
check("even when the clip has to be short", st2 <= trig and en2 - st2 <= 26.5, (st2, en2, trig))

# ---------------------------------------------------------------- picking: merging, score, breakdown
an = make_an(chat_spikes=[(300, 8, 40), (318, 8, 35)], audio_spikes=[(300, 3, -5)])
clips = engine.pick_clips(an, count=5, min_len=15, max_len=90)
near = [c for c in clips if c.start < 330 and c.end > 300]
check("two waves of the same moment become one clip", len(near) == 1, [(c.start, c.end, c.merged) for c in near])
c0 = clips[0]
check("clips get a 0-100 score", 0 < c0.quality <= 100 and isinstance(c0.quality, int), c0.quality)
check("scores rank like the clips", all(a.quality >= b.quality for a, b in zip(clips, clips[1:])), [c.quality for c in clips])
bd = c0.parts
check("breakdown names the signals", "chat" in bd and all(isinstance(v, float) for v in bd.values()), bd)
check("breakdown adds up to the score", abs(sum(v for k, v in bd.items() if k != "tuned") - c0.score) < 0.6, (sum(bd.values()), c0.score))
check("quality: higher score, higher number", detect.quality(1) < detect.quality(3) < detect.quality(6) < detect.quality(12) <= 100)
few = engine.pick_clips(make_an(chat_spikes=[(t, 6, 15 + (t % 7) * 4) for t in range(60, 560, 45)]), count=0, sensitivity="fewer")
more = engine.pick_clips(make_an(chat_spikes=[(t, 6, 15 + (t % 7) * 4) for t in range(60, 560, 45)]), count=0, sensitivity="more")
check("sensitivity: more finds more", len(more) > len(few), (len(few), len(more)))
lens = [c.end - c.start for c in engine.pick_clips(an, count=5, min_len=30, max_len=45)]
check("shortest and longest clip respected", all(29 <= x <= 46 for x in lens), lens)

# ---------------------------------------------------------------- presets and settings
s = detect.settings({"preset": "podcasts"})
check("preset brings its own clip lengths", s["min_len"] == 30 and s["max_len"] == 150 and s["chat_delay"] == "auto")
s = detect.settings({"preset": "nope", "min_len": 1, "max_len": 999, "sensitivity": "loud", "chat_delay": "abc"})
check("settings are kept sensible", s == dict(s, preset="any", min_len=3.0, max_len=180.0, sensitivity="normal", chat_delay="auto"), s)
check("settings described in words", "Gaming" in detect.describe(detect.settings({"preset": "gaming"}), 5.0)
      and "5 s (worked out)" in detect.describe(detect.settings({}), 5.0) and "no chat" in detect.describe({}, None, False))
an.combine(60, detect.preset("reactions"))
r1 = an.score.copy()
an.combine(60, detect.preset("interviews"))
check("presets change the ranking weights", not np.allclose(r1, an.score))

# ---------------------------------------------------------------- emotes
es = emotes.for_channel("77")
check("7TV, BTTV and FFZ emotes for the channel and globals", {"ICANT", "Aware", "catJAM", "KEKW", "LULW", "OMEGALUL", "EZ"} <= set(es.by_name),
      sorted(es.by_name))
check("emote names match whatever case chat types", es.find("icant") == "ICANT" and es.find("ok") is None)
check("emote kinds from their names", es.by_name["ICANT"][1] == "laugh" and es.by_name["catJAM"][1] == "hype")
n0 = HITS["n"]
emotes._mem.clear()
es2 = emotes.for_channel("77")
check("emote lists are cached (no new requests)", HITS["n"] == n0 and len(es2) == len(es))
HITS["down"] = True
emotes._mem.clear()
es3 = emotes.for_channel("77", refresh=True)
check("services down: keeps the cached lists", len(es3) == len(es), len(es3))
es4 = emotes.for_channel("999", refresh=True)
check("nothing cached and offline: carries on without", isinstance(es4, emotes.EmoteSet))
HITS["down"] = False
chat = [(100 + i * 0.3, "ICANT ICANT", f"u{i}", "") for i in range(30)] + \
       [(101 + i * 0.3, "ICANT", "u1", "") for i in range(10)] + [(102, "!ICANT", "x", ""), (103, "ICANT", "nightbot", "")]
er, top = detect.emote_rate(sorted(chat), 200, 0.0, es)
check("emote waves count chatters, not spam, bots or commands", float(er.sum()) == 30, float(er.sum()))
check("the emote a wave was made of", any(e == "ICANT" for e, _ in top.values()), top)
w = detect.weigher(es)
check("emotes add to a message's weight (live)", w("catJAM catJAM") > engine.chat_weight("catJAM catJAM")
      and w("KEKW") == engine.chat_weight("KEKW"))

# ---------------------------------------------------------------- learning from trims
for i in range(6):
    learning.record_trim("streamy", f"k{i}", -3.0 - i * 0.1, 1.0)
off = learning.trim_offsets("streamy")
check("learns where you like clips to start and end", off["n"] == 6 and -3.6 <= off["start"] <= -3.0 and off["end"] == 1.0, off)
check("not before five trims", learning.trim_offsets("someoneelse")["start"] == 0.0)
learning.record_reason("streamy", "noise")
learning.record_reason("streamy", "bogus")
learning.record_added("streamy")
prof = next(p for p in learning.summary()["profiles"] if p["streamer"] == "streamy")
check("skip reasons and clips added by hand are counted", prof["reasons"][0]["key"] == "noise" and prof["added"] == 1, prof)

# ---------------------------------------------------------------- the number of clips you choose
big = make_an(n=3 * 3600, chat_spikes=[(t, 6, 25) for t in range(400, 10400, 900)])     # 11 clear moments in 3 h
for want in (5, 15, 25, 30):
    got = engine.pick_clips(big, count=want)
    check(f"asking for {want} clips gives {want}", len(got) == want, len(got))
# a quiet stream with three clear moments: asking for 20 used to return 4 without a word
qn = 7200
q_loud = np.full(qn, -30.0, dtype=np.float32) + rng.normal(0, 0.3, qn).astype(np.float32)
q_rate = np.full(qn, 3.0, dtype=np.float32)
for t0 in (1000, 3000, 5000):
    q_rate[t0:t0 + 6] += 40
    q_loud[t0:t0 + 3] = -8
quiet_an = engine.score_signals(q_loud, [], qn, float(qn), 6.0, 60, rate=q_rate, stats={})
detect.enrich(quiet_an, [], 6.0, None, rate=q_rate)
quiet_an.combine(60, detect.preset("any"))
got = engine.pick_clips(quiet_an, count=20)
check("a quiet stream still gives the 20 clips you asked for", len(got) == 20, len(got))
check("with its clear moments first", sorted(int(c.peak) // 100 for c in got[:3]) == [10, 30, 50], [c.peak for c in got[:3]])
auto = engine.pick_clips(big, count=0)
check("automatic still follows its own estimate", 0 < len(auto) <= engine.estimate(big)["suggest"], (len(auto), engine.estimate(big)))
short = make_an(n=600)
few = engine.pick_clips(short, count=60)
check("a 10-minute video can't hold 60 clips, and doesn't pretend to", 0 < len(few) < 60, len(few))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import server  # noqa: E402
pr = {"analysis": short, "chat": [], "chat_delay": 6.0, "streamer": ""}
server._pick(pr, 60, 0)
check("and says so instead of cutting the list short quietly", pr["pick_note"].startswith(f"Found {len(few)} of the 60"), pr["pick_note"])
server._pick(pr, 5, 0)
check("no note when the number is met", pr["pick_note"] == "")

# ---- chat delay on a long stream (real-world test: a 6-hour IRL stream, chat clearly ~4 s behind but
# weak and spread over 2-6 s, read as "couldn't tell"); random data never gets a delay it's "sure" of
def _sim(seed, n, couple=True, react=0.7):
    rng = np.random.default_rng(seed)
    audio = rng.normal(0, 1, n).clip(-3, 8).astype(np.float32)
    for t in rng.integers(0, n, n // 30):
        audio[t:t + 3] += rng.uniform(1, 5)
    chat = rng.poisson(4, n).astype(np.float32)
    if couple:
        for t in np.where(audio > 3)[0][::2]:
            for L in range(2, 7):
                if t + L < n:
                    chat[t + L] += rng.poisson(react)
    return chat, audio


long_found = [detect.estimate_delay(*_sim(s, 21832), None) for s in range(3)]
check("a long stream's weak, spread-out chat delay is found", all(sure and 2 <= d <= 6 for d, sure in long_found), long_found)
fp = [detect.estimate_delay(*_sim(100 + s, n, couple=False), None)[1] for n in (1800, 7200, 21832) for s in range(8)]
check("random data is never 'sure' of a delay, short or long", not any(fp), sum(fp))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
