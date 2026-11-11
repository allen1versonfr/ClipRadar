"""
Series (4.51): clips that belong together, such as one storyline between two streamers ("Jason × Emily"), posted
as numbered parts with the same name, so viewers know there's more and have a reason to follow. From the first
user report: 582K views made 95 followers, and the clips that kept growing were one ongoing storyline.

All optional. Nothing is in a series until you put it there:
  - Suggestions: Clip Radar notices who's in a clip (names said on stream and typed by chat) and, when a name
    keeps coming back for one streamer, suggests a series. One click adds the clip as the next part.
    Settings › Series › Suggest series turns this off.
  - Add by itself (off unless you turn it on): a clip whose people match a series joins it as the next part.
  - Naming: the part goes before the title on the video ("Jason × Emily Pt. 6: Emily steals his car"), after it,
    or only in the post caption. The caption starts with the part and gets the series' hashtag.
  - End card (off unless you turn it on): "Follow for Pt. 7" over the last seconds of the clip.

Stored in series.json in Clip Radar's data folder.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Optional

import paths
import persist
import titles

DEFAULTS = {
    "suggest": True,          # suggest series from who's in a clip
    "auto_add": False,        # put a clip in a matching series by itself
    "naming": "before",       # before | after | caption: where "Name Pt. N" goes
    "caption_line": True,     # the post caption starts with the part
    "hashtag": True,          # the series' hashtag in the post caption
    "end_card": False,        # "Follow for Pt. N+1" over the clip's last seconds
    "end_card_text": "Follow for Pt. {next}",
}
NAMINGS = ("before", "after", "caption")
END_CARD_SECONDS = 2.5
MIN_MENTIONS = 2              # a name said or typed at least this often in a clip counts as someone in it
SUGGEST_AFTER = 2             # clips of one streamer a name has to be in before a new series is suggested
_lock = threading.RLock()
_cache: dict = {}

# capitalised words Whisper writes that aren't anyone (names are what's left)
NOT_NAMES = set("""
i im ive id ill god jesus christ lord chat bro bruh dude man guys yo oh okay ok yes yeah no nah hey hi hello bye
twitch youtube tiktok kick discord instagram twitter google netflix amazon spotify fortnite minecraft valorant league
gta roblox apex warzone overwatch america american americans english spanish monday tuesday wednesday thursday
friday saturday sunday january february march april may june july august september october november december
mr mrs ms sir mom dad mommy daddy brother sister baby bae bestie king queen boss chief doctor dr officer
lol lmao omg wtf bro's what why how who when where which this that these those there here the and but or so
""".split())


def _path() -> str:
    return os.path.join(str(paths.DATA_DIR), "series.json")


def _load() -> dict:
    if _cache.get("data") is not None:
        return _cache["data"]
    data = {"settings": dict(DEFAULTS), "list": [], "seen": {}}
    got = persist.read_json(_path(), {})
    if isinstance(got, dict):
        data["settings"].update({k: v for k, v in (got.get("settings") or {}).items() if k in DEFAULTS})
        data["list"] = [s for s in got.get("list") or [] if isinstance(s, dict) and s.get("id")]
        data["seen"] = got.get("seen") if isinstance(got.get("seen"), dict) else {}
    _cache["data"] = data
    return data


def _save():
    try:
        persist.write_json(_path(), _load())
    except OSError:
        pass


def reset_cache():
    _cache.clear()


# ---------------------------------------------------------------- settings

def settings() -> dict:
    with _lock:
        return dict(_load()["settings"])


def save_settings(patch: dict) -> dict:
    with _lock:
        s = _load()["settings"]
        for k in ("suggest", "auto_add", "caption_line", "hashtag", "end_card"):
            if k in patch:
                s[k] = bool(patch[k])
        if patch.get("naming") in NAMINGS:
            s["naming"] = patch["naming"]
        if "end_card_text" in patch:
            t = str(patch["end_card_text"] or "").strip()[:60]
            s["end_card_text"] = t or DEFAULTS["end_card_text"]
        _save()
        return dict(s)


# ---------------------------------------------------------------- who's in a clip

def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def people(words: list, chat: list, streamer: str = "") -> list:
    """Names in a clip, most mentioned first: capitalised words said on stream (not at the start of a sentence,
    where every word is capitalised) and the same names typed by chat, each chatter once. The streamer isn't one."""
    said: dict = {}
    prev = ""
    for i, w in enumerate(words or []):
        raw = str(w.get("word") or "").strip()
        tok = re.sub(r"^[^\w]+|[^\w]+$", "", raw)
        starts = i == 0 or prev.endswith((".", "!", "?"))
        prev = raw
        if starts or len(tok) < 3 or not tok[:1].isupper() or not tok.isalpha() or tok.isupper() and len(tok) > 3:
            continue
        if _key(tok) in NOT_NAMES:
            continue
        said.setdefault(_key(tok), [tok, 0])[1] += 1
    me = _key(streamer)
    typed: dict = {}
    for i, m in enumerate(chat or []):
        text, who = titles._msg(m)
        for t in set(_key(x) for x in re.findall(r"[A-Za-z]{3,}", str(text))):
            typed.setdefault(t, set()).add(who or i)
    out = []
    for k, (shown, n) in said.items():
        total = n + len(typed.get(k, ()))
        if k != me and k and not k.startswith(me or "\0") and total >= MIN_MENTIONS:
            out.append((total, shown))
    # names chat typed a lot that a series already has (chat writes them in lowercase)
    with _lock:
        known = {_key(p): p for s in _load()["list"] for p in s.get("people", [])}
    for k, shown in known.items():
        if k not in said and k != me and len(typed.get(k, ())) >= MIN_MENTIONS + 1:
            out.append((len(typed[k]), shown))
    out.sort(key=lambda x: -x[0])
    return [shown for _, shown in out[:3]]


def seen(streamer: str, names: list, clip_key: str):
    """Remember that these people were in this clip of this streamer (for suggesting a new series)."""
    if not names or not streamer:
        return
    with _lock:
        d = _load()["seen"].setdefault(_key(streamer), {})
        for n in names:
            e = d.setdefault(_key(n), {"name": n, "clips": []})
            if clip_key not in e["clips"]:
                e["clips"] = (e["clips"] + [clip_key])[-50:]
        _save()


# ---------------------------------------------------------------- series

def all_series() -> list:
    with _lock:
        return [dict(s) for s in _load()["list"]]


def get(sid: str) -> Optional[dict]:
    with _lock:
        return next((s for s in _load()["list"] if s["id"] == sid), None)


def default_name(streamer: str, names: list) -> str:
    who = [n for n in [streamer] + list(names) if n]
    return " × ".join(who[:3]) or "My series"


def hashtag(name: str) -> str:
    return "#" + _key(name.replace("×", "x"))[:30] if _key(name) else ""


def create(name: str, streamer: str = "", people_: Optional[list] = None) -> dict:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:60] or default_name(streamer, people_ or [])
    with _lock:
        s = {"id": uuid.uuid4().hex[:8], "name": name, "streamer": streamer or "", "people": list(people_ or [])[:5],
             "parts": [], "created": time.time()}
        _load()["list"].append(s)
        _save()
        return dict(s)


def update(sid: str, patch: dict) -> Optional[dict]:
    with _lock:
        s = get(sid)
        if not s:
            return None
        if "name" in patch:
            s["name"] = re.sub(r"\s+", " ", str(patch["name"] or "")).strip()[:60] or s["name"]
        if isinstance(patch.get("people"), list):
            s["people"] = [str(p).strip()[:30] for p in patch["people"] if str(p).strip()][:5]
        _save()
        return dict(s)


def delete(sid: str) -> bool:
    with _lock:
        data = _load()
        n = len(data["list"])
        data["list"] = [s for s in data["list"] if s["id"] != sid]
        _save()
        return len(data["list"]) < n


def add(sid: str, clip_key: str, title: str = "") -> Optional[dict]:
    """Put a clip in a series as its next part (or give back the part it already is). {"id", "name", "part"}."""
    with _lock:
        s = get(sid)
        if not s:
            return None
        part = next((p for p in s["parts"] if p["key"] == clip_key), None)
        if not part:
            n = max([p["part"] for p in s["parts"]], default=0) + 1
            part = {"key": clip_key, "part": n, "title": str(title or "")[:120], "at": time.time()}
            s["parts"].append(part)
            _save()
        return {"id": s["id"], "name": s["name"], "part": part["part"]}


def remove(sid: str, clip_key: str) -> bool:
    """Take a clip out of a series. The other parts keep their numbers (some may be posted already)."""
    with _lock:
        s = get(sid)
        if not s:
            return False
        n = len(s["parts"])
        s["parts"] = [p for p in s["parts"] if p["key"] != clip_key]
        _save()
        return len(s["parts"]) < n


def suggest(streamer: str, names: list, clip_key: str = "") -> Optional[dict]:
    """The series this clip looks like part of: one of this streamer's series with someone in the clip in it, or a
    new one when a name has come back in SUGGEST_AFTER of the streamer's clips. None when off or nothing fits.
    {"id": series id or "", "name", "people", "part": the part it would be, "new": bool, "why"}."""
    if not settings()["suggest"] or not names:
        return None
    keys = {_key(n) for n in names}
    me = _key(streamer)
    with _lock:
        for s in _load()["list"]:
            if s.get("streamer") and _key(s["streamer"]) != me:
                continue
            hit = [p for p in s.get("people", []) if _key(p) in keys]
            if hit:
                if any(p["key"] == clip_key for p in s["parts"]):
                    return None                          # it's in it already
                nxt = max([p["part"] for p in s["parts"]], default=0) + 1
                return {"id": s["id"], "name": s["name"], "people": s["people"], "part": nxt, "new": False,
                        "why": f"{hit[0]} is in this clip"}
        seen_ = _load()["seen"].get(me, {})
        for n in names:
            e = seen_.get(_key(n))
            clips = [c for c in (e or {}).get("clips", []) if c != clip_key]
            if e and len(clips) + 1 >= SUGGEST_AFTER:
                return {"id": "", "name": default_name(streamer, [e["name"]]), "people": [e["name"]], "part": 1,
                        "new": True, "why": f"{e['name']} is in {len(clips) + 1} of {streamer or 'this streamer'}'s clips"}
    return None


# ---------------------------------------------------------------- what a series changes

def part_label(name: str, part: int) -> str:
    return f"{name} Pt. {int(part)}"


def apply(title: str, post: str, ser: Optional[dict], cfg: Optional[dict] = None) -> tuple:
    """(title on the video, post caption, end card text) for a clip in a series ({"name", "part"}); unchanged when
    it isn't in one. Never adds the part twice (a clip queued again)."""
    if not ser or not ser.get("name") or not ser.get("part"):
        return title, post, ""
    cfg = cfg or settings()
    label = part_label(ser["name"], ser["part"])
    if label.lower() not in (title or "").lower():
        if cfg["naming"] == "before":
            title = f"{label}: {title}" if title else label
        elif cfg["naming"] == "after":
            title = f"{title} | {label}" if title else label
    if cfg["caption_line"] and label.lower() not in (post or "").lower():
        post = f"{label}\n{post}" if post else label
    tag = hashtag(ser["name"])
    if cfg["hashtag"] and tag and tag.lower() not in (post or "").lower():
        post = f"{post} {tag}" if post else tag
    card = ""
    if cfg["end_card"]:
        card = (cfg.get("end_card_text") or DEFAULTS["end_card_text"]).replace("{next}", str(int(ser["part"]) + 1)) \
            .replace("{series}", ser["name"]).strip()[:60]
    return title[:120], post[:2200], card


def end_card_mark(text: str, clip_len: float) -> dict:
    """The end card as a watermark text layer: big, in a box, in the middle, over the last seconds."""
    return {"kind": "text", "text": text, "pos": "free", "x": 0.5, "y": 0.62, "size": 170, "opacity": 100,
            "box": True, "box_color": "#141414", "color": "#FFFFFF", "outline": False, "font": "classic",
            "start": max(0.0, clip_len - END_CARD_SECONDS), "end": 0}


def view() -> dict:
    with _lock:
        d = _load()
        return {"settings": dict(d["settings"]),
                "list": [dict(s, count=len(s["parts"]), next=max([p["part"] for p in s["parts"]], default=0) + 1,
                              tag=hashtag(s["name"])) for s in d["list"]]}

