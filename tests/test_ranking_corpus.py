"""
Building the ranking corpus (scripts/eval_ranking.py build, docs/plans/ranking-eval.md section 5), with Twitch,
yt-dlp and the emote services stood in for: the manifest, the stretch picked at random but the same every time,
who made each clip, the viewers' clips frozen once, and a build that stops being started again.

  python tests/test_ranking_corpus.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
ROOT = os.path.normpath(os.path.join(HERE, ".."))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import emotes  # noqa: E402
import eval_ranking as er  # noqa: E402
import title_corpus as tc  # noqa: E402
import twitchclips  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


# ---------------------------------------------------------------- the manifest
TEXT = """# Ranking corpus
# a comment line

2930702722@3600-9600 cat=irl size=big lang=en  # asmongold, just chatting
2931596568 cat=fps size=small lang=es holdout
2921768812  # no tags [built 2026-01-01: 9 clips, 4 moments, 2 truth]
"""
es = er.parse_manifest(TEXT)
check("three VODs, comments and blank lines skipped", [e["vod"] for e in es] == ["2930702722", "2931596568", "2921768812"], es)
check("a stretch given in the manifest", (es[0]["a"], es[0]["b"]) == (3600, 9600))
check("none given: picked at build time", es[1]["a"] is None and es[1]["b"] is None)
check("tags", es[0]["tags"] == {"cat": "irl", "size": "big", "lang": "en"} and es[1]["tags"]["lang"] == "es", es[0]["tags"])
check("holdout", es[1]["holdout"] and not es[0]["holdout"])
check("the note, without the counts of an earlier build", es[0]["note"] == "asmongold, just chatting" and es[2]["note"] == "no tags", es[2])
check("which line each is on", [e["line"] for e in es] == [3, 4, 5])
check("a word that isn't key=value is an error", raises(lambda: er.parse_manifest("123 big")))
check("a stretch that ends before it starts is an error", raises(lambda: er.parse_manifest("123@500-400")))
check("a line that isn't a VOD id is an error", raises(lambda: er.parse_manifest("https://twitch.tv/videos/123")))
line = er.manifest_line(dict(es[1], a=100, b=3700), {"built": "2026-11-17", "clips": 50, "moments": 20, "truth": 9})
check("a built line: stretch, tags, holdout, counts",
      line == "2931596568@100-3700 cat=fps size=small lang=es holdout  # [built 2026-11-17: 50 clips, 20 moments, 9 truth]", line)
check("and reads back the same", er.parse_manifest(line)[0]["a"] == 100 and er.parse_manifest(line)[0]["holdout"])

tmp = tempfile.mkdtemp(prefix="rank_corpus_test_")
mpath = os.path.join(tmp, "manifest.txt")
with open(mpath, "w", encoding="utf-8") as f:
    f.write(TEXT)
er.write_manifest(mpath, es, {"2921768812": {"window": [0, 5000], "built": "2026-11-17", "clips": 30, "moments": 12, "truth": 8}})
after = open(mpath, encoding="utf-8").read().splitlines()
check("write_manifest fills in the built VOD only, comments kept",
      after[0] == "# Ranking corpus" and after[3] == TEXT.splitlines()[3]
      and after[5] == "2921768812@0-5000  # no tags [built 2026-11-17: 30 clips, 12 moments, 8 truth]", after)

# ---------------------------------------------------------------- where every clip is known
check("fewer clips than a page: all of them", er.coverage([10, 20], [20, 10], 5000) == [[0.0, 5000.0]])
check("two full pages that meet: all of them", er.coverage(list(range(0, 3000, 30)), list(range(9000, 2000, -70)), 9000) == [[0.0, 9000.0]])
check("two full pages that don't meet: the start and the end, a second short of each page's last clip",
      er.coverage(list(range(0, 3000, 30)), list(range(9000, 6000, -30)), 9000) == [[0.0, 2969.0], [6031.0, 9000.0]],
      er.coverage(list(range(0, 3000, 30)), list(range(9000, 6000, -30)), 9000))
check("no clips: nothing is missing", er.coverage([], [], 5000) == [[0.0, 5000.0]])

# ---------------------------------------------------------------- the stretch
check("a VOD under 3 hours: all of it", er.stretch("1", 2 * 3600) == (0, 7200))
a, b = er.stretch("2930702722", 30787)
check("a long VOD: 60 to 120 minutes inside it", 3600 <= b - a <= 7200 and 0 <= a and b <= 30787, (a, b))
check("the same every time", er.stretch("2930702722", 30787) == (a, b))
check("another seed, another stretch", er.stretch("2930702722", 30787, seed=1) != (a, b))
check("another VOD, another stretch", er.stretch("2930702723", 30787) != (a, b))
check("exactly 3 hours: a stretch, not all of it", er.stretch("5", 3 * 3600)[1] - er.stretch("5", 3 * 3600)[0] <= 7200)
gap = [[0, 2500], [20000, 30787]]
ok = all(er.inside(*er.stretch(str(v), 30787, complete=gap), gap) for v in range(40))
check("only where every clip is known", ok)
check("a short VOD with clips missing in the middle: a stretch, not all of it",
      er.inside(*er.stretch("6", 9000, complete=[[0, 4000], [6000, 9000]]), [[0, 4000], [6000, 9000]]))
st = er.stretch("7", 30000, complete=[[0, 2400], [27000, 30000]])
check("where no complete part holds 60 minutes: as long as one does (at least 30)", st is not None and st[1] - st[0] == 3000
      and er.inside(*st, [[27000, 30000]]), st)
check("under 30 minutes complete anywhere: none", er.stretch("8", 30000, complete=[[0, 1500], [29000, 30000]]) is None)

# ---------------------------------------------------------------- every clip of a VOD, with who made it
def node(cid, t, d, views=1, title="", who="", vod="777"):
    return {"node": {"id": cid, "title": title, "viewCount": views, "durationSeconds": d, "videoOffsetSeconds": t,
                     "curator": {"login": who} if who else None, "video": {"id": vod}}}


pages = {"ASC": [node("c1", 4000, 30, 900, "big one", "ann"), node("c2", 4001, 28, 40, "big one", "bob"),
                 node("x", 4000, 30, 5, "other vod", "cy", vod="9"), node("c3", 5000, 30, 3, "no curator")],
         "DESC": [node("c3", 5000, 30, 3, "no curator"), node("c2", 4001, 28, 40, "big one", "bob"),
                  node("bad", None, 30), node("c1", 4000, 30, 900, "big one", "ann")]}
asked = []
real_gql = tc.gql
tc.gql = lambda q, tries=4: (asked.append(q), {"data": {"video": {"lengthSeconds": 9000, "clips": {"edges": pages[
    "DESC" if "sort:DESC" in q else "ASC"]}}}})[1]
vc = er.vod_clips("777")
check("both ends of the VOD's own list asked for", len(asked) == 2 and "sort:ASC" in asked[0] and "sort:DESC" in asked[1]
      and 'video(id:"777")' in asked[0], asked)
check("each clip once, in order, from this VOD only, ending where Twitch's offset says (it's the end, not the start)",
      [(c["id"], c["start"], c["end"]) for c in vc["clips"]] == [("c1", 3970.0, 4000.0), ("c2", 3973.0, 4001.0), ("c3", 4970.0, 5000.0)], vc)
check("with views, title and creator", (vc["clips"][0]["views"], vc["clips"][0]["title"], vc["clips"][0]["creator"], vc["clips"][2]["creator"])
      == (900, "big one", "ann", ""), vc["clips"])
check("and where the list is complete", vc["complete"] == [[0.0, 9000.0]] and vc["length"] == 9000, vc)
tc.gql = lambda q, tries=4: {}
check("Twitch not answering: None (nothing frozen)", er.vod_clips("777") is None)

vc = {"vod": "777", "fetched": 1.0, "complete": [[0, 30000]], "clips": [
    {"start": 3000.0, "end": 3030.0, "views": 5, "title": "before", "creator": "a"},
    {"start": 4000.0, "end": 4030.0, "views": 900, "title": "big one", "creator": "ann"},
    {"start": 7190.0, "end": 7220.0, "views": 3, "title": "across the end", "creator": "b"}]}
fz = er.freeze_clips(vc, 3600, 7200)
check("only clips wholly inside the stretch, times from its start", [(c["start"], c["title"], c["creator"]) for c in fz["clips"]] ==
      [(400.0, "big one", "ann")], fz["clips"])
check("the stretch and when it was frozen are kept", fz["window"] == [3600, 7200] and fz["frozen"] > 0 and fz["vod"] == "777")

# ---------------------------------------------------------------- building a project (everything stood in for)
calls = {"clips": 0, "chat": 0, "download": 0}
fail_download = [True]


def fake_info(vod):
    return {"title": "a stream", "lengthSeconds": 30000, "createdAt": "2026-11-16T18:44:49Z", "game": {"name": "Just Chatting"},
            "owner": {"login": "somebody", "displayName": "Somebody"}}


def at(t):        # a time in the stretch build will pick, in the VOD
    return A + t


def fake_gql(q, tries=4):
    calls["clips"] += 1
    if 'video(id:"999")' in q:          # a VOD with more clips than Twitch lists: the middle isn't known
        edges = [node(f"a{i}", 10 * i, 20, vod="999") for i in range(100)] if "ASC" in q else \
            [node(f"d{i}", 29000 - 10 * i, 20, vod="999") for i in range(100)]
        return {"data": {"video": {"lengthSeconds": 30000, "clips": {"edges": edges}}}}
    edges = [node("b", at(-600), 30, 5, "before", "zed"), node("c1", at(400), 30, 900, "big one", "ann"),
             node("c2", at(401), 28, 40, "big one", "bob"), node("c3", at(1400), 30, 3, "no curator"),
             node("e", B + 20, 30, 3, "across the end", "eve")]
    return {"data": {"video": {"lengthSeconds": 30000, "clips": {"edges": edges}}}}


def fake_chat(vod, a, b, workers=8):
    calls["chat"] += 1
    return [{"id": "m1", "contentOffsetSeconds": A + 10, "commenter": {"login": "x", "displayName": "X"},
             "message": {"fragments": [{"text": "KEKW"}]}}]


def fake_download(vod, a, b, out, ytdlp):
    calls["download"] += 1
    if fail_download[0]:
        raise tc.subprocess.CalledProcessError(1, ["yt-dlp"])
    with open(out, "wb") as f:
        f.write(b"\0" * 20000)


A, B = er.stretch("777", 30000)
tc.video_info, tc.chat, tc.download_audio, tc.gql = fake_info, fake_chat, fake_download, fake_gql
real_video_ok = er.video_ok
er.video_ok = lambda path, want: os.path.getsize(path) > 10000
twitchclips.fetch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("the channel's lists aren't the answer key"))
emotes.for_channel = lambda twitch_id="", login="", log=None, refresh=False: emotes.EmoteSet({"7tv": ["KEKW", "xdd"], "bttv": ["catJAM"]})
corpus = os.path.join(tmp, "corpus")
os.makedirs(corpus)
entry = er.parse_manifest("777 cat=irl holdout  # test")[0]
r = er.build_one(entry, corpus, "yt-dlp", log=lambda m: None)
part = os.path.join(corpus, f"somebody_777_{A}.part")
check("a download that fails: an error, and the half-built project is kept to resume",
      "couldn't download" in r.get("error", "") and os.path.isdir(part), r)
check("it isn't a project score would use yet", er.projects(corpus) == [])
check("the VOD's clips were frozen before the download", os.path.isfile(os.path.join(corpus, "_clips", "777.json"))
      and os.path.isfile(os.path.join(part, "twitch_clips.json")) and calls["clips"] == 2, calls)

fail_download[0] = False
tc.gql = lambda q, tries=4: (_ for _ in ()).throw(AssertionError("asked again"))
r = er.build_one(entry, corpus, "yt-dlp", log=lambda m: None)
final = os.path.join(corpus, f"somebody_777_{A}")
check("run again: built, without asking Twitch for the clips again", not r.get("error") and os.path.isdir(final) and not os.path.exists(part), r)
check("the chat is read once the video is there", calls["chat"] == 1, calls)
check("a project score uses (the frozen lists folder isn't one)", er.projects(corpus) == [final])
meta = json.load(open(os.path.join(final, "meta.json"), encoding="utf-8"))
check("meta: the stretch, the VOD, the category and holdout",
      (meta["start"], meta["end"], meta["vod"], meta["category"], meta["holdout"], meta["platform"]) == (A, B, "777", "irl", True, "twitch"), meta)
frozen = json.load(open(os.path.join(final, "twitch_clips.json"), encoding="utf-8"))
check("the frozen clips: times from the stretch's start, each ending at Twitch's offset, with creators",
      [(c["start"], c["end"], c["creator"]) for c in frozen["clips"]] == [(370.0, 400.0, "ann"), (373.0, 401.0, "bob"), (1370.0, 1400.0, "")],
      frozen["clips"])
check("the channel's emotes frozen, by service", json.load(open(os.path.join(final, "emotes.json"))) == {"7tv": ["KEKW", "xdd"], "bttv": ["catJAM"]})
chat = json.load(open(os.path.join(final, "chat.json"), encoding="utf-8"))
check("chat as the app reads it, times from the stretch's start", chat["comments"][0]["content_offset_seconds"] == 10
      and chat["comments"][0]["message"]["body"] == "KEKW", chat)
built = json.load(open(os.path.join(final, "built.json"), encoding="utf-8"))
check("built.json: the counts (the clips before and across the stretch's end left out)",
      (built["clips"], built["with_creator"], built["moments"], built["window"]) == (3, 2, 2, [A, B]), built)
check("one clipper with 3 views isn't a truth moment; two people is", built["truth"] == 1, built)
r = er.build_one(entry, corpus, "yt-dlp", log=lambda m: None)
check("a third run leaves a built project alone", r.get("skipped") and calls["download"] == 2, (r, calls))

tc.gql = fake_gql
calls["chat"] = 0
small = [True]
real_download = tc.download_audio
tc.download_audio = lambda vod, a, b, out, ytdlp: open(out, "wb").write(b"\0" * (262 if small[0] else 20000))
r = er.build_one(er.parse_manifest("990")[0], corpus, "yt-dlp", log=lambda m: None)
check("a video that comes down empty (a VOD muted for music): an error, no chat read, the file removed",
      "empty or short" in r.get("error", "") and calls["chat"] == 0
      and not any(f.startswith("somebody_990") and os.path.exists(os.path.join(corpus, f, "video.mp4")) for f in os.listdir(corpus)), r)
tc.download_audio = real_download
junk = os.path.join(tmp, "junk.mp4")
with open(junk, "wb") as f:
    f.write(b"\0" * 262)
check("video_ok: 262 bytes of nothing isn't a video", real_video_ok(junk, 60) is False)
tc.video_info = lambda vod: {}
check("a VOD Twitch doesn't know: an error, nothing made",
      "not found" in er.build_one(er.parse_manifest("888")[0], corpus, "yt-dlp", log=lambda m: None).get("error", ""))
tc.video_info = fake_info
check("a stretch past the VOD's end: an error",
      "past the VOD's end" in er.build_one(er.parse_manifest("889@29000-31000")[0], corpus, "yt-dlp", log=lambda m: None).get("error", ""))
r = er.build_one(er.parse_manifest("999@10000-14000")[0], corpus, "yt-dlp", log=lambda m: None)
check("a stretch in the manifest where not every clip is known: an error", "not every clip" in r.get("error", ""), r)
r = er.build_one(er.parse_manifest("999")[0], corpus, "yt-dlp", log=lambda m: None)
check("none given: one is picked where every clip is known", not r.get("error") and er.inside(*r["window"], [[0, 989], [27011, 30000]]), r)

# ---------------------------------------------------------------- the command
rc = er.main(["build", os.path.join(ROOT, "corpus_here"), "--manifest", mpath])
check("CORPUS inside the repository is refused", rc == 2 and not os.path.exists(os.path.join(ROOT, "corpus_here")))
with open(mpath, "w", encoding="utf-8") as f:
    f.write("# test\n777 cat=irl holdout  # test\n")
tc.gql = fake_gql
rc = er.main(["build", corpus, "--manifest", mpath, "--write-manifest"])
check("build --write-manifest records the stretch and counts", rc == 0 and open(mpath, encoding="utf-8").read().splitlines()[1].startswith(
    f"777@{A}-{B} cat=irl holdout  # test [built "), open(mpath, encoding="utf-8").read())

tc.gql = real_gql
shutil.rmtree(tmp, ignore_errors=True)
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
