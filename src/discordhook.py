"""
Discord webhooks: send a finished clip (or the link to where you posted it) to a clipping
campaign's submission channel, and download files shared in Discord (a campaign or watermark file,
a logo) from their link.

A webhook is a channel's own posting address (Server settings › Integrations › Webhooks); it can
post into that one channel and nothing else. Clip Radar sends a message and, when the video is
small enough for the server, the video itself.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
import uuid

UA = {"User-Agent": "ClipRadar (+https://github.com/allen1versonfr/vodclipper)"}
HOOK_RE = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d{5,25}/[\w-]{20,100}")
UPLOAD_LIMIT = 10 * 1024 * 1024        # what a server without boosts accepts per message
MAX_DOWNLOAD = 60 * 1024 * 1024
SENT: list = []                        # (webhook, payload, file name) when CLIPRADAR_FAKE_DISCORD is set, for the tests


def valid_webhook(url: str) -> bool:
    return bool(HOOK_RE.fullmatch(str(url or "").strip()))


def _ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _fake() -> bool:
    return bool(os.environ.get("CLIPRADAR_FAKE_DISCORD"))


def _error(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("message", "")
        except Exception:
            detail = ""
        if code == 404 or code == 401:
            return "Discord doesn't know that webhook any more. Ask for a new one, or check you pasted all of it."
        if code == 413:
            return "The video is too big for that Discord server."
        if code == 429:
            return "Discord says to slow down. Wait a minute and send it again."
        return f"Discord refused it ({code}{': ' + detail if detail else ''})."
    if isinstance(e, urllib.error.URLError):
        return "Couldn't reach Discord. Check your internet connection."
    return f"Couldn't send it to Discord ({str(e)[:120]})."


def send(webhook: str, content: str, file_path: str = "", limit: int = UPLOAD_LIMIT,
         timeout: float = 120.0) -> dict:
    """Post a message, with the video attached when it fits. Returns {"ok", "attached", "note"};
    raises RuntimeError with a plain message when Discord can't be reached or refuses it."""
    if not valid_webhook(webhook):
        raise RuntimeError("That isn't a Discord webhook link. In Discord: Server settings › Integrations › Webhooks › Copy webhook URL.")
    content = (content or "").strip()[:2000]
    attach = bool(file_path) and os.path.isfile(file_path) and os.path.getsize(file_path) <= limit
    note = ""
    if file_path and os.path.isfile(file_path) and not attach:
        note = f"The video is over {limit // (1024 * 1024)} MB, so only the message was sent."
    payload = {"content": content or "New clip", "username": "Clip Radar", "allowed_mentions": {"parse": []}}
    url = webhook.strip() + "?wait=true"
    if _fake():
        SENT.append((webhook, payload, os.path.basename(file_path) if attach else ""))
        return {"ok": True, "attached": attach, "note": note}
    if attach:
        boundary = "cr" + uuid.uuid4().hex
        name = os.path.basename(file_path).replace('"', "")
        with open(file_path, "rb") as f:
            video = f.read()
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n".encode(), json.dumps(payload).encode(), b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"), video, b"\r\n",
            f"--{boundary}--\r\n".encode()])
        headers = {**UA, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        body = json.dumps(payload).encode()
        headers = {**UA, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            r.read()
    except Exception as e:
        raise RuntimeError(_error(e)) from None
    return {"ok": True, "attached": attach, "note": note}


def download(url: str, limit: int = MAX_DOWNLOAD, timeout: float = 60.0) -> bytes:
    """A file from a link (a Discord attachment, a logo on a website). https only."""
    url = str(url or "").strip()
    if not re.match(r"https://[^\s/]+/\S*$", url):
        raise RuntimeError("Paste a full link starting with https://")
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            n = int(r.headers.get("Content-Length") or 0)
            if n > limit:
                raise RuntimeError("That file is too big.")
            data = r.read(limit + 1)
    except RuntimeError:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            raise RuntimeError("That link doesn't work any more. Discord links expire after a while: "
                               "copy a fresh one from the message.") from None
        raise RuntimeError(f"Couldn't download it ({e.code}).") from None
    except Exception as e:
        raise RuntimeError(f"Couldn't download it ({str(e)[:120]}).") from None
    if len(data) > limit:
        raise RuntimeError("That file is too big.")
    return data
