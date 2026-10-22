"""
Watermarks: one or more layers (text or an image) on exported clips, and watermark packs you can
share. Clipping organisations hand out their logo and @handle in Discord; a pack is one file with
the layers and their images inside, so importing it gives the same marks in the same places.

A layer:
  kind      text | image
  text      the text of a text layer (60 characters at most)
  image     a copy of the image kept in Clip Radar's own folder (never the original you picked)
  pos       tl | tr | bl | br | c (corners and centre, kept clear of TikTok's bars), or free
  x, y      with pos "free": the layer's centre, as a share of the frame's width and height
  size      percent of the normal size (text 44 px tall, an image a fifth of the frame wide)
  opacity   percent
  margin    pixels from the side at 1080 wide (corners only)
  rotation  degrees, clockwise
  color     text colour (#RRGGBB)
  font      one of the bundled caption fonts
  outline   a dark outline around text
  box       a box behind text, in box_color
  start/end seconds into the clip it appears and disappears (end 0: until the end)

Pack file (.json), shareable in Discord:
  {"clipradar": "watermark", "version": 1, "name": "...", "marks": [...], "images": {"<key>": "<base64>"}}
where each image layer's "image" is a key into "images".
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Optional

import paths

POSITIONS = ("tl", "tr", "bl", "br", "c", "free")
KINDS = ("text", "image")
FONTS = ("classic", "montserrat", "poppins", "inter", "anton", "bebas", "bangers", "luckiest")
IMAGE_EXT = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp"}
MAX_MARKS = 6
MAX_IMAGE = 20_000_000
MAX_PACK = 60_000_000
PACK_KIND = "watermark"

MARK_DEFAULTS = {"kind": "text", "text": "", "image": "", "pos": "tl", "x": 0.5, "y": 0.5, "size": 100,
                 "opacity": 70, "margin": 40, "rotation": 0, "color": "#FFFFFF", "font": "classic",
                 "outline": True, "box": False, "box_color": "#141414", "start": 0.0, "end": 0.0}


def image_dir() -> str:
    d = os.path.join(str(paths.DATA_DIR), "watermarks")
    os.makedirs(d, exist_ok=True)
    return d


def _ours(path: str) -> bool:
    """An image Clip Radar keeps itself: in its watermarks folder, or the single copy older versions kept."""
    if not path or not os.path.isfile(path):
        return False
    real, data = os.path.realpath(path), os.path.realpath(str(paths.DATA_DIR))
    if os.path.dirname(real) == os.path.realpath(image_dir()):
        return True
    return os.path.dirname(real) == data and re.fullmatch(r"watermark\.(png|jpe?g|webp)", os.path.basename(real), re.I) is not None


def _color(v, default: str) -> str:
    v = str(v or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", v):
        return "#" + v.lstrip("#").upper()
    return default


def clean_mark(m) -> Optional[dict]:
    """A layer kept within range; None when there's nothing to show (no text, no image)."""
    if not isinstance(m, dict):
        return None
    b = MARK_DEFAULTS

    def num(k, lo, hi, typ=int):
        try:
            return typ(min(max(typ(m.get(k, b[k]) or 0), lo), hi))
        except (TypeError, ValueError):
            return b[k]
    out = {"kind": m.get("kind") if m.get("kind") in KINDS else ("image" if m.get("image") else "text"),
           "text": re.sub(r"[\r\n\t]+", " ", str(m.get("text") or "")).strip()[:60],
           "image": str(m.get("image") or ""),
           "pos": m.get("pos") if m.get("pos") in POSITIONS else "tl",
           "x": round(num("x", 0, 1, float), 4), "y": round(num("y", 0, 1, float), 4),
           "size": num("size", 20, 400), "opacity": num("opacity", 5, 100), "margin": num("margin", 0, 200),
           "rotation": num("rotation", -180, 180), "color": _color(m.get("color"), b["color"]),
           "font": m.get("font") if m.get("font") in FONTS else "classic",
           "outline": bool(m.get("outline", True)), "box": bool(m.get("box")),
           "box_color": _color(m.get("box_color"), b["box_color"]),
           "start": round(num("start", 0, 3600, float), 2), "end": round(num("end", 0, 3600, float), 2)}
    if out["kind"] == "image":
        if not _ours(out["image"]):
            out["image"] = ""
        if not out["image"]:
            return None
    else:
        out["image"] = ""
        if not out["text"]:
            return None
    return out


def clean_marks(marks) -> list:
    out = []
    for m in (marks if isinstance(marks, list) else [])[:MAX_MARKS * 2]:
        c = clean_mark(m)
        if c:
            out.append(c)
        if len(out) >= MAX_MARKS:
            break
    return out


def from_legacy(d: dict) -> list:
    """Watermark settings from before 4.11 (one text or one image) as layers. An image was used
    instead of the text when both were set."""
    common = {k: d[k] for k in ("pos", "size", "opacity", "margin", "box", "start", "end") if k in d}
    if d.get("image"):
        return clean_marks([dict(common, kind="image", image=d["image"])])
    if d.get("text"):
        return clean_marks([dict(common, kind="text", text=d["text"])])
    return []


def clean(d) -> dict:
    """Settings › Export format › Watermark: {"on", "marks"}."""
    d = d if isinstance(d, dict) else {}
    marks = clean_marks(d["marks"]) if isinstance(d.get("marks"), list) else from_legacy(d)
    return {"on": bool(d.get("on", False)), "marks": marks}


def clean_presets(items) -> list:
    out = []
    for p in (items if isinstance(items, list) else [])[:30]:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            out.append({"name": str(p["name"]).strip()[:40], "marks": clean_marks(p.get("marks"))})
    return out


# ---------------------------------------------------------------- images

def image_kind(data: bytes) -> str:
    """The extension for PNG, JPEG or WebP data; '' for anything else."""
    for magic, ext in IMAGE_EXT.items():
        if data.startswith(magic) and (ext != ".webp" or data[8:12] == b"WEBP"):
            return ext
    return ""


def store_image(data: bytes) -> str:
    """Keep an image in Clip Radar's own folder, named by its content (the same logo twice is one file)."""
    if len(data) > MAX_IMAGE:
        raise ValueError("That image is over 20 MB. Use a smaller one.")
    ext = image_kind(data)
    if not ext:
        raise ValueError("That isn't a PNG, JPG or WebP image.")
    dst = os.path.join(image_dir(), hashlib.sha1(data).hexdigest()[:16] + ext)
    if not os.path.isfile(dst):
        tmp = dst + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
    return dst


def store_file(src: str) -> str:
    if not src or not os.path.isfile(src):
        raise ValueError("That file isn't there.")
    if os.path.getsize(src) > MAX_IMAGE:
        raise ValueError("That image is over 20 MB. Use a smaller one.")
    with open(src, "rb") as f:
        return store_image(f.read())


# ---------------------------------------------------------------- packs

def pack_images(marks: list) -> tuple:
    """(marks with image keys, {key: base64}) for a file you can share."""
    images, out = {}, []
    for m in marks:
        m = dict(m)
        if m.get("kind") == "image" and m.get("image") and os.path.isfile(m["image"]):
            with open(m["image"], "rb") as f:
                data = f.read()
            key = hashlib.sha1(data).hexdigest()[:16] + os.path.splitext(m["image"])[1].lower()
            images[key] = base64.b64encode(data).decode("ascii")
            m["image"] = key
        out.append(m)
    return out, images


def unpack_images(marks, images) -> list:
    """Layers from a shared file: each image is checked and stored as Clip Radar's own copy."""
    images = images if isinstance(images, dict) else {}
    out = []
    for m in (marks if isinstance(marks, list) else [])[:MAX_MARKS]:
        if not isinstance(m, dict):
            continue
        m = dict(m)
        if m.get("kind") == "image" or (m.get("image") and m.get("kind") != "text"):
            raw = images.get(str(m.get("image") or ""))
            if not isinstance(raw, str):
                continue                       # an image missing from the file: leave that layer out
            try:
                m["image"] = store_image(base64.b64decode(raw, validate=True))
            except (ValueError, OSError):
                continue
        out.append(m)
    return clean_marks(out)


def make_pack(name: str, marks: list) -> dict:
    marks, images = pack_images(clean_marks(marks))
    return {"clipradar": PACK_KIND, "version": 1, "name": str(name or "Watermark").strip()[:40] or "Watermark",
            "marks": marks, "images": images}


def read_pack(d: dict) -> dict:
    """{"name", "marks"} from a pack, or ValueError saying what's wrong with it."""
    if not isinstance(d, dict) or d.get("clipradar") != PACK_KIND:
        raise ValueError("That file isn't a Clip Radar watermark.")
    marks = unpack_images(d.get("marks"), d.get("images"))
    if not marks:
        raise ValueError("That watermark file has no layers Clip Radar can use.")
    return {"name": str(d.get("name") or "Imported watermark").strip()[:40] or "Imported watermark", "marks": marks}


def parse(data: bytes) -> dict:
    """A shared file's JSON, with a plain message when it can't be read."""
    if len(data) > MAX_PACK:
        raise ValueError("That file is too big to be a Clip Radar file.")
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("That file isn't a Clip Radar file (it couldn't be read as one).") from None


def safe_name(name: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip().rstrip(". ")
    return n[:60] or "Clip Radar"
