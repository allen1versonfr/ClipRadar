"""
Unit tests for 3.10 sources: telling Twitch, YouTube and X links apart (videos, finished streams,
livestreams, Shorts, posts, channels), start times, what yt-dlp's metadata means, plain-words
errors, links back to the source, YouTube chat replays, and YouTube livestreams in live mode.

  python tests/test_sources.py
"""

from __future__ import annotations

import json
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
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402
import live  # noqa: E402
import sources  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


C = sources.classify
k = lambda t: (C(t)["platform"], C(t)["kind"], C(t)["id"])  # noqa: E731

check("Twitch VOD", k("https://www.twitch.tv/videos/2290001234") == ("twitch", "vod", "2290001234"))
check("Twitch VOD id alone", k("2290001234") == ("twitch", "vod", "2290001234"))
check("Twitch VOD start time", C("https://www.twitch.tv/videos/22900012?t=1h2m3s")["start"] == 3723)
check("Twitch channel", k("twitch.tv/JasonTheWeen") == ("twitch", "channel", "jasontheween"))
check("Twitch clip", C("https://clips.twitch.tv/FunnyClipName-abc")["kind"] == "clip")
check("YouTube video", k("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1") == ("youtube", "video", "dQw4w9WgXcQ"))
check("youtu.be with a start time", k("https://youtu.be/dQw4w9WgXcQ?t=90") == ("youtube", "video", "dQw4w9WgXcQ")
      and C("https://youtu.be/dQw4w9WgXcQ?t=90")["start"] == 90)
check("YouTube 1m30s start", C("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s")["start"] == 90)
check("YouTube Short", k("https://youtube.com/shorts/dQw4w9WgXcQ") == ("youtube", "short", "dQw4w9WgXcQ"))
check("YouTube live link", k("https://www.youtube.com/live/dQw4w9WgXcQ?si=x") == ("youtube", "live", "dQw4w9WgXcQ"))
check("YouTube channel's live page", C("https://www.youtube.com/@SomeChannel/live")["kind"] == "live")
check("YouTube channel", C("https://www.youtube.com/@SomeChannel")["kind"] == "channel")
check("canonical YouTube link", C("youtu.be/dQw4w9WgXcQ")["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
check("X post", k("https://x.com/someone/status/1790000000000000000") == ("x", "post", "1790000000000000000"))
check("twitter.com link becomes x.com", C("https://twitter.com/someone/status/179?s=20")["url"] == "https://x.com/someone/status/179")
check("file path", C(r"C:\Users\me\Videos\stream.mp4")["platform"] == "local")
check("labels read naturally", C("https://youtu.be/dQw4w9WgXcQ")["label"] == "YouTube video"
      and C("https://www.twitch.tv/videos/123456")["label"] == "Twitch past broadcast")
check("a folder per source", sources.workdir_name(C("https://youtu.be/dQw4w9WgXcQ"), "x") == "yt_dQw4w9WgXcQ"
      and sources.workdir_name(C("https://www.twitch.tv/videos/123456"), "x") == "vod_123456"
      and sources.workdir_name(C("https://x.com/a/status/99"), "x") == "x_99")

# ---------------------------------------------------------------- what yt-dlp's metadata says
d = sources.describe({"live_status": "was_live", "title": "Big stream", "uploader": "Chan", "upload_date": "20261001",
                      "duration": 7200, "extractor_key": "Youtube"})
check("a finished YouTube livestream is a VOD", d["kind"] == "vod" and d["label"] == "YouTube past broadcast"
      and d["uploaded"] == "2026-10-01" and not d["problem"], d)
d = sources.describe({"live_status": "not_live", "extractor_key": "Youtube"})
check("an ordinary YouTube video", d["kind"] == "video" and d["label"] == "YouTube video")
check("live now", sources.describe({"live_status": "is_live", "extractor_key": "Youtube"})["live"])
check("hasn't started", "hasn't started" in sources.describe({"live_status": "is_upcoming", "extractor_key": "Youtube"})["problem"])
check("still processing", "processing" in sources.describe({"live_status": "post_live", "extractor_key": "Youtube"})["problem"])
check("members only", "members" in sources.describe({"availability": "subscriber_only", "extractor_key": "Youtube"})["problem"])
check("an X post without a video", "doesn't have a video" in sources.describe({"extractor_key": "Twitter", "title": "x"},
                                                                               C("https://x.com/a/status/1"))["problem"])

# ---------------------------------------------------------------- errors in plain words
E = sources.explain
check("private", E("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "youtube") == "That video is private.")
check("age-restricted", "age-restricted" in E("Sign in to confirm your age. This video may be inappropriate for some users.", "youtube")
      and "YouTube" in E("Sign in to confirm your age", "youtube"))
check("members only", "members" in E("Join this channel to get access to members-only content like this video", "youtube"))
check("removed", "isn't available" in E("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader"))
check("no video in the post", "doesn't have a video" in E("ERROR: [twitter] 1: No video could be found in this tweet", "x"))
check("X needs a sign-in", "signed in" in E("ERROR: [twitter] 1: NSFW tweet requires authentication", "x") and "X" in E("login required", "x"))
check("geo-blocked", "country" in E("The uploader has not made this video available in your country"))
check("rate limited", "limiting" in E("HTTP Error 429: Too Many Requests", "youtube"))
check("anything else: first line, tidied", E("ERROR: something odd\nmore") == "something odd")

# ---------------------------------------------------------------- links back to the source
check("YouTube link at a moment", sources.link_at({"source": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, 75.9) == "https://youtu.be/dQw4w9WgXcQ?t=75")
check("Twitch link at a moment", sources.link_at({"source": "https://www.twitch.tv/videos/123456"}, 3723) == "https://www.twitch.tv/videos/123456?t=1h2m3s")
check("X: the post", sources.link_at({"source": "https://x.com/a/status/9"}, 10) == "https://x.com/a/status/9")
check("files: no link", sources.link_at({"source": "C:/v.mp4"}, 10) == "")
check("link template for the app", sources.link_template({"source": "https://youtu.be/dQw4w9WgXcQ"}) == "https://youtu.be/dQw4w9WgXcQ?t={s}")

# ---------------------------------------------------------------- YouTube chat replay
lines = []
for i, (t, who, txt) in enumerate([(5.0, "Ann", "lol"), (6.5, "Bob", "no way"), (7.0, "Cat", None)]):
    item = {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": txt}] if txt else
                                                        [{"emoji": {"shortcuts": [":face-with-tears-of-joy:"]}}]},
                                            "authorName": {"simpleText": who}}}
    lines.append({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": item}}], "videoOffsetTimeMsec": str(int(t * 1000))}})
lines.append({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
    "message": {"runs": [{"text": "great stream"}]}, "authorName": {"simpleText": "Dan"}}}}}], "videoOffsetTimeMsec": "9000"}})
p = os.path.join(tempfile.mkdtemp(), "chat.json")
with open(p, "w", encoding="utf-8") as f:
    f.write("\n".join(json.dumps(x) for x in lines))
chat = engine.load_chat(p)
check("YouTube chat replay is read", [m[:3] for m in chat[:2]] == [(5.0, "lol", "ann"), (6.5, "no way", "bob")]
      and chat[2][1] == ":face-with-tears-of-joy:" and chat[3][3] == "superchat", chat)

# ---------------------------------------------------------------- YouTube livestreams in live mode
work = tempfile.mkdtemp()
s = live.LiveSession("t1", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", work, events=False)
check("live mode takes a YouTube livestream link", s.via_ytdlp and s.channel is None and s.chat_state() == "none")
os.environ["CLIPRADAR_TEST"] = "1"
os.environ["CLIPRADAR_TEST_YT_HLS"] = "http://127.0.0.1:9/live.m3u8"
check("its playlist comes from yt-dlp (here: the test stand-in)", s._resolve() == "http://127.0.0.1:9/live.m3u8" and s.title == "testchannel (live)", s.title)
try:
    live.LiveSession("t2", "https://x.com/a/status/1", work, events=False)
    check("an X post isn't a livestream", False)
except ValueError as e:
    check("an X post isn't a livestream", "YouTube" in str(e))

import server  # noqa: E402
check("the same stream twice is caught", server._same_source("twitch.tv/abc", "https://www.twitch.tv/abc")
      and server._same_source("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3")
      and not server._same_source("twitch.tv/abc", "twitch.tv/abd"))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
