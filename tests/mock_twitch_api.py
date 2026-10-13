"""
Stand-in for the two Twitch endpoints VodClipper calls when you sign in:
id.twitch.tv/oauth2/validate (whose token is this?) and gql.twitch.tv (Turbo?).
Accepts one token, GOOD, and says that account has Turbo.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOD = "abcdefghijklmnopqrstuvwxyz0123"
STATE = {"hits": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self) -> bool:
        STATE["hits"] += 1
        return self.headers.get("Authorization", "") == f"OAuth {GOOD}"

    def do_GET(self):
        emotes = {   # 7TV, BetterTTV and FrankerFaceZ stand-ins (VODCLIPPER_EMOTE_API points here)
            "/7tv/v3/emote-sets/global": {"emotes": [{"name": "EZ"}, {"name": "Clap"}]},
            "/7tv/v3/users/twitch/1": {"emote_set": {"emotes": [{"name": "ICANT"}, {"name": "Aware"}]}},
            "/bttv/3/cached/emotes/global": [{"code": "LULW"}],
            "/bttv/3/cached/users/twitch/1": {"channelEmotes": [{"code": "catJAM"}], "sharedEmotes": []},
            "/ffz/v1/set/global": {"default_sets": [3], "sets": {"3": {"emoticons": [{"name": "OMEGALUL"}]}}},
        }
        if self.path in emotes:
            STATE["emote_hits"] = STATE.get("emote_hits", 0) + 1
            return self._send(200, emotes[self.path])
        if self.path.startswith(("/7tv/", "/bttv/", "/ffz/")):
            return self._send(404, {})
        if self.path.startswith("/oauth2/validate"):
            if not self._ok():
                return self._send(401, {"status": 401, "message": "invalid access token"})
            return self._send(200, {"client_id": "kimne78kx3ncx6brgo4mv6wki5h1ko", "login": "e2e_viewer",
                                    "user_id": "42", "expires_in": 5000000, "scopes": []})
        self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path.startswith("/gql"):
            if not self._ok() or self.headers.get("Client-ID") != "kimne78kx3ncx6brgo4mv6wki5h1ko":
                return self._send(401, {"error": "Unauthorized"})
            return self._send(200, {"data": {"currentUser": {"login": "e2e_viewer", "hasTurbo": True}}})
        self._send(404, {})


def start(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
