"""
Unit tests for 4.32 detection: chat read better (laughter in many languages, stretched words, one
message repeating itself), a chat surge that's plain talk counting for less, and chat and the stream's
sound agreeing. Ends with simulated 3-hour streams that have real moments and decoys in known places.

  python tests/test_reactions.py
"""

from __future__ import annotations

import os
import sys
import tempfile

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

import chatsignal  # noqa: E402
import detect  # noqa: E402
import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


w = engine.chat_weight

# ---------------------------------------------------------------- reading chat
laughs = ["hahahaha", "AHAHAHA", "hehehe", "jajajaja", "kkkkkk", "kakaka", "wwwww", "ㅋㅋㅋㅋ", "хахаха", "mdr",
          "ptdr", "LMAOOOO", "lmfaooo", "LOOOOL", "XDDD", "草草草", "哈哈哈", "rofl", "😭😭", "im dead 💀"]
check("laughter typed out, in many languages, counts as a reaction", all(w(t) >= 2.0 for t in laughs),
      [(t, w(t)) for t in laughs if w(t) < 2.0])
stretched = {"POGGGG": "pog", "KEKWWW": "kekw", "LETSGOOOO": "letsgo", "wtffff": "wtf", "omgggg": "omg"}
check("stretched words count like the word", all(w(k) >= w(v) for k, v in stretched.items()),
      [(k, w(k), w(v)) for k, v in stretched.items()])
check("ordinary words only react when stretched", w("what") == 1.0 and w("no") == 1.0 and w("yes") == 1.0
      and w("whaaaat") > 1.0 and w("noooooo") > 1.0 and w("yesss") > 1.0)
plain = ["germany", "where are you from", "i had pasta today", "how long you been live", "good morning chat",
         "the new update is out", "what game is this", "is this ranked", "i went to the shop", "cool", "book", "www"]
check("plain talk stays at 1", all(w(t) == 1.0 for t in plain[:-1]), [(t, w(t)) for t in plain if w(t) != 1.0])
check("one message repeating an emote isn't many people", w("KEKW KEKW KEKW KEKW KEKW KEKW") < w("KEKW") + 1.5,
      w("KEKW KEKW KEKW KEKW KEKW KEKW"))
check("but still more than one", w("KEKW KEKW") > w("KEKW"))
check("reactions in phrases", w("NO SHOT") > 1.0 and w("oh my god") > 1.0 and w("holy") > 1.0)
# 4.33: capitals counted a little in 4.32; on a real YouTube stream that made "CALL HIM" and "SHOW ON SCREEN"
# (chat demanding something) read as reactions
check("capitals alone aren't a reaction", w("CALL HIM") == 1.0 and w("SHOW ON SCREEN") == 1.0)
check("clip requests still count most", w("clip it") >= 4.0)
check("is_reaction", engine.is_reaction("LMAOOO") and not engine.is_reaction("germany") and not engine.is_reaction(""))
check("words for reactions aren't added to the words chat titles leave out", "hahaha" not in engine.HYPE
      and "holy" not in engine.HYPE)

check("a number on its own is a vote or an answer, not a reaction",
      all(chatsignal.low_info(t) for t in ("50", "5,000", "100", "$20", "4k", "1.5", "07", "F", "f f fff"))
      and not any(chatsignal.low_info(t) for t in ("1v1 me", "W", "50 is crazy", "fine")))
import time  # noqa: E402
t0 = time.time()
for t in ("f" * 300 + "!", "F " * 150 + "x", "f" * 40 + "a", "1" * 300 + "x"):
    chatsignal.low_info(t)
    chatsignal.ChatScorer().weight(1.0, t, "u", "")
# real-world test (4.33): one message of 120 f's and a "!" made the old rule backtrack for ever, so Find
# clips never finished on that stream
check("a long run of f's in one message doesn't hang", time.time() - t0 < 0.5, time.time() - t0)

sc = chatsignal.ChatScorer()
talk: list = []
ws = sc.feed([(1.0, "germany", "a", ""), (2.0, "KEKW", "b", ""), (3.0, "!points", "c", ""), (4.0, "F", "d", "")], talk)
check("the scorer marks plain talk", talk == [ws[0], 0.0, 0.0, 0.0] and ws[0] > 0, (ws, talk))

# ---------------------------------------------------------------- plain talk and agreement
rng = np.random.default_rng(11)


def make(n=1800, events=None, loud_at=(), chat_bursts=(), music=()):
    """chat_bursts: (t, kind, people) with kind 'laugh' or 'talk'; loud_at: (t, seconds, dB)."""
    loud = (-30 + rng.normal(0, 1.5, n)).astype(np.float32)
    ev = {k: np.clip(rng.normal(0.04, 0.03, n), 0, 1).astype(np.float32) for k in ("laugh", "scream", "cheer", "music")}
    chat = [(float(t), ["germany", "pizza", "hi chat", "KEKW", "lol", "nice"][rng.integers(6)], f"u{rng.integers(300)}", "")
            for t in np.sort(rng.uniform(0, n, int(n * 1.2)))]
    for t, d, db in loud_at:
        loud[t:t + d] += db
    for t, d in music:
        loud[t:t + d] += 9
        ev["music"][t:t + d] = 0.9
    for t, key, lvl in (events or ()):
        ev[key][t:t + 3] = lvl
    for t, kind, k in chat_bursts:
        pool = ["KEKW", "LMAOOO", "hahaha", "😭", "OMEGALUL"] if kind == "laugh" else ["germany", "texas", "2 hours ago", "pizza"]
        chat += [(float(t + 5 + rng.uniform(0, 10)), pool[rng.integers(len(pool))], f"{kind}{t}_{j}", "") for j in range(k)]
    chat.sort(key=lambda m: m[0])
    an = engine.score_signals(loud, chat, n + 1, float(n), 5.0, 60, events=ev)
    an.chat_delay = 5.0
    detect.enrich(an, chat, 5.0, None)
    an.combine(60, detect.preset("any"))
    return an, chat


an, chat = make(chat_bursts=[(400, "talk", 40), (900, "laugh", 40)])
check("a chat surge of plain talk counts for less", float(an.talk[405:415].min()) < 0.8, float(an.talk[405:415].min()))
check("a chat surge of reactions counts in full", float(an.talk[905:915].min()) > 0.97, float(an.talk[905:915].min()))
check("talk shows in the breakdown, and it still adds up", "talk" in detect.breakdown(an, 410)
      and abs(sum(detect.breakdown(an, 410).values()) - float(an.score[410])) < 0.3, detect.breakdown(an, 410))
calm = [(float(t), "hello chat", f"u{rng.integers(300)}", "") for t in np.sort(rng.uniform(0, 1800, 2000))]
talky = engine.score_signals(None, calm, 1801, 1800.0, 5.0, 60)
detect.enrich(talky, calm, 5.0, None)
check("a chat that only ever talks isn't marked down everywhere", talky.talk is None or float(np.median(talky.talk)) > 0.97)

an, _ = make(events=[(600, "laugh", 0.8)], loud_at=[(600, 3, 9)], chat_bursts=[(600, "laugh", 30)],
             music=[(1000, 60)])
ag = detect.agreement(an)
check("the streamer laughs and chat laughs: they agree", float(ag[600:620].max()) > 1.0, float(ag[600:620].max()))
check("a loud song nobody reacts to: next to no agreement", float(ag[1000:1060].max()) < 0.5, float(ag[1000:1060].max()))
check("ordinary chat and sound: no agreement by chance", float((ag[100:550] > 0).mean()) < 0.01, float((ag[100:550] > 0).mean()))
an2, _ = make(chat_bursts=[(600, "laugh", 30)])
check("chat alone (something on screen): no agreement, nothing taken away", float(detect.agreement(an2)[600:620].max()) == 0.0
      and float(an2.score[605:615].max()) > 3, float(an2.score[605:615].max()))
bd = detect.breakdown(an, int(np.argmax(an.score[600:625])) + 600)
check("agreement shows in the breakdown", bd.get("agree", 0) > 0, bd)
an.combine(0, detect.preset("any"))
check("chat weight 0: no agreement part", "agree" not in an.parts)
an.combine(60, {k: v for k, v in detect.preset("any").items() if k != "agree"})
check("weights saved before 4.32 (no agree) still use it", "agree" in an.parts)
check("every preset weighs agreement", all(p.get("agree", 0) > 0 for p in detect.PRESETS.values()))
sub = an.part(500, 700)
check("a part of the analysis keeps talk and messages per second", sub.talk is not None and len(sub.talk) == 200
      and sub.said_rate is not None and len(sub.said_rate) == 200)

# ---------------------------------------------------------------- the cache keeps what talk needs
d = tempfile.mkdtemp()
p = os.path.join(d, "analysis.npz")
engine._save_analysis(p, "k", an)
back = engine._load_analysis(p, "k")
check("a cached analysis keeps messages per second and talk", back is not None and np.allclose(back.said_rate, an.said_rate)
      and np.allclose(back.talk_rate, an.talk_rate))
detect.enrich(back, chat, 5.0, None)
check("and works talk out the same again", np.allclose(back.talk, an.talk))

# ---------------------------------------------------------------- simulated streams
TALK = ["germany", "im from texas", "pizza for sure", "like 2 hours ago", "brazil here", "what game is this",
        "how long you been live", "hi chat", "anyone know the song", "i had pasta today", "uk", "poland",
        "its 3am here", "first time here", "good morning", "the new update is out", "france", "canada eh"]
NEW_LAUGH = ["KEKW", "LMAOOOO", "hahahaha", "jajajaja", "kkkkkk", "ㅋㅋㅋㅋ", "😭😭", "LOOOOL", "OMEGALUL", "lmfaooo",
             "XDDD", "HAHAHAHA", "ahahahah", "LUL", "im dead 💀", "LMAO", "lmaooo", "hehehehe"]
NEW_HYPE = ["POGGGG", "LETSGOOOO", "NO SHOT", "W", "WHAT", "holy", "🤯", "NO WAY", "PogChamp", "insane", "OMGGGG", "WTFFFF"]
OLD_LAUGH = ["KEKW", "LUL", "OMEGALUL", "LMAO", "lol", "KEKW KEKW", "💀", "😂"]
OLD_HYPE = ["Pog", "PogChamp", "W", "insane", "letsgo", "omg", "wtf"]


def stream(seed, laugh, hype, react=12, answers=45, n=3 * 3600, delay=5):
    """Real moments (the streamer laughs or shouts and chat reacts; or chat alone reacts) and decoys (chat
    answering a question, with more people than any real moment; a loud song; a loud blip; one spammer)."""
    r = np.random.default_rng(seed)
    loud = (-30 + r.normal(0, 2.0, n)).astype(np.float32)
    ev = {k: np.clip(r.normal(0.04, 0.03, n), 0, 1).astype(np.float32) for k in ("laugh", "scream", "cheer", "music")}
    users = [f"user{i}" for i in range(500)]
    chat = [(float(t), TALK[r.integers(len(TALK))] if r.random() < 0.85 else laugh[r.integers(len(laugh))],
             users[r.integers(500)], "") for t in np.sort(r.uniform(0, n, int(n * 1.2)))]
    slots = list(r.permutation(np.arange(300, n - 300, 360))[:26])
    truth, decoys = [], []

    def burst(t0, texts, k, span=10, who=None):
        chat.extend((float(t0 + delay + r.uniform(0, span)), texts[r.integers(len(texts))], who or users[r.integers(500)], "")
                    for _ in range(k))

    for kind, count in (("funny", 6), ("hype", 4), ("chat", 3)):
        for _ in range(count):
            t = int(slots.pop())
            if kind != "chat":
                loud[t:t + 3] += 9 if kind == "funny" else 10
                ev["laugh" if kind == "funny" else "scream"][t:t + 3] = 0.8 if kind == "funny" else 0.7
            burst(t, hype if kind == "hype" else laugh, react + (6 if kind == "chat" else 0))
            truth.append(t)
    for _ in range(5):
        t = int(slots.pop())
        burst(t, TALK, answers, span=12)
        decoys.append(("question", t))
    for _ in range(3):
        t = int(slots.pop())
        loud[t:t + 60] += 9
        ev["music"][t:t + 60] = 0.9
        decoys.append(("music", t))
    for _ in range(3):
        t = int(slots.pop())
        loud[t] += 22
        decoys.append(("blip", t))
    for _ in range(2):
        t = int(slots.pop())
        burst(t, ["KEKW KEKW KEKW KEKW KEKW"], 25, span=20, who="spammer")
        decoys.append(("spam", t))
    chat.sort(key=lambda m: m[0])
    an = engine.score_signals(loud, chat, n + 1, float(n), float(delay), 60, events=ev)
    an.chat_delay = float(delay)
    detect.enrich(an, chat, float(delay), None)
    an.combine(60, detect.preset("any"))
    clips = engine.pick_clips(an, count=len(truth), chat=chat, chat_delay=float(delay))
    hit = lambda c, t: c.start - 2 <= t <= c.end  # noqa: E731
    found = sum(any(hit(c, t) for c in clips) for t in truth)
    fooled = [k for k, t in decoys if any(hit(c, t) for c in clips)]
    return found / len(truth), fooled


for label, laugh, hype in (("chat laughing in many ways", NEW_LAUGH, NEW_HYPE), ("chat using the usual emotes", OLD_LAUGH, OLD_HYPE)):
    res = [stream(s, laugh, hype) for s in range(4)]
    rec = float(np.mean([x for x, _ in res]))
    fooled = [k for _, f in res for k in f]
    # before 4.32: 71% (many ways) and 92% (usual emotes) of the real moments, with 15 and 4 question decoys
    # picked; now 100% and 98%, none
    check(f"3-hour streams, {label}: finds the real moments over decoys with more chat", rec >= 0.9, (rec, fooled))
    check(f"3-hour streams, {label}: chat answering a question is rarely a clip", fooled.count("question") <= 3, fooled)
    check(f"3-hour streams, {label}: loud songs, blips and a spammer aren't clips",
          not any(k in ("music", "blip", "spam") for k in fooled), fooled)

# ---------------------------------------------------------------- quality over quantity (4.33)
def score_only(n, peaks):
    """An analysis whose score is flat noise with the given (time, height) moments."""
    an = engine.score_signals(None, [], n, float(n), 6.0, 60, rate=np.zeros(n, dtype=np.float32), stats={})
    s = np.clip(rng.normal(0.3, 0.3, n), 0, None).astype(np.float32)
    for t, h in peaks:
        s[t - 3:t + 4] = np.maximum(s[t - 3:t + 4], h * np.array([0.4, 0.7, 0.9, 1, 0.9, 0.7, 0.4]))
    an.score = s
    return an


H4 = 4 * 3600
big = [(1000 + k * 3000, 12.0) for k in range(3)]
small = [(500 + k * 430, 3.5) for k in range(30) if all(abs(500 + k * 430 - t) > 120 for t, _ in big)]
few = score_only(H4, big + small)
got = engine.pick_clips(few, count=0)
# before, at least 3 an hour: 12 clips here, nine of them the small bumps
check("automatic: a 4-hour stream with 3 big moments gets those, not a number per hour",
      engine.MIN_AUTO_CLIPS <= len(got) <= 4 and all(any(abs(c.peak - t) < 10 for c in got) for t, _ in big),
      [(int(c.peak), c.quality) for c in got])
busy = score_only(H4, [(200 + k * 140, 8.0 + (k % 5)) for k in range(100)])
check("automatic: a very busy stream is held to the best", len(engine.pick_clips(busy, count=0)) <= engine.MAX_AUTO_CLIPS)
spread = score_only(H4, [(200 + k * 140, 2.0 + 10.0 * ((k * 37) % 60) / 60) for k in range(60)])
n_by = {k: engine.estimate(spread, k)["suggest"] for k in ("fewer", "normal", "more")}
check("fewer, normal, more", n_by["fewer"] < n_by["normal"] < n_by["more"], n_by)
check("a number you choose is still met", len(engine.pick_clips(few, count=20)) == 20)

# ---------------------------------------------------------------- clips up to 2 minutes (4.33)
check("every stream type allows 2-minute clips", all(p["max"] >= 120 for p in detect.PRESETS.values())
      and engine.MAX_LEN == 120)
long_an = score_only(1200, [])
long_an.score[400:520] = 6.0                     # a reaction that goes on for two minutes
st, en, _ = engine.smart_bounds(long_an, 430)
check("a long reaction gets a clip of up to 2 minutes", 95 <= en - st <= 120.5 and en >= 515, (st, en))
long_an.score[400:520] = 0.3
long_an.score[400:408] = 6.0
st, en, _ = engine.smart_bounds(long_an, 404)
check("a short one stays short", en - st <= 40, (st, en))

# ---------------------------------------------------------------- live
import live_replay as lr  # noqa: E402

N = 1500
lrng = np.random.default_rng(4)
loud = (-30 + lrng.normal(0, 1.5, N)).astype(np.float32)
lchat = [(t + lrng.random(), ["hello chat", "KEKW", "nice"][lrng.integers(3)], f"u{lrng.integers(0, 400)}")
         for t in range(N) for _ in range(lrng.poisson(1.5))]
for t in (300, 900):
    loud[t:t + 3] = -6
    lchat += [(t + 7 + lrng.random() * 6, "LMAOOO", f"r{t}_{k}") for k in range(40)]
for t in (600, 1200):            # the streamer asks where everyone's from
    lchat += [(t + 7 + lrng.random() * 8, ["germany", "texas", "uk", "brazil here"][k % 4], f"q{t}_{k}") for k in range(55)]
s = lr.replay(loud, chat=sorted(lchat))
found = [(f["peak"], f["score"]) for f in s.found if not f["manual"]]
real = [sc for p, sc in found if any(abs(p - t) < 25 for t in (300, 900))]
asked = [sc for p, sc in found if any(t <= p <= t + 30 for t in (600, 1200))]
check("live: real moments are clipped", len(real) == 2, found)
# before 4.32 both were clipped, the first scoring higher than a real moment (6.9 against 6.4). The first
# comes ten minutes in, when live mode has seen little of this chat, and can still just pass
check("live: chat answering a question is at most a weak clip", len(asked) <= 1 and all(sc < 0.4 * min(real) for sc in asked),
      found)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
