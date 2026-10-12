"""
A local imitation of TikTok's OAuth + Content Posting API, for tests.

It checks what the real API checks (PKCE challenge, redirect URI, chunk-size
rules, sequential Content-Range uploads, privacy options) and walks posts
through PROCESSING_UPLOAD -> PUBLISH_COMPLETE (or SEND_TO_USER_INBOX for drafts).

  python mock_tiktok.py PORT [--unaudited]
Point VodClipper at it with:
  VODCLIPPER_TIKTOK_API=http://127.0.0.1:PORT
  VODCLIPPER_TIKTOK_AUTH=http://127.0.0.1:PORT/v2/auth/authorize/
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

MB = 1024 * 1024
STATE = {"codes": {}, "tokens": {}, "uploads": {}, "posts": {}, "unaudited": False, "log": []}


def log(msg):
    STATE["log"].append(msg)
    print("[mock tiktok]", msg, flush=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg, http=400):
        self._json({"data": {}, "error": {"code": code, "message": msg, "log_id": "mock"}}, http)

    def _auth(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if tok not in STATE["tokens"] or STATE["tokens"][tok] < time.time():
            self._err("access_token_invalid", "bad token", 401)
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/v2/auth/authorize/":
            problems = []
            if not q.get("client_key"):
                problems.append("client_key")
            if q.get("response_type") != "code":
                problems.append("response_type")
            if q.get("code_challenge_method") != "S256" or not re.fullmatch(r"[0-9a-f]{64}", q.get("code_challenge", "")):
                problems.append("code_challenge must be hex sha256")
            if not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+/callback/", q.get("redirect_uri", "")):
                problems.append("redirect_uri")
            for s in ("video.publish", "video.upload"):
                if s not in q.get("scope", ""):
                    problems.append("scope " + s)
            if problems:
                log("authorize rejected: " + ", ".join(problems))
                return self._json({"error": "invalid_request", "problems": problems}, 400)
            code = secrets.token_hex(8)
            STATE["codes"][code] = {"challenge": q["code_challenge"], "redirect": q["redirect_uri"]}
            log("user approved access")
            dest = q["redirect_uri"] + "?" + urlencode({"code": code, "state": q.get("state", ""),
                                                        "scopes": q.get("scope", "")})
            self.send_response(302)
            self.send_header("Location", dest)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path == "/__log":
            return self._json(STATE["log"])
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        raw = self._body()
        if u.path in ("/v2/oauth/token/", "/v2/oauth/revoke/"):
            f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if u.path.endswith("revoke/"):
                STATE["tokens"].pop(f.get("token"), None)
                return self._json({})
            if f.get("grant_type") == "authorization_code":
                c = STATE["codes"].pop(f.get("code"), None)
                if not c:
                    return self._json({"error": "invalid_grant", "error_description": "bad code"}, 400)
                if hashlib.sha256(f.get("code_verifier", "").encode()).hexdigest() != c["challenge"]:
                    log("token rejected: PKCE verifier mismatch")
                    return self._json({"error": "invalid_grant", "error_description": "code_verifier mismatch"}, 400)
                if f.get("redirect_uri") != c["redirect"]:
                    return self._json({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, 400)
            elif f.get("grant_type") == "refresh_token":
                if not f.get("refresh_token", "").startswith("rt_"):
                    return self._json({"error": "invalid_grant"}, 400)
            else:
                return self._json({"error": "unsupported_grant_type"}, 400)
            at = "at_" + secrets.token_hex(8)
            STATE["tokens"][at] = time.time() + 86400
            log(f"issued token ({f['grant_type']})")
            return self._json({"access_token": at, "expires_in": 86400, "open_id": "open_mock",
                               "refresh_token": "rt_" + secrets.token_hex(8), "refresh_expires_in": 31536000,
                               "scope": "user.info.basic,video.publish,video.upload", "token_type": "Bearer"})

        if not self._auth():
            return
        body = json.loads(raw or b"{}")
        if u.path == "/v2/post/publish/creator_info/query/":
            return self._json({"data": {"creator_avatar_url": "", "creator_username": "mockcreator",
                                        "creator_nickname": "Mock Creator",
                                        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                                        "comment_disabled": False, "duet_disabled": False, "stitch_disabled": True,
                                        "max_video_post_duration_sec": 600},
                               "error": {"code": "ok", "message": "", "log_id": "mock"}})
        if u.path in ("/v2/post/publish/video/init/", "/v2/post/publish/inbox/video/init/"):
            draft = "inbox" in u.path
            si = body.get("source_info") or {}
            size, chunk, count = si.get("video_size", 0), si.get("chunk_size", 0), si.get("total_chunk_count", 0)
            if si.get("source") != "FILE_UPLOAD" or size <= 0:
                return self._err("invalid_params", "source_info")
            if size < 5 * MB and (count != 1 or chunk != size):
                return self._err("invalid_params", "files under 5MB must be one chunk")
            if count > 1 and not (5 * MB <= chunk <= 64 * MB):
                return self._err("invalid_params", "chunk size out of range")
            if count != max(1, size // chunk) or size - chunk * (count - 1) > 128 * MB:
                return self._err("invalid_params", "total_chunk_count mismatch")
            if not draft:
                pi = body.get("post_info") or {}
                if pi.get("privacy_level") not in ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"):
                    return self._err("privacy_level_option_mismatch", "privacy")
                if STATE["unaudited"] and pi.get("privacy_level") != "SELF_ONLY":
                    return self._err("unaudited_client_can_only_post_to_private_accounts", "unaudited", 403)
                for k in ("disable_comment", "disable_duet", "disable_stitch", "brand_content_toggle"):
                    if k not in pi:
                        return self._err("invalid_params", f"missing {k}")
            pid = "v_pub_mock_" + secrets.token_hex(6)
            STATE["uploads"][pid] = {"size": size, "got": 0, "data_head": b"", "draft": draft, "chunks": count}
            STATE["posts"][pid] = {"polls": 0, "draft": draft, "title": (body.get("post_info") or {}).get("title", "")}
            host = self.headers.get("Host")
            log(f"init {'draft' if draft else 'direct'} post: {size} bytes in {count} chunk(s)")
            return self._json({"data": {"publish_id": pid, "upload_url": f"http://{host}/upload/{pid}"},
                               "error": {"code": "ok", "message": "", "log_id": "mock"}})
        if u.path == "/v2/post/publish/status/fetch/":
            pid = body.get("publish_id")
            p = STATE["posts"].get(pid)
            if not p:
                return self._err("invalid_publish_id", "unknown", 400)
            up = STATE["uploads"][pid]
            p["polls"] += 1
            if up["got"] < up["size"]:
                st = "PROCESSING_UPLOAD"
            elif p["polls"] < 2:
                st = "PROCESSING_UPLOAD"
            else:
                st = "SEND_TO_USER_INBOX" if p["draft"] else "PUBLISH_COMPLETE"
            data = {"status": st, "uploaded_bytes": up["got"]}
            if st == "PUBLISH_COMPLETE":
                data["publicaly_available_post_id"] = [7300000000000000001]
            return self._json({"data": data, "error": {"code": "ok", "message": "", "log_id": "mock"}})
        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        u = urlparse(self.path)
        m = re.fullmatch(r"/upload/(\S+)", u.path)
        up = STATE["uploads"].get(m.group(1)) if m else None
        body = self._body()
        if not up:
            return self._json({"error": "no upload"}, 404)
        cr = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", self.headers.get("Content-Range", ""))
        if not cr or self.headers.get("Content-Type") not in ("video/mp4", "video/quicktime", "video/webm"):
            return self._json({"error": "bad headers"}, 400)
        a, b, total = map(int, cr.groups())
        if a != up["got"] or total != up["size"] or b - a + 1 != len(body):
            log(f"upload rejected: range {a}-{b}/{total}, expected start {up['got']}")
            return self._json({"error": "range"}, 416)
        if a == 0:
            up["data_head"] = body[:16]
            if b"ftyp" not in body[:16]:
                log("upload rejected: not an MP4")
                return self._json({"error": "format"}, 400)
        up["got"] = b + 1
        done = up["got"] == up["size"]
        if done:
            log(f"upload complete ({up['size']} bytes)")
        self.send_response(201 if done else 206)
        self.send_header("Content-Range", f"bytes 0-{up['got'] - 1}/{up['size']}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def start(port: int, unaudited: bool = False) -> ThreadingHTTPServer:
    STATE["unaudited"] = unaudited
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    start(int(sys.argv[1]), "--unaudited" in sys.argv)
    print("mock TikTok running", flush=True)
    while True:
        time.sleep(3600)
