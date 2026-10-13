"""
Content Check review workflow: what you decided about each flag on a clip.

safety.py finds flags. This module remembers what you did about them and works out each
clip's status:

  needs_review   a flag you haven't looked at yet
  reviewed       you looked and chose to leave it as it is
  corrected      a fix was applied (muted, bleeped, cut out, hidden in the title)
  dismissed      you marked the flags as wrong or not a problem
  excluded       you kept the clip out of rendering, without deleting it

Every flag has a fingerprint (what was said and where in the stream), so a decision sticks to
that flag: it doesn't come back on its own, but it does when the words change, when you ask for
a new check, or when the check's rules change. Nothing here touches the source video; fixes are
applied when the clip renders and can be undone.

Dismissing a flag only records your call. It never means a clip is safe for any platform.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Optional

import safety

SESSION = uuid.uuid4().hex[:8]      # this run of the app (for "don't remember dismissed flags")

STATUS_LABEL = {
    "off": "Content check is off", "unchecked": "Not checked", "clean": "Nothing flagged",
    "needs_review": "Needs review", "reviewed": "Reviewed", "corrected": "Corrected",
    "dismissed": "Dismissed", "excluded": "Excluded from export",
}
FIX_LABEL = {
    "mute": "Mute it", "bleep": "Bleep it", "cut_end": "End the clip before it",
    "cut_start": "Start the clip after it", "mask_title": "Hide it in the title",
    "mask_post": "Hide it in the post caption",
}
FIX_EXPLAIN = {
    "mute": "The audio goes silent for these words when the clip renders. Captions show them masked.",
    "bleep": "A bleep plays over these words when the clip renders. Captions show them masked.",
    "cut_end": "The clip ends just before these words. The video itself isn't changed.",
    "cut_start": "The clip starts just after these words. The video itself isn't changed.",
    "mask_title": "The flagged words in the title are replaced with stars.",
    "mask_post": "The flagged words in the post caption are replaced with stars.",
}
MIN_KEEP_S = 5.0          # a cut has to leave at least this much clip
LOG_MAX = 100


def _now() -> float:
    return round(time.time(), 1)


def state(d: dict) -> dict:
    ck = d.get("check")
    if not isinstance(ck, dict):
        ck = d["check"] = {}
    ck.setdefault("decisions", {})
    ck.setdefault("fixes", [])
    ck.setdefault("log", [])
    ck.setdefault("excluded", False)
    return ck


def log(d: dict, action: str, text: str):
    ck = state(d)
    ck["log"].append({"at": _now(), "action": action, "text": text})
    del ck["log"][:-LOG_MAX]


# ---------------------------------------------------------------- what the clip says

def clip_words(d: dict) -> list:
    """The clip's words, times relative to its start (your caption edits when you made any)."""
    src = d.get("words_edit") if d.get("wordsEdited") and d.get("words_edit") else d.get("words") or []
    s, e = d["start"], d["end"]
    return [{"start": w["start"] - s, "end": w["end"] - s, "word": w["word"]}
            for w in src if s - 0.2 <= w["start"] < e]


def post_text(d: dict) -> str:
    return d.get("postText", "") if d.get("userPost") else d.get("post", "")


def content_sig(d: dict) -> str:
    key = json.dumps([round(d["start"], 2), round(d["end"], 2), d.get("title", ""), post_text(d),
                      [w["word"] for w in clip_words(d)]])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def fingerprint(f: dict, d: dict) -> str:
    """Same flag = same words at the same moment of the stream (trimming the clip doesn't change it)."""
    text = safety.norm(f.get("term", "")) + "|" + safety.norm(f.get("text", "").replace("*", ""))
    if f.get("source") == "speech":
        at = round((d["start"] + f.get("start", 0)) * 2) / 2
        return f"speech|{f['cat']}|{text}|{at}"
    return f"{f.get('source')}|{f['cat']}|{text}"


# ---------------------------------------------------------------- statuses

def _valid(dec: dict, ck: dict, cfg: dict, rules: str) -> bool:
    if dec.get("action") == "corrected":
        return any(fx["id"] == dec.get("fix") for fx in ck["fixes"])
    if dec.get("rules") != rules:
        return False                    # the rules changed: look at it again
    if dec.get("action") == "dismissed" and not cfg.get("remember_dismissed", True):
        return dec.get("session") == SESSION
    return True


def fixes_for(f: dict, d: dict, has_audio: bool = True) -> list:
    """The automatic fixes that make sense for this flag."""
    out = []
    if f.get("source") == "speech":
        if has_audio:
            out += ["mute", "bleep"]
        length = d["end"] - d["start"]
        if f.get("start", 0) - 0.15 >= MIN_KEEP_S:
            out.append("cut_end")
        if length - f.get("end", 0) - 0.15 >= MIN_KEEP_S:
            out.append("cut_start")
    elif f.get("source") == "title":
        out.append("mask_title")
    elif f.get("source") == "post":
        out.append("mask_post")
    return out


def annotate(result: dict, d: dict, cfg: Optional[dict] = None, has_audio: bool = True) -> dict:
    """Mark each flag with its fingerprint and status, and the clip with its overall status."""
    cfg = cfg or safety.load_config()
    ck = state(d)
    rules = safety.rules_sig(cfg)
    out = dict(result)
    flags = []
    for f in result.get("flags", []):
        f = dict(f)
        f["fp"] = fingerprint(f, d)
        dec = ck["decisions"].get(f["fp"])
        f["status"] = dec["action"] if dec and _valid(dec, ck, cfg, rules) else "open"
        if f["status"] == "corrected":
            fx = next((x for x in ck["fixes"] if x["id"] == dec.get("fix")), None)
            f["fixed"] = fx and fx["kind"]
            f["fix_id"] = fx and fx["id"]
        f["fixes"] = fixes_for(f, d, has_audio) if f["status"] != "corrected" else []
        flags.append(f)
    out["flags"] = flags
    out["status"] = clip_status(out, d)
    out["label"] = STATUS_LABEL[out["status"]]
    out["open"] = sum(1 for f in flags if f["status"] == "open")
    out["rules"] = rules
    return out


def clip_status(result: Optional[dict], d: dict) -> str:
    ck = state(d)
    if result is not None and result.get("enabled") is False:
        return "off"
    if ck.get("excluded"):
        return "excluded"
    if result is None:
        return "unchecked"
    flags = result.get("flags", [])
    if any(f.get("status", "open") == "open" for f in flags):
        return "needs_review"
    if ck["fixes"] or any(f.get("status") == "corrected" for f in flags):
        return "corrected"
    if not flags:
        return "clean"
    if all(f.get("status") == "dismissed" for f in flags):
        return "dismissed"
    return "reviewed"


def check(d: dict, cfg: Optional[dict] = None, has_audio: bool = True) -> dict:
    """Scan the clip as it is now and store the result on it (d['safety'])."""
    cfg = cfg or safety.load_config()
    raw = safety.scan(clip_words(d), d.get("title", ""), post_text(d), cfg)
    res = annotate(raw, d, cfg, has_audio)
    ck = state(d)
    ck["sig"] = content_sig(d)
    ck["stale"] = False
    ck["checked_at"] = _now()
    d["safety"] = res
    return res


def refresh(d: dict, cfg: Optional[dict] = None, has_audio: bool = True):
    """After a decision: recompute statuses from the last scan (no new scan)."""
    if isinstance(d.get("safety"), dict):
        d["safety"] = annotate(d["safety"], d, cfg, has_audio)


def mark_changed(d: dict):
    """The clip was edited: its last check may be out of date."""
    ck = state(d)
    if ck.get("sig") and content_sig(d) != ck["sig"]:
        ck["stale"] = True


# ---------------------------------------------------------------- actions

def _find(d: dict, fp: str) -> Optional[dict]:
    return next((f for f in (d.get("safety") or {}).get("flags", []) if f.get("fp") == fp), None)


def _decide(d: dict, fp: str, action: str, cfg: dict, **extra):
    state(d)["decisions"][fp] = {"action": action, "at": _now(), "rules": safety.rules_sig(cfg),
                                 "session": SESSION, **extra}


def _where(f: dict) -> str:
    if f.get("source") == "speech":
        s = max(0, f.get("start", 0))
        return f" at {int(s // 60)}:{int(s % 60):02d}"
    return " in the title" if f.get("source") == "title" else " in the post caption"


def describe(f: dict) -> str:
    return f"{f.get('label', 'Flag')} “{f.get('text', '')}”{_where(f)}"


def _mask_term(text: str, f: dict, cfg: dict) -> str:
    """Mask the flagged words in a title or caption (every place they appear)."""
    import re
    parts = re.split(r"(\s+)", text)
    toks = [(i, p) for i, p in enumerate(parts) if p and not p.isspace()]
    for cat, _sev, a, b, term in safety._scan_tokens([p for _, p in toks], dict(cfg, enabled=True)):
        if cat == f["cat"] and term == f.get("term"):
            for k in range(a, b + 1):
                parts[toks[k][0]] = safety.mask(parts[toks[k][0]])
    return "".join(parts)


def apply_fix(d: dict, fp: str, kind: str, cfg: dict, has_audio: bool = True) -> dict:
    f = _find(d, fp)
    if not f:
        raise ValueError("That flag isn't on this clip any more. Check the clip again.")
    if kind not in fixes_for(f, d, has_audio):
        raise ValueError("That fix doesn't fit this flag.")
    fx = {"id": uuid.uuid4().hex[:8], "fp": fp, "kind": kind, "at": _now(), "flag": describe(f), "prev": {}}
    if kind in ("mute", "bleep"):
        fx["span"] = [round(d["start"] + f["start"] - 0.05, 3), round(d["start"] + f["end"] + 0.05, 3)]
        fx["words"] = [f.get("first"), f.get("last")]
    elif kind == "cut_end":
        fx["prev"] = {"start": d["start"], "end": d["end"], "userTrim": bool(d.get("userTrim"))}
        d["end"] = round(d["start"] + f["start"] - 0.15, 3)
        d["userTrim"] = True
    elif kind == "cut_start":
        fx["prev"] = {"start": d["start"], "end": d["end"], "userTrim": bool(d.get("userTrim"))}
        d["start"] = round(d["start"] + f["end"] + 0.15, 3)
        d["userTrim"] = True
    elif kind == "mask_title":
        fx["prev"] = {"title": d.get("title", ""), "userTitle": bool(d.get("userTitle"))}
        d["title"] = _mask_term(d.get("title", ""), f, cfg)
        d["userTitle"] = True
    elif kind == "mask_post":
        fx["prev"] = {"postText": d.get("postText", ""), "userPost": bool(d.get("userPost"))}
        d["postText"] = _mask_term(post_text(d), f, cfg)
        d["userPost"] = True
    state(d)["fixes"].append(fx)
    _decide(d, fp, "corrected", cfg, fix=fx["id"])
    log(d, "fix", f"{describe(f)} → {FIX_LABEL[kind].lower()}")
    return fx


def undo_fix(d: dict, fix_id: str) -> dict:
    ck = state(d)
    fx = next((x for x in ck["fixes"] if x["id"] == fix_id), None)
    if not fx:
        raise ValueError("That fix was already undone.")
    for k, v in fx.get("prev", {}).items():
        d[k] = v
    ck["fixes"].remove(fx)
    dec = ck["decisions"].get(fx["fp"])
    if dec and dec.get("fix") == fix_id:
        del ck["decisions"][fx["fp"]]
    log(d, "undo", f"Undid “{FIX_LABEL[fx['kind']].lower()}” for {fx.get('flag', 'a flag')}")
    return fx


def act(d: dict, action: str, args: dict, cfg: Optional[dict] = None, has_audio: bool = True) -> Optional[dict]:
    """Apply one review action to a clip. Returns a fix when one was made. The caller re-checks."""
    cfg = cfg or safety.load_config()
    ck = state(d)
    flags = (d.get("safety") or {}).get("flags", [])
    open_ = [f for f in flags if f.get("status", "open") == "open"]
    if action == "leave":                                   # A: leave it alone
        for f in open_:
            _decide(d, f["fp"], "accepted", cfg)
        log(d, "leave", f"Left as it is ({len(open_)} flag{'s' if len(open_) != 1 else ''})" if open_ else "Left as it is")
    elif action == "ack":                                   # rendering anyway, after the warning
        for f in open_:
            _decide(d, f["fp"], "accepted", cfg)
        log(d, "ack", "Rendered after the content check warning")
    elif action == "dismiss":                               # D: dismiss one flag (or all open ones)
        fps = [args["fp"]] if args.get("fp") else [f["fp"] for f in open_]
        for fp in fps:
            f = _find(d, fp)
            if f:
                _decide(d, fp, "dismissed", cfg)
                log(d, "dismiss", f"Dismissed {describe(f)}")
    elif action == "reopen":                                # take back a leave/dismiss
        f = _find(d, args.get("fp", ""))
        dec = ck["decisions"].get(args.get("fp", ""))
        if dec and dec.get("action") != "corrected":
            del ck["decisions"][args["fp"]]
            log(d, "reopen", f"Back to review: {describe(f) if f else 'a flag'}")
    elif action == "fix":                                   # C: a supported automatic fix
        return apply_fix(d, args.get("fp", ""), args.get("kind", ""), cfg, has_audio)
    elif action == "undo":
        undo_fix(d, args.get("fix", ""))
    elif action == "exclude":                               # E: keep out of rendering (or let back in)
        on = bool(args.get("on", True))
        if on != bool(ck["excluded"]):
            ck["excluded"] = on
            log(d, "exclude" if on else "include", "Excluded from export" if on else "Included in export again")
    elif action == "recheck":                               # a fresh look: earlier leave/dismiss calls are dropped
        for fp in [k for k, v in ck["decisions"].items() if v.get("action") != "corrected"]:
            del ck["decisions"][fp]
        log(d, "recheck", "Checked again")
    else:
        raise ValueError(f"Unknown action: {action}")
    return None


# ---------------------------------------------------------------- rendering

def render_spans(d: dict) -> list:
    """[(abs start, abs end, 'mute'|'bleep')] for the fixes on this clip."""
    return [(fx["span"][0], fx["span"][1], fx["kind"]) for fx in state(d)["fixes"]
            if fx["kind"] in ("mute", "bleep") and fx.get("span")]


def blocks_render(d: dict, cfg: Optional[dict] = None) -> Optional[str]:
    """Why this clip can't render right now, or None."""
    cfg = cfg or safety.load_config()
    if state(d).get("excluded"):
        return "This clip is excluded from export. Include it again first."
    sf = d.get("safety") or {}
    if cfg.get("enabled", True) and cfg.get("export_flagged") == "block" and sf.get("status") == "needs_review":
        return "This clip has flags you haven't reviewed. Leave it as it is, fix or dismiss them first."
    return None
