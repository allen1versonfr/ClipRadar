"""
Third-party Twitch emotes: 7TV, BetterTTV and FrankerFaceZ.

Chat is full of emotes those extensions add ("ICANT", "OMEGALUL", "Aware"...). Twitch's chat
replay only has their names as plain words, so to recognise them Clip Radar fetches the
channel's emote lists (and each service's global list) and caches them for a day. Only the
names are used, never the images.

When a service is down, or the channel has no emotes there, it carries on with whatever is
cached (or without that service). Nothing here is ever required for finding clips.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import paths

_BASE = os.environ.get("CLIPRADAR_EMOTE_API", "").rstrip("/")
API = {
    "7tv": (_BASE + "/7tv") if _BASE else "https://7tv.io",
    "bttv": (_BASE + "/bttv") if _BASE else "https://api.betterttv.net",
    "ffz": (_BASE + "/ffz") if _BASE else "https://api.frankerfacez.com",
}
GQL = os.environ.get("CLIPRADAR_TWITCH_GQL", "https://gql.twitch.tv/gql")
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
MAX_AGE = 24 * 3600          # refresh cached lists after a day
UA = {"User-Agent": "ClipRadar (+https://github.com/allen1versonfr/ClipRadar)"}
_lock = threading.Lock()
_mem: dict = {}

# what kind of reaction an emote usually means, from its name. Checked in this order. "calm" (4.35): an
# emote people use while nothing is happening (vibing to a song, saying hi or bye, going to bed): it isn't a
# reaction, and a wave of it (a song everyone catJAMs to, a raid saying peepoHey) isn't a moment
KINDS = [
    ("laugh", re.compile(r"(kek|lul|omegalul|icant|laugh|lmao|lmfao|xd|haha|jaja|dead$|skull|crying|wheeze|rofl|pepela|giggle)", re.I)),
    ("cringe", re.compile(r"(cringe|awkward|weirdge|weirdchamp|sus|nerd|whoasked|concerned|mlady|uuh|eeeh|oopsie)", re.I)),
    ("confused", re.compile(r"(huh|hmm|modcheck|thinking|^wot$|^buh$|^wuh$|shruge|confused|clueless\?)", re.I)),
    ("fail", re.compile(r"(sadge|pepehands|deadge|ripbozo|despair|trisad|feelsweakman|feelsbadman|^saj$|^o7$)", re.I)),
    ("calm", re.compile(r"(jam|vibe|danc|pls$|bedge|sleep|comfy|hey$|bye$|^hi+$|arrive|leave|listening|chatting|nodders|"
                        r"^yep$|^ok$|okayg|wave|peeposit|coffee|popcorn|cinema|watching|waiting)", re.I)),
    ("hype", re.compile(r"(pog|letsgo|hype|gigachad|^ez$|clap|goat|fire|cheer|riot|based|banger|wicked|cook|slay|pagman|cogg)", re.I)),
    ("shock", re.compile(r"(monka|wtf|wide|omg|shock|stare|nope|nooo|dent|aware|scared|panic|what|caught|noway|flashbang|"
                         r"^o+$|^aaa+$|ohno|meltdown|scatter|^d:$|:0|mad|rage)", re.I)),
]
# 4.35: the 7TV emotes chat uses most (7TV's top of all time), with the reaction each one means and what it
# adds to a message (like engine.HYPE). Exact names: "CAUGHT" is the emote, "caught" a word. Counted even when
# the channel's emote lists couldn't be loaded, and on YouTube and X. Emotes for vibing, greetings and sleep
# aren't here: they add nothing.
POPULAR = {
    # laughing
    "OMEGALUL": ("laugh", 2.0), "KEKW": ("laugh", 2.0), "ICANT": ("laugh", 1.8), "PepeLaugh": ("laugh", 1.8),
    "xdd": ("laugh", 1.5), "xdding": ("laugh", 1.5), "xdx": ("laugh", 1.2), "peepoGiggles": ("laugh", 1.5),
    "sadKEK": ("laugh", 1.5), "KEKL": ("laugh", 1.8), "LOL": ("laugh", 1.5), "HAH": ("laugh", 1.2),
    "TrollDespair": ("laugh", 1.2), "LULW": ("laugh", 1.8),
    # hype
    "GIGACHAD": ("hype", 1.5), "LETSGO": ("hype", 1.8), "POGGERS": ("hype", 1.8), "PogU": ("hype", 1.8),
    "Pog": ("hype", 1.8), "HYPERS": ("hype", 1.5), "VeryPog": ("hype", 1.8), "POGGIES": ("hype", 1.8),
    "PagMan": ("hype", 1.8), "gachiHYPER": ("hype", 1.5), "HYPERCLAP": ("hype", 1.5), "peepoClap": ("hype", 1.0),
    "Clap": ("hype", 1.0), "LETHIMCOOK": ("hype", 1.2), "BASED": ("hype", 1.0), "BANGER": ("hype", 1.2),
    "WICKED": ("hype", 1.2), "COGGERS": ("hype", 1.2), "peepoCheer": ("hype", 1.0), "Cheergi": ("hype", 1.0),
    "peepoRiot": ("hype", 1.2), "PauseChamp": ("hype", 0.8),
    # shock
    "NOOOO": ("shock", 1.8), "AINTNOWAY": ("shock", 1.8), "NOWAYING": ("shock", 1.8), "monkaW": ("shock", 1.5),
    "monkaS": ("shock", 1.5), "MONKA": ("shock", 1.5), "monkaChrist": ("shock", 1.5), "WHAT": ("shock", 1.5),
    "D:": ("shock", 1.2), "FLASHBANG": ("shock", 1.5), "AAAA": ("shock", 1.5), "OOOO": ("shock", 1.5),
    "ohno": ("shock", 1.2), "pepeMeltdown": ("shock", 1.2), "SCATTER": ("shock", 1.2), ":0": ("shock", 1.0),
    "CAUGHT": ("shock", 1.8), "CaughtIn4K": ("shock", 1.8), "Caught": ("shock", 1.5), "RAGEY": ("shock", 1.2),
    "Madge": ("shock", 1.0), "ReallyMad": ("shock", 1.2), "UltraMad": ("shock", 1.2), "Aware": ("shock", 1.0),
    # confused
    "HUH": ("confused", 1.5), "Hmm": ("confused", 1.0), "modCheck": ("confused", 1.0), "Thinking": ("confused", 1.0),
    "Thinking2": ("confused", 1.0), "hmmMeeting": ("confused", 1.0), "monkaHmm": ("confused", 1.0),
    "buh": ("confused", 1.0), "wuh": ("confused", 1.0), "WOT": ("confused", 1.2), "Shruge": ("confused", 0.8),
    # awkward, cringe
    "DIESOFCRINGE": ("cringe", 1.8), "uuh": ("cringe", 1.2), "Awkward": ("cringe", 1.5), "Weirdge": ("cringe", 1.2),
    "WeirdChamp": ("cringe", 1.2), "Susge": ("cringe", 1.2), "SUSSY": ("cringe", 1.2), "Nerdge": ("cringe", 1.0),
    "Nerd": ("cringe", 1.0), "WhoAsked": ("cringe", 1.0), "AYOOO": ("cringe", 1.2), "Concerned": ("cringe", 1.0),
    "eeeh": ("cringe", 1.0), "MLADY": ("cringe", 1.0), "oopsie": ("cringe", 0.8),
    # it went wrong: a death, a loss, a fail ("o7": chat saluting)
    "Sadge": ("fail", 1.0), "SadgeCry": ("fail", 1.2), "PepeHands": ("fail", 1.2), "Deadge": ("fail", 1.2),
    "RIPBOZO": ("fail", 1.2), "Despairge": ("fail", 1.2), "TriSad": ("fail", 1.0), "SAJ": ("fail", 1.0),
    "FeelsWeakMan": ("fail", 1.0), "FeelsBadMan": ("fail", 1.0), "o7": ("fail", 0.8), "O7": ("fail", 0.8),
}


def kind_of(name: str) -> str:
    if name in POPULAR:
        return POPULAR[name][0]
    for k, rx in KINDS:
        if rx.search(name):
            return k
    return ""


def _dir() -> str:
    d = os.path.join(str(paths.DATA_DIR), "emotes")
    os.makedirs(d, exist_ok=True)
    return d


def _ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _get(url: str, timeout: float = 8.0, body: Optional[bytes] = None, headers: Optional[dict] = None):
    req = urllib.request.Request(url, data=body, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx() if url.startswith("https") else None) as r:
        return json.loads(r.read().decode("utf-8"))


def _names_7tv(data) -> list:
    sets = data.get("emote_set") if isinstance(data, dict) and "emote_set" in data else data
    return [e.get("name") for e in ((sets or {}).get("emotes") or []) if isinstance(e, dict) and e.get("name")]


def _names_bttv(data) -> list:
    if isinstance(data, list):
        return [e.get("code") for e in data if isinstance(e, dict) and e.get("code")]
    out = []
    for key in ("channelEmotes", "sharedEmotes"):
        out += [e.get("code") for e in (data or {}).get(key) or [] if isinstance(e, dict) and e.get("code")]
    return out


def _names_ffz(data) -> list:
    out = []
    sets = (data or {}).get("sets") or {}
    wanted = (data or {}).get("default_sets")
    for k, s in sets.items():
        if wanted and int(k) not in wanted and str(k) not in map(str, wanted):
            continue
        out += [e.get("name") for e in (s or {}).get("emoticons") or [] if isinstance(e, dict) and e.get("name")]
    return out


SOURCES = {
    # service: (channel url, global url, parser)
    "7tv": ("{base}/v3/users/twitch/{id}", "{base}/v3/emote-sets/global", _names_7tv),
    "bttv": ("{base}/3/cached/users/twitch/{id}", "{base}/3/cached/emotes/global", _names_bttv),
    "ffz": ("{base}/v1/room/id/{id}", "{base}/v1/set/global", _names_ffz),
}


def _cached(key: str) -> Optional[dict]:
    if key in _mem:
        return _mem[key]
    try:
        with open(os.path.join(_dir(), key + ".json"), encoding="utf-8") as f:
            d = json.load(f)
        _mem[key] = d
        return d
    except (OSError, ValueError):
        return None


def _store(key: str, d: dict):
    _mem[key] = d
    try:
        import persist
        persist.write_json(os.path.join(_dir(), key + ".json"), d)
    except OSError:
        pass


def _fetch_lists(twitch_id: str, log) -> dict:
    """{service: [names]} for one channel ('' = the global lists). Services that fail are left out."""
    out = {}
    for svc, (chan_url, glob_url, parse) in SOURCES.items():
        url = (glob_url if not twitch_id else chan_url).format(base=API[svc], id=twitch_id)
        try:
            out[svc] = sorted(set(parse(_get(url))))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                out[svc] = []                      # the channel just doesn't use this service
            else:
                log(f"{svc.upper()} emotes unavailable ({e.code}).")
        except Exception as e:
            log(f"{svc.upper()} emotes unavailable ({str(e)[:60]}).")
    return out


def _get_lists(key: str, twitch_id: str, log, refresh: bool) -> dict:
    with _lock:
        c = _cached(key)
        fresh = c and time.time() - c.get("t", 0) < MAX_AGE and not refresh
    if fresh:
        return c.get("sets", {})
    got = _fetch_lists(twitch_id, log)
    with _lock:
        old = (c or {}).get("sets", {})
        sets = {svc: got.get(svc, old.get(svc, [])) for svc in SOURCES}   # keep stale lists for services that failed
        if got:
            _store(key, {"t": time.time(), "id": twitch_id, "sets": sets})
    return sets


def user_id(login: str, timeout: float = 6.0) -> str:
    """A channel's Twitch user id from its name (needed by the emote services)."""
    login = (login or "").strip().lower()
    if not re.fullmatch(r"\w{2,25}", login):
        return ""
    key = "id_" + login
    c = _cached(key)
    if c and c.get("id"):
        return c["id"]
    body = json.dumps({"query": 'query{user(login:"%s"){id}}' % login}).encode()
    try:
        d = _get(GQL, timeout, body, {"Client-ID": WEB_CLIENT_ID, "Content-Type": "application/json"})
        uid = str(((d.get("data") or {}).get("user") or {}).get("id") or "")
    except Exception:
        uid = ""
    if uid:
        _store(key, {"t": time.time(), "id": uid})
    return uid


class EmoteSet:
    """Emote names for one channel, with each one's service and kind of reaction."""

    def __init__(self, names: Optional[dict] = None):
        self.by_name: dict = {}             # exact name -> (service, kind)
        self.lower: dict = {}               # lowercase name -> exact name (for chat typed in any case)
        for svc, lst in (names or {}).items():
            for n in lst:
                if n and n not in self.by_name:
                    self.by_name[n] = (svc, kind_of(n))
                    self.lower.setdefault(n.lower(), n)

    def __len__(self):
        return len(self.by_name)

    def __bool__(self):
        return bool(self.by_name)

    def find(self, token: str) -> Optional[str]:
        if token in self.by_name:
            return token
        return self.lower.get(token.lower()) if len(token) >= 3 else None

    def counts(self) -> dict:
        out = {}
        for svc, _k in self.by_name.values():
            out[svc] = out.get(svc, 0) + 1
        return out


def for_channel(twitch_id: str = "", login: str = "", log=lambda m: None, refresh: bool = False) -> EmoteSet:
    """The channel's 7TV, BTTV and FFZ emotes plus each service's globals. Never raises."""
    try:
        if not twitch_id and login:
            twitch_id = user_id(login)
        names: dict = {}
        g = _get_lists("global", "", log, refresh)
        for svc, lst in g.items():
            names.setdefault(svc, []).extend(lst)
        if twitch_id:
            ch = _get_lists(f"ch_{re.sub(r'[^0-9A-Za-z_]', '', str(twitch_id))}", str(twitch_id), log, refresh)
            for svc, lst in ch.items():
                names.setdefault(svc, []).extend(lst)
        es = EmoteSet(names)
        if es:
            parts = ", ".join(f"{n} {s.upper()}" for s, n in sorted(es.counts().items()) if n)
            log(f"Emotes: {parts}.")
        return es
    except Exception as e:
        log(f"Couldn't load 7TV/BTTV/FFZ emotes ({str(e)[:80]}); going without them.")
        return EmoteSet()
