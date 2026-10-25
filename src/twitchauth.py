"""
Your Twitch sign-in, for ad-free live recording.

Twitch puts ads straight into the live video for viewers without Turbo (or a
sub to that channel). When Clip Radar records with your sign-in, Twitch hands
it the same ad-free stream you'd see in your browser. Without one, Clip Radar
still spots the ad breaks in the stream and leaves them out of the recording,
so nothing ends up in a clip; the recording just pauses while an ad would play.

The sign-in is Twitch's own "auth-token" cookie: either picked up from a small
Twitch login window inside Clip Radar, or pasted in Settings. It's stored
encrypted for your Windows account (like the TikTok sign-in) and is only ever
sent to Twitch.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import paths
import secret

STORE = paths.DATA_DIR / "twitch.dat"
ID_API = os.environ.get("CLIPRADAR_TWITCH_ID", "https://id.twitch.tv").rstrip("/")
GQL = os.environ.get("CLIPRADAR_TWITCH_GQL", "https://gql.twitch.tv/gql")
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"     # the twitch.tv website's client id (what the cookie belongs to)
_lock = threading.Lock()
_state: dict = {"checked": 0.0}


def _load() -> dict:
    try:
        return json.loads(secret.unprotect(STORE.read_bytes()).decode("utf-8"))
    except Exception:
        return {}


def _save(d: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_bytes(secret.protect(json.dumps(d).encode("utf-8")))
    os.replace(tmp, STORE)


def token() -> str:
    return _load().get("token", "")


def clean_token(raw: str) -> str:
    """Accepts the cookie value, 'OAuth xyz', or a whole cookie line; returns the bare token."""
    t = (raw or "").strip().strip('"').strip("'")
    m = re.search(r"auth-token[=\s\t]+([a-z0-9]{20,40})", t, re.I)
    if m:
        t = m.group(1)
    t = re.sub(r"^(oauth|bearer)\s+", "", t, flags=re.I).strip()
    if not re.fullmatch(r"[a-z0-9]{20,40}", t):
        raise ValueError("That doesn't look like a Twitch sign-in token. Copy the value of the auth-token cookie "
                         "from twitch.tv (about 30 letters and numbers).")
    return t


def _req(url: str, token_: str, body: Optional[bytes] = None, client_id: bool = False) -> dict:
    h = {"Authorization": f"OAuth {token_}", "User-Agent": "ClipRadar"}
    if client_id:
        h["Client-ID"] = WEB_CLIENT_ID
        h["Content-Type"] = "text/plain;charset=UTF-8"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=20, context=secret.ssl_context()) as r:
        return json.loads(r.read() or b"{}")


def check(token_: str) -> dict:
    """Who the token belongs to and whether they have Turbo. Raises ValueError if Twitch rejects it."""
    try:
        v = _req(ID_API + "/oauth2/validate", token_)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Twitch didn't accept that sign-in. It may have expired; sign in again.")
        raise RuntimeError(f"Couldn't reach Twitch to check the sign-in (HTTP {e.code}).")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach Twitch to check the sign-in ({e.reason}).")
    info = {"login": v.get("login", ""), "user_id": v.get("user_id", ""), "expires_in": v.get("expires_in"),
            "turbo": None}
    try:
        q = json.dumps({"query": "query { currentUser { login hasTurbo } }"}).encode()
        g = _req(GQL, token_, q, client_id=True)
        cu = ((g or {}).get("data") or {}).get("currentUser") or {}
        if "hasTurbo" in cu:
            info["turbo"] = bool(cu["hasTurbo"])
    except Exception:
        pass
    return info


def set_token(raw: str) -> dict:
    """Check and store a token (empty string signs out)."""
    with _lock:
        if not (raw or "").strip():
            try:
                STORE.unlink()
            except OSError:
                pass
            _state.clear()
            _state["checked"] = 0.0
            return status()
    t = clean_token(raw)
    info = check(t)
    with _lock:
        _save({"token": t, "login": info["login"], "turbo": info["turbo"], "saved": time.time()})
        _state.update(checked=time.time(), error="")
    return status()


def status(refresh: bool = False) -> dict:
    d = _load()
    if d.get("token") and (refresh or time.time() - _state.get("checked", 0) > 6 * 3600):
        try:
            info = check(d["token"])
            d.update(login=info["login"] or d.get("login", ""), turbo=info["turbo"])
            _save(d)
            _state.update(checked=time.time(), error="")
        except ValueError as e:
            _state.update(checked=time.time(), error=str(e))
        except Exception as e:
            _state.update(checked=time.time(), error=str(e))       # offline: keep the saved details
    return {"signed_in": bool(d.get("token")), "login": d.get("login", ""), "turbo": d.get("turbo"),
            "error": _state.get("error", ""), "window_login": bool(login_impl[0])}


@contextlib.contextmanager
def cookie_file():
    """A short-lived Netscape cookie file with the sign-in, for yt-dlp. Yields '' when signed out."""
    t = token()
    if not t:
        yield ""
        return
    fd, path = tempfile.mkstemp(prefix="vc_tw_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(f".twitch.tv\tTRUE\t/\tTRUE\t0\tauth-token\t{t}\n")
        yield path
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# app.py swaps in a function that opens a Twitch login window and returns the auth-token cookie
login_impl = [None]


def login_with_window(timeout: float = 600) -> dict:
    if not login_impl[0]:
        raise RuntimeError("Signing in through a window needs the Clip Radar app window. Paste your token instead.")
    t = login_impl[0](timeout)
    if not t:
        raise RuntimeError("The Twitch sign-in window was closed before you signed in.")
    return set_token(t)
