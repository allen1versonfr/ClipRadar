"""
Unit tests for 3.8 live clipping: a made-up stream with known moments replayed through live
detection (precision, recall, boundaries, delay), chat that drops or never was, the chat delay
worked out live, repeats, the minimum score, early candidates, latency, and priority under load.

  python tests/test_live.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import live_replay as lr  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


N = 1800
TRUTH = [300, 520, 800, 1100, 1400]
DELAY = 7


def stream(seed=3, waves=()):
    rng = np.random.default_rng(seed)
    loud = (-30 + rng.normal(0, 1.5, N)).astype(np.float32)
    chat = []
    for t in range(N):                         # ordinary chat: a few messages a second
        for _ in range(rng.poisson(1.5)):
            chat.append((t + rng.random(), "hello chat", f"u{rng.integers(0, 400)}"))
    for t in TRUTH + list(waves):
        loud[t:t + 3] = -6                      # something happens on stream
        for k in range(70):                     # and chat reacts a few seconds later, many people at once
            chat.append((t + DELAY + rng.random() * 6, "KEKW KEKW", f"r{t}_{k}"))
    loud[650] = -4                              # a door slams; nobody cares
    for k in range(80):                         # one person spamming
        chat.append((950 + k * 0.25, "LUL LUL LUL", "spammer"))
    return loud, sorted(chat)


truth = [{"at": float(t)} for t in TRUTH]
loud, chat = stream()

s = lr.replay(loud, chat=chat)
r = lr.score(s.found, truth)
check("finds the real moments (recall)", r["recall"] >= 0.8, r)
check("few clips that aren't real moments (precision)", r["precision"] >= 0.8, r)
check("ignores a lone loud blip and one person spamming", not any(600 <= f["peak"] <= 700 or 930 <= f["peak"] <= 975
                                                                  for f in s.found), [f["peak"] for f in s.found])
check("clips keep the setup and the reaction", r["boundaries_ok"] >= 0.8, r)
check("moments are cut within a minute of stream", r["delay_median"] is not None and r["delay_median"] <= 60, r)
check("works out how late chat reacts, live", s.delay_sure and abs(s.chat_delay - DELAY) <= 1.5, (s.chat_delay, s.delay_sure))
check("no moment left half-formed at the end", not s.forming and not s.pending)
lat = [f["lat"] for f in s.found if not f["manual"]]
check("latency measured per stage", lat and all({"ingest", "detect", "context"} <= set(x) and x["detect"] >= 0 for x in lat), lat[:2])
check("latency summary for the live screen", {"ingest", "detect", "context"} <= set(s.latency_summary()), s.latency_summary())
check("live status reports chat, delay, minimum and candidates",
      {"chat_state", "chat_delay", "min_quality", "forming", "latency", "skipped"} <= set(s.public()))

# chat drops half-way (or gets so delayed it's useless): keeps finding moments from the stream itself
s2 = lr.replay(loud, chat=chat, chat_lost_at=900)
r2 = lr.score(s2.found, truth)
later = [t for t in (1100, 1400) if t not in r2["missed"]]
check("chat drops: still finds moments from the stream", len(later) == 2, r2)
check("chat drops: reported as gone quiet", s2.chat_state() == "quiet", s2.chat_state())

# no chat at all
s3 = lr.replay(loud, chat_on=False)
r3 = lr.score(s3.found, truth)
check("no chat: works from audio alone", r3["recall"] >= 0.8 and s3.chat_state() == "none", r3)

# the same moment in two waves is one clip
loud4, chat4 = stream(seed=4, waves=(1612,))
s4 = lr.replay(loud4, chat=chat4, sensitivity="lots")
near = [f for f in s4.found if 1590 <= f["peak"] <= 1640]
check("two waves of one moment: one clip", len(near) == 1, [(f["start"], f["end"], f["peak"]) for f in near])
peaks = sorted(f["peak"] for f in s4.found if not f["manual"])
check("no two automatic moments within 20 s", all(b - a >= 20 for a, b in zip(peaks, peaks[1:])), peaks)

# minimum score: a weaker real moment gets through at 0 and not above its score
loud7 = loud.copy()
loud7[1650:1653] = -25
chat7 = sorted(chat + [(1650 + DELAY + i * 0.3, "LUL", f"w{i}") for i in range(9)])
weak = next((f for f in lr.replay(loud7, chat=chat7).found if 1640 <= f["peak"] <= 1665), None)
check("a weaker moment is found with no minimum", weak is not None and weak["quality"] < 95, weak)
if weak:
    s5 = lr.replay(loud7, chat=chat7, min_quality=weak["quality"] + 1)
    check("a minimum score above it leaves it out, keeps stronger ones", all(f["quality"] > weak["quality"] for f in s5.found)
          and s5.skipped["confidence"] >= 1 and len(s5.found) >= 4, ([f["quality"] for f in s5.found], s5.skipped))
s6 = lr.replay(loud, chat=chat, min_quality=0)
check("minimum 0 keeps the default behaviour", len(s6.found) == len(s.found))

# early candidates appear before the moment is cut
seen = []


class Peek(lr.ReplaySession):
    def _detect(self, final=False):
        super()._detect(final)
        if self.forming:
            seen.append(dict(next(iter(self.forming.values()))))


orig = lr.ReplaySession
lr.ReplaySession = Peek
try:
    lr.replay(loud, chat=chat)
finally:
    lr.ReplaySession = orig
check("a moment shows as forming before it's cut", seen and "quality" in seen[0] and any(abs(x["at"] - 302) < 15 for x in seen),
      seen[:2])

# priority under load: the strongest moment waiting for Whisper goes first, your own clip before all
import server  # noqa: E402

g = server.PriorityGate(1)
order = []
g.acquire(0)                                       # something is already being listened to


def job(name, prio):
    g.acquire(prio)
    order.append(name)
    time.sleep(0.05)
    g.release()


ts = [threading.Thread(target=job, args=a) for a in (("weak", 40), ("strong", 80), ("yours", 1000))]
for t in ts:
    t.start()
    time.sleep(0.1)
g.release()
for t in ts:
    t.join(5)
check("under load: your clip first, then the strongest", order == ["yours", "strong", "weak"], order)
check("chat delay setting: number or auto", server._live_delay("") == "auto" and server._live_delay("8") == 8.0
      and server._live_delay("x") == "auto" and server._live_delay(99) == 30.0)

# ---- live and VOD treat chat the same way
import detect  # noqa: E402
import emotes as emotes_mod  # noqa: E402
es = emotes_mod.EmoteSet({"7tv": ["catJAM", "Pepega"]})
loud8, chat8 = stream(seed=8)
for t in range(30, N, 25):                       # the channel's emotes in everyday use
    chat8.append((t + 0.3, "Pepega", f"p{t}"))
for k in range(60):                              # a wave of a channel emote nobody's built-in list knows
    chat8.append((1700 + DELAY + k * 0.1, "catJAM catJAM", f"e{k}"))
s8 = lr.replay(loud8, chat=sorted(chat8), emote_set=es)
s8.chat_usable = lambda: True              # the replay has ended, so chat reads as gone quiet; look at it as it was
an8 = s8._analysis(len(s8.loud))
check("live counts channel emote waves, like VODs", an8.emote_z is not None and float(an8.emote_z[1690:1715].max()) > 1,
      None if an8.emote_z is None else float(an8.emote_z[1690:1715].max()))
vod_rate, _ = detect.emote_rate([(m[0], m[1], m[2], "") for m in sorted(chat8)], N, s8.chat_delay, es)
live_rate = an8.emote_rate
vod_z = detect.emote_signal(vod_rate)
check("and the emote wave stands out just as it does for a VOD", an8.emote_z is not None
      and abs(float(vod_z[1690:1715].max()) - float(an8.emote_z[1690:1715].max())) <= 0.5,
      (float(vod_z[1690:1715].max()), None if an8.emote_z is None else float(an8.emote_z[1690:1715].max())))
check("with the same per-second emote count as a VOD", live_rate is not None
      and abs(float(vod_rate[1690:1720].sum()) - float(live_rate[1690:1720].sum())) <= 2,
      (float(vod_rate[1690:1720].sum()), None if live_rate is None else float(live_rate[1690:1720].sum())))
s8b = lr.replay(loud8, chat=sorted(chat8))
s8b.chat_usable = lambda: True
check("before the emotes load, live works as before", s8b._analysis(len(s8b.loud)).emote_z is None)

loud9 = (-30 + np.random.default_rng(9).normal(0, 1.5, N)).astype(np.float32)
chat9 = [(t + 0.5, "hello chat", f"u{t % 300}") for t in range(0, N, 1)]
chat9 += [(900 + k * 0.1, "F", f"f{k}") for k in range(80)]          # an F wave, nothing on stream
s9 = lr.replay(loud9, chat=sorted(chat9))
check("an F wave alone doesn't make a live moment either", not any(880 <= f["peak"] <= 930 for f in s9.found),
      [f["peak"] for f in s9.found])

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
