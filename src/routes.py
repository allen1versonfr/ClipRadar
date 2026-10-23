"""
Clip Radar's HTTP routes: the request handler for the local server.

Moved out of server.py so routing and the app's work (projects, jobs, the render queue, live
sessions) live apart. Everything the handler uses from server.py is read as S.<name> at request
time, so tests that swap a server function or setting (server._render_item = ..., server.WEB_DIR
= ...) still reach the handler. The full route list: docs/api-routes.md.
"""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

from version import VERSION
import audio_events
import campaigns
import checkflow
import cleanup
import detect
import discordhook
import engine
import exports
import framing
import hashtags
import hotkey
import learning
import locations
import paths
import persist
import safety
import studio
import tools_setup
import twitchauth
import updater
import watermarks

import server as S


class Handler(BaseHTTPRequestHandler):
    server_version = "Clip Radar"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > S.MAX_BODY:
            raise ValueError("body too large")
        if not n:
            return {}
        # JSON only: a page on another site can't send this type without a CORS preflight
        # (which this server never answers), so it can't reach the API even if Origin is missing.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValueError("expected application/json")
        return json.loads(self.rfile.read(n) or b"{}")

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _local_only(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._err("forbidden", 403)
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", origin):
                self._err("forbidden", 403)
                return False
        return True

    def _send_file(self, path: str, ctype=None, cache=False):
        if not os.path.isfile(path):
            return self._err("not found", 404)
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype in ("application/javascript", "text/javascript", "text/css", "text/html") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        if path.lower().endswith(".mkv"):
            ctype = "video/webm"  # lets Chrome/Edge try to play Matroska
        rng = self.headers.get("Range")
        start, end, code = 0, size - 1, 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache if isinstance(cache, str) else "max-age=3600" if cache else "no-cache")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._local_only():
            return
        u = urlparse(self.path)
        p = unquote(u.path)
        q = parse_qs(u.query)

        legacy_index = str(S.HERE / "ui" / "index.html")
        if p in ("/legacy", "/legacy/", "/legacy/index.html"):
            return self._send_file(legacy_index, "text/html; charset=utf-8")
        mode = S.ui_mode()
        if p in ("/", "/index.html"):
            if mode == "web":
                return self._send_file(str(S.WEB_DIR / "index.html"), "text/html; charset=utf-8")
            if mode == "missing":
                return self._err("The new UI isn't built (run npm run build in web/), or unset CLIPRADAR_UI.", 503)
            return self._send_file(legacy_index, "text/html; charset=utf-8")
        if p in ("/next", "/next/", "/next/index.html") or (p.startswith("/next/") and "." not in p.rsplit("/", 1)[-1]):
            # the React UI, to try while the original page stays at / (its screens switch by #hash)
            if S.web_available():
                return self._send_file(str(S.WEB_DIR / "index.html"), "text/html; charset=utf-8")
            return self._err("The new UI isn't in this build.", 404)
        if p == "/favicon.ico":
            return self._send_file(str(S.HERE / "ui" / "icon.svg"), "image/svg+xml", cache=True)
        if mode == "web" and not p.startswith(S._UI_PREFIXES):
            # files from the React build: hashed bundles under /assets/ never change, so cache them
            # for good; anything else at the top (robots.txt, a manifest) is served as is; a path with
            # no file extension is a client-side route and gets the app's index.html.
            base = os.path.realpath(S.WEB_DIR)
            path = os.path.realpath(os.path.join(base, p.lstrip("/")))
            if not path.startswith(base + os.sep):
                return self._err("forbidden", 403)
            if os.path.isfile(path):
                return self._send_file(path)
            if "." not in p.rsplit("/", 1)[-1]:
                return self._send_file(str(S.WEB_DIR / "index.html"), "text/html; charset=utf-8")
            return self._err("not found", 404)
        if p.startswith("/assets/") and S.web_available():
            base = os.path.realpath(S.WEB_DIR / "assets")
            path = os.path.realpath(os.path.join(base, p[len("/assets/"):]))
            if path.startswith(base + os.sep):
                return self._send_file(path, cache="public, max-age=31536000, immutable")
            return self._err("forbidden", 403)
        if p.startswith("/ui/"):
            base = os.path.realpath(S.HERE / "ui")
            path = os.path.realpath(os.path.join(base, p[4:]))
            if path.startswith(base + os.sep):
                # no long caching: after an update the window must pick up the new app.js/app.css
                ctype = "font/woff" if path.endswith(".woff") else None
                return self._send_file(path, ctype, cache=path.endswith((".svg", ".ico", ".png", ".woff")))
            return self._err("forbidden", 403)
        m = re.fullmatch(r"/wmimg/([\w.-]+)", p)
        if m:
            # a watermark layer's image, for the preview: only Clip Radar's own copies
            for img in (os.path.join(watermarks.image_dir(), m.group(1)), os.path.join(str(paths.DATA_DIR), m.group(1))):
                if watermarks.clean_mark({"kind": "image", "image": img}):
                    return self._send_file(img, cache=True)
            return self._err("no watermark image", 404)
        if p == "/api/status":
            ts = tools_setup.status()
            return self._json({
                "ffmpeg": ts["ffmpeg"],
                "ytdlp": ts["ytdlp_exe"] or S._has("yt_dlp", load=False),
                "whisper": S._has("faster_whisper", load=False),
                "laughter": S._has("onnxruntime", load=False) and audio_events.model_path() is not None,
                "twitchdownloader": ts["twitchdownloader"],
                "can_download_tools": ts["can_download"],
                "workroot": S.WORKROOT,
                "locations_warn": [x["problem"] for x in locations.problems()],
                "window": S.MODE["window"],
                "frozen": paths.FROZEN,
                "version": VERSION,
                "api": S.API_VERSION,
                "ui": S.ui_mode(), "next": S.web_available(),
                "running": S.busy(),
                "live": (S.live_active().id if S.live_active() else ""),
                "lives": [{"id": x.id, "title": x.title, "status": x.status} for x in S.live_list()],
                "hotkey": hotkey.status(),
            })
        if p == "/api/update":
            return self._json(updater.view())
        if p == "/api/update/versions":
            return self._json(updater.versions(refresh="refresh" in (urlparse(self.path).query or ""), log=S._say))
        if p == "/api/safety":
            return self._json(safety.public_config())
        if p == "/api/hotkey":
            return self._json(hotkey.status())
        if p == "/api/ping":
            S.last_ping[0] = time.time()
            return self._json({"ok": True})
        if p == "/api/recent":
            rec_ = [r_ for root in (locations.project_roots() or [S.WORKROOT]) for r_ in engine.recent_projects(root)]
            return self._json(sorted(rec_, key=lambda x: -x["updated"])[:8])
        m = re.fullmatch(r"/api/live/(\w+)", p)
        if m:
            sess = S.live_sessions.get(m.group(1))
            return self._json(S.live_view(sess)) if sess else self._err("That live session is gone.", 404)
        if p == "/api/twitch":
            return self._json(twitchauth.status(refresh="refresh" in q))
        if p == "/api/learning":
            return self._json(learning.summary())
        if p == "/api/titles":
            return self._json(S.title_prefs())
        if p == "/api/hashtags":
            return self._json(S.hashtags_view((q.get("streamer") or [""])[0]))
        if p == "/api/cleanup":
            return self._json({"config": cleanup.load(), "last": cleanup.last})
        if p == "/api/learning/explain":
            return self._json(learning.explain((q.get("streamer") or [""])[0]))
        if p == "/api/queue":
            with S.queue_lock:
                items = [S.queue_public(it) for it in S.queue]
            return self._json({"items": items, "eta": S.queue_eta(), "workers": S.MAX_WORKERS[0]})
        if p == "/api/export":
            return self._json(S.export_view())
        if p == "/api/campaigns":
            return self._json(S.campaigns_view())
        if p == "/api/sources":
            return self._json(S.sources_view())
        if p == "/api/locations":
            return self._json(locations.view())
        m = re.fullmatch(r"/api/framing/(\w+)", p)
        if m:
            # Layout › Auto-frame: what fits this video (a facecam, a person filling the frame, or neither)
            pr = S.projects.get(m.group(1))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            if not framing.available():
                return self._json({"layout": "", "reason": "Face finding isn't available in this copy.", "available": False})
            try:
                res = framing.suggest(pr["video"], float(pr["info"].get("duration") or 0) or 60.0,
                                      cache_dir=locations.temp_dir(os.path.basename(pr["workdir"]), "framing"))
            except Exception as e:
                return self._err(f"Couldn't look for faces: {str(e)[:160]}")
            return self._json(dict(res, available=True))
        if p == "/api/perf":
            return self._json(S.perf_view())
        m = re.fullmatch(r"/api/strip/(\w+)", p)
        if m:
            # what the editing timeline draws for a stretch of the video: loudness, laughter, chat
            pr = S.projects.get(m.group(1))
            if not pr or "analysis" not in pr:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                a, b = float(q.get("a", ["0"])[0]), float(q.get("b", ["0"])[0])
            except ValueError:
                return self._err("bad range")
            return self._json(S.strip_data(pr, a, b))
        m = re.fullmatch(r"/api/project/(\w+)", p)
        if m:
            if m.group(1) not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            return self._json(S.project_view(m.group(1)))
        m = re.fullmatch(r"/api/clips/(\w+)", p)
        if m:
            pr = S.projects.get(m.group(1))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            lj = S.jobs.get(pr.get("listen_job", ""))
            return self._json({"clips": pr["clips"], "gems": pr.get("gems", []), "deep": S.deep_status(pr),
                               "listening": bool(lj and lj["status"] == "running"),
                               "step": lj["step"] if lj else ""})
        if p == "/api/storage":
            st = S._storage_all()
            st["uninstaller"] = bool(S.uninstaller_path())
            return self._json(st)
        m = re.fullmatch(r"/api/job/(\w+)", p)
        if m:
            job = S.jobs.get(m.group(1))
            return self._json(S.job_public(job)) if job else self._err("That job is gone. Start it again.", 404)
        m = re.fullmatch(r"/export/(\w+)", p)
        if m:
            with S.queue_lock:
                it = next((x for x in S.queue if x["id"] == m.group(1)), None)
            if not it or it["status"] != "done":
                return self._err("not found", 404)
            return self._send_file(it.get("path", ""))
        m = re.fullmatch(r"/vod/(\w+)", p)
        if m and m.group(1) in S.projects:
            return self._send_file(S.projects[m.group(1)]["video"])
        m = re.fullmatch(r"/api/frame/(\w+)", p)
        if m and m.group(1) in S.projects:
            pr = S.projects[m.group(1)]
            try:
                t = float(q.get("t", ["0"])[0])
            except ValueError:
                t = 0.0
            out = os.path.join(pr["workdir"], "thumbs", f"frame_{pr.get('thumb_prefix', '')}{int(t)}.jpg")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if not os.path.exists(out):
                try:
                    engine.make_thumb(pr["video"], t, out, width=1280)
                except Exception as e:
                    return self._err(str(e), 500)
            return self._send_file(out, "image/jpeg", cache=True)
        m = re.fullmatch(r"/files/(\w+)/(.+)", p)
        workdir = ""
        if m:
            if m.group(1) in S.projects:
                workdir = S.projects[m.group(1)]["workdir"]
            else:   # Exports after a restart: the clip's VOD isn't open, but its folder is known
                with S.queue_lock:
                    workdir = next((x.get("workdir") or "" for x in S.queue if x.get("pid") == m.group(1)), "")
        if m and workdir:
            base = os.path.realpath(workdir)
            if m.group(2).startswith("preview/"):          # exact previews live with the temporary files
                base = os.path.realpath(locations.temp_dir(os.path.basename(workdir)))
            path = os.path.realpath(os.path.join(base, m.group(2)))
            if not path.startswith(base + os.sep):
                return self._err("forbidden", 403)
            return self._send_file(path, cache="/thumbs/" in path.replace("\\", "/"))
        return self._err("not found", 404)

    def do_POST(self):
        if not self._local_only():
            return
        p = urlparse(self.path).path
        try:
            body = self._body()
        except Exception as e:
            self.close_connection = True          # the body may be unread: don't reuse this connection
            if "too large" in str(e):
                return self._err("Request too large.", 413)
            if "application/json" in str(e):
                return self._err("Send JSON (Content-Type: application/json).", 415)
            return self._err("bad request")
        if not isinstance(body, dict):
            return self._err("bad request")
        try:
            return self._post(p, body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            raise
        except (KeyError, TypeError, ValueError) as e:
            traceback.print_exc()
            return self._err(f"Bad request ({type(e).__name__}: {str(e)[:120]})")
        except Exception as e:
            traceback.print_exc()
            return self._err(f"Something went wrong: {str(e)[:200]}", 500)

    def _post(self, p: str, body: dict):

        if p == "/api/analyze":
            src = (body.get("source") or "").strip()
            dup = next((j for j in S.jobs.values() if j["kind"] == "analyze" and j["status"] == "running"
                        and S._same_source((j.get("body") or {}).get("source", ""), src)), None)
            if dup:
                return self._json({"job": dup["id"], "already": True})     # opening it already
            job = S.new_job("analyze")
            job["body"] = dict(body)
            S.run_job(job, lambda: S.do_analyze(job, body))
            return self._json({"job": job["id"]})

        if p == "/api/locations":
            if S.live_list() or S.busy():
                return self._err("Wait for downloads, renders and live clipping to finish, then change folders.", 409)
            res = locations.save(body or {})
            S._set_workroot()
            return self._json(dict(locations.view(), errors=res["errors"]))

        if p == "/api/storage/clear_cache":
            keep = [os.path.dirname(sess.seg_dir) for sess in S.live_list()]
            res = locations.clear_temp(keep=tuple(keep))
            part = locations.stale_partials([locations.path("downloads")] + locations.project_roots())
            freed2, _ = engine._remove(part)
            res["freed"] += freed2
            return self._json(dict(res, locations=locations.view()["locations"]))

        if p == "/api/sources/pause":
            job = S.jobs.get(body.get("job", ""))
            if job and job["kind"] == "analyze" and job["status"] == "running":
                job["pause"] = job["cancel"] = True
                job["step"] = "Pausing"
            return self._json(S.sources_view())

        if p == "/api/sources/resume":
            old = S.jobs.get(body.get("job", ""))
            if not old or old["kind"] != "analyze" or old["status"] not in ("paused", "error", "cancelled"):
                return self._err("Nothing to resume.")
            job = S.new_job("analyze")
            job["body"] = dict(old.get("body") or {})
            S.run_job(job, lambda: S.do_analyze(job, job["body"]))
            S.jobs.pop(old["id"], None)
            return self._json({"job": job["id"]})

        if p == "/api/sources/forget":
            job = S.jobs.get(body.get("job", ""))
            if job and job["status"] != "running":
                S.jobs.pop(job["id"], None)
            sess = S.live_sessions.get(body.get("live", ""))
            if sess and sess.status not in S.LIVE_ON:
                sess.started = 0                    # drops out of the list
            return self._json(S.sources_view())

        if p == "/api/setup_tools":
            want = [w for w in body.get("want", []) if w in ("ffmpeg", "twitchdownloader", "ytdlp", "laughter")]
            if not want:
                return self._err("Nothing to download.")
            job = S.new_job("setup")

            def go():
                log, step = S.job_logger(job), S.job_step(job)
                step("Downloading tools")
                tools = [w for w in want if w != "laughter"]
                res = tools_setup.setup(tools, log=log, progress=S.job_progress(job, 0, 0.8 if "laughter" in want else 1),
                                        check=S.job_check(job)) if tools else {}
                if "laughter" in want and not audio_events.available():
                    audio_events.download(log, progress=S.job_progress(job, 0.8 if tools else 0, 1))
                return res
            S.run_job(job, go)
            return self._json({"job": job["id"]})

        if p == "/api/watermark/image":
            # a watermark layer's image: a copy is kept, so moving the original doesn't break exports
            try:
                img = watermarks.store_file(str(body.get("path") or ""))
            except (ValueError, OSError) as e:
                return self._err(str(e) if isinstance(e, ValueError) else f"Couldn't copy the image: {e}")
            return self._json({"image": img, "url": "/wmimg/" + os.path.basename(img)})

        if p == "/api/import":
            # a Clip Radar file shared in Discord (a campaign or a watermark), or an image for a layer,
            # from a file on this PC or from its link
            try:
                if body.get("url"):
                    data = discordhook.download(str(body["url"]))
                else:
                    src = str(body.get("path") or "")
                    if not os.path.isfile(src):
                        return self._err("That file isn't there.")
                    if os.path.getsize(src) > watermarks.MAX_PACK:
                        return self._err("That file is too big to be a Clip Radar file.")
                    with open(src, "rb") as f:
                        data = f.read()
                if watermarks.image_kind(data):
                    img = watermarks.store_image(data)
                    return self._json({"kind": "image", "image": img, "url": "/wmimg/" + os.path.basename(img)})
                res = campaigns.import_any(data)
            except (ValueError, RuntimeError, OSError) as e:
                return self._err(str(e))
            if res["kind"] == "watermark":
                pre = res["preset"]
                ex = exports.load()
                keep = [x for x in ex["wm_presets"] if x["name"].lower() != pre["name"].lower()]
                exports.save({"wm_presets": keep + [pre]})        # saved; the window puts it in use
                return self._json({"kind": "watermark", "name": pre["name"], "marks": pre["marks"], "export": S.export_view()})
            return self._json({"kind": "campaign", "name": res["campaign"]["name"], "id": res["campaign"]["id"],
                               "campaigns": S.campaigns_view()})

        if p == "/api/watermark/share":
            # Settings › Watermark › Share: one file with the layers and their images, for Discord
            pack = watermarks.make_pack(str(body.get("name") or "Watermark"), body.get("marks") or [])
            if not pack["marks"]:
                return self._err("Add a text or image layer first.")
            return self._json(S._share_file(pack, f"{watermarks.safe_name(pack['name'])} - watermark.json"))

        if p.startswith("/api/campaigns/"):
            act = p.rsplit("/", 1)[1]
            try:
                if act == "save":
                    camps = campaigns.save(body.get("campaign") or {})
                    if body.get("activate"):
                        cid = (body.get("campaign") or {}).get("id") or camps["list"][-1]["id"]
                        campaigns.set_active(cid)
                elif act == "delete":
                    campaigns.delete(str(body.get("id") or ""))
                elif act == "active":
                    campaigns.set_active(str(body.get("id") or ""))
                elif act == "share":
                    c = campaigns.get(str(body.get("id") or ""))
                    if not c:
                        return self._err("That campaign isn't there any more.")
                    pack = campaigns.make_pack(c, with_webhook=bool(body.get("webhook")))
                    return self._json(S._share_file(pack, f"{watermarks.safe_name(c['name'])} - campaign.json"))
                elif act == "test":
                    hook = str(body.get("webhook") or "")
                    discordhook.send(hook, "Clip Radar is connected. Finished clips will be sent here.")
                    return self._json({"ok": True})
                else:
                    return self._err("not found", 404)
            except (ValueError, RuntimeError) as e:
                return self._err(str(e))
            return self._json(S.campaigns_view())

        if p == "/api/queue/post_url":
            # Exports › Submit: where you posted the clip, for the campaign's Discord channel
            url = str(body.get("url") or "").strip()[:300]
            if url and not re.match(r"https?://\S+$", url):
                return self._err("Paste the link to your post (it starts with https://).")
            with S.queue_lock:
                it = next((x for x in S.queue if x["id"] == body.get("item")), None)
                if not it:
                    return self._err("That clip isn't in Exports any more.")
                it["post_url"] = url
            S.save_queue_soon()
            return self._json({"ok": True})

        if p == "/api/discord/send":
            with S.queue_lock:
                it = next((x for x in S.queue if x["id"] == body.get("item")), None)
            if not it or it["status"] != "done":
                return self._err("That clip isn't rendered yet.")
            if "post_url" in body:
                it["post_url"] = str(body.get("post_url") or "").strip()[:300]
            try:
                r = S.send_to_discord(it, with_file=body.get("file"))
            except RuntimeError as e:
                return self._err(str(e))
            return self._json(dict(r, item=S.queue_public(it)))

        if p == "/api/pick_file":
            kind = body.get("kind", "video")
            return self._json({"path": S.pick_file(kind)})

        if p == "/api/update/check":
            return self._json(updater.check(log=S._say))
        if p == "/api/update/token":
            try:
                updater.set_token(str(body.get("token") or ""))
            except ValueError as e:
                return self._err(str(e))
            return self._json(updater.check(log=S._say))
        if p == "/api/update/settings":
            updater.set_prefs(bool(body.get("auto", True)))
            return self._json(updater.view())
        if p == "/api/update/install":
            if body.get("version") and not updater.newer(str(body["version"]), VERSION):
                return self._err("You can only install a version newer than the one you have.", 400)
            if not updater.installed():
                return self._err("This copy of Clip Radar wasn't installed with Setup, so it can't update itself. "
                                 "Download the new version from GitHub instead.", 400)
            if body.get("version") and updater.view()["state"] not in ("downloading", "installing"):
                try:
                    updater.choose(str(body["version"]))      # a particular newer version, from All versions
                except ValueError as e:
                    return self._err(str(e), 400)
            v = updater.view()
            if not v["available"]:
                return self._err("You already have the latest version.", 400)
            if v["state"] in ("downloading", "installing"):
                return self._json(v)
            if S.busy():
                return self._err("Finish or stop what's running (a download, render or live recording), then update.", 409)
            if not S._update_running.acquire(blocking=False):
                return self._json(updater.view())
            with updater._lock:     # show progress right away
                updater.state.update(state="downloading", progress=0.0, error="")
            threading.Thread(target=S._install_update, daemon=True).start()
            return self._json(updater.view())

        if p == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: S.shutdown_hook[0]()).start()
            return

        if p == "/api/delete":
            what = body.get("what")
            if what not in ("vods", "clips"):
                return self._err("Unknown delete request.")
            if S.live_active():
                return self._err("Stop live clipping first, then delete.", 409)
            if S.busy():
                return self._err("Wait for the current download or render to finish (or cancel it), then try again.", 409)
            # stop background listening so it isn't reading a VOD while it's deleted
            for j in S.jobs.values():
                if j["kind"] in ("listen", "deepscan") and j["status"] == "running":
                    j["cancel"] = True
            end_wait = time.time() + 8
            while time.time() < end_wait and any(j["kind"] in ("listen", "deepscan") and j["status"] == "running"
                                                 for j in S.jobs.values()):
                time.sleep(0.1)
            fn = engine.delete_vods if what == "vods" else engine.delete_clips
            res = {"deleted": 0, "freed": 0, "failed": []}
            tries = 4
            for attempt in range(1, tries + 1):
                failed = []
                for root in locations.project_roots() or [S.WORKROOT]:
                    r_ = fn(root)
                    if attempt == 1:
                        res["deleted"] += r_["deleted"]
                    res["freed"] += r_["freed"]
                    failed += r_["failed"]
                left = engine.storage_summary(S.WORKROOT)["vods" if what == "vods" else "clips"]["bytes"]
                if not left or attempt == tries:
                    res["failed"] = failed if left else []
                    break
                # Windows can keep a file open for a moment after it was read (a preview, a scan,
                # a just-finished render): wait a little longer each time and try again
                print(f"  Some files were busy ({left // 1_000_000} MB); trying again", flush=True)
                time.sleep(2.5 * attempt)
            closed = []
            if what == "vods":
                # projects whose downloaded VOD is gone can't be previewed or rendered any more
                for pid, pr in list(S.projects.items()):
                    if not os.path.exists(pr["video"]):
                        S.projects.pop(pid, None)
                        closed.append(pid)
            with S.queue_lock:
                S.queue[:] = [it for it in S.queue if not (it["status"] == "done" and not os.path.exists(it.get("path", "")))
                            and not (it["status"] == "error" and it["pid"] in closed)]
            S.save_queue_soon()
            res["closed"] = closed
            res["storage"] = S._storage_all()
            return self._json(res)

        if p == "/api/uninstall":
            exe = S.uninstaller_path()
            if not exe:
                return self._err("No uninstaller found. If you're using the portable version, just delete its folder.", 404)
            try:
                subprocess.Popen([exe], close_fds=True)
            except Exception as e:
                return self._err(f"Couldn't start the uninstaller: {e}", 500)
            self._json({"ok": True})
            threading.Timer(0.5, lambda: S.shutdown_hook[0]()).start()
            return

        if p == "/api/live/start":
            src = (body.get("source") or "").strip()
            on = S.live_list()
            if any(S._same_source(sess.source, src) for sess in on):
                return self._err("That stream is already being clipped.", 409)
            limit = exports.load()["lives"]
            if len(on) >= limit:
                return self._err(f"Already clipping {len(on)} stream{'s' if len(on) != 1 else ''}. Stop one first, or allow more under "
                                 "Settings › Performance › Live streams at once.", 409)
            if not engine.which("ffmpeg"):
                return self._err("ffmpeg is missing. Download the tools first.")
            try:
                sess = S.start_live_session(src, dict(body))
            except ValueError as e:
                return self._err(str(e))
            return self._json(S.live_view(sess))

        m = re.fullmatch(r"/api/live/(\w+)/(stop|clip|settings)", p)
        if m:
            sess = S.live_sessions.get(m.group(1))
            if not sess:
                return self._err("That live session is gone.", 404)
            act = m.group(2)
            if act == "stop":
                sess.extra["user_stopped"] = True
                if sess.status == "waiting":
                    sess.status = "stopped"
                threading.Thread(target=sess.stop, daemon=True).start()
            elif act == "clip":
                if sess.status not in ("recording", "reconnecting"):
                    return self._err("Recording hasn't started yet." if sess.status == "starting" else "The stream isn't recording.")
                sess.clip_now(float(body.get("seconds", 30)))
            else:
                if "auto_render" in body:
                    sess.extra["auto_render"] = bool(body["auto_render"])
                if "opts" in body:
                    sess.extra["opts"] = dict(body["opts"] or {})
                level = body.get("frequency") or body.get("sensitivity")
                if level:
                    sess.set_frequency(level)
                if "min_quality" in body:
                    try:
                        sess.min_quality = int(min(max(int(body["min_quality"] or 0), 0), 95))
                    except (TypeError, ValueError):
                        pass
                for k in ("keep_watching",):
                    if k in body:
                        sess.extra[k] = bool(body[k])
                if "hands_free" in body:
                    sess.extra["auto_render"] = bool(body["hands_free"])
                for k in ("model", "language"):
                    if k in body:
                        sess.extra[k] = body[k]
            return self._json(S.live_view(sess))

        if p == "/api/handoff":
            # TikTok Studio: copy the caption (in the window), show the file, open the upload page
            with S.queue_lock:
                it = next((x for x in S.queue if x["id"] == body.get("item")), None)
            if not it or it["status"] != "done":
                return self._err("That clip isn't rendered yet.")
            try:
                r = studio.handoff(it.get("path", ""), it.get("title", ""), it.get("post", ""),
                                   reveal_file=body.get("reveal", True) is not False)
            except RuntimeError as e:
                return self._err(str(e))
            it["handoff"] = {"at": r["opened_at"], "url": r["url"]}
            S.save_queue_soon()
            try:
                S.record_feedback(it["pid"], it.get("clip_id"), "post")    # you chose to post it: a strong "keep"
            except Exception:
                pass
            return self._json(r)

        if p == "/api/open_url":
            url = str(body.get("url", ""))
            if re.match(r"^https://([a-z0-9-]+\.)*(tiktok\.com|github\.com)/", url):
                threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
                return self._json({"ok": True})
            return self._err("That link isn't allowed.", 400)

        if p == "/api/cancel":
            job = S.jobs.get(body.get("job", ""))
            if job and job["status"] == "running":
                job["cancel"] = True
                job["step"] = "Cancelling"
            return self._json({"ok": True})

        if p == "/api/repick":
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            pr = S.projects[pid]
            an = pr["analysis"]
            det = pr.get("detect") or detect.settings({})
            if isinstance(body.get("detect"), dict):
                base = dict(det)
                if body["detect"].get("preset") and body["detect"]["preset"] != det.get("preset"):
                    base.pop("min_len", None)          # a new stream type brings its own clip lengths
                    base.pop("max_len", None)
                det = detect.settings(dict(base, **{k: v for k, v in body["detect"].items() if v is not None}))
            if body.get("dry_run"):
                # "Try": what these settings would suggest, compared with the list you have now
                return self._json(S._compare_settings(pr, det, float(body.get("chat_weight", an.chat_weight_pct)),
                                                    int(body.get("count", 0) or 0), float(body.get("length", 0) or 0)))
            an.combine(float(body.get("chat_weight", an.chat_weight_pct)), detect.preset(det["preset"]))
            recut = det != pr.get("detect")          # new settings: clips you haven't touched get new cuts
            pr["detect"] = det
            length = float(body.get("length", 0) or 0)
            pr["length"] = length
            clips = S._pick(pr, int(body.get("count", 0) or 0), length, det)
            # clips you kept, skipped, trimmed, retitled or added stay whatever the sliders say
            mine = [c for c in pr["clips"] if persist.touched(c)]
            cur = {c["id"]: c for c in pr["clips"]}
            taken = {c["id"] for c in mine}
            clips = [c for c in clips if not any(c.start < u["end"] - 2 and c.end > u["start"] + 2 for u in mine)]
            S._number(pr, clips, "hype", taken)
            gone = S._removed_nums(pr)                 # clips you removed don't come back
            clips = [c for c in clips if c.id not in gone]
            fresh, order = [], []
            for c in clips:
                o = cur.get(c.id)
                if o and not o.get("manual"):
                    # the same moment: it keeps its number, title and transcript
                    o.update(score=c.score, reason=c.reason, feat=c.feat, tuned=c.tuned)
                    if o.get("len_mode", 0) != length or recut:     # new length or settings: new cuts, then a fresh listen
                        o.update(start=c.start, end=c.end, peak=c.peak, len_mode=length, listened=False,
                                 orig=[c.start, c.end])
                    order.append(o)
                else:
                    fresh.append(c)
                    order.append(c)
            made = {d["id"]: d for d in S.clip_dicts(pid, fresh)}
            for d in made.values():
                d["len_mode"] = length
            new = [made[x.id] if isinstance(x, engine.Clip) else x for x in order]
            for u in mine:                     # yours slot in by score
                at = next((i for i, c in enumerate(new) if c.get("score", 0) < u.get("score", 0)), len(new))
                new.insert(at, u)
            pr["clips"] = new
            pr["clips_ver"] = pr.get("clips_ver", 0) + 1
            S.save_project_soon(pid)
            return self._json(S.project_view(pid))

        if p == "/api/add_clip":
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            pr = S.projects[pid]
            an = pr["analysis"]
            t = float(body["t"])
            length = float(body.get("length", 0) or 0)
            if length > 0:
                length = min(length, an.duration)
                start = max(0.0, min(t - length * 0.5, an.duration - length))
                end = min(an.duration, start + length)
            else:
                start, end, _ = engine.smart_bounds(an, t)
            sc = an.score[int(min(max(t, 0), len(an.score) - 1))]
            clip = engine.Clip(0, round(start, 2), round(end, 2), t, round(float(sc), 2), "Added by you",
                               engine.clip_features(an, start, end, t, pr["chat"], engine.chat_times(pr["chat"]),
                                                    pr["chat_delay"]))
            S._number(pr, [clip], "hype", {c["id"] for c in pr["clips"] + pr.get("gems", [])})
            d = S.clip_dicts(pid, [clip])[0]
            d["manual"] = True
            d["kind"] = "hype"
            d["quality"] = detect.quality(float(sc))
            d["parts"] = detect.breakdown(an, t)
            pr["clips"].append(d)
            learning.record_added(pr.get("streamer", ""))
            S.save_project_soon(pid)
            return self._json(d)

        if p == "/api/check":
            # Content Check review actions on one clip, or a few at once (leave, dismiss, exclude)
            pid = body.get("pid")
            pr = S.projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            cfg = safety.load_config()
            has_audio = pr["info"].get("has_audio", True)
            ids = body["ids"] if isinstance(body.get("ids"), list) else [body.get("id")]
            action = str(body.get("action") or "")
            if len(ids) > 1 and action not in ("leave", "dismiss", "exclude"):
                return self._err("That can't be done to several clips at once.")
            out, fix = [], None
            preview = action == "fix" and bool((body.get("args") or {}).get("preview"))
            for cid in ids:
                d = S._find_clip(pr, cid)
                if not d:
                    continue
                if preview:          # what a fix would do, without doing it
                    d = copy.deepcopy(d)
                if len(ids) == 1 and any(k in body for k in ("start", "title", "state", "words_edit", "postText")):
                    S._apply_clip_state(pr, d, body)       # the clip as it is in the app right now
                if d.get("state") == "kept" and action in ("fix", "undo"):
                    return self._err("This clip is already in the render queue. Remove it from the queue to change it.")
                if not d.get("safety") and action not in ("", "recheck", "exclude"):
                    checkflow.check(d, cfg, has_audio)
                try:
                    fix = checkflow.act(d, action, body.get("args") or {}, cfg, has_audio) if action else None
                except ValueError as e:
                    return self._err(str(e))
                if action in ("", "recheck") or (action in ("fix", "undo") and cfg.get("auto_recheck", True)):
                    checkflow.check(d, cfg, has_audio)
                else:
                    checkflow.refresh(d, cfg, has_audio)
                    if action in ("fix", "undo"):
                        checkflow.mark_changed(d)
                if action == "exclude" and (body.get("args") or {}).get("on", True):
                    if S._drop_from_queue(pid, d["id"]) and d.get("state") == "kept":
                        d["state"] = "todo"
                out.append(S._check_view(d))
            if not pr.get("live") and not preview:
                S.save_project_soon(pid, 0.2)
            return self._json({"clips": out, "fix": fix})

        if p in ("/api/clip_remove", "/api/clip_restore"):
            # F: take a clip out of the project (its source video and rendered files stay), or undo that
            pid = body.get("pid")
            pr = S.projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            cid = body.get("id")
            if p == "/api/clip_remove":
                d = S._find_clip(pr, cid)
                if not d:
                    return self._err("That clip is gone.", 404)
                for key in ("clips", "gems"):
                    if d in pr.get(key, []):
                        pr[key].remove(d)
                pr.setdefault("removed", []).append(d)
                for m in (pr.get("book") or {}).get("moments", []):
                    if m["num"] == d["id"]:
                        m["removed"] = True
                S._drop_from_queue(pid, d["id"])
                checkflow.log(d, "remove", "Removed from the project")
            else:
                d = next((x for x in pr.get("removed", []) if x.get("id") == cid), None)
                if not d:
                    return self._err("That clip can't be brought back.", 404)
                pr["removed"].remove(d)
                key = "gems" if d.get("kind") == "gem" else "clips"
                lst = pr.setdefault(key, [])
                at = next((i for i, c in enumerate(lst) if c.get("score", 0) < d.get("score", 0)), len(lst))
                lst.insert(at, d)
                for m in (pr.get("book") or {}).get("moments", []):
                    if m["num"] == d["id"]:
                        m.pop("removed", None)
                checkflow.log(d, "restore", "Put back in the project")
            pr["clips_ver"] = pr.get("clips_ver", 0) + 1
            if not pr.get("live"):
                S.save_project_soon(pid, 0.2)
            return self._json({"ok": True, "clip": d if p == "/api/clip_restore" else None})

        if p == "/api/preview_render":
            # a few seconds rendered exactly like the export, to check an edit without exporting
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                job = S.start_preview_render(pid, body.get("clip") or {}, body.get("opts") or {}, float(body.get("at", 0)))
            except RuntimeError as e:
                return self._err(str(e))
            return self._json({"job": job["id"]})

        if p == "/api/titles":
            return self._json(S.set_title_prefs(body))
        if p == "/api/cleanup":
            cfg = cleanup.save(body.get("config") or {})
            if body.get("run"):
                S.run_cleanup(wait=True)
            return self._json({"config": cfg, "last": cleanup.last})
        if p == "/api/hashtags":
            hashtags.save({k: v for k, v in body.items() if k in ("file", "post")})
            return self._json(S.hashtags_view(str(body.get("streamer") or "")))

        if p == "/api/framing/track":
            # where the crop will be over a clip (Layout › Follow face), for the preview
            pr = S.projects.get(body.get("pid"))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                a, b = float(body["start"]), float(body["end"])
            except (KeyError, TypeError, ValueError):
                return self._err("Bad clip.")
            ro = S._ropts(dict(body.get("opts") or {}, track="auto"))
            fr = S._framing_for(pr, a, b, ro) if b - a >= 1 else None
            if fr is None:
                return self._json({"path": [], "reason": "Follow face works with the Whole frame layout." if engine.effective_layout(ro) != "full"
                                   else "Face finding isn't available in this copy.", "confidence": 0})
            return self._json(fr)

        if p == "/api/export":
            prefs = exports.save(body)
            S.apply_perf(prefs)
            return self._json(S.export_view(body.get("sample")))

        if p == "/api/streamer":
            # the streamer's name as it should appear in titles (Title B) and descriptions
            pid = body.get("pid")
            pr = S.projects.get(pid)
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:40]
            if name and not re.fullmatch(r"[\w.\- ]{2,40}", name):
                return self._err("Use letters, numbers, spaces, dots, dashes or underscores.")
            pr["streamer_display"] = name
            if not pr.get("live"):
                engine._save_meta(pr["workdir"], streamer_display=name)
            used: set = set()
            for d in pr.get("clips", []) + pr.get("gems", []):
                S._title_clip(pr, d, d.get("words") or [], used)
            if not pr.get("live"):
                S.save_project_soon(pid)
            return self._json({"name": name, "clips": [{k: d.get(k) for k in ("id", "title", "title_a", "title_b", "title_b_named",
                                                                                  "titles", "description", "hashtags", "post")}
                                                         for d in pr.get("clips", []) + pr.get("gems", [])]})

        if p == "/api/clip_state":
            # what you did to a clip in the app (keep, skip, trims, title, captions), saved with the VOD
            pr = S.projects.get(body.get("pid"))
            if not pr:
                return self._err("This session expired. Press Find clips again.", 404)
            d = S._find_clip(pr, body.get("id"))
            if not d:
                return self._err("That clip is gone.", 404)
            S._apply_clip_state(pr, d, body)
            if not pr.get("live"):
                S.save_project_soon(body["pid"], float(body.get("delay", 0.5) or 0))
            return self._json({"ok": True})

        if p == "/api/listen":
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            job = S.start_listening(pid, [int(i) for i in body.get("order", [])],
                                  body.get("model") or "small", body.get("language") or "")
            return self._json({"job": job["id"]})

        if p == "/api/queue/add":
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            try:
                it = S.queue_add(pid, body.get("clip") or {}, body.get("opts") or {}, sound=body.get("sound", True) is not False)
            except (KeyError, TypeError, ValueError):
                return self._err("Bad clip.")
            except RuntimeError as e:
                return self._err(str(e))
            return self._json(S.queue_public(it))

        if p == "/api/queue/remove":
            with S.queue_lock:
                it = next((x for x in S.queue if x["id"] == body.get("id")), None)
                if it and it["status"] == "rendering":
                    it["cancel"] = True          # the worker drops it once ffmpeg stops
                elif it:
                    S.queue.remove(it)
            S.save_queue_soon()
            return self._json({"ok": True})

        if p == "/api/queue/retry":
            n = 0
            with S.queue_lock:
                for it in S.queue:
                    redo = it["status"] == "error" or (it["status"] == "done" and it.get("captions_skipped"))
                    if redo and (body.get("id") in (it["id"], "all")):
                        # Drop stale render state so a retry starts a fresh attempt, not a
                        # half-finished UI lifecycle. Keep the chosen output name for replacement.
                        it.update(status="queued", stage="queued", progress=0.0, error="", detail="", step="Waiting",
                                  cancel=False, eta=None, attempts=it.get("attempts", 0), interrupted=0)
                        it.pop("error_kind", None)
                        it.pop("retryable", None)
                        if it.pop("captions_skipped", None):
                            it.pop("warning", None)
                        n += 1
            S.queue_wake.set()
            S.save_queue_soon()
            if not n:
                return self._err("Nothing to retry.", 404)
            return self._json({"ok": True, "retried": n})

        if p.startswith("/api/test/") and os.environ.get("CLIPRADAR_TEST") == "1":
            act = p[len("/api/test/"):]
            if act in ("fail_next_render", "break_next_output"):
                S.TEST_HOOKS[act] = True
                return self._json({"ok": True})
            if act == "hotkey":
                return self._json({"ok": hotkey.simulate()})
            if act == "yt_hls":
                # stands in for YouTube: live links resolve to this playlist (a local test stream)
                os.environ["CLIPRADAR_TEST_YT_HLS"] = str(body.get("url") or "")
                os.environ["CLIPRADAR_TEST_YT_CHAT"] = str(body.get("chat") or "")      # a recorded live chat to play back
                return self._json({"ok": True})
            return self._err("not found", 404)

        if p == "/api/safety":
            safety.save_config(body or {})
            S.rescan_all()
            return self._json(safety.public_config())
        if p == "/api/safety/scan":
            ws = S._clean_words(body.get("words")) or []
            return self._json(safety.scan(ws, str(body.get("title") or ""), str(body.get("post") or "")))
        if p == "/api/hotkey":
            hotkey.set_enabled(bool(body.get("enabled", True)))
            return self._json(hotkey.status())

        if p == "/api/deepscan":
            pid = body.get("pid")
            if pid not in S.projects:
                return self._err("This session expired. Press Find clips again.", 404)
            if body.get("cancel"):
                j = S.jobs.get(S.projects[pid].get("deep_job", ""))
                if j and j["status"] == "running":
                    j["cancel"] = True
                return self._json(S.deep_status(S.projects[pid]))
            try:
                S.start_deep_scan(pid, body.get("model") or "auto", body.get("language") or "")
            except RuntimeError as e:
                return self._err(str(e))
            return self._json(S.deep_status(S.projects[pid]))

        if p == "/api/feedback":
            action = str(body.get("action") or "")
            if action == "reason":
                pr = S.projects.get(body.get("pid")) or {}
                learning.record_reason(pr.get("streamer", ""), str(body.get("reason") or ""))
                return self._json({"ok": True})
            S.record_feedback(body.get("pid"), body.get("clip"), action)
            return self._json({"ok": True})
        if p == "/api/learning":
            if "enabled" in body:
                learning.set_enabled(bool(body["enabled"]))
            return self._json(learning.summary())
        if p == "/api/learning/forget":
            learning.forget(str(body.get("streamer") or ""))
            S._refresh_live_learning()
            return self._json(learning.summary())

        if p == "/api/twitch/token":
            try:
                return self._json(twitchauth.set_token(str(body.get("token") or "")))
            except ValueError as e:
                return self._err(str(e))
            except RuntimeError as e:
                return self._err(str(e), 502)
        if p == "/api/twitch/logout":
            return self._json(twitchauth.set_token(""))
        if p == "/api/twitch/login":
            if not twitchauth.login_impl[0]:
                return self._err("Signing in through a window needs the Clip Radar app window. Paste your token instead.")
            job = S.new_job("twitchlogin")
            S.run_job(job, lambda: twitchauth.login_with_window())
            return self._json({"job": job["id"]})

        if p == "/api/open_folder":
            folder = body.get("folder", "")
            if folder == "root":
                folder = S.WORKROOT
            elif folder in locations.KEYS:
                folder = locations.path(folder) or S.WORKROOT
            if folder and os.path.isdir(folder) and any(os.path.realpath(folder) == r_ or os.path.realpath(folder).startswith(r_ + os.sep)
                                                        for r_ in S._allowed_roots()):
                try:
                    if sys.platform == "win32":
                        os.startfile(folder)  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", folder])
                    else:
                        subprocess.Popen(["xdg-open", folder])
                except Exception as e:
                    return self._err(str(e), 500)
                return self._json({"ok": True})
            return self._err("Folder not found.", 404)

        return self._err("not found", 404)
