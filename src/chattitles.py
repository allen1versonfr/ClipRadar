"""
Titles from what chat said about a moment.

When many different chatters type the same short phrase around a clip ("he actually hit that",
"the dog walked in"), that phrase usually names the moment better than any template. This finds
such a phrase with plain counting, no AI and no network:

  1. Each chatter's messages in the clip's chat window, with bots, commands, notices, F-spam and
     poll votes already left out (chatsignal), links, @mentions and numbers removed, and stretched
     words squeezed ("noooooo" -> "no").
  2. Every 2-5 word run in those messages, counted once per chatter: support is people, not
     messages, so one viewer spamming can't make a title.
  3. A phrase needs at least MIN_PEOPLE chatters and a real share of everyone who chatted, has to
     say something (not only emotes, filler or a generic "let's go"), and can't contain a
     chatter's name or anything that looks like personal details.
  4. Longer phrases beat their own shorter parts when nearly as many people typed them; the best
     is scored by people, length and how specific it is.

The title says it's chat's words (Chat: "..."), so it never puts words in the streamer's mouth.
Anything unexpected returns no title; the normal titles are used instead.
"""

from __future__ import annotations

import re
from typing import Optional

MIN_PEOPLE = 3          # different chatters who typed the phrase
MIN_SHARE = 0.12        # ...and at least this share of everyone who chatted in the window
MAX_WORDS_MSG = 14      # longer messages are rants or copypasta, not reactions
MAX_TITLE_CHARS = 48

STOP = {"a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on", "at", "for", "is", "it", "its",
        "it's", "this", "that", "i", "im", "i'm", "me", "my", "you", "your", "u", "ur", "we", "he", "she", "they",
        "him", "her", "them", "his", "be", "was", "are", "were", "do", "did", "just", "like", "lol", "lmao", "bro",
        "chat", "yo", "oh", "ok", "okay", "yeah", "yes", "no", "not", "what", "why", "how", "with", "all", "get",
        "got", "go", "can", "cant", "can't", "dont", "don't", "has", "have", "had", "will", "would", "there", "here",
        "now", "then", "too", "very", "really", "omg", "wtf", "lmfao", "xd", "haha", "hahaha", "ha", "ah", "uh",
        "um", "hmm", "am", "if", "as", "by", "from", "up", "out", "lets", "let's", "gonna", "wanna"}
# reactions everyone types: the reaction templates already cover these
GENERIC = {"lets go", "let's go", "no way", "oh my god", "oh my", "my god", "what the", "clip it", "clip that",
           "clip this", "i cant", "i can't", "im dead", "i'm dead", "so good", "so funny", "good game", "well played",
           "w streamer", "l streamer", "what just happened", "what happened", "bro what", "nah bro", "oh no",
           "gg wp", "first time", "hi chat", "love you", "so cute"}
_URL = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|net|org|tv|gg|io|ly)\S*", re.I)
_MENTION = re.compile(r"@\w+")
_EMAIL = re.compile(r"\S+@\S+\.\w+")
_DIGITS = re.compile(r"\d")
_WORD = re.compile(r"[a-z][a-z']*")
_STRETCH = re.compile(r"([a-z])\1{2,}")


def _clean(text: str) -> Optional[list]:
    """Words of one message, or None when the message shouldn't count."""
    if not text or _EMAIL.search(text):
        return None
    t = _MENTION.sub(" ", _URL.sub(" ", text))
    t = _STRETCH.sub(r"\1", t.lower())          # "noooo" -> "no", "innnn" -> "in" ("good" stays)
    words = _WORD.findall(t)
    if not words or len(words) > MAX_WORDS_MSG:
        return None
    return words


def _ok_phrase(ws: tuple, names: set, emotes: set) -> bool:
    if any(w in names for w in ws):
        return False                                   # a chatter's username: never in a title
    content = [w for w in ws if w not in STOP and w not in emotes and len(w) >= 3]
    if not content:
        return False
    if ws[0] in emotes or ws[-1] in {"the", "a", "an", "and", "or", "to", "of", "but", "so", "my", "your"}:
        return False
    if " ".join(ws) in GENERIC or " ".join(ws[:2]) in GENERIC and len(ws) <= 3:
        return False
    return True


def find(msgs: list, emotes: Optional[set] = None, streamer: str = "") -> dict:
    """msgs: (text, user) pairs from the clip's chat window (noise already removed).
    Returns {"title", "phrase", "people", "chatters"}; title is "" when chat didn't agree on anything."""
    out = {"title": "", "phrase": "", "people": 0, "chatters": 0}
    try:
        emotes = {e.lower() for e in (emotes or set())}
        import chatsignal
        per_user: dict = {}
        names = set()
        for text, user in msgs:
            u = (user or "").lower()
            if not u or chatsignal.low_info(text):
                continue
            names.add(u)
            names.add(re.sub(r"[^a-z]", "", u))          # "cooluser99" typed in chat reads as "cooluser"
            ws = _clean(text)
            if ws:
                per_user.setdefault(u, []).append(ws)
        me = (streamer or "").lower()
        names -= {me, re.sub(r"[^a-z]", "", me), ""}     # the streamer's own name may stay in a phrase
        chatters = len(per_user)
        out["chatters"] = chatters
        if chatters < MIN_PEOPLE:
            return out
        support: dict = {}
        for u, lists in per_user.items():
            seen = set()
            for ws in lists:
                for n in range(2, 6):
                    for i in range(len(ws) - n + 1):
                        seen.add(tuple(ws[i:i + n]))
            for g in seen:
                support[g] = support.get(g, 0) + 1
        need = max(MIN_PEOPLE, MIN_SHARE * chatters)
        cands = {g: c for g, c in support.items() if c >= need and _ok_phrase(g, names, emotes)}
        if not cands:
            return out
        # a longer phrase wins over its own shorter part when nearly as many typed it
        best = None
        for g, c in cands.items():
            longer = [h for h, hc in cands.items() if len(h) > len(g) and hc >= 0.75 * c
                      and any(h[i:i + len(g)] == g for i in range(len(h) - len(g) + 1))]
            if longer:
                continue
            content = sum(1 for w in g if w not in STOP and w not in emotes)
            score = c * (1 + 0.25 * (len(g) - 2)) * (1 + 0.2 * content)
            if best is None or score > best[0]:
                best = (score, g, c)
        if best is None:
            return out
        _, g, c = best
        phrase = " ".join(g)
        if _DIGITS.search(phrase) or len(phrase) > MAX_TITLE_CHARS:
            return out
        phrase = phrase[0].upper() + phrase[1:]
        out.update(title=f'Chat: "{phrase}"', phrase=phrase, people=c)
    except Exception:
        return {"title": "", "phrase": "", "people": 0, "chatters": out.get("chatters", 0)}
    return out
