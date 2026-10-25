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

# what kind of reaction an emote usually means, from its name
KINDS = [
    ("laugh", re.compile(r"(kek|lul|omegalul|icant|laugh|lmao|lmfao|xd|haha|jaja|dead|skull|crying|wheeze|rofl|pepela)", re.I)),
    ("hype", re.compile(r"(pog|letsgo|hype|gigachad|ez|clap|goat|fire|peepohey|catjam|ratjam|vibe|dance|cheer|wow)", re.I)),
    ("shock", re.compile(r"(monka|wtf|wide|omg|shock|stare|sus|hmm|what|huh|nope|nooo|dent|aware|scared|panic)", re.I)),
]


def kind_of(name: str) -> str:
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
