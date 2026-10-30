"""
Every failure and error Clip Radar runs into, kept for fixing them later.

One JSON object per line in errors.jsonl, next to clipradar.log (Settings › Storage › Logs): when, which
version, where it happened (a download, a render, a live session, a request, the UI, a crash), the
message the person saw and the technical details (the traceback, FFmpeg's last lines). Secrets are
blanked and the home folder becomes ~, so the file can be attached to a bug report as it is.
clipradar.log is everything the app prints and starts over at 2 MB; this file only holds failures, so
they last far longer (5 MB, then one older file).

  record(where, message, detail="", exc=None, **context)   a failure
  exc(where, **context)                                      the exception being handled, printed and recorded
  recent(limit)                                              newest first, for Settings › About
  install()                                                  record crashes on any thread
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from typing import Optional

import paths

MAX_BYTES = 5_000_000
_lock = threading.Lock()
_count = {"session": 0}

_SECRET = re.compile(r"(?i)((?:oauth|token|access_token|client_secret|authorization|password|api[_-]?key)"
                     r"[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,]+")


def redact(text: str) -> str:
    """Diagnostics without secrets: tokens and keys are blanked, the home folder becomes ~."""
    text = re.sub(r"(?i)\b(bearer|oauth)\s+[^\s\"']+", r"\1 ***", str(text or ""))
    text = _SECRET.sub(r"\1***", text)
    home = os.path.expanduser("~")
    return text.replace(home, "~") if len(home) > 3 else text


def path() -> str:
    return str(paths.LOG_FILE.with_name("errors.jsonl"))


def _version() -> str:
    try:
        from version import VERSION
        return VERSION
    except Exception:
        return ""


def record(where: str, message: str, detail: str = "", exc: Optional[BaseException] = None, **context) -> None:
    """Add a failure to the error log. Never raises: recording an error must not cause another."""
    try:
        e = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "version": _version(), "where": str(where),
             "message": redact(str(message or (type(exc).__name__ if exc else "error")))[:2000]}
        if exc is not None:
            e["type"] = type(exc).__name__
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            detail = (str(detail) + "\n" if detail else "") + tb
        if detail:
            e["detail"] = redact(str(detail))[-6000:]
        ctx = {k: redact(str(v))[:500] for k, v in context.items() if v not in (None, "")}
        if ctx:
            e["context"] = ctx
        line = json.dumps(e, ensure_ascii=False) + "\n"
        p = path()
        with _lock:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    os.replace(p, p[:-len(".jsonl")] + ".old.jsonl")
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            _count["session"] += 1
    except Exception:
        pass


def exc(where: str, **context) -> None:
    """In an except block: print the traceback to clipradar.log as before, and record it here."""
    e = sys.exc_info()[1]
    traceback.print_exc()
    if e is not None:
        record(where, str(e) or type(e).__name__, exc=e, **context)


def recent(limit: int = 200) -> list:
    """The newest failures first (this file, then the older one)."""
    out: list = []
    p = path()
    for f in (p, p[:-len(".jsonl")] + ".old.jsonl"):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
            if len(out) >= limit:
                return out
    return out


def view(limit: int = 50) -> dict:
    """/api/errors: where the log is, how many this session, and the newest ones."""
    items = recent(limit)
    return {"path": path(), "folder": os.path.dirname(path()), "session": _count["session"], "items": items}


def install() -> None:
    """Record crashes nobody caught, on the main thread and every other one."""
    old = sys.excepthook

    def hook(t, v, tb):
        if not issubclass(t, KeyboardInterrupt):
            record("crash", str(v) or t.__name__, exc=v)
        old(t, v, tb)
    sys.excepthook = hook
    old_t = threading.excepthook

    def thook(args):
        if args.exc_type is not SystemExit and args.exc_value is not None:
            record("crash", str(args.exc_value) or args.exc_type.__name__, exc=args.exc_value,
                   thread=getattr(args.thread, "name", ""))
        old_t(args)
    threading.excepthook = thook
