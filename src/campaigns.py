"""
Campaigns: the rules a clipping organisation (or a streamer's clipping Discord) sets for the clips
you make for them, kept together so switching between them is one click (Settings › Campaigns).

A campaign:
  name, org, link, notes    what it is, who runs it, where its rules live (a Discord invite, a page)
  caption                   text every post must carry ("@brand", "#ad", "Code KAI at ..."),
  caption_where             at the end of the post caption, or at the start
  tags                      hashtags every post must carry (added on top of your own, never trimmed)
  min_len, max_len          seconds a clip must last (0: no limit); clips outside get a warning
  watermark                 {"use": bool, "marks": [...]}: its own watermark instead of yours
  discord                   {"webhook", "auto", "file", "message"}: where to send finished clips,
                            whether to send each one as soon as it renders, with the video attached
                            or not, and the message ({title}, {caption}, {post_url}, {campaign},
                            {source}, {file})

The active campaign applies to clips queued while it's active (Exports shows which).

Campaign file (.json), shareable in Discord:
  {"clipradar": "campaign", "version": 1, "campaign": {...}, "images": {"<key>": "<base64>"}}
The Discord webhook is left out unless you choose to share it (anyone with it can post there).
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from typing import Optional

import discordhook
import hashtags
import paths
import persist
import watermarks

PACK_KIND = "campaign"
MAX_CAMPAIGNS = 40
DEFAULT_MESSAGE = "**{title}**\n{post_url}"
_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "campaigns.json")  # noqa: E731


def _text(v, n: int) -> str:
    return str(v or "").strip()[:n]


def clean_one(c) -> Optional[dict]:
    if not isinstance(c, dict) or not _text(c.get("name"), 60):
        return None

    def secs(k):
        try:
            return int(min(max(float(c.get(k) or 0), 0), 600))
        except (TypeError, ValueError):
            return 0
    wm = c.get("watermark") if isinstance(c.get("watermark"), dict) else {}
    dc = c.get("discord") if isinstance(c.get("discord"), dict) else {}
    hook = _text(dc.get("webhook"), 300)
    out = {"id": re.sub(r"[^\w-]", "", str(c.get("id") or ""))[:16] or uuid.uuid4().hex[:8],
           "name": _text(c.get("name"), 60), "org": _text(c.get("org"), 60), "link": _text(c.get("link"), 300),
           "notes": str(c.get("notes") or "").strip()[:2000],
           "caption": str(c.get("caption") or "").strip()[:500],
           "caption_where": "start" if c.get("caption_where") == "start" else "end",
           "tags": hashtags.tags(c.get("tags") or [], 15),
           "min_len": secs("min_len"), "max_len": secs("max_len"),
           "watermark": {"use": bool(wm.get("use")), "marks": watermarks.clean_marks(wm.get("marks"))},
           "discord": {"webhook": hook if discordhook.valid_webhook(hook) else "", "auto": bool(dc.get("auto")),
                       "file": bool(dc.get("file", True)),
                       "message": str(dc.get("message") or DEFAULT_MESSAGE).strip()[:1500] or DEFAULT_MESSAGE}}
    if out["max_len"] and out["min_len"] > out["max_len"]:
        out["min_len"], out["max_len"] = out["max_len"], out["min_len"]
    return out


def clean(d) -> dict:
    d = d if isinstance(d, dict) else {}
    items, seen = [], set()
    for c in (d.get("list") or [])[:MAX_CAMPAIGNS]:
        c = clean_one(c)
        if c and c["id"] not in seen:
            seen.add(c["id"])
            items.append(c)
    active = d.get("active") if d.get("active") in seen else ""
    return {"active": active, "list": items}


def load() -> dict:
    with _lock:
        return clean(persist.read_json(_FILE(), {}) or {})


def _write(d: dict) -> dict:
    d = clean(d)
    with _lock:
        try:
            persist.write_json(_FILE(), d)
        except OSError:
            pass
    return d


def save(c: dict) -> dict:
    """Add a campaign or replace the one with the same id."""
    one = clean_one(c)
    if not one:
        raise ValueError("Give the campaign a name.")
    d = load()
    items = [x for x in d["list"] if x["id"] != one["id"]]
    pos = next((i for i, x in enumerate(d["list"]) if x["id"] == one["id"]), len(items))
    if len(items) >= MAX_CAMPAIGNS:
        raise ValueError(f"You have {MAX_CAMPAIGNS} campaigns already. Delete one first.")
    items.insert(pos, one)
    d["list"] = items
    return _write(d)


def delete(cid: str) -> dict:
    d = load()
    d["list"] = [x for x in d["list"] if x["id"] != cid]
    if d["active"] == cid:
        d["active"] = ""
    return _write(d)


def set_active(cid: str) -> dict:
    d = load()
    d["active"] = cid if any(x["id"] == cid for x in d["list"]) else ""
    return _write(d)


def get(cid: str) -> Optional[dict]:
    return next((x for x in load()["list"] if x["id"] == cid), None) if cid else None


def active() -> Optional[dict]:
    return get(load()["active"])


# ---------------------------------------------------------------- applying the rules

def apply_post(post: str, c: Optional[dict], title: str = "") -> str:
    """The post caption with the campaign's required text and hashtags (each added once)."""
    if not c:
        return post
    text = (post or "").strip() or (title or "").strip()
    low = text.lower()
    need = (c.get("caption") or "").strip()
    if need and need.lower() not in low:
        text = f"{need}\n{text}" if c.get("caption_where") == "start" else f"{text}\n{need}"
    have = {t.lower() for t in re.findall(r"#\w+", text)}
    extra = [t for t in c.get("tags") or [] if t.lower() not in have]
    if extra:
        text = f"{text} {' '.join(extra)}" if text else " ".join(extra)
    return text.strip()[:2200]


def length_warning(c: Optional[dict], seconds: float) -> str:
    if not c:
        return ""
    lo, hi = c.get("min_len") or 0, c.get("max_len") or 0
    if lo and seconds < lo - 0.25:
        return f"{c['name']} wants clips of at least {lo} s; this one is {seconds:.0f} s. Make it longer, or it may not count."
    if hi and seconds > hi + 0.25:
        return f"{c['name']} wants clips of at most {hi} s; this one is {seconds:.0f} s. Trim it, or it may not count."
    return ""


def watermark_for(c: Optional[dict], own: dict) -> dict:
    """The watermark a clip gets: the campaign's own when it has one, otherwise yours."""
    if c and c["watermark"]["use"]:
        return {"on": bool(c["watermark"]["marks"]), "marks": c["watermark"]["marks"]}
    return own


def message(c: dict, fields: dict) -> str:
    """The Discord message for one clip, from the campaign's template."""
    tpl = (c.get("discord") or {}).get("message") or DEFAULT_MESSAGE
    vals = {k: str(fields.get(k) or "") for k in ("title", "caption", "post_url", "source", "file")}
    vals["campaign"] = c.get("name", "")
    out = re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), tpl)
    out = "\n".join(line for line in out.splitlines() if line.strip())    # an empty {post_url} leaves no blank line
    return out.strip()[:2000]


# ---------------------------------------------------------------- sharing

def make_pack(c: dict, with_webhook: bool = False) -> dict:
    c = clean_one(c) or {}
    marks, images = watermarks.pack_images(c.get("watermark", {}).get("marks") or [])
    out = dict(c, watermark=dict(c.get("watermark") or {}, marks=marks))
    out.pop("id", None)
    if not with_webhook:
        out["discord"] = dict(out.get("discord") or {}, webhook="")
    return {"clipradar": PACK_KIND, "version": 1, "campaign": out, "images": images}


def read_pack(d: dict) -> dict:
    """A campaign from a shared file, as a new campaign (its own id), images stored as our copies."""
    if not isinstance(d, dict) or d.get("clipradar") != PACK_KIND or not isinstance(d.get("campaign"), dict):
        raise ValueError("That file isn't a Clip Radar campaign.")
    c = dict(d["campaign"])
    wm = c.get("watermark") if isinstance(c.get("watermark"), dict) else {}
    c["watermark"] = dict(wm, marks=watermarks.unpack_images(wm.get("marks"), d.get("images")))
    c["id"] = uuid.uuid4().hex[:8]
    one = clean_one(c)
    if not one:
        raise ValueError("That campaign file has no name.")
    return one


def import_any(data: bytes) -> dict:
    """Import a shared Clip Radar file: a campaign or a watermark. Returns {"kind", ...}."""
    d = watermarks.parse(data)
    kind = d.get("clipradar") if isinstance(d, dict) else None
    if kind == PACK_KIND:
        c = read_pack(d)
        names, base, n = {x["name"].lower() for x in load()["list"]}, c["name"][:52], 2
        while c["name"].lower() in names:
            c["name"], n = f"{base} ({n})", n + 1
        save(c)
        return {"kind": "campaign", "campaign": c}
    if kind == watermarks.PACK_KIND:
        return {"kind": "watermark", "preset": watermarks.read_pack(d)}
    raise ValueError("That file isn't a Clip Radar campaign or watermark.")
