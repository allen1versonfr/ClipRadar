"""
Turns raw Twitch chat into a clean "how hard is chat reacting" signal.

Raw message counts are noisy: bots post, giveaways make everyone type !enter,
gift-sub bombs and raids flood chat with thank-yous and raid emotes, and one
excited viewer can post ten times in five seconds. None of that means the
stream did something clip-worthy. So each message gets a weight:

  bots, !commands, sub/gift/raid notices      0
  someone who already chatted in the last 8 s  a quarter (counts chatters, not messages)
  the third+ copy of a long copypasta          0.15
  low-information messages ("F", a poll      0
  vote "1"/"2", a number, a lone "+")
  anything during a raid or gift-bomb flood    damped for a minute or so

Chat messages are tuples (seconds, text, user, kind). user may be "" and kind is
"" for normal chat or a Twitch notice id ("sub", "subgift", "raid", "bits"...).
"""

from __future__ import annotations

import re
from collections import deque

BOTS = {
    "nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot", "botisimo", "deepbot",
    "ankhbot", "phantombot", "coebot", "vivbot", "soundalerts", "sery_bot", "kofistreambot", "commanderroot",
    "pokemoncommunitygame", "blerp", "own3d", "frostytoolsdotcom", "lumiastream", "songlistbot", "streamstickers",
    "tangiabot", "creatisbot", "streamholics", "anotherttvviewer", "wzbot", "sambot", "mikuia", "stay_hydrated_bot",
    "pretzelrocks", "restreambot", "streamcaptainbot", "rainmaker", "fyowbot", "lolrankbot", "deckmasterbot",
    "scottybot", "revlobot", "hnlbot", "electricallongboard", "streamlootsbot", "kittiesbot", "timeoutwithbits",
    "playwithviewersbot", "buttsbot", "potatbotat", "supibot", "titlechange_bot", "streamjar", "tipeeebot",
}
_CMD = re.compile(r"^\s*[!$](\w)")
_SYSTEM = re.compile(
    r"gifted (?:a |an |\d+ )?(?:tier \d )?subs?\b|is gifting \d+|gifting \d+ (?:tier \d )?subs?|"
    r"subscribed (?:at tier|with prime|for \d+ months)|just (?:re)?subscribed|\bresubscribed\b|"
    r"converted from a prime|continuing the gift sub|paying (?:it|the gift) forward|"
    r"thanks? (?:you )?(?:so much )?for the (?:gifted |gift )?(?:sub|subs|gift|gifts|bits|raid|follow|prime|cheer|donation)|"
    r"\bty for the (?:sub|gift|bits|raid|follow)|welcome,? raiders|raiders? (?:incoming|welcome)|"
    r"\bis raiding\b|\bhas raided\b|\braid incoming\b|\bjust raided\b|"
    r"(?:enter|join) (?:the )?giveaway|giveaway (?:is )?(?:open|started|live|ending)|type ![\w-]+ to (?:enter|join|win)|"
    r"\bjust followed\b|thanks for following",
    re.I)
# One-key replies that say nothing about *what* happened: an F wave or a salute after a death, a poll
# vote, an agreeing "+". A wave of them alone must not make a moment. Even a small weight (0.05) let an
# F wave in a calm chat read as "chat explodes" in live mode, so they count for nothing, like bot posts;
# the moment itself is still found from the stream's sound and the rest of chat. "?", "!" and "W"/"L"
# are left alone: they carry a reaction.
# "f f fff" as one run of letters and spaces: a nested repeat here ((?:f+\s*)+) took seconds on "ffff...f!" and
# for ever on a longer one (4.33: a real chat had 120 f's in one message, and Find clips never finished)
# 4.33: a number on its own too ("50", "5,000", "100"): a vote, an answer or a price, not a reaction
# 4.35: "o7" (chat saluting after a death or a loss) counts again, a little, as a fail (emotes.POPULAR)
LOW_INFO = re.compile(r"^\s*(?:f[f\s]*|\$?[0-9][0-9,.]*k?|[+\-.^]+)\s*$", re.I)
LOW_INFO_WEIGHT = 0.0
_NORM = re.compile(r"[^\w\s]")
_SP = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SP.sub(" ", _NORM.sub("", text.lower())).strip()


def low_info(text: str) -> bool:
    """'F', '1', '+' and the like: a reply, not a reaction to what's on stream."""
    return bool(LOW_INFO.match(text or ""))


def is_bot(user: str) -> bool:
    u = (user or "").lower()
    return u in BOTS or (len(u) > 4 and (u.endswith("bot") or u.endswith("_bot")))


def is_noise(text: str, user: str = "", kind: str = "") -> bool:
    """Bot posts, commands and sub/raid/giveaway notices: not reactions to the stream."""
    return bool(kind) or is_bot(user) or bool(_CMD.match(text or "")) or bool(_SYSTEM.search(text or ""))


def human(chat: list) -> list:
    """Chat without the noise, same tuple shape."""
    return [m for m in chat if not is_noise(m[1], m[2] if len(m) > 2 else "", m[3] if len(m) > 3 else "")]


class ChatScorer:
    """Weighs messages in time order. Keeps its state, so live mode can feed it as chat arrives."""

    def __init__(self, hype_weight=None):
        if hype_weight is None:
            from engine import chat_weight as hype_weight
        self.hype = hype_weight
        self.last_counted: dict = {}           # user -> time their message last counted fully
        self.repeat_n: dict = {}               # user -> messages since then, inside those 8 s
        self.seen: set = set()
        self.pasta: dict = {}                  # normalised long text -> recent times
        self.events: deque = deque()           # recent sub / gift notice times
        self.newcomers: deque = deque()        # (time, first message from this chatter?)
        self.damp_until = -1e18
        self.damp = 1.0
        self.first_t = None
        self.last_raid = -1e18
        self.last_hype = None                  # the last message's reaction weight (None: not counted at all)
        self.stats = {"messages": 0, "bots": 0, "commands": 0, "notices": 0, "repeats": 0, "copypasta": 0,
                      "raids": 0, "floods": 0, "low_info": 0}
        self._n = 0

    # ---- floods: raids and gift bombs make chat explode about something that isn't on stream
    def _flood(self, t: float, seconds: float, factor: float, raid: bool = False):
        if raid:
            if t - self.last_raid < 120:
                return
            self.last_raid = t
            self.stats["raids"] += 1
        else:
            self.stats["floods"] += 1
        prev = self.damp_until
        self.damp = min(self.damp, factor) if t < prev else factor
        self.damp_until = max(prev, t + seconds)

    def _event(self, t: float, kind: str):
        if kind in ("raid",):
            self._flood(t, 90, 0.2, raid=True)
            return
        if kind in ("submysterygift", "anonsubmysterygift"):
            self._flood(t, 45, 0.1)
            return
        if kind in ("bits", "announcement", "viewermilestone"):
            return
        self.events.append(t)
        while self.events and self.events[0] < t - 30:
            self.events.popleft()
        if len(self.events) >= 4:            # a sub train or a run of gifted subs
            self._flood(t, 40, 0.1)

    def weight(self, t: float, text: str, user: str = "", kind: str = "") -> float:
        st = self.stats
        st["messages"] += 1
        self.last_hype = None
        if self.first_t is None:
            self.first_t = t
        text = text or ""
        if kind:
            st["notices"] += 1
            self._event(t, kind)
            return 0.0
        if is_bot(user):
            st["bots"] += 1
            if re.search(r"\braid|raiding|raided", text, re.I):
                self._flood(t, 90, 0.2, raid=True)
            return 0.0
        if _CMD.match(text):
            st["commands"] += 1
            return 0.0
        if _SYSTEM.search(text):
            st["notices"] += 1
            if re.search(r"raid", text, re.I):
                self._flood(t, 90, 0.2, raid=True)
            elif re.search(r"gift", text, re.I):
                self._event(t, "subgift")
            return 0.0
        w = float(self.hype(text))
        self.last_hype = w
        if LOW_INFO.match(text):
            st["low_info"] += 1
            w *= LOW_INFO_WEIGHT
        # copypasta: the same long message pasted over and over
        norm = _norm(text)
        if len(norm) >= 24 or norm.count(" ") >= 5:
            dq = self.pasta.get(norm)
            if dq is None:
                dq = self.pasta[norm] = deque()
            while dq and dq[0] < t - 90:
                dq.popleft()
            dq.append(t)
            if len(dq) >= 3:
                st["copypasta"] += 1
                w *= 0.15
        u = (user or "").lower()
        if u:
            new = u not in self.seen
            if new:
                self.seen.add(u)
            last = self.last_counted.get(u)
            if last is not None and t - last < 8:
                st["repeats"] += 1
                k = self.repeat_n[u] = self.repeat_n.get(u, 0) + 1
                w *= 0.25 ** min(k, 4)         # one chatter, not many: count each person about once per 8 s
            else:
                self.last_counted[u] = t
                self.repeat_n[u] = 0
            # a raid without a notice: a wave of people who never chatted before
            self.newcomers.append((t, new))
            while self.newcomers and self.newcomers[0][0] < t - 20:
                self.newcomers.popleft()
            if new and t - self.first_t > 600 and len(self.newcomers) >= 20:
                fresh = sum(1 for _, n in self.newcomers if n)
                if fresh >= 15 and fresh >= 0.7 * len(self.newcomers):
                    self._flood(t, 75, 0.25, raid=True)
        if t < self.damp_until:
            w *= self.damp
        self._n += 1
        if self._n % 5000 == 0:                # keep the copypasta table small on long streams
            self.pasta = {k: v for k, v in self.pasta.items() if v and v[-1] >= t - 90}
        return w

    def feed(self, chat: list, talk: list = None) -> list:
        """Weights for messages in time order, one per message. talk: if given, gets how much each message
        counts as a message (count()) when it's plain talk (no laugh, hype, shock or emote in it), else 0."""
        out = []
        for m in chat:
            w = self.weight(m[0], m[1], m[2] if len(m) > 2 else "", m[3] if len(m) > 3 else "")
            out.append(w)
            if talk is not None:
                talk.append(self.count(w) if self.plain() else 0.0)
        return out

    def count(self, w: float) -> float:
        """How much a message weighed w counts as one message: 1, or less for a repeat or during a flood.
        Reactions weigh more than talk, so shares of chat are taken from this, not from the weight."""
        return min(1.0, w / self.last_hype) if self.last_hype else 0.0

    def plain(self) -> bool:
        """The last message counted, but only as talk: "where are you from" answered, not a reaction."""
        return self.last_hype is not None and self.last_hype <= 1.0

    @property
    def chatters(self) -> int:
        return len(self.seen)

    def summary(self) -> dict:
        s = dict(self.stats)
        s["chatters"] = len(self.seen)
        s["ignored"] = s["bots"] + s["commands"] + s["notices"]
        return s
