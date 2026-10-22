"""
Unit tests for YouTube videos without chat (ytextra): Most replayed, timestamped comments, YouTube
captions and chapters are read from yt-dlp-shaped data, turned into ranking signals that put clips
where viewers rewatch and point, give titles from comments, and stay out of the way when a video
has none of them.

  python tests/test_ytextra.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402
import engine  # noqa: E402
import ytextra as Y  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


DUR = 600.0
# yt-dlp's heatmap: 100 segments, value 0-1, a bump at 300-330 s
heat = [{"start_time": i * 6.0, "end_time": (i + 1) * 6.0, "value": 1.0 if 50 <= i <= 54 else 0.2 + 0.02 * (i % 3)}
        for i in range(100)]
INFO = {"duration": DUR, "language": "en", "heatmap": heat,
        "chapters": [{"start_time": 0, "end_time": 280, "title": "Intro"}, {"start_time": 280, "end_time": 600, "title": "Boss fight"}],
        "comments": [{"text": "4:20 when the dog walked in 😂", "like_count": 3, "author_id": "a1", "parent": "root"},
                     {"text": "the dog walked in at 4:21 lmao", "like_count": 0, "author_id": "a2", "parent": "root"},
                     {"text": "4:22 the dog walked in", "like_count": 1, "author_id": "a3", "parent": "root"},
                     {"text": "4:20 yes", "like_count": 0, "author_id": "a4", "parent": "reply-of-a1"},
                     {"text": "0:00 intro 1:00 setup 2:00 fight 3:00 boss 4:00 end", "like_count": 50, "author_id": "a5",
                      "parent": "root"},
                     {"text": "9:99 nope, 59:00 too late", "like_count": 0, "author_id": "a6", "parent": "root"},
                     {"text": "great video", "like_count": 900, "author_id": "a7", "parent": "root"}]}

# ---- reading
d = Y.from_info(INFO)
check("Most replayed: 100 segments read", len(d.get("heatmap", [])) == 100)
check("chapters read", [c[2] for c in d["chapters"]] == ["Intro", "Boss fight"])
check("timestamps: m:ss and h:mm:ss", Y.timestamps("at 4:20 and 1:02:03") == [260.0, 3723.0])
check("timestamps: invalid seconds and past the end are ignored", Y.timestamps("9:99 and 59:00", DUR) == [])
cs = Y.comments_from_info(INFO, DUR)
check("comments: one entry per timestamp, replies and chapter lists left out",
      sorted((c[0], c[2]) for c in cs) == [(260.0, "a1"), (261.0, "a2"), (262.0, "a3")], cs)
check("comments: the timestamp is taken out of the text", cs[0][1] == "when the dog walked in 😂", cs[0][1])
check("no heatmap or chapters: nothing made up", Y.from_info({"duration": 10}).get("heatmap") is None)

# captions: automatic (timed words) and uploaded (one line per event)
auto = {"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "no"}, {"utf8": " way", "tOffsetMs": 400},
                                                                    {"utf8": " dude", "tOffsetMs": 900}]},
                   {"tStartMs": 3000, "segs": [{"utf8": "\n"}]},
                   {"tStartMs": 4000, "dDurationMs": 1000, "segs": [{"utf8": "[Music]"}]}]}
w = Y.caption_words(auto)
check("automatic captions keep each word's time", [(x["word"], x["start"]) for x in w] == [("no", 1.0), ("way", 1.4), ("dude", 1.9)], w)
check("[Music] and empty lines are dropped", all(x["word"] != "[Music]" for x in w))
check("every word ends before the next starts", all(a["end"] <= b["start"] + 1e-9 for a, b in zip(w, w[1:])))
manual = {"events": [{"tStartMs": 10000, "dDurationMs": 3000, "segs": [{"utf8": "I can't believe that worked"}]}]}
mw = Y.caption_words(manual)
check("uploaded captions: the line's words spread over its time", len(mw) == 5 and mw[0]["start"] == 10.0
      and 12.3 < mw[-1]["start"] < 12.5, [(x["word"], x["start"]) for x in mw])
tracks = {"language": "en",
          "subtitles": {"en-GB": [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "manual"}]},
          "automatic_captions": {"en-orig": [{"ext": "json3", "url": "auto"}]}}
check("uploaded captions are preferred to automatic ones", Y.caption_track(tracks) == ("manual", False))
check("automatic captions in the video's own language otherwise",
      Y.caption_track(dict(tracks, subtitles={})) == ("auto", True))
check("no captions: none", Y.caption_track({"language": "en"}) is None)

# ---- signals
n = 601
rp = Y.replay_signal(d, n)
check("Most replayed signal peaks where viewers rewatch", rp is not None and float(rp[310]) == 1.0 and float(rp[100]) < 0.1,
      (rp[310] if rp is not None else None, rp[100] if rp is not None else None))
check("a flat graph gives no signal", Y.replay_signal({"heatmap": [[i, i + 6, 0.5] for i in range(0, 600, 6)]}, n) is None)
d["comments"] = cs
sig, people = Y.comment_signal(d, n)
check("three commenters pointing at 4:20 make a signal there", sig is not None and sig[263] > 0.3 and people[263] == 3,
      (sig[263] if sig is not None else None, people[263] if people is not None else None))
lone, _ = Y.comment_signal({"comments": [[100.0, "lol", "x", 0]]}, n)
check("one commenter alone is weak", lone is not None and float(lone.max()) <= 0.13, float(lone.max()))
check("a commenter counts once per moment", Y.comment_signal({"comments": [[100.0, "a", "x", 0], [101.0, "b", "x", 0]]}, n)[1][102] == 1)

ct = Y.comment_title(d, 255, 290)
check("comments agreeing on a phrase title the clip", ct == 'Comments: "The dog walked in"', ct)
check("no comments in the window: no title", Y.comment_title(d, 0, 100) == "")
check("chapter at a time", Y.chapter_at(d, 300) == "Boss fight" and Y.chapter_at(d, 10) == "Intro")

# ---- ranking: a talking-head video (flat sound, no chat); Most replayed and comments decide
if shutil.which("ffmpeg"):
    tmp = tempfile.mkdtemp(prefix="vc_yt_")
    vid = os.path.join(tmp, "upload.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=gray:size=320x180:rate=10:duration={int(DUR)}",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.05:duration={int(DUR)}:seed=5",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", vid], check=True)
    plain = engine.analyze_cached(tmp, vid, [], events=False)
    top_plain = engine.pick_clips(plain, count=3)
    Y.save(tmp, dict(d, words=[]))
    an = engine.analyze_cached(tmp, vid, [], events=False)
    top = engine.pick_clips(an, count=3, chat=[], chat_delay=6)
    peaks = [round(c.peak) for c in top]
    check("with Most replayed and comments, the top clip is the rewatched moment",
          top and 255 <= top[0].peak <= 335, (peaks, [c.reason for c in top]))
    check("its reason says why", top and ("replayed" in top[0].reason or "comments point here" in top[0].reason), top and top[0].reason)
    check("the chapter is named in the reason", any("Boss fight" in c.reason for c in top), [c.reason for c in top])
    check("without them, the same video ranks by sound only (no change for other sources)",
          not any(255 <= c.peak <= 335 for c in top_plain[:1]) or len(top_plain) == 0, [round(c.peak) for c in top_plain])
    os.remove(Y.path(tmp))
    again = engine.analyze_cached(tmp, vid, [], events=False)
    check("a video without youtube.json gets no YouTube signals", again.replay is None and again.comments is None)
else:
    print("[skip] no ffmpeg here: ranking not checked")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
