"""
Content check for clips (the "TOS scanner").

Reads what a clip says (Whisper words), its title and its post caption, and
flags things that get TikTok videos removed, age-restricted or held back:
hate slurs, telling someone to hurt themselves, threats, sexual terms, drugs,
gambling, personal info (phone numbers, email and street addresses) and any
words you add yourself.

It can also censor: flagged words are masked in captions and titles ("f***")
and muted or bleeped in the audio. In hands-free live mode, clips with
serious flags are held back for you to look at instead of rendering.

It only works on words. It can't see what's on screen or recognise music, so
treat it as a safety net, not a guarantee.
"""

from __future__ import annotations

import codecs
import json
import re
import threading
from typing import Optional

import paths
from safety_words import TERMS

CATEGORIES = {
    "slurs": {"label": "Hate slur", "severity": 3},
    "harassment": {"label": "Self-harm or harassment", "severity": 3},
    "personal": {"label": "Personal info", "severity": 3},
    "custom": {"label": "Your blocked word", "severity": 3},
    "violence": {"label": "Violence or threat", "severity": 2},
    "sexual": {"label": "Sexual", "severity": 2},
    "drugs": {"label": "Drugs", "severity": 2},
    "profanity": {"label": "Swearing", "severity": 1},
    "gambling": {"label": "Gambling", "severity": 1},
}
LEVELS = ["clean", "low", "medium", "high"]
CENSOR_MODES = ("off", "mask", "mute", "bleep")

DEFAULTS = {
    "enabled": True,                                   # scan clips at all
    "categories": {k: True for k in CATEGORIES},       # what to look for
    "censor": "off",                                   # off | mask (captions only) | mute | bleep
    "censor_categories": ["slurs", "harassment", "personal", "custom", "sexual", "profanity"],
    "hold": True,                                      # hands-free: hold back clips with high-risk flags
    "custom": [],                                      # extra words or phrases
    # review workflow (3.3)
    "auto": True,                                      # check clips by themselves (else: Check now on each clip)
    "notify": "now",                                   # now: flags show in the list | later: collected in Flagged
    "sensitivity": "all",                              # all | skip_mild (no swearing/gambling) | serious
    "export_flagged": "ask",                           # rendering a clip with unreviewed flags: ask | block | allow
    "remember_dismissed": True,                        # a dismissed flag stays dismissed next time
    "auto_recheck": True,                              # re-check a clip after you edit or fix it
}
SENSITIVITY = {"all": 1, "skip_mild": 2, "serious": 3}    # lowest severity that gets flagged
NOTIFY = ("now", "later")
EXPORT_FLAGGED = ("ask", "block", "allow")
CONFIG_FILE = paths.DATA_DIR / "safety.json"
_lock = threading.Lock()
_cfg_cache: list = [None]

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
SUFFIX = r"(?:s|es|ed|er|ers|ing|in|y|z|ez)?"


# ---------------------------------------------------------------- config

def load_config() -> dict:
    with _lock:
        if _cfg_cache[0] is not None:
            return json.loads(json.dumps(_cfg_cache[0]))
        cfg = json.loads(json.dumps(DEFAULTS))
        try:
            saved = json.loads(CONFIG_FILE.read_text("utf-8"))
            cfg.update(_clean(saved))
        except Exception:
            pass
        _cfg_cache[0] = cfg
        return json.loads(json.dumps(cfg))


def save_config(new: dict) -> dict:
    cfg = load_config()
    cfg.update(_clean(new))
    with _lock:
        _cfg_cache[0] = cfg
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            import persist
            persist.write_json(str(CONFIG_FILE), cfg, indent=1)
        except OSError:
            pass
    _compiled.clear()
    return json.loads(json.dumps(cfg))


def _clean(d: dict) -> dict:
    out = {}
    if not isinstance(d, dict):
        return out
    if "enabled" in d:
        out["enabled"] = bool(d["enabled"])
    for k in ("hold", "auto", "remember_dismissed", "auto_recheck"):
        if k in d:
            out[k] = bool(d[k])
    if d.get("sensitivity") in SENSITIVITY:
        out["sensitivity"] = d["sensitivity"]
    if d.get("notify") in NOTIFY:
        out["notify"] = d["notify"]
    if d.get("export_flagged") in EXPORT_FLAGGED:
        out["export_flagged"] = d["export_flagged"]
    if d.get("censor") in CENSOR_MODES:
        out["censor"] = d["censor"]
    if isinstance(d.get("categories"), dict):
        out["categories"] = {k: bool(d["categories"].get(k, True)) for k in CATEGORIES}
    if isinstance(d.get("censor_categories"), list):
        out["censor_categories"] = [k for k in d["censor_categories"] if k in CATEGORIES]
    if "custom" in d:
        raw = d["custom"]
        items = raw if isinstance(raw, list) else re.split(r"[,\n]", str(raw))
        seen, words = set(), []
        for w in items:
            w = re.sub(r"\s+", " ", str(w)).strip()[:60]
            if w and w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        out["custom"] = words[:300]
    return out


# ---------------------------------------------------------------- matching

def norm(token: str) -> str:
    """Lowercase, undo leetspeak, drop everything but letters and digits."""
    t = token.lower().translate(LEET)
    return re.sub(r"[^0-9a-zÀ-ɏ]", "", t)


def _token_regex(spec: str) -> str:
    stem, mode = spec, ""
    if spec.endswith("~") or spec.endswith("*"):
        stem, mode = spec[:-1], spec[-1]
    body = "".join(re.escape(ch) + "+" for ch in norm(stem))
    if mode == "~":
        body += SUFFIX
    elif mode == "*":
        body += "[0-9a-zÀ-ɏ]*"
    return "^" + body + "$"


_compiled: dict = {}


def _terms(cfg: dict) -> list:
    """[(category, severity, [token regexes], term)] for the enabled categories."""
    key = json.dumps([cfg.get("categories"), cfg.get("custom")], sort_keys=True)
    if key in _compiled:
        return _compiled[key]
    out = []
    lists = dict(TERMS)
    lists["slurs"] = [codecs.decode(t, "rot13") for t in lists.pop("slurs_rot13")]
    lists["custom"] = [w for w in cfg.get("custom", []) if norm(w.split()[0] if w.split() else "")]
    cats = cfg.get("categories") or {}
    for cat, terms in lists.items():
        if cat not in CATEGORIES or not cats.get(cat, True):
            continue
        for term in terms:
            sev = CATEGORIES[cat]["severity"]
            m = re.match(r"^(.*):([123])$", term)
            if m and cat != "custom":
                term, sev = m.group(1), int(m.group(2))
            toks = [t for t in term.split() if norm(t.rstrip("~*"))]
            if not toks:
                continue
            out.append((cat, sev, [re.compile(_token_regex(t)) for t in toks], term))
    _compiled.clear()
    _compiled[key] = out
    return out


def mask(word: str) -> str:
    """'fucking!' -> 'f******!'"""
    m = re.match(r"^(\W*)(\w)(\w*)(.*)$", word, re.S)
    if not m:
        return word
    return m.group(1) + m.group(2) + "*" * len(m.group(3)) + m.group(4)


PERSONAL = [
    ("phone number", re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")),
    ("email address", re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)),
    ("email address", re.compile(r"\b[\w.]+ at (?:gmail|yahoo|hotmail|outlook|icloud|proton(?:mail)?) dot com\b", re.I)),
    ("street address", re.compile(r"\b\d{1,5} (?:[a-z]+ ){1,3}(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|"
                                  r"boulevard|blvd|court|ct|way|place|pl|terrace|circle)\b", re.I)),
    ("card number", re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)")),
]


def _scan_tokens(tokens: list, cfg: dict) -> list:
    """tokens: [str]. Returns [(category, severity, first index, last index, term)]."""
    normed = [norm(t) for t in tokens]
    hits = []
    for cat, sev, regs, term in _terms(cfg):
        n = len(regs)
        for i in range(len(normed) - n + 1):
            if all(regs[k].match(normed[i + k]) for k in range(n)):
                hits.append((cat, sev, i, i + n - 1, term))
    # personal info: regexes over the joined text, mapped back to tokens
    if (cfg.get("categories") or {}).get("personal", True):
        text, spans = "", []
        for t in tokens:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(t)))
            text += t
        for label, rx in PERSONAL:
            for m in rx.finditer(text):
                idx = [k for k, (a, b) in enumerate(spans) if a < m.end() and b > m.start()]
                if idx:
                    hits.append(("personal", 3, idx[0], idx[-1], label))
    # longest, most severe match wins when they overlap
    hits.sort(key=lambda h: (-h[1], -(h[3] - h[2]), h[2]))
    taken, out = set(), []
    for h in hits:
        rng = set(range(h[2], h[3] + 1))
        if rng & taken:
            continue
        taken |= rng
        out.append(h)
    return sorted(out, key=lambda h: h[2])


def scan(words: list, title: str = "", post: str = "", cfg: Optional[dict] = None) -> dict:
    """words: [{start, end, word}] (any time base). Returns {level, flags, enabled}."""
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        return {"level": "clean", "flags": [], "enabled": False}
    flags = []
    toks = [w["word"] for w in words]
    for cat, sev, a, b, term in _scan_tokens(toks, cfg):
        said = " ".join(toks[a:b + 1])
        flags.append({"cat": cat, "label": CATEGORIES[cat]["label"], "severity": sev, "source": "speech",
                      "text": said if cat in ("personal", "custom") else " ".join(mask(t) for t in toks[a:b + 1]),
                      "start": words[a]["start"], "end": words[b]["end"], "first": a, "last": b, "term": term})
    for source, text in (("title", title or ""), ("post", post or "")):
        parts = re.findall(r"\S+", text)
        for cat, sev, a, b, term in _scan_tokens(parts, cfg):
            flags.append({"cat": cat, "label": CATEGORIES[cat]["label"], "severity": sev, "source": source,
                          "text": " ".join(parts[a:b + 1]) if cat in ("personal", "custom")
                          else " ".join(mask(t) for t in parts[a:b + 1]), "term": term})
    floor = SENSITIVITY.get(cfg.get("sensitivity", "all"), 1)
    flags = [f for f in flags if f["severity"] >= floor]
    level = LEVELS[max([f["severity"] for f in flags], default=0)]
    return {"level": level, "flags": flags, "enabled": True}


def rules_sig(cfg: Optional[dict] = None) -> str:
    """Changes whenever what counts as a flag changes (categories, your words, sensitivity, the word
    lists themselves), so earlier decisions about flags are looked at again."""
    import hashlib
    cfg = cfg or load_config()
    key = json.dumps([cfg.get("categories"), sorted(w.lower() for w in cfg.get("custom", [])),
                      cfg.get("sensitivity", "all"), TERMS], sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def censored(words: list, result: dict, cfg: Optional[dict] = None) -> tuple:
    """(words with flagged ones masked, [(start, end)] spans to mute or bleep, mode)."""
    cfg = cfg or load_config()
    mode = cfg.get("censor", "off")
    if mode == "off" or not result.get("flags"):
        return list(words), [], "off"
    cats = set(cfg.get("censor_categories") or [])
    out = [dict(w) for w in words]
    spans = []
    for f in result["flags"]:
        if f["source"] != "speech" or f["cat"] not in cats:
            continue
        for k in range(f["first"], f["last"] + 1):
            if k < len(out):
                out[k]["word"] = mask(out[k]["word"])
        spans.append((max(0.0, f["start"] - 0.05), f["end"] + 0.05))
    return out, (spans if mode in ("mute", "bleep") else []), mode


def mask_text(text: str, cfg: Optional[dict] = None) -> str:
    """Mask flagged words in a title or post caption (when censoring is on)."""
    cfg = cfg or load_config()
    if not text or cfg.get("censor", "off") == "off" or not cfg.get("enabled", True):
        return text
    cats = set(cfg.get("censor_categories") or [])
    parts = re.split(r"(\s+)", text)
    toks = [(i, p) for i, p in enumerate(parts) if p and not p.isspace()]
    for cat, sev, a, b, term in _scan_tokens([p for _, p in toks], cfg):
        if cat in cats and cat != "personal":
            for k in range(a, b + 1):
                i = toks[k][0]
                parts[i] = mask(parts[i])
    return "".join(parts)


def public_config() -> dict:
    cfg = load_config()
    cfg["category_info"] = [{"key": k, **v} for k, v in CATEGORIES.items()]
    cfg["rules"] = rules_sig(cfg)
    return cfg
