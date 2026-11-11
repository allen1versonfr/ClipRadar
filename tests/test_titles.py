"""
Unit tests for 3.6 titles and captions: title A and B, the streamer's name, tidying, no invented
claims, no repeats across clips, descriptions kept apart from titles, and hashtags.

  python tests/test_titles.py
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import titles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def W(text, t0=0.0):
    return [{"start": t0 + i * 0.35, "end": t0 + i * 0.35 + 0.3, "word": w} for i, w in enumerate(text.split())]


laugh = ["KEKW"] * 12 + ["LUL"] * 4
words = W("um so NO WAY HE ACTUALLY DID THAT!!!", 2)

r = titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen")
check("two titles: A and B", r["title_a"] and r["title_b"] and r["title_a"] != r["title_b"], (r["title_a"], r["title_b"]))
check("B has the streamer's name once", r["title_b"].count("JasonTheWeen") == 1 and r["title_b_named"], r["title_b"])
check("A doesn't shout", not re.search(r"[A-Z]{4,}", r["title_a"]), r["title_a"])
check("one '!' at most in a row", "!!" not in r["title_a"])
check("A is the line that was said", r["title_a"].lower().startswith("no way he actually did that"), r["title_a"])
check("both kept as options", r["title_a"] in r["titles"] and r["title_b"] in r["titles"])
check("pick B puts B on the video", titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen", pick="b")["title"] == r["title_b"])

r0 = titles.generate(words, laugh, 4.0, set(), streamer="")
check("no name known: B is still a real second choice", r0["title_b"] and r0["title_b"] != r0["title_a"] and not r0["title_b_named"],
      (r0["title_a"], r0["title_b"]))
check("no name known: no placeholder name", "{n}" not in " ".join(r0["titles"]))

# a name already in the line isn't repeated
rn = titles.generate(W("Jason you are actually insane!", 1), laugh, 2.0, set(), streamer="Jason")
check("a name that's already in the line isn't added again", rn["title_b"].lower().count("jason") == 1, rn["title_b"])

# no invented claims: templates only talk about chat's reaction or ask you to watch
claims = re.compile(r"\b(clutch|best play|cleanest|world record|insane play|why i stream|goat)\b", re.I)
all_templates = [t for v in titles.VIBES.values() for t in v["templates"] + v["named"]] + titles.NEUTRAL + titles.NEUTRAL_NAMED
check("no template claims an outcome the clip can't show", not [t for t in all_templates if claims.search(t)],
      [t for t in all_templates if claims.search(t)])
check("no template shouts", not [t for t in all_templates if re.search(r"\b[A-Z]{3,}\b", t.replace("{n}", ""))])

# no repeats across clips
used: set = set()
got = []
for i in range(4):
    x = titles.generate([], laugh, None, used, streamer="Sam")
    got.append(x["title_a"])
    used.add(x["title_a"].lower())
check("different clips get different titles", len(set(got)) == 4, got)

# tidying
check("tidy: shouting to normal case", titles.polish("WHAT IS GOING ON HERE") == "What is going on here")
check("tidy: stammered repeats", titles.polish("no no no no no no way") == "No no no way")
check("tidy: trailing filler", titles.polish("i cant believe this like") == "I can't believe this")
long = "so then I walked over to the other side of the map and found the whole squad waiting there for me"
check("tidy: long lines end on a word", len(titles.polish(long)) <= 61 and titles.polish(long).endswith("..."), titles.polish(long))

# descriptions and hashtags
check("description isn't the title", r["description"] != r["title_a"] and r["description"] != r["title_b"], r["description"])
check("description names the stream when known", "JasonTheWeen" in r["description"], r["description"])
check("a few relevant hashtags", 2 <= len(r["hashtags"]) <= titles.MAX_TAGS and "#jasontheween" in r["hashtags"]
      and {"#funny", "#funnymoments"} & set(r["hashtags"]) and "#fyp" not in r["hashtags"], r["hashtags"])
check("hashtags in the post when switched on", r["post"].endswith(" ".join(r["hashtags"])))
off = titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen", with_tags=False)
check("no hashtags when switched off", "#" not in off["post"] and off["post"] == off["description"], off["post"])
check("never more than five tags", len(titles.hashtags("funny", "Someone", "Minecraft")) <= 5)

check("plain description reads naturally", titles.describe(None, "", "Sam") == "A moment from Sam's stream 👀"
      and titles.describe(None, "") == "A moment from the stream 👀")

# Whisper mishearing noise is never a title (all seen on a real 6-hour stream)
for junk in ["5, 4, 5, 5, 5, 6,.p", "L!, c!, I'm d, b, b, t, c!", "TH, d, s,fore, dad, oh, u,ne",
             "Girls insp, h, wap, r, wap", "Uu, uu, uu, luumph, uu, uu, uu tha", "😂 Yippy 😆 👛 It's", "U dram fave, l!"]:
    check(f"garbled: {junk!r}", titles.garbled(junk))
for fine in ["No, actually, bro, what is it?", "How come, bruh", "Yo bud, sh!t!", "What the, what", "I never seen",
             "It's time to go, bruh", "He pranked calling people, right?", "No no no way"]:
    check(f"a real line isn't garbled: {fine!r}", not titles.garbled(fine))
rj = titles.generate(W("L!, c!, I'm d, b, b, t, c!", 1), laugh, 2.0, set())
check("a garbled line falls back to a template", rj["title_a"] in [titles.polish(t) for t in titles.VIBES["funny"]["templates"] + titles.NEUTRAL], rj["title_a"])

# automatic captions have no punctuation: long runs are cut at a pause, not mid-sentence
cap = ("okay I've had my little pink decorating moment and I loved every second of it this starter house is "
       "definitely the most colorful I've ever had").split()
ws, t = [], 0.6
for w in cap:
    ws.append({"start": t, "end": t + 0.25, "word": w})
    t += 0.3 + (0.45 if w in ("moment", "it") else 0.0)       # the speaker pauses after "moment" and "it"
pieces = [titles._phrase_text(p) for p in titles._phrases(ws)]
check("captions: no piece ends on a word that leads into the next", not any(p.split()[-1].lower() in titles.DANGLING_END for p in pieces[:-1]), pieces)
check("captions: cut where the speaker paused", any(p.endswith("of it") for p in pieces), pieces)
rc = titles.generate(ws, [], 5.0, set())
check("captions: the title is a whole thought", not rc["title_a"].lower().endswith(" this"), rc["title_a"])

# 4.41: titles that say what happened
auto = W("so i went to the store yesterday and the guy behind the counter just looks at me and goes are you famous "
         "and i was like no man i just have one of those faces")
pieces = [" ".join(w["word"] for w in ph) for ph in titles._phrases(auto)]
check("automatic captions are cut where clauses start, not every few words",
      pieces == ["so i went to the store yesterday", "and the guy behind the counter just looks at me",
                 "and goes are you famous", "and i was like no man", "i just have one of those faces"], pieces)
check("'goes' before a quote isn't part of the title", titles._phrase_text(titles._phrases(auto)[2]) == "Are you famous",
      titles._phrase_text(titles._phrases(auto)[2]))
burger = titles.generate(W("this is the biggest burger i have ever seen in my life it's literally the size of my head "
                           "okay here goes nothing"), [], 4.0, set())
check("a line from automatic captions is a whole thought", burger["title_a"] in (
    "It's literally the size of my head", "This is the biggest burger i have ever seen in my life"), burger["title_a"])
spider = titles.generate(W("Okay so I'm gonna go grab some water real quick. Wait, what is that? Oh my god, there's a "
                           "spider on my desk! Get it off, get it off!"), ["KEKW"] * 20, 8.0, set())
check("what happened beats a reaction to it", "spider on my desk" in spider["title_a"], spider["title_a"])
plain = titles.generate(W("So today we're going to be looking at the new update. I think the patch notes said they "
                          "changed the recoil on the rifle. Let's see if that's true."), [], 6.0, set(), streamer="Sam")
check("a flat line with nothing specific isn't the title, near the peak or not",
      plain["title_a"] != "Let's see if that's true" and "recoil" in plain["title_a"], plain["title_a"])
check("the caption doesn't repeat the title in quotes", plain["title_a"] not in plain["description"], plain["description"])
check("nor stop before what was said", not plain["description"].split('"')[1].endswith("said"), plain["description"])
check("a YouTube upload is a video, not a stream", titles.describe(None, "", "Sam", platform="youtube").startswith(
    "A moment from Sam's video"))

# many clips of one mood: the templates take turns once they're all used
used, got = set(), []
for i in range(30):
    x = titles.generate([], ["KEKW"] * (8 + i % 7), float(i % 5), used)
    got.append(x["title_a"])
    used.add(x["title_a"].lower())
top = max(got.count(g) for g in set(got))
check("30 funny clips without words: no title on more than a quarter of them", top <= 30 // 4, sorted(got))

# a YouTube video: YouTube's tags and name, not Twitch's
ry = titles.generate(words, laugh, 4.0, set(), streamer="GeminiTay", platform="youtube")
check("YouTube: no Twitch tags", not any("twitch" in t for t in ry["hashtags"]) and "#youtube" in ry["hashtags"], ry["hashtags"])
check("YouTube: the description says YouTube", "Twitch" not in ry["description"], ry["description"])
check("Twitch is still the default", "#twitch" in titles.generate(words, laugh, 4.0, set(), streamer="X1")["hashtags"])

# ---------------------------------------------------------------- 4.36: mood, topic, variety, game hashtags
people = lambda texts: [(float(i), t, f"u{i}") for i, t in enumerate(texts)]  # noqa: E731
mixed = people(["KEKW", "LMAOOO", "L", "😂😂", "L", "hahaha", "OMEGALUL", "L", "xdd", "ICANT"])
check("a laughing wave with some L's in it is funny, not a fail", titles.generate([], mixed)["vibe"] == "funny")
one = [(float(i), "L", "same") for i in range(10)] + people(["KEKW", "LMAO", "😂"])
check("one person typing L ten times is one chatter", titles.generate([], one)["vibe"] == "funny")
hair = people(["NOOO not the hair", "dont cut the hair", "the hair 😭", "his hair was fine", "save the hair",
               "bro the hair", "hair cut LMAO", "KEKW", "noooo", "W"])
th = titles.generate([], hair, streamer="Speedy")
check("what chat talks about becomes the topic", th["topic"] == "hair", th["topic"])
check("and is in a title and a hashtag", any("the hair" in t for t in th["titles"]) and "#hair" in th["hashtags"], th)
for bad in (["thats cap", "CAP", "the cap is crazy", "big cap", "cap cap", "that cap"],
            ["the speedy show", "the speedy fans", "speedy speedy", "go speedy", "speedy W", "speedy pls"],
            ["the KEKW", "a KEKW", "KEKW", "KEKW", "KEKW", "KEKW"],
            ["the stream", "this stream", "stream lag", "stream", "stream ok", "the stream"]):
    tb = titles.generate([], people(bad * 2), streamer="Speedy")
    check(f"not a topic: {bad[0]!r}", tb["topic"] == "", tb["topic"])
check("a few people aren't a topic", titles.topic(people(["the car", "the car", "the car"])) == "")

# a stream's clips: titles, titles B and captions vary
used, got = set(), {"a": [], "b": [], "d": []}
for k in range(12):
    g = titles.generate([], people(["POGGG", "LETSGOOO", "W", "W", "INSANE", "🔥"] + ["W"] * k), peak_rel=k * 1.3,
                        used=used, streamer="Kai")
    used.update(x.lower() for x in (g["title"], g["title_a"], g["title_b"], g["description"]))
    got["a"].append(g["title_a"])
    got["b"].append(g["title_b"])
    got["d"].append(g["description"])
check("12 hype clips: 12 titles", len(set(got["a"])) == 12, got["a"])
check("and at least 10 different titles B and captions", len(set(got["b"])) >= 10 and len(set(got["d"])) >= 10, got)

# a video without chat or words: the neutral titles, none twice in its first clips (title B marked as used too
# took them twice as fast, and the 4th clip repeated the 1st)
used, quiet = set(), []
for k in range(6):
    g = titles.generate([], [], peak_rel=k * 1.1, used=used)
    used.update(x.lower() for x in (g["title"], g["title_a"], g["title_b"] if g["title_b_named"] else "", g["description"]) if x)
    quiet.append(g["title_a"])
check("a video without chat or words: 6 clips, 6 titles", len(set(quiet)) == 6, quiet)

check("the game is a hashtag, in the form people search", titles.hashtags("hype", "Kai", "Grand Theft Auto V")[:2] == ["#kai", "#gta5"]
      and "#minecraft" in titles.hashtags("funny", "x", "Minecraft"))
check("#gaming only for a game", "#gaming" in titles.hashtags("funny", "x", "Minecraft")
      and "#gaming" not in titles.hashtags("funny", "x", "Just Chatting") and "#gaming" not in titles.hashtags("funny", "x"))
check("Just Chatting has its own tag", "#justchatting" in titles.hashtags("funny", "x", "Just Chatting"))
check("YouTube's Gaming category is a game", "#gaming" in titles.hashtags("funny", "x", platform="youtube", category="Gaming")
      and "#gaming" not in titles.hashtags("funny", "x", platform="youtube", category="Entertainment"))
check("the streamer's clips tag when there's room", "#kaiclips" in titles.hashtags("funny", "Kai"))
full = titles.hashtags("funny", "Kai", "Minecraft", topic_word="creeper")
check("five at most, the platform's tag kept", len(full) == 5 and full[-1] == "#twitch" and full[:3] == ["#kai", "#minecraft", "#creeper"],
      full)
check("no #twitchclips on every clip any more", "#twitchclips" not in titles.hashtags("funny", "Kai"))

# ---------------------------------------------------------------- 4.47: what viewers called their clips
vt = ["JASON EATS SHIT", "TKO"]
quiet_words = W("okay okay", 1)
gv = titles.generate(quiet_words, laugh, 1.0, set(), streamer="Jason", viewer_titles=["TKO", "ninja"])
check("a viewer's clip title beats the templates", gv["title_a"] == "TKO" and gv["viewer_title"] == "TKO", gv["title_a"])
check("and is kept as an option", "TKO" in gv["titles"], gv["titles"])
gq = titles.generate(words, laugh, 4.0, set(), streamer="Jason", viewer_titles=["TKO"])
check("a strong line said on stream still beats it", gq["title_a"].lower().startswith("no way he actually did that")
      and "TKO" in gq["titles"], (gq["title_a"], gq["titles"]))
gc = titles.generate(quiet_words, laugh, 1.0, set(), viewer_titles=["TKO"], chat_title='Chat: "REF"')
check("and it comes before chat's phrase", gc["title_a"] == "TKO" and 'Chat: "REF"' in gc["titles"], gc["titles"])
gm = titles.generate(quiet_words, laugh, 1.0, set(), viewer_titles=vt)
check("a viewer's title is tidied and its swearing masked", gm["title_a"] == "Jason eats s***", gm["title_a"])
import safety  # noqa: E402
cfg = dict(safety.DEFAULTS, censor="bleep", custom=["springfield"])
masked = safety.mask_text(titles.generate(quiet_words, laugh, 1.0, set(), viewer_titles=["springfield arc"])["title_a"], cfg)
check("and the content check's masking applies to it as to any title", masked == "S********** arc", masked)
used_v: set = set()
firsts = []
for k in range(2):
    g = titles.generate(quiet_words, laugh, 1.0 + k, used_v, viewer_titles=["TKO", "ninja"])
    used_v.update(x.lower() for x in (g["title"], g["title_a"], g["description"]) if x)
    firsts.append(g["title_a"])
check("the same viewer title isn't given to two clips", firsts == ["TKO", "Ninja"], firsts)
g3 = titles.generate(quiet_words, laugh, 3.0, used_v, viewer_titles=["TKO", "ninja"])
check("all of them taken: back to the usual titles", g3["title_a"] not in ("TKO", "Ninja") and not g3["viewer_title"], g3["title_a"])
check("no viewer titles: as before", titles.generate(words, laugh, 4.0, set(), streamer="JasonTheWeen")["title_a"] == r["title_a"])


# ---------------------------------------------------------------- titles-v2 (4.48): specific beats generic
def score_of(text, peak=1.0):
    ph = titles._phrases(W(text))[0]
    return titles._score_phrase(ph, titles.polish(titles._phrase_text(ph)), peak)


split = [titles._phrase_text(p) for p in titles._phrases(W("Kiss your lips No! Why are you never going to sleep"))]
check("v2: a capitalised everyday word starts a new sentence", split[:2] == ["Kiss your lips", "No!"], split)
split = [titles._phrase_text(p) for p in titles._phrases(W("Jynxzi, Jynxzi Oh my god"))]
check("v2: ...after a name too", split == ["Jynxzi, Jynxzi", "Oh my god"], split)
keep = [titles._phrase_text(p) for p in titles._phrases(W("and call coach Tariq for this himself"))]
check("v2: a name in the middle of a sentence doesn't split it", len(keep) == 1, keep)
keep = [titles._phrase_text(p) for p in titles._phrases(W("I think I can do it"))]
check("v2: nor does 'I'", len(keep) == 1, keep)
check("v2: Whisper's spacing is tidied", titles.polish("Did y 'all see that?") == "Did y'all see that?"
      and titles.polish("It was 1 ,000 yen") == "It was 1,000 yen", (titles.polish("Did y 'all see that?"),))
check("v2: a line that stops on 'after' scores less than the whole thought",
      score_of("Tell them after this game after") < score_of("Tell them after this game"))
check("v2: a word said over and over scores less", score_of("Bar Bar Bar Go bar") < score_of("Go to the bar now")
      and score_of("On roof, on roof") < score_of("He is on the roof"))
for junk in ["Yup", "LMFAOOOOO", "GGs……", "?", "LOL Ayooo😂🤣", "OMFG", "lol", "INSANE"]:
    check(f"v2: a viewer title that only reacts says nothing: {junk!r}", titles.says_nothing(junk))
for fine in ["TKO", "ninja", "Kai got smacked", "theres a villager", "scripted??"]:
    check(f"v2: a viewer title that names something: {fine!r}", not titles.says_nothing(fine))
gy = titles.generate(quiet_words, laugh, 1.0, set(), viewer_titles=["LMFAOOOOO", "GGS", "Yup"])
check("v2: viewer titles that only react aren't used", gy["title_a"] not in ("Lmfaoooooo", "LMFAOOOOO", "GGS", "Yup")
      and not gy["viewer_title"], gy["title_a"])
fair = W("Sarah slowing down the pace a little bit")
gf = titles.generate(fair, laugh, 1.0, set(), viewer_titles=["KAI GOT SMACKED"])
check("v2: a viewer's title beats a line that's only fair", gf["title_a"] == "Kai got smacked", gf["title_a"])
check("v2: and the line said is still an option", any("Sarah" in t for t in gf["titles"]), gf["titles"])
gs = titles.generate(fair, laugh, 1.0, set(), chat_title='Chat: "Pig dawg"')
check("v2: chat's phrase beats a line that's only fair", gs["title_a"] == 'Chat: "Pig dawg"', gs["title_a"])
good = W("Why are you trying to give me five")
check("v2: (a good line, for the next checks)", titles.QUOTE_FAIR <= score_of("Why are you trying to give me five", 1.0)
      < titles.QUOTE_STRONG, score_of("Why are you trying to give me five", 1.0))
gl = titles.generate(good, laugh, 1.0, set(), chat_title='Chat kept saying "Leave"')
check("v2: one word chat kept typing loses to a good line said", gl["title_a"].startswith("Why are you"), gl["title_a"])
gw = titles.generate(W("okay okay"), laugh, 1.0, set(), chat_title='Chat kept saying "Diana"')
check("v2: ...and beats the templates", gw["title_a"] == 'Chat kept saying "Diana"', gw["title_a"])

# post captions: something specific the title doesn't say, then a question, then whose stream; tags last
gc = titles.generate(words, laugh, 4.0, set(), streamer="Jason", viewer_titles=["TKO"])
check("v2: the caption leads with what the title doesn't say", gc["title_a"].lower().startswith("no way")
      and gc["description"].startswith("TKO"), gc["description"])
check("v2: then asks something, then says whose stream", gc["description"].endswith("(Jason on Twitch)")
      and "?" in gc["description"] or "1 to 10" in gc["description"], gc["description"])
check("v2: the hashtags stay at the end", gc["post"].endswith(" ".join(gc["hashtags"])) and "\n\n#" in gc["post"], gc["post"])
two = W("Wait, there's a spider on my desk! My cat just knocked the lamp over!", 1)
g2 = titles.generate(two, laugh, 2.0, set(), streamer="Jason")
check("v2: a second good line said can lead the caption",
      g2["description"].startswith('"') and g2["description"].split('"')[1].lower() not in g2["title_a"].lower(),
      (g2["title_a"], g2["description"]))
gp = titles.generate(W("How the hell do you make crack?", 1), laugh, 2.0, set(), streamer="Hasan",
                     viewer_titles=["how tf you make crack?"])
check("v2: the caption doesn't say the title again in other words",
      "crack" not in gp["description"].lower() or "crack" not in gp["title_a"].lower(), (gp["title_a"], gp["description"]))
gn = titles.generate([], laugh, 2.0, set(), streamer="Jason")
hooks = [h for h in titles.VIBES["funny"]["hooks"]]
check("v2: nothing specific: a line about chat, a question, whose stream",
      any(gn["description"].startswith(h) for h in hooks) and gn["description"].endswith("(Jason on Twitch)")
      and any(q in gn["description"] for q in titles.QUESTIONS["funny"]), gn["description"])
qs = [q for v in titles.QUESTIONS.values() for q in v]
check("v2: the questions only ask, they never say what happened",
      not [q for q in qs if claims.search(q)] and all(q.endswith("?") or q.startswith("Rate") or q.startswith("Tag")
                                                     for q in qs), qs)
used, leads = set(), []
for k in range(3):
    g = titles.generate(quiet_words, laugh, 1.0 + k, used, streamer="Jason", chat_title='Chat kept saying "Diana"')
    used.update(x.lower() for x in (g["title"], g["title_a"], g["title_b"] if g["title_b_named"] else "", g["description"]) if x)
    leads.append(g["description"])
check("v2: what one clip's title or caption said isn't another clip's caption",
      sum("Diana" in d for d in leads) <= 1, leads)
import studio_auto  # noqa: E402
cap = titles.generate(words, laugh, 4.0, set(), streamer="Jason", viewer_titles=["TKO"])["post"]
check("v2: the caption fits TikTok's limit as it is", studio_auto.fit_caption(cap) == cap, cap)
masked = titles.generate(W("okay okay"), laugh, 1.0, set(), viewer_titles=["what the fuck", "JASON EATS SHIT"])
check("v2: a viewer's title in the caption is masked like the title", "shit" not in masked["description"].lower()
      and "fuck" not in masked["description"].lower(), masked["description"])

# 4.51: an argument or a moment chat takes sides on asks who's right (the first user report: drama clips got the
# most shares, and "Thoughts?" got next to no comments)
argue_chat = [(i * 0.5, t, f"u{i}") for i, t in enumerate(["he's wrong", "ratio", "she violated him", "W", "L", "L",
                                                              "W", "W", "L", "exposed"])]
ga = titles.generate(W("you're lying, shut up, you're wrong"), argue_chat, 1.0, set(), streamer="Adin")
check("4.51: an argument asks who's right", ga["conflict"] and any(q in ga["description"] for q in titles.QUESTIONS["conflict"]),
      ga["description"])
check("4.51: a laugh isn't an argument", not titles.conflict(laugh, W("that was so funny")))
check("4.51: chat split between W and L is", titles.conflict([(0, "W", "a"), (0, "W", "b"), (0, "W", "c"), (0, "L", "d"),
                                                            (0, "L", "e"), (0, "L", "f")], []))
check("4.51: one chatter typing it ten times isn't", not titles.conflict([(i, "ratio", "same") for i in range(10)], []))
check("4.51: no more questions without a quick answer", "Thoughts?" not in qs and "What do you think?" not in qs)

import engine  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
cf = tempfile.mktemp(suffix=".json")
json.dump({"streamer": {"name": "Kai", "id": "1"}, "video": {"chapters": [
    {"startMilliseconds": 0, "lengthMilliseconds": 600000, "gameDisplayName": "Just Chatting"},
    {"startMilliseconds": 600000, "lengthMilliseconds": 3000000, "gameDisplayName": "Minecraft"}]}, "comments": []},
    open(cf, "w"))
info = {}
engine.load_chat(cf, info)
check("Twitch chat replay: the game, and each chapter's", info.get("game") == "Minecraft" and len(info.get("games", [])) == 2
      and engine.game_at(info, 30) == "Just Chatting" and engine.game_at(info, 900) == "Minecraft", info)
check("no game known: ''", engine.game_at({}, 5) == "")
import sources  # noqa: E402
check("YouTube's category from the link check", sources.describe({"categories": ["Gaming"], "title": "x"})["category"] == "Gaming")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
