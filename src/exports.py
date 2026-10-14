"""
Export settings (3.7): platform presets, file names, caption and performance preferences, and
how fast this PC renders (for honest time estimates).

Presets follow each platform's published upload guidance; the platforms change these, so
they're starting points you can adjust under Advanced, not guarantees.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

import paths
import persist

PRESETS = {
    "tiktok": {"label": "TikTok", "aspect": "9:16", "scale": 1.0, "fps": 30, "vcodec": "h264", "crf": 20, "vbitrate": 0,
               "acodec": "aac", "abitrate": 192, "container": "mp4", "loudness": -14.0, "max_len": 600,
               "note": "1080 × 1920, up to 10 minutes."},
    "shorts": {"label": "YouTube Shorts", "aspect": "9:16", "scale": 1.0, "fps": 30, "vcodec": "h264", "crf": 18, "vbitrate": 12000,
               "acodec": "aac", "abitrate": 192, "container": "mp4", "loudness": -14.0, "max_len": 180,
               "note": "1080 × 1920 at about 12 Mbps, up to 3 minutes."},
    "reels": {"label": "Instagram Reels", "aspect": "9:16", "scale": 1.0, "fps": 30, "vcodec": "h264", "crf": 20, "vbitrate": 0,
              "acodec": "aac", "abitrate": 128, "container": "mp4", "loudness": -14.0, "max_len": 180,
              "note": "1080 × 1920, up to 3 minutes."},
    "x": {"label": "X", "aspect": "9:16", "scale": 0.6667, "fps": 30, "vcodec": "h264", "crf": 21, "vbitrate": 0,
          "acodec": "aac", "abitrate": 128, "container": "mp4", "loudness": -14.0, "max_len": 140,
          "note": "720 × 1280 (X's portrait size), up to 2:20 for most accounts."},
}
ADVANCED = {   # what Advanced can change, and the values allowed
    "scale": (1.0, 0.6667, 0.5), "fps": (24, 25, 30, 50, 60), "vcodec": ("h264", "hevc", "vp9"),
    "acodec": ("aac", "opus"), "abitrate": (96, 128, 160, 192, 256, 320), "container": ("mp4", "mov", "webm"),
    "loudness": (0.0, -14.0, -16.0, -18.0, -23.0),
}
SUBTITLES = ("burn", "burn_srt", "srt")
TOKENS = ("title", "streamer", "platform", "source", "number", "date", "hashtags", "project", "preset")
DEFAULT_TEMPLATE = "{title}"
MAX_NAME = 120

_lock = threading.Lock()
_FILE = lambda: os.path.join(str(paths.DATA_DIR), "export.json")  # noqa: E731
_PERF = lambda: os.path.join(str(paths.DATA_DIR), "perf.json")  # noqa: E731

DEFAULTS = {
    "preset": "tiktok", "custom": {}, "template": DEFAULT_TEMPLATE, "subtitles": "burn",
    "denoise": False, "voice": False,
    # captions: words to swap in captions, and words Whisper should expect
    "replace": [], "vocab": [],
    # performance
    "renders": 1, "downloads": 1, "priority": "responsive",
    # saved caption styles (Text tab) and framings (Layout tab)
    "cap_presets": [], "frame_presets": [],
}
CAP_KEYS = ("captions", "caption_pos", "caption_color", "caption_size", "words_per_line", "uppercase", "caption_style",
            "caption_font", "caption_anim", "cap_x", "cap_y", "cap_outline", "cap_shadow", "title_style", "title_font",
            "title_size", "title_x", "title_y")
FRAME_KEYS = ("layout", "cam", "cam_height", "game_zoom", "game_x", "track", "track_target", "focus_y", "aspect")


def _frame_val(k, v):
    if k == "cam":
        if isinstance(v, (list, tuple)) and len(v) == 4:
            try:
                return [round(min(max(float(x), 0.0), 1.0), 4) for x in v]
            except (TypeError, ValueError):
                return None
        return None
    return v if isinstance(v, (int, float, str, bool)) else None


def load() -> dict:
    with _lock:
        d = persist.read_json(_FILE(), {}) or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in d.items() if k in DEFAULTS})
    return clean(out)


def clean(d: dict) -> dict:
    out = dict(DEFAULTS)
    out["preset"] = d.get("preset") if d.get("preset") in PRESETS else "tiktok"
    cust = {}
    for k, allowed in ADVANCED.items():
        v = (d.get("custom") or {}).get(k)
        if v is None:
            continue
        try:
            v = type(allowed[0])(v)
        except (TypeError, ValueError):
            continue
        if v in allowed:
            cust[k] = v
    for k in ("crf", "vbitrate"):
        v = (d.get("custom") or {}).get(k)
        if v is not None:
            try:
                cust[k] = int(min(max(int(v), 14 if k == "crf" else 0), 34 if k == "crf" else 60000))
            except (TypeError, ValueError):
                pass
    out["custom"] = cust
    t = str(d.get("template") or DEFAULT_TEMPLATE).strip()[:200]
    out["template"] = t if "{" in t else DEFAULT_TEMPLATE
    out["subtitles"] = d.get("subtitles") if d.get("subtitles") in SUBTITLES else "burn"
    out["denoise"], out["voice"] = bool(d.get("denoise")), bool(d.get("voice"))
    rep = []
    for pair in (d.get("replace") or [])[:200]:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[0]).strip():
            rep.append([str(pair[0]).strip()[:40], str(pair[1]).strip()[:40]])
    out["replace"] = rep
    out["vocab"] = [str(w).strip()[:40] for w in (d.get("vocab") or []) if str(w).strip()][:200]
    out["renders"] = int(min(max(int(d.get("renders") or 1), 1), 3))
    out["downloads"] = int(min(max(int(d.get("downloads") or 1), 1), 2))
    out["priority"] = d.get("priority") if d.get("priority") in ("responsive", "fast") else "responsive"
    presets = []
    for cp in (d.get("cap_presets") or [])[:30]:
        if isinstance(cp, dict) and str(cp.get("name") or "").strip() and isinstance(cp.get("opts"), dict):
            presets.append({"name": str(cp["name"]).strip()[:40],
                            "opts": {k: v for k, v in cp["opts"].items() if k in CAP_KEYS and isinstance(v, (int, float, str, bool))}})
    out["cap_presets"] = presets
    frames = []
    for fp in (d.get("frame_presets") or [])[:30]:
        if isinstance(fp, dict) and str(fp.get("name") or "").strip() and isinstance(fp.get("opts"), dict):
            o = {k: _frame_val(k, v) for k, v in fp["opts"].items() if k in FRAME_KEYS}
            frames.append({"name": str(fp["name"]).strip()[:40], "opts": {k: v for k, v in o.items() if v is not None}})
    out["frame_presets"] = frames
    return out


def save(patch: dict) -> dict:
    cur = load()
    cur.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
    if "preset" in (patch or {}) and "custom" not in patch:
        cur["custom"] = {}                     # a new preset starts from its own settings
    cur = clean(cur)
    with _lock:
        try:
            persist.write_json(_FILE(), cur)
        except OSError:
            pass
    return cur


def settings(prefs: Optional[dict] = None) -> dict:
    """The export settings in effect: the preset with any Advanced changes on top."""
    p = prefs or load()
    s = dict(PRESETS[p["preset"]])
    s.update(p.get("custom") or {})
    if s.get("container") == "webm":
        s["vcodec"], s["acodec"] = "vp9", "opus"
    elif s.get("acodec") == "opus":
        s["acodec"] = "aac"                    # mp4/mov: AAC
    if s.get("vcodec") == "vp9" and s.get("container") != "webm":
        s["container"] = "webm"
        s["acodec"] = "opus"
    s.update(preset=p["preset"], subtitles=p["subtitles"], denoise=p["denoise"], voice=p["voice"], template=p["template"])
    return s


def describe(s: dict) -> str:
    import engine
    w, h = engine.SHAPES.get(s.get("aspect", "9:16"), (1080, 1920))
    k = float(s.get("scale", 1.0))
    if k < 0.999:
        w, h = engine._even(w * k), engine._even(h * k)
    q = f"{s['vbitrate'] / 1000:g} Mbps" if s.get("vbitrate") else "quality " + str(s.get("crf", 20))
    loud = f"{s['loudness']:g} LUFS" if s.get("loudness") else "volume as is"
    return f"{PRESETS[s['preset']]['label']}{' (changed)' if s.get('custom_changed') else ''} · {w}×{h} · {s.get('fps', 30)} fps · " \
           f"{s.get('vcodec', 'h264').upper()} {q} · {s.get('container', 'mp4').upper()} · {loud}"


# ---------------------------------------------------------------- file names

def safe(text: str, limit: int = MAX_NAME) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    if text.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?i)(com|lpt)\d", text):
        text = "_" + text                     # names Windows reserves
    return text[:limit].rstrip(". ")


def file_name(template: str, fields: dict) -> str:
    """Fill a naming template like '{streamer} - {title}'. Unknown or empty fields drop out cleanly."""
    def sub(m):
        v = fields.get(m.group(1), "")
        return str(v) if v not in (None, "") else ""
    name = re.sub(r"\{(\w+)\}", sub, template or DEFAULT_TEMPLATE)
    name = re.sub(r"(\s*[-_·|]\s*){2,}", " - ", name)        # separators left next to an empty field
    name = re.sub(r"^\s*[-_·|]\s*|\s*[-_·|]\s*$", "", name)
    name = safe(name)
    return name or "clip"


def fields_for(clip: dict, project: dict, preset: str, number: Optional[int] = None) -> dict:
    tags = " ".join(t.lstrip("#") for t in (clip.get("hashtags") or [])[:3])
    src = project.get("source", "")
    platform = "Twitch" if "twitch.tv" in src else "YouTube" if "youtu" in src else "X" if re.search(r"(x|twitter)\.com", src) else "Local"
    return {
        "title": clip.get("title") or "clip",
        "streamer": project.get("streamer_display") or project.get("streamer") or "",
        "platform": platform,
        "source": project.get("title", ""),
        "number": f"{int(number):02d}" if number else "",
        "date": time.strftime("%Y-%m-%d"),
        "hashtags": tags,
        "project": project.get("title", ""),
        "preset": PRESETS.get(preset, {}).get("label", ""),
    }


# ---------------------------------------------------------------- render speed (for time estimates)

def speed_key(encoder: str, s: dict) -> str:
    return f"{encoder}|{s.get('vcodec', 'h264')}|{s.get('fps', 30)}|{float(s.get('scale', 1.0)):.2f}"


def speed(key: str) -> Optional[float]:
    """Seconds of clip rendered per second, seen on this PC before (None until there's a render)."""
    d = persist.read_json(_PERF(), {}) or {}
    v = d.get(key) or d.get(key.split("|")[0])
    return float(v) if v else None


def record_speed(key: str, factor: float):
    if not (0.01 < factor < 100):
        return
    with _lock:
        d = persist.read_json(_PERF(), {}) or {}
        for k in (key, key.split("|")[0]):
            old = d.get(k)
            d[k] = round(factor if not old else 0.6 * float(old) + 0.4 * factor, 3)
        try:
            persist.write_json(_PERF(), d)
        except OSError:
            pass


def all_speeds() -> dict:
    return persist.read_json(_PERF(), {}) or {}


# ---------------------------------------------------------------- captions

def replace_words(words: list, pairs: list) -> list:
    """Swap words in captions (Settings › Captions › Replace words), keeping their timing."""
    if not pairs or not words:
        return words
    table = {a.lower(): b for a, b in pairs if " " not in a}
    out = []
    for w in words:
        core = re.sub(r"^\W+|\W+$", "", w["word"])
        rep = table.get(core.lower())
        if rep is None:
            out.append(w)
            continue
        lead = w["word"][:len(w["word"]) - len(w["word"].lstrip("\"'([¿¡"))]
        trail = w["word"][len(w["word"].rstrip(".,!?\"')]…")):]
        if core.isupper() and len(core) > 1:
            rep = rep.upper()
        elif core[:1].isupper():
            rep = rep[:1].upper() + rep[1:]
        out.append(dict(w, word=f"{lead}{rep}{trail}" if rep else ""))
    return [w for w in out if w["word"]]


def prompt(streamer: str = "", emote_names=None, vocab=None) -> str:
    """Words Whisper should expect, so names and emotes are spelled right."""
    names = [n for n in [streamer] + list(vocab or []) if n]
    if emote_names:
        names += sorted(emote_names, key=len)[:40]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return ", ".join(out)[:400]
