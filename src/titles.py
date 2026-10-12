"""
Auto titles for clips.

VodClipper "watches" a clip the way a clipper would: it listens to what the
streamer says (Whisper transcript) and reads how chat reacted. From that it
picks a vibe (funny / hype / shock / fail / wholesome), looks for a quotable
line near the peak, and writes:

  title    short text burned onto the video (no emoji, so it renders everywhere)
  titles   a few alternatives to cycle through
  post     a ready-to-paste TikTok caption with emoji and hashtags
"""

from __future__ import annotations

import re
from typing import Optional

VIBES = {
    "funny": {
        "emoji": "😂",
        "tags": ["#funny", "#funnymoments"],
        "chat": {"kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "kekl", "lulw", "kek",
                 "pepelaugh", "xd", "haha", "hahaha", "dead", "😂", "🤣", "💀"},
        "said": {"haha", "hahaha", "lol", "funny", "hilarious", "joke", "laughing", "dying", "crying"},
        "templates": ["Chat could not stop laughing", "I can't believe this happened",
                      "This had chat in tears", "Why is this so funny", "I lost it here"],
    },
    "hype": {
        "emoji": "🔥",
        "tags": ["#clutch", "#gaming"],
        "chat": {"pog", "pogchamp", "poggers", "pogu", "letsgo", "lets", "w", "ez", "goat", "insane",
                 "hypers", "clutch", "gg", "🔥", "🐐"},
        "said": {"lets", "let's", "go", "yes", "clutch", "insane", "crazy", "goat", "easy", "gg",
                 "won", "win", "finally"},
        "templates": ["Chat went crazy for this", "Nobody expected this play",
                      "The cleanest play of the stream", "This is why I stream", "Absolutely clutch"],
    },
    "shock": {
        "emoji": "😳",
        "tags": ["#unexpected", "#wtf"],
        "chat": {"wtf", "omg", "monkas", "noway", "wideeyes", "nooo", "what", "d:", "😱", "😳", "huh"},
        "said": {"what", "wait", "how", "huh", "wtf", "omg", "no", "way", "holy", "god"},
        "templates": ["Wait for it...", "Chat was NOT ready for this", "What just happened",
                      "Nobody saw this coming", "I did not expect that"],
    },
    "fail": {
        "emoji": "💀",
        "tags": ["#fail", "#unlucky"],
        "chat": {"l", "rip", "f", "sadge", "unlucky", "throw", "throwing", "bozo", "copium", "classic"},
        "said": {"no", "why", "nooo", "unlucky", "rip", "dead", "died", "lost", "terrible", "throw"},
        "templates": ["This did not go as planned", "It was all going so well...",
                      "Chat will never let me live this down", "Pain.", "How did I lose this"],
    },
    "wholesome": {
        "emoji": "🥹",
        "tags": ["#wholesome"],
        "chat": {"<3", "❤", "❤️", "aww", "awww", "cute", "wholesome", "peepohappy", "hug"},
        "said": {"love", "thank", "thanks", "cute", "aww", "appreciate"},
        "templates": ["This made my whole stream", "Chat was so wholesome here",
                      "I love my community", "Moments like this"],
    },
}
NEUTRAL = ["You have to see this", "Wait for it...", "Best moment of the stream",
           "This moment from stream", "Watch till the end"]
CLIP_REQ = ["Chat said clip it", "Chat made me clip this"]
BASE_TAGS = ["#twitch", "#twitchclips", "#streamer", "#fyp"]

STRONG = {"no", "way", "what", "bro", "dude", "wait", "insane", "crazy", "oh", "god", "yes", "let's",
          "lets", "go", "holy", "dead", "how", "why", "never", "literally", "actually", "impossible",
          "clutch", "chat", "bruh", "nah", "yo", "stop", "run"}
FILLER = {"um", "uh", "uhh", "umm", "like", "so", "yeah", "okay", "ok", "the", "a", "and", "i", "you",
          "it", "is", "to", "of", "that", "this", "but", "just", "mm", "hmm"}
LEAD_FILLER = {"um", "uh", "uhh", "umm", "so", "and", "but", "like", "yeah", "okay", "ok", "well", "mm", "hmm"}
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
    # split very long phrases at commas, else into ~8 word pieces
    final = []
    for ph in out:
        if len(ph) <= 11:
            final.append(ph)
            continue
        piece = []
        for w in ph:
            piece.append(w)
            if (w["word"].endswith(",") and len(piece) >= 3) or len(piece) >= 8:
                final.append(piece)
                piece = []
        if piece:
            final.append(piece)
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
    return s


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


def generate(words: list, chat_msgs: list, peak_rel: Optional[float] = None,
             used: Optional[set] = None) -> dict:
    """
    words      transcript words with times relative to the clip start: [{start,end,word}]
    chat_msgs  chat message texts from the moments right after the clip
    peak_rel   seconds from clip start where the hype peaked
    used       titles already given to other clips (to avoid repeats)
    """
    used = used or set()
    vibe, counts, clip_req = _vibe(chat_msgs, words)

    quotes = []
    for ph in _phrases(words):
        text = _phrase_text(ph)
        if len(text) < 4:
            continue
        quotes.append((_score_phrase(ph, text, peak_rel), _mask(text)))
    quotes.sort(key=lambda q: -q[0])
    good_quotes = [q for s, q in quotes if s >= 2.5]

    templates = list(VIBES[vibe]["templates"]) if vibe else []
    if clip_req >= 8:
        templates = CLIP_REQ + templates
    templates += NEUTRAL

    titles = []
    for q in good_quotes[:2]:
        titles.append(q)
    for t in templates:
        titles.append(t)
    # de-duplicate, prefer ones not used by other clips
    seen, ordered = set(), []
    for t in titles:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            ordered.append(t)
    fresh = [t for t in ordered if t.lower() not in used]
    ordered = fresh + [t for t in ordered if t.lower() in used]
    title = ordered[0] if ordered else "Best moment of the stream"

    emoji = VIBES[vibe]["emoji"] if vibe else "👀"
    tags = BASE_TAGS + (VIBES[vibe]["tags"] if vibe else [])
    post = f"{title} {emoji}\n\n{' '.join(tags)}"
    return {
        "title": title,
        "titles": ordered[:6],
        "post": post,
        "vibe": vibe or "",
        "emoji": emoji,
        "quote": good_quotes[0] if good_quotes else "",
    }


def post_for(title: str, vibe: str) -> str:
    emoji = VIBES[vibe]["emoji"] if vibe in VIBES else "👀"
    tags = BASE_TAGS + (VIBES[vibe]["tags"] if vibe in VIBES else [])
    return f"{title.strip()} {emoji}\n\n{' '.join(tags)}"
