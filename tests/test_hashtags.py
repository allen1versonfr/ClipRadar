"""
Unit tests for hashtags: real '#Tag' text in file names (Windows-safe, no duplicates, within a
safe length, kept on retry), and post-caption tags configured separately (all posts, a streamer,
a platform).

  python tests/test_hashtags.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import exports  # noqa: E402
import hashtags as H  # noqa: E402

failures = []
WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- tags themselves
check("a tag keeps its '#' and case", H.tag("#KaiCenat") == "#KaiCenat")
check("spaces and symbols are dropped from a tag", H.tag("kai cenat!") == "#kaicenat", H.tag("kai cenat!"))
check("Windows-forbidden characters never survive", H.tag('#a<b>c:d"e/f\\g|h?i*j') == "#abcdefghij")
check("unicode letters are kept", H.tag("#스트리머") == "#스트리머")
check("duplicates go, ignoring case", H.tags(["#Gaming", "#gaming", "GAMING", "#Clips"]) == ["#Gaming", "#Clips"])
check("a list can be typed with spaces or commas", H.tags("#a, #b  c") == ["#a", "#b", "#c"])

# ---- file names
H.save({"file": {"general": ["#TwitchClips", "#Gaming"], "streamers": {"Kai Cenat": ["#KaiCenat", "#Mafia"]}, "max": 4}})
ft = H.file_tags("Kai Cenat")
check("a streamer's own tags, then the general ones, no repeats", ft == ["#KaiCenat", "#Mafia", "#TwitchClips", "#Gaming"], ft)
name = H.with_tags("KaiCenat_FunnyMoment", ft)
check("the file name carries the real tags", name == "KaiCenat_FunnyMoment_#KaiCenat_#Mafia_#TwitchClips_#Gaming", name)
check("another streamer doesn't get Kai Cenat's community tags", "#Mafia" not in H.file_tags("xQc"), H.file_tags("xQc"))
check("but does get their own name and the general tags", H.file_tags("xQc") == ["#xQc", "#TwitchClips", "#Gaming"], H.file_tags("xQc"))
yt = H.file_tags("GeminiTay", platform="YouTube")
check("a YouTube video gets #YouTubeClips, not #TwitchClips", yt == ["#GeminiTay", "#YouTubeClips", "#Gaming"], yt)
check("a Twitch VOD and a file keep #TwitchClips", "#TwitchClips" in H.file_tags("xQc", platform="Twitch")
      and "#TwitchClips" in H.file_tags("xQc", platform="Local"))
check("a tag already in the title isn't added twice", H.with_tags("Clip #Gaming", ["#gaming", "#TwitchClips"]) == "Clip #Gaming_#TwitchClips")
long_title = "This is an extremely long clip title that goes on and on " * 4
n = H.with_tags(long_title, ft)
check("long names stay within the limit", len(n) <= H.NAME_LIMIT, len(n))
check("tags give way from the end before the title is cut short", "#KaiCenat" in n and len(n.split("_#")[0]) >= H.MIN_BASE, n)
n = H.with_tags("Ünïcödé 名前 ✨ clip", ["#Clips"])
check("unicode titles keep their characters", n.startswith("Ünïcödé 名前 ✨ clip_#Clips"), n)
n = H.with_tags('What? "No" way: a/b\\c|d*', ["#Clips"])
check("Windows-forbidden characters are removed from the title, '#' stays", not WIN_BAD.search(n) and n.endswith("_#Clips"), n)
H.save({"file": {"enabled": False}})
check("switched off: no tags in file names", H.file_tags("Kai Cenat") == [])
H.save({"file": {"enabled": True}})

# ---- through the render queue: chosen once, kept on retry, a clash adds (2) without losing tags
import server  # noqa: E402
out = tempfile.mkdtemp(prefix="vc_out_")
it = {"id": "a", "title": "Chat lost it", "start": 0, "name_fields": exports.fields_for({"title": "Chat lost it"},
      {"streamer": "Kai Cenat", "title": "s"}, "tiktok"), "export": {"container": "mp4", "template": "{title}"}}
n1 = server._file_name(it, out)
check("an export's file name has its tags", n1 == "Chat lost it_#KaiCenat_#Mafia_#TwitchClips_#Gaming.mp4", n1)
H.save({"file": {"general": ["#Different"]}})
check("a retry keeps the same name (and tags)", server._file_name(it, out) == n1)
H.save({"file": {"general": ["#TwitchClips", "#Gaming"]}})
open(os.path.join(out, n1), "w").close()
it2 = dict(it, id="b", name=None, folder=None)
n2 = server._file_name(it2, out)
check("another clip with the same title gets (2), tags intact", n2 == "Chat lost it_#KaiCenat_#Mafia_#TwitchClips_#Gaming (2).mp4", n2)
check("the name is a valid Windows file name", not WIN_BAD.search(n2) and len(n2) < 200)

# ---- post captions: separate lists
H.save({"post": {"global": ["#fyp"], "streamers": {"kaicenat": ["#KaiCenatClips"]},
                 "platforms": {"shorts": ["#Shorts"], "tiktok": ["#TikTokClips"]}, "max": 6}})
auto = ["#twitch", "#kaicenat", "#funny"]
pt = H.post_tags(auto, "Kai Cenat", "shorts")
check("post tags: the clip's own, then every post, this streamer, this platform", pt == ["#twitch", "#kaicenat", "#funny", "#fyp", "#KaiCenatClips", "#Shorts"], pt)
check("a platform's tags only on that platform", "#Shorts" not in H.post_tags(auto, "Kai Cenat", "tiktok"))
check("kept to the number you set", len(H.post_tags(auto + ["#a", "#b", "#c"], "Kai Cenat", "tiktok")) == 6)
check("file-name tags and post tags are separate", "#fyp" not in H.file_tags("Kai Cenat") and "#Mafia" not in pt)
H.save({"post": {"auto": False}})
check("Clip Radar's own tags can be left out of posts", H.post_tags(auto, "Kai Cenat", "shorts")[0] == "#fyp")

# ---- kept across restarts (it's a file), and the settings API previews both
H._FILE()  # same path every time
check("settings survive a restart", json.load(open(H._FILE(), encoding="utf-8"))["post"]["global"] == ["#fyp"])
srv, url = server.start(7994)
v = json.loads(urllib.request.urlopen(url + "/api/hashtags?streamer=Kai%20Cenat", timeout=10).read())
check("the settings page previews a file name", v["preview"]["file"].endswith("_#KaiCenat_#Mafia_#TwitchClips_#Gaming.mp4"), v["preview"]["file"])
check("and each platform's post tags", "#Shorts" in v["preview"]["post"]["shorts"] and "#Shorts" not in v["preview"]["post"]["x"], v["preview"]["post"])
server.SHUTTING_DOWN[0] = True
srv.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
