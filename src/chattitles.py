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
# titles-v2: one word many chatters typed ("POLLO", "DIANA", "TIP"), when chat didn't agree on a phrase. It needs
# more people than a phrase: this many, and this share of the chatters who typed anything but emotes and laughs
SHOUT_PREFIX = "Chat kept saying "      # titles.SHOUT_PREFIX: titles ranks a shout below a good line said on stream
SHOUT_PEOPLE = 6
SHOUT_SHARE = 0.10

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
_HASHTAG = re.compile(r"#\w+")
# a Twitch sub emote: the channel's prefix in lower case, then the emote's name ("caseohHamster", "jynxziBadAim")
_SUB_EMOTE = re.compile(r"^[a-z][a-z0-9]{2,11}[A-Z][A-Za-z0-9]*$")
# reactions that are words: never a shout on their own
SHOUT_STOP = {"yes", "yeah", "yep", "yup", "nope", "nah", "true", "false", "real", "same", "facts", "cap", "based",
              "ratio", "mid", "aura", "cooked", "goated", "valid", "fire", "insane", "crazy", "wild", "huh", "what",
              "why", "how", "who", "where", "when", "ggs", "ggwp", "geeg", "damn", "bruh", "sheesh", "wow", "woah",
              "ayo", "nice", "clean", "classic", "deserved", "rip", "sadge", "pain", "help", "stop", "run", "wait",
              "first", "hello", "hey", "bye", "please", "pls", "thanks", "thank", "love", "cute", "ugly", "gross",
              "eww", "yuck", "cringe", "sus", "lmao", "lmfao", "omg", "wtf", "lol", "lul", "dead", "skull",
              "chat", "stream", "streamer", "clip", "clipped", "mods", "ban", "banned", "timeout", "caught", "erm",
              "shit", "fuck", "ass", "bitch", "hell", "pussy", "dick", "goofy", "goober", "noob", "bozo",
              "trash", "loser", "winner", "tweaking", "tweak", "slay", "slayyy", "cinema", "peak", "fake", "scripted",
              "again", "never", "always", "everyone", "everybody", "nobody", "someone", "lets", "let's", "letsgo",
              "perfect", "good", "great", "better", "best", "amazing", "awesome", "beautiful", "smooth", "easy",
              "hard", "close", "lucky", "unlucky", "top", "down", "back", "over", "done", "win", "won", "lost",
              "lose", "fail", "epic", "hype", "huge", "big", "small", "little", "bad", "worst", "wrong", "right",
              "correct", "sure", "maybe", "okay", "alright", "fine", "cool", "sick", "dope", "lit", "hot", "cold"}
# Twitch's own emotes, which aren't in the channel's 7TV/BTTV/FFZ lists
TWITCH_EMOTES = {"jebaited", "kappa", "pogchamp", "pog", "pogbones", "kreygasm", "biblethump", "residentsleeper",
                 "notlikethis", "seemsgood", "vohiyo", "twitchconhype", "dansgame", "smorc", "cmonbruh", "babyrage",
                 "wutface", "pjsalt", "trihard", "heyguys", "komodohype", "coolstorybob", "lul", "keepo", "kappapride",
                 "failfish", "swiftrage", "mrdestructoid", "opieop", "sourpls", "bloodtrail", "minglee", "ezclap",
                 "monkas", "pepega", "pausechamp", "copium", "hopium", "kekw", "omegalul", "sadge", "madge", "deadge",
                 "yump", "clappi", "peepocheer", "monka", "gulp", "cinema", "erm", "kayaya", "gigakaya", "letsgo",
                 "kkona", "kkonaw", "toobased", "hesright", "chad", "yep", "nope", "stare", "looking", "icant"}


def _clean(text: str) -> Optional[list]:
    """Words of one message, or None when the message shouldn't count."""
    if not text or _EMAIL.search(text):
        return None
    t = _HASHTAG.sub(" ", _MENTION.sub(" ", _URL.sub(" ", text)))
    t = " ".join(w for w in t.split() if not _SUB_EMOTE.match(w))      # "caseohHamster": an emote, not a word
    t = _STRETCH.sub(r"\1", t.lower())          # "noooo" -> "no", "innnn" -> "in" ("good" stays)
    words = ["w" if w == "ww" else "l" if w == "ll" else w for w in _WORD.findall(t)]
    if not words or len(words) > MAX_WORDS_MSG:
        return None
    return words


def _squeeze(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _is_context(ws: tuple, avoid: str) -> bool:
    """titles-v2: the stream's own title, event or game typed back ("NMSBoxing", "nms boxing"), a hashtag
    rather than something about the moment."""
    sq = _squeeze("".join(ws))
    return bool(avoid) and len(sq) >= 4 and sq in avoid


def _ok_phrase(ws: tuple, names: set, emotes: set, avoid: str = "") -> bool:
    if any(w in names for w in ws):
        return False                                   # a chatter's username: never in a title
    if len(set(ws)) == 1 or _is_context(ws, avoid):
        return False                                   # "nmsboxing nmsboxing", the event's name
    content = [w for w in ws if w not in STOP and w not in emotes and len(w) >= 3]
    if not content:
        return False
    if ws[0] in emotes or ws[-1] in emotes or ws[-1] in {"the", "a", "an", "and", "or", "to", "of", "but", "so", "my",
                                                         "your"}:
        return False
    if " ".join(ws) in GENERIC or " ".join(ws[:2]) in GENERIC and len(ws) <= 3:
        return False
    return True


def _shout_ok(w: str, names: set, emotes: set, avoid: str, streamer: str) -> bool:
    import engine
    import titles
    return (len(w) >= 3 and w not in STOP and w not in SHOUT_STOP and w not in emotes and w not in names
            and w not in titles.TOPIC_STOP and w not in titles.PLAIN and w not in titles.REACTION_ONLY
            and not (streamer and (w in streamer or streamer in w)) and not _is_context((w,), avoid)
            and not titles.PROFANITY.search(w) and not engine.LAUGH_TEXT.match(w) and w not in engine.HYPE
            and w not in engine.REACT and not engine.reaction_kind(w) and not _DIGITS.search(w))


def _cased(word: str, raw: list) -> str:
    """How chat writes the word when it isn't shouting: "Diana", "pollo"; capitalised when most do."""
    up = low = 0
    for text in raw:
        for t in re.findall(r"[A-Za-z][A-Za-z']*", text):
            if _STRETCH.sub(r"\1", t.lower()) != word or t.isupper():
                continue
            up, low = up + t[0].isupper(), low + t[0].islower()
    return word[0].upper() + word[1:] if up * 2 >= low or not (up or low) else word


def find(msgs: list, emotes: Optional[set] = None, streamer: str = "", avoid=()) -> dict:
    """msgs: (text, user) pairs from the clip's chat window (noise already removed). avoid: the stream's title and
    game, which chat typing back isn't about the moment (titles-v2).
    Returns {"title", "phrase", "people", "chatters"}; title is "" when chat didn't agree on anything."""
    out = {"title": "", "phrase": "", "people": 0, "chatters": 0}
    try:
        emotes = {e.lower() for e in (emotes or set())} | TWITCH_EMOTES
        avoid_sq = " ".join(_squeeze(a) for a in avoid if a)
        import chatsignal
        per_user: dict = {}
        names = set()
        raw = []
        for text, user in msgs:
            u = (user or "").lower()
            if not u or chatsignal.low_info(text):
                continue
            names.add(u)
            names.add(re.sub(r"[^a-z]", "", u))          # "cooluser99" typed in chat reads as "cooluser"
            ws = _clean(text)
            if ws:
                per_user.setdefault(u, []).append(ws)
                raw.append(text)
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
        # titles-v2: a share of the chatters who said something, not of everyone who typed LOL (on a big stream's
        # clip 12% of everyone was more than ever agreed on a phrase)
        talking = sum(1 for lists in per_user.values()
                      if any(w not in STOP and w not in emotes and len(w) >= 3 for ws in lists for w in ws)) or chatters
        need = max(MIN_PEOPLE, MIN_SHARE * talking)
        cands = {g: c for g, c in support.items() if c >= need and _ok_phrase(g, names, emotes, avoid_sq)}
        if not cands:
            # one word many chatters typed alone or nearly ("POLLO", "W DIANA", "CALL IT"): it names the person or
            # thing the moment was about. Shown as chat typed it most ("Call it", "New York")
            me_sq = re.sub(r"[^a-z]", "", me)
            backing: dict = {}
            forms: dict = {}
            for u, lists in per_user.items():
                mine = {tuple(w for w in ws if w not in emotes) for ws in lists if len(ws) <= 3}
                for m in mine:
                    for w in set(m):
                        backing.setdefault(w, set()).add(u)
                        forms.setdefault(w, {}).setdefault(m, set()).add(u)
            ok = [(len(us), w) for w, us in backing.items() if len(us) >= max(SHOUT_PEOPLE, SHOUT_SHARE * talking)
                  and _shout_ok(w, names, emotes, avoid_sq, me_sq)
                  and not _is_context(max(forms[w], key=lambda f: len(forms[w][f])), avoid_sq)]
            if not ok:
                return out
            c, w = max(ok)
            form, fc = max(forms[w].items(), key=lambda kv: (len(kv[1]), -len(kv[0])))
            if len(fc) < 0.4 * c or any(x in names or _DIGITS.search(x) for x in form):
                form = (w,)
            form = tuple(x for x in form if x not in {"w", "l"}) if len(form) > 1 and form[0] not in {"w", "l"} else form
            words = [x.upper() if x in {"w", "l"} else _cased(x, raw) for x in form]
            word = " ".join(words)
            word = word[0].upper() + word[1:]
            out.update(title=f'Chat: "{word}"' if len(words) > 1 else f'{SHOUT_PREFIX}"{word}"', phrase=word, people=c)
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
        phrase = re.sub(r"\b([wl])\b", lambda m: m.group(1).upper(), phrase)     # "W diana" -> "W Diana"
        head, *rest = phrase.split()
        phrase = " ".join([head] + [_cased(x, raw) if x.isalpha() and len(x) > 2 else x for x in rest])
        out.update(title=f'Chat: "{phrase}"', phrase=phrase, people=c)
    except Exception:
        return {"title": "", "phrase": "", "people": 0, "chatters": out.get("chatters", 0)}
    return out
