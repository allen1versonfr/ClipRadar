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
        "tags": ["#confused"],
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
NEUTRAL = ["You have to see this", "Wait for it...", "Watch till the end", "Don't scroll past this one",
           "This had to be clipped", "Watch this one", "The moment it happened", "Keep watching"]
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
# a piece that stops on one of these stops before what was said or thought ("I think the patch notes said")
LEADS_INTO = {"said", "says", "goes", "think", "thought", "know", "knew", "like", "asked", "told", "then"}
DANGLING_START = {"of", "to", "and", "but", "or", "than", "that", "which", "with", "for", "as", "at", "in", "on",
                  "would", "could", "should", "is", "are", "was", "were", "be", "been", "being"}
# where a new clause starts, in speech without punctuation (YouTube's automatic captions): a good place to cut
# a long run ("...yesterday | and the guy behind the counter just looks at me | and goes are you famous")
CLAUSE_START = {"and", "but", "so", "because", "then", "okay", "ok", "oh", "wait", "i", "i'm", "it's", "he", "she",
                "we", "they", "there's", "that's", "this", "no", "yeah", "like", "goes", "what", "why", "how"}
# words that say nothing about what happened: a line made only of these ("Let's see if that's true", "Why did I do
# that") is a poor title however close to the peak it was said; one with a specific word ("spider", "cliff",
# "queen") says what the clip is
PLAIN = set("""
let's lets see seen if that's thats true really just here there then now know think thought want wanna gonna going
get got getting do did does done doing make made have has had having will would can could should be been being am
not all out up off one some any how who what why when where which this these those that it it's its me him her them
his hers our ours their they're you're we're i'm im i'd i'll i've don't dont can't cant didn't won't isn't wasn't
again still even ever never always very much more most too also only well right good bad nice cool okay yes yeah
come came go went say said tell told look looks looking guys chat bro dude man thing things stuff something
anything nothing everything way time today real quick literally actually honestly basically every
""".split())
# titles-v2: everyday words that, capitalised in the middle of a run of speech, start a new sentence
SENTENCE_WORDS = (PLAIN | STRONG | FILLER | CLAUSE_START | LEAD_FILLER | DANGLING_START | {
    "cut", "kiss", "look", "stop", "come", "give", "put", "wait", "watch", "please", "sorry", "thank", "thanks",
    "hey", "hi", "yo", "yep", "nope", "damn", "holy", "god", "jesus", "nice", "bro", "dude", "man", "guys"}) - {
    "i", "i'm", "i'd", "i'll", "i've", "god"}
# "...and goes are you famous": the words before a quote, dropped from a title made of it
QUOTATIVE = re.compile(r"^(?:(?:he|she|they|i|you|and|then)\s+)*(?:goes|says|was like|were like|is like|be like)\s+",
                       re.I)
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


def _new_sentence(prev: str, txt: str) -> bool:
    """titles-v2: Whisper often leaves out a full stop but starts the next sentence with a capital ("Kiss your lips
    No!", "Jynxzi, Jynxzi Oh my god"). A capitalised everyday word after a word with no punctuation starts a new
    sentence; names ("call coach Tariq") and "I" don't."""
    if not prev or prev[-1:] in ",;:-" or not txt[:1].isupper() or len(txt) < 2:
        return False
    bare = _bare(txt)
    txt = txt.rstrip(".,!?;:")
    if len(txt) < 2 or not txt[1:].replace("'", "").isalpha() or not txt[1:].islower() or bare.startswith("i'") or bare == "i":
        return False
    return bare in SENTENCE_WORDS


def _phrases(words: list) -> list:
    """Split a transcript into phrases at sentence ends and pauses."""
    out, cur = [], []
    for i, w in enumerate(words):
        txt = _clean_word(w["word"])
        if not txt:
            continue
        if cur and (w["start"] - cur[-1]["end"] > 0.7 or _new_sentence(cur[-1]["word"], txt)):
            out.append(cur)
            cur = []
        cur.append({**w, "word": txt})
        if txt[-1:] in ".!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    # Split very long phrases into pieces of 3-11 words, choosing all the cuts together: where the speaker
    # paused, after a comma, before a word that starts a clause, and never right after a word that leads into
    # the next ("the", "my", "this"). Automatic captions have no punctuation at all: a cut every few words landed
    # mid-sentence ("I loved every second of it this", "In my life it's literally the size"). Pieces cut this
    # way are marked for _score_phrase.
    final = []
    for ph in out:
        if len(ph) <= 11:
            final.append(ph)
            continue
        for k, (a, b) in enumerate(_cuts(ph)):
            piece = [dict(w) for w in ph[a:b]]
            if a:
                piece[0]["_cut_start"] = True
            if b < len(ph) and not piece[-1]["word"].endswith(","):
                piece[-1]["_cut_end"] = True
            final.append(piece)
    return final


def _bare(w: str) -> str:
    return re.sub(r"[^\w']", "", w).lower()


def _cuts(ph: list) -> list:
    """Where to cut a long run of speech: [(start, end)] pieces of 3-11 words, the best cuts over the whole run."""
    n = len(ph)

    def brk(k: int) -> float:                 # how good a cut before ph[k] is
        prev, nxt = ph[k - 1]["word"], ph[k]["word"]
        g = 3.0 * min(1.0, max(0.0, ph[k]["start"] - ph[k - 1]["end"]))
        g += 1.0 if prev.endswith((",", ";", ":")) else 0.0
        g += 0.8 if _bare(nxt) in CLAUSE_START else -1.0 if _bare(nxt) in DANGLING_START else 0.0
        g -= 1.5 if _bare(prev) in DANGLING_END else 0.0
        return g

    best = [float("-inf")] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for b in range(3, n + 1):
        for a in range(max(0, b - 11), b - 2):
            if best[a] == float("-inf"):
                continue
            ln = b - a
            v = best[a] + (brk(b) - 0.4 if b < n else 0.0) - 0.15 * abs(ln - 7)     # a cut needs a reason
            if v > best[b]:
                best[b], back[b] = v, a
    if best[n] == float("-inf"):
        return [(0, n)]
    cuts, b = [], n
    while b > 0:
        cuts.append((back[b], b))
        b = back[b]
    return cuts[::-1]


def _phrase_text(ph: list) -> str:
    words = [w["word"] for w in ph]
    while words and re.sub(r"[^\w']", "", words[0]).lower() in LEAD_FILLER:
        words = words[1:]
    text = " ".join(words)
    bare = QUOTATIVE.sub("", text)
    if bare != text and len(bare.split()) >= 3:
        text = bare
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
    strong = sum(1 for t in toks if t in STRONG)
    s += min(2.0, 0.6 * strong)
    if toks and all(t in FILLER for t in toks):
        s -= 3
    # a title says what happened: specific words ("spider", "cliff", "queen") count; a line with none, said
    # flatly, says nothing on its own ("Let's see if that's true")
    specific = sum(1 for t in toks if len(t) >= 3 and t not in PLAIN and t not in FILLER and t not in STRONG
                   and t not in DANGLING_END and t not in DANGLING_START)
    s += min(1.8, 0.6 * specific)
    if not specific and strong < 2 and "!" not in text:
        s -= 1.5 if text.endswith("?") else 2.5
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
    if ph[-1].get("_cut_end") and toks and (toks[-1] in DANGLING_END or toks[-1] in LEADS_INTO):
        s -= 1.5
    # titles-v2: the same stopping mid-thought where Whisper ended the line ("Tell them after this game after",
    # "Feeling like wait till"), and a line that's mostly one word said over and over ("Bar Bar Bar Go bar",
    # "On roof, on roof", "Oh Baking soda baking soda take baking")
    if toks and toks[-1] in TRAIL_PREP:
        s -= 1.0
    if len(toks) >= 3 and (len(set(toks)) < 0.75 * len(toks) or max(toks.count(t) for t in toks) >= 3):
        s -= 1.5 + max(1.0, 4 * (0.75 - len(set(toks)) / len(toks)))
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


TRAIL_PREP = {"after", "till", "until", "before", "about", "from", "into", "onto", "like", "the", "a", "an", "to", "of",
              "and", "but", "or", "so", "my", "your", "our", "in", "on", "at", "for", "with", "as", "if", "when",
              "because", "than", "you're", "we're", "they're", "it's", "i'm", "he's", "she's", "that's", "there's", "let's"}


QUOTE_OK = 2.5          # a line said on stream good enough to be a title at all
QUOTE_STRONG = 6.0      # titles-v2: ...and good enough to beat a viewer's clip title or chat's phrase
QUOTE_FAIR = 5.5        # ...or one word chat kept typing
SHOUT_PREFIX = "Chat kept saying "
CAPTION_QUOTE = 4.0     # a line said that's good enough to lead a post caption

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


# titles-v2: words that only react ("lol", "GGS", "OMFG", "yup"): a viewer's clip title made of them says nothing
REACTION_ONLY = {"lol", "lmao", "lmfao", "rofl", "gg", "ggs", "ggwp", "omg", "omfg", "wtf", "damn", "yup", "yep",
                 "nope", "yes", "no", "wow", "bruh", "bro", "huh", "what", "w", "l", "ww", "ll", "nice", "clip",
                 "clipped", "this", "that", "it", "insane", "crazy", "wild", "sheesh", "oh", "my", "god", "ok",
                 "okay", "alright", "dead", "im", "i'm", "lmaooo", "haha", "xd", "pog", "poggers", "moment", "funny",
                 "lul", "kekw", "omegalul", "ayo", "ayoo", "yo", "the", "a", "so", "real", "fr", "true", "same"}


def says_nothing(text: str) -> bool:
    """A title made only of reactions, emoji and punctuation ("Yup", "LMFAOOOOO", "GGs……", "LOL Ayooo😂🤣", "?")."""
    eng = _engine()
    ws = [re.sub(r"([a-z])\1{2,}", r"\1", w) for w in re.findall(r"[a-z][a-z']*", (text or "").lower())]
    return not any(w not in REACTION_ONLY and not eng.LAUGH_TEXT.match(w) and w not in eng.HYPE and w not in eng.REACT
                   and not eng.reaction_kind(w) for w in ws)


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
    # titles-v2: Whisper's spacing ("y 'all", "1 ,000")
    t = re.sub(r"(\w) '(all|re|s|ll|ve|d|m|t)\b", r"\1'\2", t)
    t = re.sub(r"(\d) ,(\d{3})\b", r"\1,\2", t)
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
        what = "stream" if where == "Twitch" else "video"       # a YouTube or X upload isn't a stream
        text = f"A moment from {name}'s {what}" if name else f"A moment from the {what}"
    return f"{text} {emoji}".strip()


# titles-v2: a question at the end of a caption, for comments. Only asks; never says what happened
QUESTIONS = {
    "funny": ["Who laughed first?", "Did you laugh too?", "Who needs to see this?", "Rate this from 1 to 10"],
    "hype": ["Were you watching live?", "Rate this from 1 to 10", "Who was there for this?"],
    "shock": ["Did you see that coming?", "What would you have done?", "Did you expect that?"],
    "fail": ["What would you have done?", "How would you have handled it?"],
    "confused": ["Can someone explain?", "What did we just watch?", "Does this make sense to anyone?"],
    "cringe": ["How would you have reacted?", "Would you have said something?"],
    "wholesome": ["Who else needed this today?", "Tag someone who needs this"],
    # 4.51: "What do you think?" and "Thoughts?" got next to no answers (first user report: 0.58 comments per 1,000
    # views). A question with a quick answer gets more
    None: ["Rate this from 1 to 10", "W or L?", "What would you have done?"],
    # 4.51: people arguing or taking sides. In the first user report these clips got most of the shares and the
    # best like rates, and a side to pick is the easiest comment to write
    "conflict": ["Who's right here?", "Whose side are you on?", "Who won this one?", "Who was wrong here?"],
}

# 4.51: chat taking sides, and an argument on stream (conflict)
CONFLICT_CHAT = {"ratio", "ratioed", "cooked", "fried", "violated", "violation", "exposed", "beef", "drama", "diss",
                 "disrespect", "disrespectful", "snake", "fraud", "lying", "liar", "wrong", "slander", "defend",
                 "toxic", "sides", "side", "team", "deserved", "owes", "apologize", "fight"}
CONFLICT_SAID = {"argue", "arguing", "argument", "wrong", "shut", "beef", "disrespect", "disrespectful", "lying",
                 "liar", "fraud", "snake", "fight", "apologize", "drama", "lie", "lied", "exposed", "blame"}


def conflict(chat_msgs: list, words: list) -> bool:
    """Whether the clip is an argument or a moment people take sides on: enough chatters using words for it, or
    chat split between W and L, or an argument heard on stream. Each chatter counts once."""
    sided, w_or_l = set(), {"w": set(), "l": set()}
    for i, m in enumerate(chat_msgs):
        text, who = _msg(m)
        toks = set(_TOKEN.findall(text.lower()))
        key = who or i
        if toks & CONFLICT_CHAT:
            sided.add(key)
        for k in ("w", "l"):
            if k in toks:
                w_or_l[k].add(key)
    said = sum(1 for w in words if re.sub(r"[^\w']", "", w["word"]).lower() in CONFLICT_SAID)
    split = min(len(w_or_l["w"]), len(w_or_l["l"])) >= 3
    return len(sided) + 0.5 * said >= 3 or split


NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "zero"}


def _has_specific(text: str) -> bool:
    """A line with a word that says something ("spider", "nether"), not only "Oh my god" or "Three, two, one"."""
    return any(len(t) >= 3 and t not in PLAIN and t not in STRONG and t not in FILLER and t not in NUMBER_WORDS
               and t not in TRAIL_PREP for t in (_bare(w) for w in text.split()))


def caption(vibe: Optional[str], lines: list, streamer: str = "", emoji: str = "👀", platform: str = "twitch",
            used: Optional[set] = None, seed: int = 0, ask: Optional[str] = None) -> str:
    """titles-v2: a post caption that says something specific the title doesn't: the first of lines (another line
    said on stream, a viewer's clip title, chat's phrase), else a line about how chat took it; then a question
    for comments and whose stream. Nothing in it is given twice in a stream where it can be helped."""
    used = used or set()
    name = _name(streamer)
    where = PLATFORM_NAMES.get(platform, "Twitch")
    lead = ""
    for kind, text in lines:
        cand = f'"{text}"' if kind == "quote" else text
        if text and not any(cand.lower() in u or text.lower() in u for u in used):
            lead = cand
            break
    credit = f" ({name} on {where})" if name else ""
    if not lead:
        if vibe not in VIBES:
            return describe(vibe, "", streamer, emoji, platform, used, seed) + (_ask(ask, used, seed) if ask else "")
        pool = _pick(VIBES[vibe]["hooks"], set(), seed)
        times = {h: sum(1 for u in used if u.startswith(h.lower())) for h in pool}
        lead = min(pool, key=lambda h: times[h])
    return f"{lead} {emoji}{_ask(ask or vibe, used, seed)}{credit}".strip()


def _ask(vibe: Optional[str], used: set, seed: int) -> str:
    pool = _pick(QUESTIONS.get(vibe if vibe in QUESTIONS else None), set(), seed)
    times = {q: sum(1 for u in used if q.lower() in u) for q in pool}
    return " " + min(pool, key=lambda q: times[q])


def compose(description: str, tags: list, with_tags: bool = True) -> str:
    """The post text: the description, then the hashtags when they're switched on."""
    return f"{description}\n\n{' '.join(tags)}" if with_tags and tags else description


def generate(words: list, chat_msgs: list, peak_rel: Optional[float] = None,
             used: Optional[set] = None, streamer: str = "", with_tags: bool = True,
             pick: str = "a", game: str = "", chat_title: str = "", platform: str = "twitch",
             category: str = "", viewer_titles: Optional[list] = None) -> dict:
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
    viewer_titles  titles viewers gave their own clips of this moment, most viewed first
               (twitchclips.titles_near, 4.47)
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
    good_quotes = [q for s, q in quotes if s >= QUOTE_OK]
    strong = bool(quotes) and quotes[0][0] >= QUOTE_STRONG
    fair = bool(quotes) and quotes[0][0] >= QUOTE_FAIR

    templates = []
    if vibe and t_word:
        templates += [t.format(t=t_word) for t in _pick(VIBES[vibe]["topic"], used, seed)]
    templates += _pick(VIBES[vibe]["templates"], used, seed) if vibe else []
    if clip_req >= 8:
        templates += CLIP_REQ            # chat asking for a clip says little about the clip: after the mood's
    templates += NEUTRAL

    # 4.47: what viewers called the clips they made of this moment. The first that reads well and another clip
    # of this stream hasn't got already: people who watched it named it, so it beats chat's phrase and templates
    viewer_title = ""
    for v in viewer_titles or []:
        v = _mask(polish(v))
        if len(v) >= 3 and not garbled(v) and not says_nothing(v) and v.lower() not in used:
            viewer_title = v
            break

    seen, ordered = set(), []
    # titles-v2: specific beats generic. A strong line said on stream comes first; then what viewers called their
    # clips and what chat agreed on, which name the moment; then a line that's only fair ("Sarah slowing down the
    # pace a little bit" used to beat "KAI GOT SMACKED"); then the templates
    # one word chat kept typing (chattitles' shout) is less than a phrase: a good line said on stream beats it
    shout = chat_title.startswith(SHOUT_PREFIX)
    specific = ([viewer_title] if viewer_title else []) + ([chat_title] if chat_title and not shout else [])
    after = [chat_title] if chat_title and shout else []
    if strong:
        lead = good_quotes[:1] + specific + after + good_quotes[1:2]
    elif fair:
        lead = specific + good_quotes[:1] + after + good_quotes[1:2]
    else:
        lead = specific + after + good_quotes[:2]
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
    # the description says something the title on the video doesn't: the reaction when the title is the quote, or
    # (no reaction to go on) the next best line said. It repeated the title in quotes before 4.41.
    # titles-v2: the caption leads with something specific the title on the video doesn't say: a line said on stream,
    # what viewers called their clip, or chat's phrase (before, a mood line whenever the title was the quote)
    shown = " ".join((title_a, title_b)).lower()
    shown_words = {_bare(w) for w in shown.split()} - PLAIN - FILLER - STRONG

    def new_to(text: str) -> bool:             # not the title again in other words ("How tf you make crack?")
        ws = {_bare(w) for w in text.split()} - PLAIN - FILLER - STRONG
        return text.lower() not in shown and (not ws or len(ws & shown_words) < 0.5 * len(ws))
    # A line said only makes the caption when it's a good one: a weak second line ("Not panic let's", "Two one two")
    # says less than chat's phrase
    said = [(sc, q) for sc, q in quotes if new_to(q) and len(q.split()) >= 3 and _has_specific(q)]
    lines = [("quote", q) for sc, q in said[:1] if sc >= QUOTE_FAIR]
    lines += [("viewer", viewer_title)] if viewer_title and new_to(viewer_title) else []
    lines += [("chat", chat_title)] if chat_title and new_to(polish(chat_title)) else []
    lines += [("quote", q) for sc, q in said[:2] if CAPTION_QUOTE <= sc < QUOTE_FAIR]
    if not vibe:
        # no reaction to go on: the next best line said, as before (4.41)
        lines += [("quote", q) for q in good_quotes if q.lower() not in shown][:1]
    argue = conflict(chat_msgs, words)
    desc = caption(vibe, lines, name, emoji, platform, used | {title_a.lower(), title_b.lower()}, seed,
                   ask="conflict" if argue else None)
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
        "viewer_title": viewer_title if viewer_title in alts else "",
        "topic": t_word,
        "conflict": argue,
    }
