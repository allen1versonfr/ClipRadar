"""
Unit tests for the 3.1 clip-finding parts: chat cleaning, Twitch ad markers,
smart clip boundaries, sentence-aligned cuts, the clip estimate, learning,
the laughter detector's chunking and the Twitch sign-in helpers.

  python tests/test_signals.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import chatsignal  # noqa: E402
import engine  # noqa: E402
import learning  # noqa: E402
import live  # noqa: E402
import twitchauth  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ------------------------------------------------------------------ chat
sc = chatsignal.ChatScorer()
w_bot = sc.weight(1, "Follow me on twitter KEKW", "nightbot")
w_cmd = sc.weight(2, "!enter", "alice")
w_sub = sc.weight(3, "", "bob", "subgift")
w_sys = sc.weight(4, "thanks for the gifted sub!", "carl")
w_a = sc.weight(5, "KEKW KEKW", "dave")
w_a2 = sc.weight(6, "KEKW KEKW", "dave")
check("bots, commands and notices count for nothing", w_bot == w_cmd == w_sub == w_sys == 0, [w_bot, w_cmd, w_sub, w_sys])
check("one person spamming counts about once", w_a > 1 and w_a2 < w_a * 0.3, f"{w_a:.2f} then {w_a2:.2f}")
pasta = "THIS IS THE BEST STREAM EVER MADE PogChamp PogChamp"
ws = [sc.weight(20 + i * 0.1, pasta, f"p{i}") for i in range(5)]
check("copypasta is played down after a couple of copies", ws[3] < ws[0] * 0.2, [round(x, 2) for x in ws])
sc2 = chatsignal.ChatScorer()
before = sc2.weight(100, "LETSGO", "x1")
sc2.weight(101, "", "raider", "raid")
after = sc2.weight(105, "LETSGO", "x2")
check("a raid damps chat for a while", after < before * 0.3 and sc2.summary()["raids"] == 1, f"{before:.2f} -> {after:.2f}")
check("human() drops the noise", len(chatsignal.human([(1, "hi", "a", ""), (2, "!points", "b", ""), (3, "x", "nightbot", ""),
                                                        (4, "", "c", "sub")])) == 1)

# ------------------------------------------------------------------ Twitch playlists and ads
TW = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:100
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00.000Z
#EXTINF:2.000,live
https://edge/seg100.ts
#EXT-X-DATERANGE:ID="stitched-ad-1-30",CLASS="twitch-stitched-ad",START-DATE="2026-01-01T00:00:02.000Z",DURATION=4.000
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:02.000Z
#EXTINF:2.000,Amazon|123
https://edge/ad1.ts
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:04.000Z
#EXTINF:2.000,live
https://edge/ad2.ts
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:06.000Z
#EXTINF:2.000,live
https://edge/seg103.ts
#EXT-X-TWITCH-PREFETCH:https://edge/seg104.ts
"""
pl = live.parse_playlist(TW, "https://edge/playlist.m3u8")
check("Twitch ad segments recognised (by title and by date range)",
      [s["ad"] for s in pl["segments"]] == [False, True, True, False] and pl["segments"][0]["seq"] == 100,
      [(s["uri"].rsplit("/", 1)[-1], s["ad"]) for s in pl["segments"]])
check("prefetch hints aren't treated as segments", len(pl["segments"]) == 4)
NOCLOCK = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:5
#EXTINF:2.0,
a.ts
#EXT-X-DATERANGE:ID="stitched-ad-9",CLASS="twitch-stitched-ad",DURATION=4
#EXTINF:2.0,
b.ts
#EXTINF:2.0,
c.ts
#EXTINF:2.0,
d.ts
"""
pl = live.parse_playlist(NOCLOCK, "http://x/live/index.m3u8")
check("ad length is trusted when the playlist has no clock", [s["ad"] for s in pl["segments"]] == [False, True, True, False]
      and pl["segments"][0]["uri"] == "http://x/live/a.ts", [(s["uri"], s["ad"]) for s in pl["segments"]])
MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,VIDEO="chunked"
https://v/1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,VIDEO="720p60"
https://v/720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=160000,CODECS="mp4a.40.2",VIDEO="audio_only"
https://v/audio.m3u8
"""
pl = live.parse_playlist(MASTER, "https://v/master.m3u8")
check("picks the best quality allowed", live.pick_variant(pl["variants"], 1080) == "https://v/1080.m3u8"
      and live.pick_variant(pl["variants"], 720) == "https://v/720.m3u8")
check("fragmented MP4 playlists go to ffmpeg", live.parse_playlist("#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:2,\na.m4s\n", "http://x/")["unsupported"])

# ------------------------------------------------------------------ smart boundaries
n = 600
s = np.zeros(n, dtype=np.float32)
s[280:300] = np.linspace(0.5, 3, 20)       # build-up
s[300:310] = 8                             # the moment
s[310:330] = np.linspace(6, 0.2, 20)       # reaction dies down
an = engine.Analysis(float(n), np.zeros(n, np.float32), np.zeros(n, np.float32), False, True)
an.score = s
st, en, done = engine.smart_bounds(an, 305)
check("smart cut includes the build-up and the reaction", st <= 286 and 320 <= en <= 335 and done, (st, en))
check("smart cut stays within 15-90 s", 15 <= en - st <= 90, en - st)
s2 = np.zeros(n, dtype=np.float32)
s2[100:400] = 6
an.score = s2
st, en, _ = engine.smart_bounds(an, 250)
check("a very long moment is cut to 90 s", en - st <= 90.5, (st, en))
an.score = s
st, en, done = engine.smart_bounds(an, 305, avail=315)
check("live: waits while the reaction is still going", not done, (st, en))
check("estimate counts the strong moment", engine.estimate(an)["strong"] == 1, engine.estimate(an))

# ------------------------------------------------------------------ sentence-aligned cuts
words = []
t = 10.0
for sent in ["So I walked into the room.", "And then the whole thing just fell over!", "Chat I swear."]:
    for wd in sent.split():
        words.append({"start": t, "end": t + 0.3, "word": wd})
        t += 0.35
    t += 1.0
mid = words[9]                                    # inside the second sentence
a2, b2 = engine.refine_with_words(mid["start"] + 0.1, words[12]["end"] - 0.1, mid["start"], words)
sent2 = [w for w in words if w["start"] >= words[6]["start"] and w["end"] <= words[13]["end"]]
check("start moves back to the start of the sentence", abs(a2 - (words[6]["start"] - 0.2)) < 0.01, (a2, words[6]["start"]))
check("end moves on to the end of the sentence", abs(b2 - (words[13]["end"] + 0.35)) < 0.01, (b2, words[13]["end"]))

# ------------------------------------------------------------------ learning
feats = []
for i in range(30):
    laugh = float(i % 2)
    feats.append(({"chat": 1.0, "audio": 3.0 - 2 * laugh, "laugh": laugh, "scream": 0, "cheer": 0, "speech": 0,
                   "length": 0.4, "clipreq": 0, "pos": i / 30}, "keep" if laugh else "skip"))
for i, (f, act) in enumerate(feats):
    learning.record("streamer_a", f"vod|{i}", f, act)
adj = learning.adjuster("streamer_a")
up, down = adj([dict(feats[1][0]), dict(feats[0][0])]) if adj else (0, 0)
check("learning moves laughter up for a streamer who keeps laughs", adj is not None and up > 0.5 and down < -0.5, (up, down))
other = learning.describe("someone_else")
check("other streamers fall back to all your picks", other["active"] and other["using"] == "all your picks", other)
learning.forget("streamer_a")
check("forgetting a streamer", learning.describe("streamer_a")["decisions"] == 0)

# ------------------------------------------------------------------ laughter detector chunking
import audio_events  # noqa: E402
if audio_events.available():
    rng = np.random.default_rng(0)
    wave = (rng.standard_normal(32000 * 37) * 0.05).astype(np.float32)
    whole = audio_events.Scanner()
    whole.feed(wave, final=True)
    parts = audio_events.Scanner()
    for k in range(0, wave.size, 32000 * 3 + 123):
        parts.feed(wave[k:k + 32000 * 3 + 123])
    parts.feed(np.zeros(0, np.float32), final=True)
    r1, r2 = whole.result(38), parts.result(38)
    check("laughter detector: same answer however the audio arrives",
          all(np.allclose(r1[k], r2[k], atol=1e-4) for k in r1), {k: float(np.abs(r1[k] - r2[k]).max()) for k in r1})
    check("laughter detector: a value for every second", len(r1["laugh"]) == 38 and np.isfinite(r1["laugh"]).all())
else:
    print("[skip] laughter detector model not present")

# ------------------------------------------------------------------ Twitch sign-in helpers
check("token from a cookie line", twitchauth.clean_token("auth-token=abcdefghijklmnopqrstuvwxyz0123") == "abcdefghijklmnopqrstuvwxyz0123")
check("token from 'OAuth xyz'", twitchauth.clean_token("OAuth abcdefghijklmnopqrstuvwxyz0123") == "abcdefghijklmnopqrstuvwxyz0123")
try:
    twitchauth.clean_token("not a token")
    check("rejects junk", False)
except ValueError:
    check("rejects junk", True)
twitchauth._save({"token": "abcdefghijklmnopqrstuvwxyz0123"})
with twitchauth.cookie_file() as cf:
    body = open(cf, encoding="utf-8").read()
    gone_later = cf
check("cookie file for yt-dlp", ".twitch.tv\tTRUE\t/\tTRUE\t0\tauth-token\tabcdefghijklmnopqrstuvwxyz0123" in body
      and not os.path.exists(gone_later))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
