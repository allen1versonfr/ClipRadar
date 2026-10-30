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
    # named: the same with the streamer's name ({n})
    "funny": {
        "emoji": "😂",
        "tags": ["#funny"],
        "chat": {"kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "kekl", "lulw", "kek",
                 "pepelaugh", "xd", "haha", "hahaha", "dead", "😂", "🤣", "💀"},
        "said": {"haha", "hahaha", "lol", "funny", "hilarious", "joke", "laughing", "dying", "crying"},
        "templates": ["Chat could not stop laughing", "This had chat in tears", "Why is this so funny",
                      "Chat lost it at this", "This one broke chat", "The whole chat lost it"],
        "named": ["{n} had chat crying laughing", "{n} made chat lose it", "Chat could not stop laughing at {n}"],
        "describe": "Chat could not stop laughing at this one",
    },
    "hype": {
        "emoji": "🔥",
        "tags": ["#hype"],
        "chat": {"pog", "pogchamp", "poggers", "pogu", "letsgo", "lets", "w", "ez", "goat", "insane",
                 "hypers", "clutch", "gg", "🔥", "🐐"},
        "said": {"lets", "let's", "go", "yes", "clutch", "insane", "crazy", "goat", "easy", "gg",
                 "won", "win", "finally"},
        "templates": ["Chat went crazy for this", "Chat could not believe this", "The whole chat was hyped"],
        "named": ["Chat went crazy for {n}", "{n} got the whole chat hyped", "{n}'s chat could not believe this"],
        "describe": "Chat went crazy for this one",
    },
    "shock": {
        "emoji": "😳",
        "tags": ["#unexpected"],
        "chat": {"wtf", "omg", "monkas", "noway", "wideeyes", "nooo", "what", "d:", "😱", "😳", "huh"},
        "said": {"what", "wait", "how", "huh", "wtf", "omg", "no", "way", "holy", "god"},
        "templates": ["Wait for it...", "Chat was not ready for this", "What just happened",
                      "Nobody saw this coming"],
        "named": ["{n}'s chat was not ready for this", "{n} and chat did not see this coming", "Wait for {n}'s reaction..."],
        "describe": "Nobody saw this coming",
    },
    "fail": {
        "emoji": "💀",
        "tags": ["#fail"],
        "chat": {"l", "rip", "sadge", "unlucky", "throw", "throwing", "bozo", "copium", "classic"},
        "said": {"no", "why", "nooo", "unlucky", "rip", "dead", "died", "lost", "terrible", "throw"},
        "templates": ["This did not go as planned", "It was all going so well...", "Chat felt this one"],
        "named": ["It was all going so well for {n}...", "This did not go as planned for {n}", "{n}'s chat felt this one"],
        "describe": "It was all going so well",
    },
    "wholesome": {
        "emoji": "🥹",
        "tags": ["#wholesome"],
        "chat": {"<3", "❤", "❤️", "aww", "awww", "cute", "wholesome", "peepohappy", "hug"},
        "said": {"love", "thank", "thanks", "cute", "aww", "appreciate"},
        "templates": ["Chat was so wholesome here", "Moments like this"],
        "named": ["A wholesome moment with {n}", "{n}'s chat was so wholesome here"],
        "describe": "A wholesome moment",
    },
}
NEUTRAL = ["You have to see this", "Wait for it...", "Watch till the end"]
NEUTRAL_NAMED = ["{n} moment you have to see", "Wait for it... ({n})", "Watch {n} till the end"]
CLIP_REQ = ["Chat said clip it", "Chat made me clip this"]
CLIP_REQ_NAMED = ["{n}'s chat said clip it"]
BASE_TAGS = ["#twitch", "#twitchclips"]
# a video from YouTube or X gets that platform's tags and name instead (sources.PLATFORMS keys); files
# and other sites keep Twitch's, as they're usually stream recordings
PLATFORM_TAGS = {"youtube": ["#youtube", "#gaming"], "x": ["#gaming"]}
PLATFORM_NAMES = {"youtube": "YouTube", "x": "X"}
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


def _vibe(chat_msgs: list, words: list) -> tuple:
    counts = {k: 0.0 for k in VIBES}
    clip_req = 0
    for msg in chat_msgs:
        low = msg.lower()
        if "clip" in low:
            clip_req += 1
        toks = set(_TOKEN.findall(low))
        if "no way" in low:
            toks.add("noway")
        if "let's go" in low or "lets go" in low or "letsgo" in low:
            toks.add("letsgo")
        if "???" in low:
            toks.add("what")
        for k, v in VIBES.items():
            if toks & v["chat"]:
                counts[k] += 1
    said = [re.sub(r"[^\w']", "", w["word"]).lower() for w in words]
    for k, v in VIBES.items():
        counts[k] += 0.5 * sum(1 for t in said if t in v["said"])
    best = max(counts, key=lambda k: counts[k])
    if counts[best] < 2:
        best = None
    return best, counts, clip_req


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


def _named(title: str, name: str, vibe: Optional[str], quote: str, used: set) -> list:
    """Title B options: the streamer's name worked in once, naturally."""
    if not name:
        return []
    low = name.lower()
    out = []
    if quote and low not in quote.lower() and len(quote) <= 45:
        out.append(f'{name}: "{quote}"')
    if low in title.lower():
        out.append(title)                                     # A already names them: don't say it twice
    pool = (VIBES[vibe]["named"] if vibe else []) + NEUTRAL_NAMED
    out += [p.format(n=name) for p in pool]
    seen, res = set(), []
    for t in out:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            res.append(t)
    fresh = [t for t in res if t.lower() not in used]
    return fresh + [t for t in res if t.lower() in used]


def hashtags(vibe: Optional[str], streamer: str = "", game: str = "", platform: str = "twitch") -> list:
    """A few relevant tags: the platform, the streamer, the game (when known) and the clip's mood."""
    tags = list(PLATFORM_TAGS.get(platform, BASE_TAGS))
    n = re.sub(r"[^\w]", "", _name(streamer))
    if n:
        tags.insert(1, "#" + n.lower())
    g = re.sub(r"[^\w]", "", game or "")
    if g:
        tags.append("#" + g.lower())
    if vibe in VIBES:
        tags += VIBES[vibe]["tags"]
    out = []
    for t in tags:
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:MAX_TAGS]


def describe(vibe: Optional[str], quote: str, streamer: str = "", emoji: str = "👀", platform: str = "twitch") -> str:
    """A post caption, separate from the title: what was said (or how chat took it), and whose stream."""
    name = _name(streamer)
    if quote:
        text = f'"{quote}"' + (f" ({name} on {PLATFORM_NAMES.get(platform, 'Twitch')})" if name else "")
    elif vibe in VIBES:
        text = VIBES[vibe]["describe"] + (f", live on {name}'s stream" if name else "")
    else:
        text = f"A moment from {name}'s stream" if name else "A moment from the stream"
    return f"{text} {emoji}".strip()


def compose(description: str, tags: list, with_tags: bool = True) -> str:
    """The post text: the description, then the hashtags when they're switched on."""
    return f"{description}\n\n{' '.join(tags)}" if with_tags and tags else description


def generate(words: list, chat_msgs: list, peak_rel: Optional[float] = None,
             used: Optional[set] = None, streamer: str = "", with_tags: bool = True,
             pick: str = "a", game: str = "", chat_title: str = "", platform: str = "twitch") -> dict:
    """
    words      transcript words with times relative to the clip start: [{start,end,word}]
    chat_msgs  chat message texts from the moments right after the clip
    peak_rel   seconds from clip start where the hype peaked
    used       titles already given to other clips (to avoid repeats)
    streamer   the streamer's name for title B and the description ('' when unknown)
    with_tags  add hashtags to the post text
    pick       which title goes on the video by default: 'a' or 'b'
    chat_title a title from a phrase many chatters typed (chattitles.find), or ''
    platform   where the video came from (sources.PLATFORMS key), for the tags and description
    """
    used = used or set()
    name = _name(streamer)
    vibe, counts, clip_req = _vibe(chat_msgs, words)

    quotes = []
    for ph in _phrases(words):
        text = polish(_phrase_text(ph))
        if len(text) < 4 or garbled(text):
            continue
        quotes.append((_score_phrase(ph, text, peak_rel), _mask(text)))
    quotes.sort(key=lambda q: -q[0])
    good_quotes = [q for s, q in quotes if s >= 2.5]

    templates = list(VIBES[vibe]["templates"]) if vibe else []
    if clip_req >= 8:
        templates = CLIP_REQ + templates
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
    named = _named(title_a, name, vibe, quote, used)
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
    tags = hashtags(vibe, name, game, platform)
    # the description says something the title on the video doesn't: the reaction when the title is the quote
    desc = describe(vibe, "" if quote and quote == title_a and vibe else quote, name, emoji, platform)
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
    }
