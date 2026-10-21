"""
Hashtags, two separate lists (Settings › Hashtags):

  file names    the actual tag text, '#' included, added to each exported file's name:
                KaiCenat_Chat lost it_#KaiCenat_#Mafia_#TwitchClips.mp4
  post captions the tags under the caption you copy for TikTok, Shorts, Reels or X

Each has its own general list and its own per-streamer list, so a streamer's community tags
(#Mafia for Kai Cenat) go only where you put them. Post captions can also have a list per
platform. Both are de-duplicated (ignoring case) and kept to a set number.

The '#' is kept in file names: it's allowed on Windows. Only the characters Windows forbids
(< > : " / \\ | ? *) and control characters are removed, separately from the tags themselves.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Optional

import paths
import persist

PLATFORMS = ("tiktok", "shorts", "reels", "x")
GENERAL_SUGGESTIONS = ["#TwitchClips", "#Streamer", "#FunnyMoments", "#Gaming", "#ViralClips"]
DEFAULTS = {
    "file": {"enabled": True, "streamer_tag": True, "general": ["#TwitchClips"], "streamers": {}, "max": 4},
    "post": {"global": [], "streamers": {}, "platforms": {p: [] for p in PLATFORMS}, "max": 8, "auto": True},
}
MAX_TAG = 40
MAX_FILE_TAGS = 8
MAX_POST_TAGS = 15
NAME_LIMIT = 150          # whole file name without the extension; leaves room for a long folder path
MIN_BASE = 24             # tags give way before the title is cut shorter than this

_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "hashtags.json")  # noqa: E731
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def tag(text) -> str:
    """'kai cenat' -> '#kaicenat', '#Mafia' -> '#Mafia'; '' when nothing usable is left."""
    t = _BAD.sub("", str(text or "")).strip().lstrip("#")
    t = re.sub(r"[^\w]", "", t)             # letters (any language), digits and _ only
    return ("#" + t[:MAX_TAG]) if t else ""


def tags(items, limit: int = MAX_POST_TAGS) -> list:
    """Clean, de-duplicated (ignoring case) tags, in order."""
    if isinstance(items, str):
        items = re.split(r"[\s,]+", items)
    out, seen = [], set()
    for it in items or []:
        t = tag(it)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= limit:
            break
    return out


def streamer_key(name: str) -> str:
    return re.sub(r"[^\w]", "", (name or "").lower())


def _streamer_map(raw) -> dict:
    """{name as you typed it: tags}; one entry per streamer however it's capitalised or spaced."""
    out, seen = {}, set()
    if isinstance(raw, dict):
        for k, v in list(raw.items())[:200]:
            name = re.sub(r"\s+", " ", _BAD.sub("", str(k or ""))).strip()[:40]
            key = streamer_key(name)
            t = tags(v, MAX_FILE_TAGS)
            if key and t and key not in seen:
                seen.add(key)
                out[name] = t
    return out


def _for(streamers: dict, streamer: str) -> list:
    key = streamer_key(streamer)
    return next((v for k, v in streamers.items() if streamer_key(k) == key), []) if key else []


def clean(d: dict) -> dict:
    d = d if isinstance(d, dict) else {}
    f, p = d.get("file") or {}, d.get("post") or {}
    out = {"file": {
        "enabled": bool(f.get("enabled", DEFAULTS["file"]["enabled"])),
        "streamer_tag": bool(f.get("streamer_tag", True)),
        "general": tags(f.get("general", DEFAULTS["file"]["general"]), MAX_FILE_TAGS),
        "streamers": _streamer_map(f.get("streamers")),
        "max": int(min(max(int(f.get("max") or DEFAULTS["file"]["max"]), 1), MAX_FILE_TAGS)),
    }, "post": {
        "global": tags(p.get("global"), MAX_POST_TAGS),
        "streamers": _streamer_map(p.get("streamers")),
        "platforms": {k: tags((p.get("platforms") or {}).get(k), MAX_POST_TAGS) for k in PLATFORMS},
        "max": int(min(max(int(p.get("max") or DEFAULTS["post"]["max"]), 1), MAX_POST_TAGS)),
        "auto": bool(p.get("auto", True)),
    }}
    return out


def load() -> dict:
    with _lock:
        return clean(persist.read_json(_FILE(), {}) or {})


def save(patch: dict) -> dict:
    cur = load()
    for part in ("file", "post"):
        if isinstance((patch or {}).get(part), dict):
            cur[part].update(patch[part])
    cur = clean(cur)
    with _lock:
        try:
            persist.write_json(_FILE(), cur)
        except OSError:
            pass
    return cur


# ---------------------------------------------------------------- file names

def file_tags(streamer: str, cfg: Optional[dict] = None) -> list:
    """The tags for a clip's file name: the streamer's own tag, your tags for that streamer, then
    your general tags. Empty when file-name hashtags are off."""
    c = (cfg or load())["file"]
    if not c["enabled"]:
        return []
    items = []
    if c["streamer_tag"] and streamer:
        items.append(tag(re.sub(r"\s+", "", streamer)))       # "Kai Cenat" -> "#KaiCenat"
    items += _for(c["streamers"], streamer)
    items += c["general"]
    return tags(items, c["max"])


def with_tags(base: str, file_tags_: list, limit: int = NAME_LIMIT) -> str:
    """'Title' + ['#A', '#B'] -> 'Title_#A_#B', within `limit` characters: tags drop off the end
    first, and the title is only shortened (never below MIN_BASE) when even one tag won't fit."""
    base = _BAD.sub("", base or "").strip().rstrip(". ") or "clip"
    have = {t.lower() for t in re.findall(r"#\w+", base)}
    extra = [t for t in file_tags_ if t.lower() not in have]          # a template may already hold some
    while extra and len(base) + sum(len(t) + 1 for t in extra) > limit:
        if len(extra) == 1 and len(base) > MIN_BASE:
            base = base[:max(MIN_BASE, limit - len(extra[0]) - 1)].rstrip(" ._-")
            break
        extra.pop()
    name = "_".join([base] + extra)
    return name[:limit].rstrip(" .")


# ---------------------------------------------------------------- post captions

def post_tags(auto: list, streamer: str, platform: str, cfg: Optional[dict] = None) -> list:
    """The tags under a post caption: the clip's own (platform, streamer, mood) when 'Clip Radar's
    tags' is on, then your defaults for every post, for this streamer and for this platform."""
    c = (cfg or load())["post"]
    items = list(auto or []) if c["auto"] else []
    items += c["global"]
    items += _for(c["streamers"], streamer)
    items += c["platforms"].get(platform, [])
    return tags(items, c["max"])
