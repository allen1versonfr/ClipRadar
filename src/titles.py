"""
Auto titles for clips.

Clip Radar "watches" a clip the way a clipper would: it listens to what the
streamer says (Whisper transcript) and reads how chat reacted. From that it
picks a vibe (funny / hype / shock / fail / wholesome), looks for a quotable
line near the peak, and writes:

  title_a  the best line said near the peak, or a short line about how chat reacted
  title_b  the same idea with the streamer's name worked in (when it's known)
  titles   alternatives to cycle through
  description  a post caption, written separately from the title
  hashtags     a few relevant tags (optional: Settings › Titles and captions)

Titles only describe what the clip shows: what was said, and how chat and the stream
reacted. Templates never claim an outcome ("clutch", "best play") the signals can't back up.
"""

from __future__ import annotations

import re
from typing import Optional

import spelling

VIBES = {
    # templates: titles about how the clip went over, all backed by the vibe's evidence (chat and speech)
    # named: the same with the streamer's name ({n}). topic / topic_named (4.36): with what chat was talking
    # about ({t}, a noun chat used: "the hair", "his phone"). hooks: post captions. tags: a mood hashtag
    # each, varied between clips
    "funny": {
        "emoji": "😂",
        "tags": ["#funny", "#funnymoments"],
        "chat": {"haha", "hahaha"},
        "said": {"haha", "hahaha", "lol", "funny", "hilarious", "joke", "laughing", "dying", "crying"},
        "templates": ["Chat could not stop laughing", "This had chat in tears", "Why is this so funny",
                      "Chat lost it at this", "This one broke chat", "The whole chat lost it",
                      "I can't stop replaying this", "Nobody was ready for this one", "This is too funny",
                      "The timing on this", "Chat is still recovering from this"],
        "named": ["{n} had chat crying laughing", "{n} made chat lose it", "Chat could not stop laughing at {n}",
                  "{n} broke chat with this", "Only {n} could make chat laugh like this", "{n} had everyone in tears",
                  "{n} is too funny for this", "{n} had chat on the floor", "Chat will never let {n} live this down",
                  "This {n} moment had chat dying"],
        "topic": ["Chat lost it over the {t}", "The {t} had chat crying", "Chat could not get over the {t}"],
        "topic_named": ["{n} and the {t} had chat crying", "{n} vs the {t}"],
        "hooks": ["Chat could not stop laughing at this one", "I can't stop replaying this", "The whole chat lost it",
                  "Nobody was ready for this", "This had chat in tears", "Wait for the reaction",
                  "I'm still laughing at this", "Chat was in tears", "Watch it twice, it gets funnier",
                  "The way chat reacted", "This one never gets old"],
        "describe": "Chat could not stop laughing at this one",
    },
    "hype": {
        "emoji": "🔥",
        "tags": ["#hype", "#letsgo", "#insane"],
        "chat": {"letsgo"},
        "said": {"lets", "let's", "go", "yes", "clutch", "insane", "crazy", "goat", "easy", "gg",
                 "won", "win", "finally"},
        "templates": ["Chat went crazy for this", "Chat could not believe this", "The whole chat was hyped",
                      "Chat erupted for this", "This had chat going crazy", "The energy in chat",
                      "Chat was screaming for this", "Chat lost its mind over this", "The loudest chat has ever been",
                      "Turn your sound up for this one", "Chat could not calm down after this",
                      "This is why we watch"],
        "named": ["Chat went crazy for {n}", "{n} got the whole chat hyped", "{n}'s chat could not believe this",
                  "{n} had chat going crazy", "{n} made chat erupt", "The whole chat went wild for {n}",
                  "{n} had the whole chat screaming", "{n}'s chat lost its mind", "Nobody hypes chat up like {n}",
                  "{n} and chat at full volume"],
        "topic": ["Chat went crazy over the {t}", "The {t} had chat going crazy"],
        "topic_named": ["{n} and the {t} had chat going crazy", "Chat went crazy for {n}'s {t}"],
        "hooks": ["Chat went crazy for this one", "The energy in chat", "Chat erupted for this",
                  "Turn the sound up for this one", "The whole chat was screaming", "Chat lost its mind",
                  "The energy here is unreal", "Chat could not calm down after this", "Listen to chat on this one",
                  "This is why we watch"],
        "describe": "Chat went crazy for this one",
    },
    "shock": {
        "emoji": "😳",
        "tags": ["#unexpected", "#wtf", "#nowaying"],
        "chat": {"noway"},
        "said": {"what", "wait", "how", "huh", "wtf", "omg", "no", "way", "holy", "god"},
        "templates": ["Wait for it...", "Chat was not ready for this", "What just happened",
                      "Nobody saw this coming", "Chat could not believe what happened", "No one expected this",
                      "Did that really just happen", "Chat went silent for a second", "I had to watch this twice",
                      "The way chat reacted to this"],
        "named": ["{n}'s chat was not ready for this", "{n} and chat did not see this coming", "Wait for {n}'s reaction...",
                  "Nobody expected this from {n}", "{n} left chat speechless"],
        "topic": ["Nobody expected the {t}", "Chat was not ready for the {t}"],
        "topic_named": ["{n} was not ready for the {t}", "Nobody expected {n}'s {t}"],
        "hooks": ["Nobody saw this coming", "Chat was not ready for this", "Wait for it", "What just happened",
                  "I had to watch this twice", "Chat went quiet, then this", "Nobody expected that",
                  "The way chat reacted to this"],
        "describe": "Nobody saw this coming",
    },
    "fail": {
        "emoji": "💀",
        "tags": ["#fail", "#unlucky", "#rip"],
        "chat": {"rip", "sadge", "unlucky", "throw", "throwing", "bozo", "copium", "classic"},
        "said": {"no", "why", "nooo", "unlucky", "rip", "dead", "died", "lost", "terrible", "throw"},
        "templates": ["This did not go as planned", "It was all going so well...", "Chat felt this one",
                      "That did not end well", "So close", "Pain"],
        "named": ["It was all going so well for {n}...", "This did not go as planned for {n}", "{n}'s chat felt this one",
                  "{n} will not forget this one", "Chat felt this one for {n}"],
        "topic": ["The {t} did not go as planned", "It was all going so well with the {t}..."],
        "topic_named": ["{n} and the {t} did not go as planned"],
        "hooks": ["It was all going so well", "Chat felt this one", "This did not go as planned", "So close",
                  "The pain in chat"],
        "describe": "It was all going so well",
    },
    # 4.34: chat not getting it ("???", "huh", "wdym") and an awkward moment ("uhhh", "😬", "yikes") are told
    # apart by engine.reaction_kind; "huh" and "???" used to count as shock
    "confused": {
        "emoji": "🤔",
        "tags": ["#confused", "#wait"],
        "chat": set(),
        "said": {"huh", "confused", "understand", "wait", "what"},
        "templates": ["Chat was so confused", "Nobody understood what just happened", "Can someone explain this",
                      "Wait, what just happened?", "Chat is still trying to figure this out"],
        "named": ["{n} left chat so confused", "Can someone explain what {n} just did", "Nobody understood {n} here",
                  "{n} had chat asking questions"],
        "topic": ["Nobody understood the {t}", "Can someone explain the {t}"],
        "topic_named": ["Can someone explain {n}'s {t}"],
        "hooks": ["Chat had no idea what just happened", "Can someone explain this", "Wait, what?",
                  "Chat is still confused"],
        "describe": "Chat had no idea what just happened",
    },
    "cringe": {
        "emoji": "😬",
        "tags": ["#awkward", "#cringe"],
        "chat": set(),
        "said": {"awkward", "uh", "um", "uhh", "umm", "sorry", "cringe", "embarrassing"},
        "templates": ["This got awkward fast", "The silence after this", "Chat could not handle this",
                      "Secondhand embarrassment", "The awkward silence", "Chat did not know what to say"],
        "named": ["{n} made it so awkward", "This got awkward fast for {n}", "{n}'s chat could not handle this",
                  "{n} and the awkward silence"],
        "topic": ["The {t} got awkward fast", "Chat could not handle the {t}"],
        "topic_named": ["{n} and the {t} got awkward fast"],
        "hooks": ["This got awkward fast", "The silence after this", "Secondhand embarrassment",
                  "Chat did not know what to say"],
        "describe": "This got awkward fast",
    },
    "wholesome": {
        "emoji": "🥹",
        "tags": ["#wholesome", "#cute"],
        "chat": {"<3", "❤", "❤️", "aww", "awww", "cute", "wholesome", "peepohappy", "hug"},
        "said": {"love", "thank", "thanks", "cute", "aww", "appreciate"},
        "templates": ["Chat was so wholesome here", "Moments like this", "This one hits different"],
        "named": ["A wholesome moment with {n}", "{n}'s chat was so wholesome here", "{n} moments like this"],
        "topic": ["The {t} was so wholesome"],
        "topic_named": ["{n} and the {t}"],
        "hooks": ["A wholesome moment", "Moments like this", "This one hits different"],
        "describe": "A wholesome moment",
    },
}
NEUTRAL = ["You have to see this", "Wait for it...", "Watch till the end"]
NEUTRAL_NAMED = ["{n} moment you have to see", "Wait for it... ({n})", "Watch {n} till the end"]
NEUTRAL_HOOKS = ["You have to see this", "Watch till the end", "Wait for it"]
CLIP_REQ = ["Chat said clip it", "Chat made me clip this"]
CLIP_REQ_NAMED = ["{n}'s chat said clip it"]
BASE_TAGS = ["#twitch"]
# a video from YouTube or X gets that platform's tag and name instead (sources.PLATFORMS keys); files and other
# sites keep Twitch's, as they're usually stream recordings. #gaming only when it's known to be a game (4.36)
PLATFORM_TAGS = {"youtube": ["#youtube"], "x": []}
PLATFORM_NAMES = {"youtube": "YouTube", "x": "X"}
NOT_GAMES = {"just chatting", "irl", "talk shows & podcasts", "music", "art", "asmr", "travel & outdoors",
             "food & drink", "sports", "special events", "pools, hot tubs, and beaches", "makers & crafting",
             "science & technology", "software and game development", "i'm only sleeping", "chess", "politics"}
# a few of Twitch's non-game categories have a tag people use for them
CATEGORY_TAGS = {"just chatting": "#justchatting", "irl": "#irl", "talk shows & podcasts": "#podcast",
                 "music": "#music", "chess": "#chess", "sports": "#sports", "asmr": "#asmr"}
MAX_TAGS = 5
STRONG = {"no", "way", "what", "bro", "dude", "wait", "insane", "crazy", "oh", "god", "yes", "let's",
          "lets", "go", "holy", "dead", "how", "why", "never", "literally", "actually", "impossible",
          "clutch", "chat", "bruh", "nah", "yo", "stop", "run"}
FILLER = {"um", "uh", "uhh", "umm", "like", "so", "yeah", "okay", "ok", "the", "a", "and", "i", "you",
          "it", "is", "to", "of", "that", "this", "but", "just", "mm", "hmm"}
LEAD_FILLER = {"um", "uh", "uhh", "umm", "so", "and", "but", "like", "yeah", "okay", "ok", "well", "mm", "hmm"}
# a piece cut out of a longer run of speech reads as a fragment when it ends on one of these
# ("...every second of it this") or starts on one of the others ("to have like a cute little...")
DANGLING_END = {"the", "a", "an", "to", "of", "and", "but", "or", "so", "my", "your", "our", "this", "that", "i",
                "i'm", "it's", "is", "was", "are", "in", "on", "at", "for", "with", "as", "be", "we",
                "they", "he", "she", "if", "when", "because", "than", "there", "just", "really", "very"}
DANGLING_START = {"of", "to", "and", "but", "or", "than", "that", "which", "with", "for", "as", "at", "in", "on",
                  "would", "could", "should", "is", "are", "was", "were", "be", "been", "being"}
# short words that are real words, so a line made of them isn't taken for Whisper mishearing noise
SHORT_WORDS = {"i", "a", "no", "so", "oh", "ok", "go", "my", "me", "we", "he", "is", "it", "to", "do", "up", "on",
               "in", "at", "be", "us", "yo", "ya", "of", "or", "an", "as", "if", "by", "am", "hi", "ah", "uh", "um"}
PROFANITY = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|cunt\w*|dick\w*|pussy\w*|asshole\w*|nigg\w*|fag\w*|retard\w*)\b",
                       re.I)
_TOKEN = re.compile(r"<3|[\w']+|[^\w\s]")


def _clean_word(w: str) -> str:
    return w.strip().strip("\"“”")


def _mask(text: str) -> str:
    def m(mo):
        w = mo.group(0)
        return w[0] + "*" * (len(w) - 1)
    return PROFANITY.sub(m, text)


def _phrases(words: list) -> list:
    """Split a transcript into phrases at sentence ends and pauses."""
    out, cur = [], []
    for i, w in enumerate(words):
        txt = _clean_word(w["word"])
        if not txt:
            continue
        if cur and w["start"] - cur[-1]["end"] > 0.7:
            out.append(cur)
            cur = []
        cur.append({**w, "word": txt})
        if txt[-1:] in ".!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    # split very long phrases at commas, else into pieces of 4-9 words, cut where the speaker paused
    # longest and never right after a word that leads into the next ("the", "my", "this"). Automatic
    # captions have no punctuation at all, so a cut every 8 words landed mid-sentence: "I loved every
    # second of it this" (this starter house...). Pieces cut this way are marked for _score_phrase.
    final = []
    for ph in out:
        if len(ph) <= 11:
            final.append(ph)
            continue
        i = 0
        while i < len(ph):
            rest = ph[i:]
            if len(rest) <= 11:
                cut = len(rest)
            else:
                cut = next((k + 1 for k in range(2, 9) if rest[k]["word"].endswith(",")), 0)
                if not cut:
                    def gap(k):        # the break after rest[k - 1]
                        lead = re.sub(r"[^\w']", "", rest[k - 1]["word"]).lower() in DANGLING_END
                        return rest[k]["start"] - rest[k - 1]["end"] - (1.0 if lead else 0.0) + 0.01 * k
                    cut = max(range(4, 10), key=gap)
            piece = [dict(w) for w in rest[:cut]]
            if i:
                piece[0]["_cut_start"] = True
            if i + cut < len(ph) and not piece[-1]["word"].endswith(","):
                piece[-1]["_cut_end"] = True
            final.append(piece)
            i += cut
    return final


def _phrase_text(ph: list) -> str:
    words = [w["word"] for w in ph]
    while words and re.sub(r"[^\w']", "", words[0]).lower() in LEAD_FILLER:
        words = words[1:]
    text = " ".join(words)
    text = re.sub(r"\s+([,.!?])", r"\1", text).strip(" ,;:-")
    text = text.rstrip(".")
    text = re.sub(r",$", "", text)
    if text:
        text = text[0].upper() + text[1:]
    return text


def _score_phrase(ph: list, text: str, peak_rel: float) -> float:
    toks = [re.sub(r"[^\w']", "", w["word"]).lower() for w in ph]
    toks = [t for t in toks if t]
    n = len(text.split())
    if n == 0:
        return -99
    s = 0.0
    if 3 <= n <= 8:
        s += 2
    elif 9 <= n <= 11:
        s += 1
    elif n <= 2:
        s -= 1 if not text.endswith("!") else 0
    if text.endswith("!") or "!" in text:
        s += 1.5
    if text.endswith("?"):
        s += 1
    s += min(2.0, 0.6 * sum(1 for t in toks if t in STRONG))
    if toks and all(t in FILLER for t in toks):
        s -= 3
    if len(text) > 55:
        s -= 2
    mid = (ph[0]["start"] + ph[-1]["end"]) / 2
    if peak_rel is not None:
        s += 1.5 * (1 - min(1.0, abs(mid - peak_rel) / 10))
    # words Whisper hallucinates on silence/music
    if re.search(r"thank(s| you) for watching|subscribe|♪", text, re.I):
        s -= 5
    # a piece cut out of a longer sentence that starts or stops mid-thought
    if ph[0].get("_cut_start") and toks and toks[0] in DANGLING_START:
        s -= 1.5
    if ph[-1].get("_cut_end") and toks and toks[-1] in DANGLING_END:
        s -= 1.5
    return s


def garbled(text: str) -> bool:
    """Whisper mishearing noise rather than a line someone said: "5, 4, 5, 5, 5, 6,.p",
    "L!, c!, I'm d, b, b, t, c!", "Uu, uu, uu, luumph", or a string of emoji."""
    toks = re.findall(r"[^\W_]+(?:['!*][^\W_]+)*", text)          # "sh!t" (masked) is one word
    if not toks:
        return True
    odd = sum(1 for t in toks if t.isdigit() or (len(t) <= 2 and t.lower() not in SHORT_WORDS))
    long = sum(1 for t in toks if len(t) >= 3 and not t.isdigit())
    if long == 0 or (long == 1 and odd) or odd / len(toks) >= 0.5:
        return True
    # a stammered list: most words cut off by commas, with fragments among them
    commas = len(re.findall(r"\w[!?]?,", text))
    if commas >= 3 and commas / len(toks) >= 0.5 and (odd / len(toks) >= 0.25 or len(set(map(str.lower, toks))) < 0.7 * len(toks)):
        return True
    symbols = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in ".,!?'\"-…*"))
    return symbols >= 2


KIND_VIBE = {"laugh": "funny", "hype": "hype", "shock": "shock", "fail": "fail", "confused": "confused",
             "cringe": "cringe"}


def _engine():
    import engine
    return engine


def _msg(m) -> tuple:
    """(text, who) for a chat message given as text or as a (seconds, text, user, ...) / (text, user) tuple."""
    if isinstance(m, str):
        return m, ""
    if isinstance(m, (list, tuple)):
        if len(m) >= 3 and isinstance(m[1], str):
            return m[1], str(m[2] or "")
        if len(m) >= 2 and isinstance(m[0], str):
            return m[0], str(m[1] or "")
    return "", ""


def _vibe(chat_msgs: list, words: list) -> tuple:
    """How the clip went over: (vibe or None, {vibe: weight}, clip requests). Chat is read like detection reads
    it (engine.reaction_kind, 4.36), each chatter once for how they reacted most, so a title's mood is the
    clip's: a laughing wave with some "L"s in it is funny, not a fail. What was said counts half a chatter
    a word."""
    counts = {k: 0.0 for k in VIBES}
    clip_req = 0
    per: dict = {}
    for i, m in enumerate(chat_msgs):
        text, who = _msg(m)
        low = text.lower()
        if "clip" in low:
            clip_req += 1
        kind = _engine().reaction_kind(text)
        toks = set(_TOKEN.findall(low))
        if not kind and toks & VIBES["wholesome"]["chat"]:
            kind = "wholesome"
        if not kind:
            for k, v in VIBES.items():
                if toks & v["chat"]:
                    kind = k
                    break
        if not kind:
            continue
        v = KIND_VIBE.get(kind, kind)
        d = per.setdefault(who or i, {})
        d[v] = d.get(v, 0) + 1
    for d in per.values():
        counts[max(d, key=lambda k: d[k])] += 1
    said = [re.sub(r"[^\w']", "", w["word"]).lower() for w in words]
    for k, v in VIBES.items():
        counts[k] += 0.5 * sum(1 for t in said if t in v["said"])
    best = max(counts, key=lambda k: counts[k])
    if counts[best] < 2:
        best = None
    return best, counts, clip_req


# words chat uses all the time, whatever happened: never a topic
TOPIC_STOP = set("""
about above after again against also always another anyone anything around away back because been before being
better between both bring came come comes coming could didnt does doesnt doing done dont down each even ever every
everyone everything fine first from gets give going gonna good gotta great guys have having hear here hers hell
him himself just keep kind know last least left less like little live long look looking lots love made make
making many maybe mean more most much must need never next nice none nothing only other over people please pretty
real really right said same says seen should since some someone something soon still stop stream streamer
streaming such sure take talk tell than thank thanks that thats their them then there these they thing things
think this those though thought through time today told tomorrow too took truly very wait want wanna watch watching
well were what whats when where which while whole will wish with without wont word work would yeah year years
your youre yours chat clip clips moment play playing game games video videos guy dude bros bro lmao lmfao haha
hahaha yall cant aint isnt wasnt didn doesn shes hes theyre were weve ive youve ill hed shed theyd okay alright
again first last next part start ended ending hello today tonight night morning yesterday week weekend
cap ratio mid rizz aura bussin fax facts sus based cringe goat valid fire lit sheesh bet deadass fr frfr
""".split())
DETERMINERS = {"the", "his", "her", "that", "this", "a", "an", "my", "your", "their", "our", "its", "those", "these"}
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def topic(chat_msgs: list, streamer: str = "", exclude=()) -> str:
    """What chat was talking about at the clip, as a noun ("hair", "phone", "car"), or '' when chat didn't
    agree on one. A word counts once a chatter; it needs at least 4 people and a tenth of those talking, and
    chat must have used it as a thing ("the hair", "his phone") at least twice, so titles like "Chat lost it
    over the {t}" stay grammatical. Never a reaction, an emote, a chatter's or the streamer's name, or a swear."""
    eng = _engine()
    import emotes
    people: dict = {}
    noun: dict = {}
    names = {re.sub(r"[^a-z]", "", (streamer or "").lower())}
    chatters = set()
    for i, m in enumerate(chat_msgs):
        text, who = _msg(m)
        low = text.lower()
        if not low or low.startswith("!"):
            continue
        who = who or f"#{i}"
        chatters.add(who)
        names.add(re.sub(r"[^a-z]", "", who.lower()))
        ws = _WORD.findall(low)
        for j, w in enumerate(ws):
            if len(w) < 3 or len(w) > 14:
                continue
            people.setdefault(w, set()).add(who)
            if j and ws[j - 1] in DETERMINERS:
                noun[w] = noun.get(w, 0) + 1
    need = max(4, len(chatters) // 10)
    best, score = "", 0
    for w, who in people.items():
        n = len(who)
        if n < need or noun.get(w, 0) < 2 or w in TOPIC_STOP or w in FILLER or w in exclude:
            continue
        if any(w in nm or (len(nm) >= 4 and nm in w) for nm in names if nm):
            continue                                   # "speed" in "ishowspeed", a chatter's name
        if w in eng.HYPE or w in eng.REACT or w in eng.STRETCHED or eng.reaction_kind(w) or PROFANITY.search(w) \
                or w in {e.lower() for e in emotes.POPULAR} or eng.LAUGH_TEXT.match(w):
            continue
        if n > score:
            best, score = w, n
    return best


def _pick(pool: list, used: set, seed: int) -> list:
    """The pool, those not used yet first, starting at a place set by seed so clips of one mood don't all get
    the same one (used holds titles and captions already given to this stream's clips)."""
    if not pool:
        return []
    k = seed % len(pool)
    turned = pool[k:] + pool[:k]
    return [t for t in turned if t.lower() not in used] + [t for t in turned if t.lower() in used]


TRAIL_FILLER = {"like", "so", "and", "but", "um", "uh", "the", "a", "to", "of", "or", "you", "know"}


def polish(text: str) -> str:
    """Tidy a title: sentence case for shouting, no stammered repeats, no trailing filler, one '!'."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 4 and sum(c.isupper() for c in letters) > 0.6 * len(letters):
        t = t.lower()                                          # SHOUTING -> normal case
    t = spelling.fix(t)[0]                                     # "dont", "im", "teh", a lone "i"
    # "no no no no no" -> "no no no"; "the the" -> "the"
    t = re.sub(r"\b(\w+)(?:[ ,]+\1\b){3,}", lambda m: " ".join([m.group(1)] * 3), t, flags=re.I)
    t = re.sub(r"\b(\w{2,})(?: \1\b)+", lambda m: m.group(1) if len(m.group(1)) > 3 else m.group(0), t, flags=re.I)
    t = re.sub(r"([!?])\1+", r"\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    words = t.split()
    while len(words) > 2 and re.sub(r"[^\w']", "", words[-1]).lower() in TRAIL_FILLER:
        words = words[:-1]
    t = " ".join(words).strip(" ,;:-")
    if len(t) > 60:                                            # keep titles readable on a phone
        cut = t[:58].rsplit(" ", 1)[0].rstrip(",;:-")
        t = cut + "..."
    if t:
        t = t[0].upper() + t[1:]
    return t


def _name(streamer: str) -> str:
    n = (streamer or "").strip()
    return n if re.fullmatch(r"[\w.\- ]{2,40}", n) else ""


def _named(title: str, name: str, vibe: Optional[str], quote: str, used: set, t_word: str = "",
           seed: int = 0) -> list:
    """Title B options: the streamer's name worked in once, naturally."""
    if not name:
        return []
    low = name.lower()
    out = []
    if quote and low not in quote.lower() and len(quote) <= 45:
        out.append(f'{name}: "{quote}"')
    if low in title.lower():
        out.append(title)                                     # A already names them: don't say it twice
    pool = []
    if vibe and t_word:
        pool += _pick(VIBES[vibe]["topic_named"], set(), seed)
    pool += (_pick(VIBES[vibe]["named"], set(), seed) if vibe else []) + NEUTRAL_NAMED
    out += [p.format(n=name, t=t_word) for p in pool]
    seen, res = set(), []
    for t in out:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            res.append(t)
    fresh = [t for t in res if t.lower() not in used]
    return fresh + [t for t in res if t.lower() in used]


def is_game(game: str, category: str = "") -> bool:
    """A real game (Minecraft), not one of Twitch's other categories (Just Chatting); or YouTube's Gaming."""
    g = (game or "").strip().lower()
    return bool(g) and g not in NOT_GAMES or (not g and (category or "").strip().lower() == "gaming")


def game_tag(game: str) -> str:
    """'Grand Theft Auto V' -> '#gta5'... in the form people search; '' when there's no usable tag."""
    g = (game or "").strip().lower()
    if g in CATEGORY_TAGS:
        return CATEGORY_TAGS[g]
    if g in NOT_GAMES:
        return ""
    g = re.sub(r"\s+", "", re.sub(r"[™®:'’.!&,\-]", "", GAME_ALIASES.get(g, g)))
    return "#" + g[:30] if g and re.fullmatch(r"\w+", g) else ""


# what people search for, where a game's name doesn't make a good tag as it is
GAME_ALIASES = {"grand theft auto v": "gta5", "counter-strike": "cs2", "counter-strike 2": "cs2",
                "league of legends": "leagueoflegends", "call of duty: warzone": "warzone",
                "fortnite": "fortnite", "minecraft": "minecraft", "valorant": "valorant",
                "rocket league": "rocketleague", "apex legends": "apexlegends", "ea sports fc 25": "eafc25",
                "ea sports fc 24": "eafc24", "nba 2k25": "nba2k25", "nba 2k24": "nba2k24", "dead by daylight": "dbd",
                "five nights at freddy's": "fnaf", "the binding of isaac: rebirth": "isaac",
                "world of warcraft": "wow", "overwatch 2": "overwatch2", "dota 2": "dota2", "rust": "rustgame",
                "lethal company": "lethalcompany", "roblox": "roblox", "elden ring": "eldenring"}


def hashtags(vibe: Optional[str], streamer: str = "", game: str = "", platform: str = "twitch",
             topic_word: str = "", category: str = "", seed: int = 0) -> list:
    """A few relevant tags, most specific first: the streamer, the game or what kind of stream it is, what chat
    was talking about, the clip's mood (one of a few, varied by seed), then the platform. #gaming only when it's
    known to be a game (4.36: before, every YouTube video got #gaming, and every Twitch clip #twitchclips)."""
    tags = []
    n = re.sub(r"[^\w]", "", _name(streamer))
    if n:
        tags.append("#" + n.lower())
    gt = game_tag(game) if game else ""
    if gt:
        tags.append(gt)
    if topic_word:
        tags.append("#" + topic_word)
    if vibe in VIBES:
        pool = VIBES[vibe]["tags"]
        tags.append(pool[seed % len(pool)])
    if is_game(game, category) and "#gaming" not in tags:
        tags.append("#gaming")
    if n and len(tags) < MAX_TAGS - 1:
        tags.append("#" + n.lower() + "clips")       # fans follow "#kaicenatclips", "#ishowspeedclips"
    tags += PLATFORM_TAGS.get(platform, BASE_TAGS)
    out = []
    for t in tags:
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    plat = PLATFORM_TAGS.get(platform, BASE_TAGS)
    if len(out) > MAX_TAGS and plat and plat[0] in out:
        # the platform's tag stays (people follow #twitch); what goes is the least specific of the rest
        out = [t for t in out if t != plat[0]][:MAX_TAGS - 1] + [plat[0]]
    return out[:MAX_TAGS]


def describe(vibe: Optional[str], quote: str, streamer: str = "", emoji: str = "👀", platform: str = "twitch",
             used: Optional[set] = None, seed: int = 0) -> str:
    """A post caption, separate from the title: what was said (or a line about how chat took it, one of a few,
    not given twice in a stream), and whose stream."""
    name = _name(streamer)
    where = PLATFORM_NAMES.get(platform, "Twitch")
    if quote:
        text = f'"{quote}"' + (f" ({name} on {where})" if name else "")
        return f"{text} {emoji}".strip()
    if vibe in VIBES:
        pool = _pick(VIBES[vibe]["hooks"], set(), seed)
        # used holds whole captions ("Wait for it, live on X's stream 😳"): the hook used least so far, so a long
        # stream goes round them all before repeating one
        times = {h: sum(1 for u in (used or ()) if u.startswith(h.lower())) for h in pool}
        hook = min(pool, key=lambda h: times[h]) if pool else VIBES[vibe]["describe"]
        credit = [f", live on {name}'s stream", f" ({name} on {where})", f" · {name}'s stream"][seed % 3]
        text = hook + (credit if name else "")
    else:
        text = f"A moment from {name}'s stream" if name else "A moment from the stream"
    return f"{text} {emoji}".strip()


def compose(description: str, tags: list, with_tags: bool = True) -> str:
    """The post text: the description, then the hashtags when they're switched on."""
    return f"{description}\n\n{' '.join(tags)}" if with_tags and tags else description


def generate(words: list, chat_msgs: list, peak_rel: Optional[float] = None,
             used: Optional[set] = None, streamer: str = "", with_tags: bool = True,
             pick: str = "a", game: str = "", chat_title: str = "", platform: str = "twitch",
             category: str = "") -> dict:
    """
    words      transcript words with times relative to the clip start: [{start,end,word}]
    chat_msgs  chat messages from the moments right after the clip: texts, or (seconds, text, user) tuples
               (4.36: with who said them, each chatter counts once for the mood and the topic)
    peak_rel   seconds from clip start where the hype peaked
    used       titles already given to other clips (to avoid repeats)
    streamer   the streamer's name for title B and the description ('' when unknown)
    with_tags  add hashtags to the post text
    pick       which title goes on the video by default: 'a' or 'b'
    chat_title a title from a phrase many chatters typed (chattitles.find), or ''
    platform   where the video came from (sources.PLATFORMS key), for the tags and description
    game       the game being played at the clip ('' when unknown), category YouTube's ("Gaming"...)
    """
    used = used or set()
    name = _name(streamer)
    vibe, counts, clip_req = _vibe(chat_msgs, words)
    # what's in this clip (its chat, its words, where it peaks) picks where in each pool to start, so clips of
    # one mood don't all get the first template, caption and tag
    seed = len(chat_msgs) * 7 + len(words) * 3 + int((peak_rel or 0) * 10)
    t_word = topic(chat_msgs, streamer)

    quotes = []
    for ph in _phrases(words):
        text = polish(_phrase_text(ph))
        if len(text) < 4 or garbled(text):
            continue
        quotes.append((_score_phrase(ph, text, peak_rel), _mask(text)))
    quotes.sort(key=lambda q: -q[0])
    good_quotes = [q for s, q in quotes if s >= 2.5]

    templates = []
    if vibe and t_word:
        templates += [t.format(t=t_word) for t in _pick(VIBES[vibe]["topic"], used, seed)]
    templates += _pick(VIBES[vibe]["templates"], used, seed) if vibe else []
    if clip_req >= 8:
        templates += CLIP_REQ            # chat asking for a clip says little about the clip: after the mood's
    templates += NEUTRAL

    seen, ordered = set(), []
    # what chat agreed on names the moment better than a template; a strong line said on stream still comes first
    lead = good_quotes[:1] + ([chat_title] if chat_title else []) + good_quotes[1:2]
    for t in lead + templates:
        t = polish(t)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            ordered.append(t)
    fresh = [t for t in ordered if t.lower() not in used]
    stale = [t for t in ordered if t.lower() in used]
    if not fresh and stale:
        # every option is taken already (a long stream with many clips of one mood): spread them out
        # instead of giving each clip the first template. `used` holds each title once, so it can't
        # count turns; what's in this clip (its chat, its words, where it peaks) picks one instead
        k = (len(chat_msgs) + len(words) + int((peak_rel or 0) * 10)) % len(stale)
        stale = stale[k:] + stale[:k]
    ordered = fresh + stale
    title_a = ordered[0] if ordered else "You have to see this"

    quote = good_quotes[0] if good_quotes else ""
    named = _named(title_a, name, vibe, quote, used, t_word, seed)
    if clip_req >= 8 and name:
        named = [t.format(n=name) for t in CLIP_REQ_NAMED] + named
    # no name known: B is a different kind of title (a reaction line if A is a quote, and the other
    # way round), so there are still two real choices
    other = [t for t in ordered[1:] if (t in good_quotes) != (title_a in good_quotes)]
    title_b = named[0] if named else (other[0] if other else ordered[1] if len(ordered) > 1 else title_a)
    title = title_b if pick == "b" else title_a

    alts = []
    for t in [title_a, title_b] + ordered + named:
        if t and t.lower() not in [x.lower() for x in alts]:
            alts.append(t)

    emoji = VIBES[vibe]["emoji"] if vibe else "👀"
    tags = hashtags(vibe, name, game, platform, t_word, category, seed)
    # the description says something the title on the video doesn't: the reaction when the title is the quote
    desc = describe(vibe, "" if quote and quote == title_a and vibe else quote, name, emoji, platform,
                    used | {title_a.lower(), title_b.lower()}, seed)
    return {
        "title": title,
        "title_a": title_a,
        "title_b": title_b,
        "title_b_named": bool(named),
        "titles": alts[:8],
        "description": desc,
        "hashtags": tags,
        "post": compose(desc, tags, with_tags),
        "vibe": vibe or "",
        "emoji": emoji,
        "quote": quote,
        "chat_title": chat_title if chat_title and chat_title in alts else "",
        "topic": t_word,
    }
