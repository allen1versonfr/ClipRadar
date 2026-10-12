"""
Posting to TikTok with the official Content Posting API.

TikTok only lets registered apps post, so you create a (free) app at
developers.tiktok.com and paste its Client key and Client secret into
VodClipper's Settings. Sign-in uses TikTok's desktop flow (PKCE + a redirect
back to this app on 127.0.0.1); tokens are stored encrypted for your Windows
user account and refreshed automatically.

Two ways to post:
  direct  publishes the video with the title, privacy and interaction settings
          you chose (scope video.publish). Until TikTok audits your app, these
          posts can only be private ("Only me").
  draft   sends the video to your TikTok inbox; you finish posting it in the
          TikTok app (scope video.upload).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

import paths

AUTH_URL = os.environ.get("VODCLIPPER_TIKTOK_AUTH", "https://www.tiktok.com/v2/auth/authorize/")
API = os.environ.get("VODCLIPPER_TIKTOK_API", "https://open.tiktokapis.com").rstrip("/")
SCOPES = "user.info.basic,video.publish,video.upload"
REDIRECT_PATH = "/callback/"
REDIRECT_TO_REGISTER = "http://127.0.0.1:*/callback/"
STORE = paths.DATA_DIR / "tiktok.dat"

MB = 1024 * 1024
_lock = threading.Lock()
_pending: dict = {}          # state -> {verifier, redirect, created}

FRIENDLY = {
    "unaudited_client_can_only_post_to_private_accounts":
        "TikTok hasn't audited your developer app yet, so direct posts must be private. "
        "Choose \"Only me\", or send it to your TikTok drafts instead.",
    "privacy_level_option_mismatch": "That privacy option isn't available for this account. Pick another one.",
    "spam_risk_too_many_posts": "TikTok's daily posting limit for this account has been reached. Try again tomorrow.",
    "spam_risk_too_many_pending_share":
        "You have 5 drafts waiting in TikTok already. Finish or delete some in the TikTok app first.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting through apps.",
    "reached_active_user_cap": "Your TikTok developer app reached its daily user limit. Try again tomorrow.",
    "access_token_invalid": "Your TikTok sign-in expired. Reconnect TikTok in Settings.",
    "scope_not_authorized": "Your TikTok app doesn't have posting permission. In the TikTok developer portal, "
                            "add the Content Posting API (video.publish and video.upload), then reconnect.",
    "rate_limit_exceeded": "TikTok is rate-limiting requests. Wait a minute and try again.",
    "duration_check_failed": "TikTok rejected the video's length for this account.",
    "file_format_check_failed": "TikTok couldn't read the video file.",
    "frame_rate_check_failed": "TikTok rejected the video's frame rate.",
    "picture_size_check_failed": "TikTok rejected the video's resolution.",
    "spam_risk_text": "TikTok flagged the caption text. Edit the caption and try again.",
    "spam_risk": "TikTok flagged this post as spam risk.",
    "auth_removed": "VodClipper's access was removed in TikTok. Reconnect TikTok in Settings.",
    "publish_cancelled": "The post was cancelled.",
}


class TikTokError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(FRIENDLY.get(code) or message or code)


# ----------------------------------------------------------------- storage (DPAPI on Windows)

def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return b"plain:" + base64.b64encode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    src = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "VodClipper", None, None, None, 0x1,
                                                  ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return b"dpapi:" + base64.b64encode(ctypes.string_at(out.pbData, out.cbData))
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(blob: bytes) -> bytes:
    kind, _, b64 = blob.partition(b":")
    raw = base64.b64decode(b64)
    if kind == b"plain":
        return raw
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    src = BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1,
                                                    ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load() -> dict:
    try:
        return json.loads(_unprotect(STORE.read_bytes()).decode("utf-8"))
    except Exception:
        return {}


def save(d: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_bytes(_protect(json.dumps(d).encode("utf-8")))
    os.replace(tmp, STORE)


# ----------------------------------------------------------------- HTTP

def _ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _request(method: str, url: str, *, data: bytes = None, headers: dict = None, timeout: float = 60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx() if url.startswith("https") else None) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except urllib.error.URLError as e:
        raise TikTokError("network", f"Couldn't reach TikTok ({e.reason}). Check your internet connection.")


def _form(url: str, fields: dict) -> dict:
    status, _, body = _request("POST", url, data=urllib.parse.urlencode(fields).encode(),
                               headers={"Content-Type": "application/x-www-form-urlencoded",
                                        "Cache-Control": "no-cache"})
    try:
        j = json.loads(body or b"{}")
    except ValueError:
        raise TikTokError("bad_response", f"TikTok returned an unexpected response ({status}).")
    if status >= 400 or "error" in j and isinstance(j["error"], str) and j["error"]:
        raise TikTokError(j.get("error", "oauth_error"),
                          j.get("error_description") or f"TikTok sign-in failed ({status}).")
    return j


def _api(path: str, body: dict, token: str) -> dict:
    status, _, raw = _request("POST", API + path, data=json.dumps(body).encode(),
                              headers={"Authorization": f"Bearer {token}",
                                       "Content-Type": "application/json; charset=UTF-8"})
    try:
        j = json.loads(raw or b"{}")
    except ValueError:
        raise TikTokError("bad_response", f"TikTok returned an unexpected response ({status}).")
    err = j.get("error") or {}
    code = err.get("code", "ok") if isinstance(err, dict) else str(err)
    if status >= 400 or code != "ok":
        raise TikTokError(code or f"http_{status}", (err.get("message") if isinstance(err, dict) else "") or
                          f"TikTok returned an error ({status}).")
    return j.get("data") or {}


# ----------------------------------------------------------------- account

def status() -> dict:
    d = load()
    return {
        "configured": bool(d.get("client_key") and d.get("client_secret")),
        "client_key": d.get("client_key", ""),
        "connected": bool(d.get("refresh_token")),
        "user": d.get("user") or {},
        "scopes": d.get("scope", ""),
        "redirect_uri": REDIRECT_TO_REGISTER,
        "required_scopes": SCOPES,
    }


def configure(client_key: str, client_secret: str):
    client_key, client_secret = client_key.strip(), client_secret.strip()
    if not client_key or not client_secret:
        raise TikTokError("config", "Enter both the Client key and the Client secret.")
    with _lock:
        d = load()
        if d.get("client_key") != client_key:
            d = {}                    # different app: old sign-in no longer applies
        d.update(client_key=client_key, client_secret=client_secret)
        save(d)


def auth_url(port: int) -> str:
    d = load()
    if not d.get("client_key"):
        raise TikTokError("config", "Add your TikTok app's Client key and secret first.")
    verifier = secrets.token_urlsafe(64)[:96]          # 43-128 chars of [A-Za-z0-9-._~]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()   # TikTok desktop wants hex, not base64
    state = secrets.token_urlsafe(24)
    redirect = f"http://127.0.0.1:{port}{REDIRECT_PATH}"
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["created"] > 900]:
        _pending.pop(k, None)
    _pending[state] = {"verifier": verifier, "redirect": redirect, "created": now}
    q = urllib.parse.urlencode({
        "client_key": d["client_key"], "scope": SCOPES, "response_type": "code",
        "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return f"{AUTH_URL}?{q}"


def handle_callback(params: dict) -> str:
    """Finish sign-in from the browser redirect. Returns the connected nickname."""
    if params.get("error"):
        raise TikTokError(params["error"], params.get("error_description") or "TikTok sign-in was cancelled.")
    state = params.get("state", "")
    pend = _pending.pop(state, None)
    if not pend:
        raise TikTokError("state", "This sign-in link expired or was already used. Start again from VodClipper.")
    d = load()
    j = _form(API + "/v2/oauth/token/", {
        "client_key": d["client_key"], "client_secret": d["client_secret"],
        "code": params.get("code", ""), "grant_type": "authorization_code",
        "redirect_uri": pend["redirect"], "code_verifier": pend["verifier"],
    })
    _store_tokens(d, j)
    try:
        info = creator_info()
        d = load()
        d["user"] = {"nickname": info.get("creator_nickname", ""), "username": info.get("creator_username", ""),
                     "avatar": info.get("creator_avatar_url", "")}
        save(d)
        return d["user"]["nickname"] or d["user"]["username"]
    except TikTokError:
        return ""


def _store_tokens(d: dict, j: dict):
    now = time.time()
    d.update(access_token=j["access_token"], refresh_token=j.get("refresh_token", d.get("refresh_token")),
             expires_at=now + float(j.get("expires_in", 86400)),
             refresh_expires_at=now + float(j.get("refresh_expires_in", 31536000)),
             open_id=j.get("open_id", ""), scope=j.get("scope", ""))
    save(d)


def token() -> str:
    with _lock:
        d = load()
        if not d.get("refresh_token"):
            raise TikTokError("not_connected", "Connect your TikTok account in Settings first.")
        if time.time() < d.get("expires_at", 0) - 300:
            return d["access_token"]
        if time.time() > d.get("refresh_expires_at", 0):
            raise TikTokError("access_token_invalid")
        j = _form(API + "/v2/oauth/token/", {
            "client_key": d["client_key"], "client_secret": d["client_secret"],
            "grant_type": "refresh_token", "refresh_token": d["refresh_token"],
        })
        _store_tokens(d, j)
        return j["access_token"]


def disconnect():
    d = load()
    if d.get("access_token"):
        try:
            _form(API + "/v2/oauth/revoke/", {"client_key": d["client_key"], "client_secret": d["client_secret"],
                                              "token": d["access_token"]})
        except TikTokError:
            pass
    keep = {k: d[k] for k in ("client_key", "client_secret") if k in d}
    save(keep)


def creator_info() -> dict:
    return _api("/v2/post/publish/creator_info/query/", {}, token())


# ----------------------------------------------------------------- posting

def chunk_plan(size: int) -> tuple:
    """(chunk_size, total_chunk_count) following TikTok's media transfer rules."""
    if size <= 0:
        raise TikTokError("file", "The video file is empty.")
    if size <= 64 * MB:
        return size, 1                       # one chunk (files under 5 MB must be a single chunk anyway)
    chunk = 32 * MB
    return chunk, size // chunk              # the last chunk absorbs the remainder (stays under 128 MB)


def post_video(path: str, *, mode: str = "direct", title: str = "", privacy: str = "",
               allow_comment: bool = False, allow_duet: bool = False, allow_stitch: bool = False,
               brand_organic: bool = False, brand_content: bool = False,
               progress: Callable = lambda x: None, step: Callable = lambda s: None,
               check: Callable = lambda: None) -> dict:
    size = os.path.getsize(path)
    chunk, count = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    tok = token()
    step("Starting upload")
    if mode == "draft":
        data = _api("/v2/post/publish/inbox/video/init/", {"source_info": source}, tok)
    else:
        if not privacy:
            raise TikTokError("privacy", "Choose who can see the post.")
        post_info = {"title": title[:2200], "privacy_level": privacy,
                     "disable_comment": not allow_comment, "disable_duet": not allow_duet,
                     "disable_stitch": not allow_stitch,
                     "brand_content_toggle": bool(brand_content), "brand_organic_toggle": bool(brand_organic)}
        data = _api("/v2/post/publish/video/init/", {"post_info": post_info, "source_info": source}, tok)
    publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
    if not publish_id or not upload_url:
        raise TikTokError("bad_response", "TikTok didn't return an upload address.")

    with open(path, "rb") as f:
        for i in range(count):
            check()
            first = i * chunk
            last = size - 1 if i == count - 1 else first + chunk - 1
            f.seek(first)
            body = f.read(last - first + 1)
            step(f"Uploading {int(first / size * 100)}%")
            for attempt in range(4):
                st, _, raw = _request("PUT", upload_url, data=body, timeout=300, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                if st in (200, 201, 206):
                    break
                if st >= 500 and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                if st == 403:
                    raise TikTokError("upload_expired", "The upload link expired. Try posting again.")
                raise TikTokError(f"upload_{st}", f"TikTok rejected the upload ({st}).")
            progress((last + 1) / size * 0.8)

    step("TikTok is processing the video")
    deadline = time.time() + 15 * 60
    result = {"publish_id": publish_id, "status": "PROCESSING_UPLOAD", "post_id": ""}
    while time.time() < deadline:
        check()
        try:
            st = _api("/v2/post/publish/status/fetch/", {"publish_id": publish_id}, token())
        except TikTokError as e:
            if e.code == "rate_limit_exceeded":
                time.sleep(10)
                continue
            raise
        s = st.get("status", "")
        result["status"] = s
        ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
        if ids:
            result["post_id"] = str(ids[0])
        if s == "FAILED":
            raise TikTokError(st.get("fail_reason") or "failed", "TikTok couldn't publish the video.")
        if s in ("SEND_TO_USER_INBOX",) or (s == "PUBLISH_COMPLETE"):
            progress(1.0)
            return result
        progress(0.85)
        time.sleep(3)
    result["status"] = "PROCESSING"
    return result


def post_url(post_id: str) -> str:
    u = (load().get("user") or {}).get("username", "")
    return f"https://www.tiktok.com/@{u}/video/{post_id}" if u and post_id else ""
