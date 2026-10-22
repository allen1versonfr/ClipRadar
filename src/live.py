"""
Live mode: clip a Twitch stream while it's still going.

  recorder   yt-dlp finds the stream's HLS playlist (with your Twitch sign-in if
             you added one, so Turbo / subs get the ad-free stream). Clip Radar
             then reads the playlist itself, every couple of seconds, and saves
             the video in 10-second pieces. Ad breaks Twitch stitches into the
             stream are recognised and left out, so they never reach a clip.
             If the connection drops it reconnects; when the channel goes
             offline the session ends. (Playlists Clip Radar can't read itself,
             like encrypted ones, are recorded with ffmpeg instead.)
  chat       reads the channel's chat anonymously over Twitch IRC, with
             usernames and sub/raid notices, so bots, commands, floods and
             raids can be played down (see chatsignal.py).
  detector   after every piece it scores loudness, laughter and screams, and
             chat exactly like a VOD, and turns every strong peak into a
             moment once the reaction has died down. There is no limit.
  moments    each moment is cut from the pieces into a small MP4 that the
             rest of Clip Radar treats like a tiny VOD: preview, titles,
             captions and rendering all work on it.
  buffer     only the last few minutes of raw video are kept, so a 10-hour
             stream doesn't fill the disk.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Optional

import numpy as np

import engine

SEG_SECONDS = 10
SR = 8000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")


def _now() -> float:
    return time.time()


def parse_channel(text: str) -> Optional[str]:
    """'twitch.tv/name', 'https://www.twitch.tv/name', '@name' or 'name' -> 'name'."""
    t = text.strip().strip('"').strip()
    m = re.match(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,25})/?(?:\?.*)?$", t)
    if m and m.group(1).lower() not in ("videos", "directory", "settings", "downloads", "p", "search"):
        return m.group(1).lower()
    m = re.match(r"^@?([A-Za-z0-9_]{3,25})$", t)
    if m:
        return m.group(1).lower()
    return None


class NotLive(Exception):
    """The channel isn't streaming (kind "offline") or doesn't exist (kind "missing")."""
    def __init__(self, msg: str, kind: str = "offline"):
        super().__init__(msg)
        self.kind = kind


# ------------------------------------------------------------------ HLS playlists

def _attrs(s: str) -> dict:
    out = {}
    for m in re.finditer(r'([A-Z0-9-]+)=("([^"]*)"|[^,]*)', s):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(2)
    return out


def _epoch(iso: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso.strip().replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def parse_playlist(text: str, base: str) -> dict:
    """An HLS playlist: variants (master) or segments (media), with Twitch's ad markers."""
    pl = {"variants": [], "segments": [], "seq": 0, "target": 2.0, "end": False, "unsupported": "", "ads": []}
    pend: dict = {}
    var = None
    idx = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            var = _attrs(line[18:])
        elif var is not None and not line.startswith("#"):
            res = re.match(r"(\d+)x(\d+)", var.get("RESOLUTION", ""))
            pl["variants"].append({"uri": urllib.parse.urljoin(base, line), "height": int(res.group(2)) if res else 0,
                                   "bw": int(var.get("BANDWIDTH", "0") or 0), "name": var.get("VIDEO", "")})
            var = None
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            pl["seq"] = int(float(line.split(":", 1)[1] or 0))
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            pl["target"] = float(line.split(":", 1)[1] or 2)
        elif line.startswith("#EXT-X-ENDLIST"):
            pl["end"] = True
        elif line.startswith("#EXT-X-KEY:"):
            if _attrs(line[11:]).get("METHOD", "NONE").upper() != "NONE":
                pl["unsupported"] = "encrypted"
        elif line.startswith("#EXT-X-MAP:"):
            pl["unsupported"] = "fragmented MP4"
        elif line == "#EXT-X-DISCONTINUITY":
            pend["disc"] = True
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pend["pdt"] = _epoch(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-DATERANGE:"):
            a = _attrs(line[17:])
            if a.get("CLASS", "") == "twitch-stitched-ad" or a.get("ID", "").startswith("stitched-ad"):
                dur = 0.0
                for k in ("DURATION", "PLANNED-DURATION", "X-TV-TWITCH-AD-POD-LENGTH"):
                    try:
                        dur = float(a.get(k) or 0)
                    except ValueError:
                        dur = 0.0
                    if dur:
                        break
                start = _epoch(a.get("START-DATE", "")) if a.get("START-DATE") else None
                pl["ads"].append((start, dur or 30.0))
                pend["ad_tag"] = dur or 30.0
        elif line.startswith("#EXTINF:"):
            d, _, title = line[8:].partition(",")
            try:
                pend["dur"] = float(d)
            except ValueError:
                pend["dur"] = pl["target"]
            pend["title"] = title.strip()
        elif line.startswith("#"):
            continue                       # #EXT-X-TWITCH-PREFETCH and friends: not real segments yet
        else:
            pl["segments"].append({"seq": pl["seq"] + idx, "uri": urllib.parse.urljoin(base, line),
                                   "dur": pend.get("dur", pl["target"]), "title": pend.get("title", ""),
                                   "disc": pend.get("disc", False), "pdt": pend.get("pdt"),
                                   "ad_tag": pend.get("ad_tag")})
            idx += 1
            pend = {}
    # which segments are ads: Twitch titles them "Amazon|..." (live ones say "live"), and they fall
    # inside a twitch-stitched-ad date range
    clock, prev, left = None, 0.0, 0.0
    for s in pl["segments"]:
        clock = s["pdt"] if s["pdt"] is not None else (clock + prev if clock is not None else None)
        prev = s["dur"]
        t = s["title"].lower()
        ad = "amazon" in t or t.startswith("ad|") or t in ("ad", "advertisement", "commercial")
        if clock is not None and any(a0 is not None and a0 - 0.25 <= clock < a0 + d for a0, d in pl["ads"]):
            ad = True
        if s["ad_tag"]:
            left = s["ad_tag"]
        if clock is None and left > 0.05:
            ad = True                          # no clock in this playlist: trust the ad tag's length
            left -= s["dur"]
        s["ad"] = ad
    return pl


def pick_variant(variants: list, max_height: int) -> str:
    video = [v for v in variants if v["height"] > 0 and "audio" not in v["name"].lower()] or variants
    fit = [v for v in video if not v["height"] or v["height"] <= max_height] or video
    best = max(fit, key=lambda v: (v["height"], v["bw"]))
    return best["uri"]


class LiveSession:
    # how many clips: (score a moment needs, seconds between automatic moments)
    FREQUENCY = {"fewer": (4.0, 120.0), "normal": (3.0, 45.0), "more": (2.4, 20.0), "lots": (1.9, 0.0)}
    ALIASES = {"low": "fewer", "high": "more"}          # names used before 3.0
    SENSITIVITY = {"low": 4.0, "normal": 3.0, "high": 2.4, "fewer": 4.0, "more": 2.4, "lots": 1.9}
    WAIT_POLL = float(os.environ.get("CLIPRADAR_WAIT_POLL", "30"))
    STALL = 45.0
    DUP_GAP = 20.0          # two automatic moments with peaks this close are the same moment
    CHAT_QUIET = 120.0      # chat that went silent this long (after being busy) is treated as gone
    DELAY_EVERY = 120       # re-work-out the chat delay every this many recorded seconds
    LAT_STAGES = ("ingest", "detect", "context", "cut", "title", "render", "total")

    def __init__(self, sid: str, source: str, workroot: str, *, temp_root: str = "", length: float = 0, sensitivity: str = "normal",
                 chat_delay="auto", chat_weight: float = 60, max_height: int = 1080, cookies: str = "",
                 warmup: float = 90, buffer_minutes: float = 15, wait_for_live: bool = False,
                 events: bool = True, on_moment: Callable = None, log: Callable = None, min_quality: int = 0):
        self.id = sid
        self.source = source.strip()
        import sources
        self.origin = sources.classify(self.source)
        self.channel = parse_channel(self.source)
        self.direct = bool(re.search(r"\.m3u8(\?|$)", self.source)) or os.path.isfile(self.source)
        # YouTube livestreams (a video link, youtube.com/live/…, or a channel's /live page) go through yt-dlp
        self.via_ytdlp = not self.channel and not self.direct and self.origin["platform"] == "youtube" \
            and self.origin["kind"] in ("video", "live", "vod")
        if not self.channel and not self.direct and not self.via_ytdlp:
            raise ValueError("Enter a Twitch channel (twitch.tv/name) or a YouTube livestream link.")
        label = self.channel or (re.sub(r"[^\w-]", "", self.origin.get("id") or "")[:24] if self.via_ytdlp else "") or "stream"
        self.title = f"{label} (live)"
        stamp = time.strftime("%Y-%m-%d %H-%M")
        self.dir = os.path.join(workroot, f"live_{label}_{stamp}")
        n = 2
        while os.path.exists(self.dir):           # two sessions in the same minute get their own folders
            self.dir = os.path.join(workroot, f"live_{label}_{stamp} ({n})")
            n += 1
        # the rolling recording buffer is temporary (Settings › Storage › Temporary files); moments stay in the project
        self.seg_dir = os.path.join(temp_root, os.path.basename(self.dir), "segments") if temp_root else os.path.join(self.dir, "segments")
        self.temp_buffer = bool(temp_root)
        self.moment_dir = os.path.join(self.dir, "moments")
        os.makedirs(self.seg_dir, exist_ok=True)
        os.makedirs(self.moment_dir, exist_ok=True)
        engine._save_meta(self.dir, title=self.title, source=self.source, live=True, streamer=self.channel or "")

        self.length = float(length or 0)          # 0 = each moment gets its own length (15-90 s)
        self.set_frequency(sensitivity)
        self.wait_for_live = bool(wait_for_live)
        self.waited = 0.0
        # chat reacts a few seconds after the picture; "auto" works it out from the stream as it goes
        self.delay_auto = chat_delay in (None, "", "auto")
        self.chat_delay = 6.0 if self.delay_auto else float(chat_delay)
        self.delay_sure = not self.delay_auto
        self._delay_at = 0
        self.min_quality = int(min(max(int(min_quality or 0), 0), 95))
        self.chat_weight = float(chat_weight)
        self.max_height = int(max_height)
        self.cookies = cookies
        self.warmup = float(warmup)
        self.buffer_s = max(1.0, float(buffer_minutes)) * 60
        self.on_moment = on_moment or (lambda *a, **k: None)
        self._log = log or (lambda m: None)

        self.status = "starting"          # starting | waiting | recording | reconnecting | ended | stopped | error
        self.error = ""
        self.started = _now()
        self.segments: list = []               # {path, start, dur, name}
        self.loud: list = []                   # dB per second
        self.fine: list = []                   # dB per 0.1 s
        self._pending_audio = np.zeros(0, dtype=np.float32)
        self.chat: list = []                   # (media seconds, text, user, kind), recent part
        self.chat_lock = threading.Lock()
        import chatsignal
        self.scorer = chatsignal.ChatScorer(engine.chat_weight)
        self._raw: list = []                   # chat reaction per media second it arrived (weighed, not shifted)
        self.emote_set = None                  # the channel's 7TV/BTTV/FFZ emotes, once loaded (server.load_emotes)
        self._emote_raw: list = []             # chatters reacting with an emote, per media second (like VODs)
        self._emote_last: dict = {}            # user -> media time their emote last counted (once per 8 s)
        self._last_chat: Optional[float] = None   # wall clock of the last chat message
        self._busy_chat = False                # chat has been active this session
        self._arrive: list = []                # (media end, wall clock, seconds spent measuring) per piece
        self._cand: dict = {}                  # peak -> timings while a moment forms
        self.forming: dict = {}                # peak -> what the UI shows while a moment forms
        self.lat: dict = {k: [] for k in self.LAT_STAGES}   # recent latencies per stage (seconds)
        self.last_lat: dict = {}
        self.skipped = {"repeat": 0, "confidence": 0, "learned": 0, "crowd": 0}
        self.taken: list = []                  # (start, end) already clipped
        self.auto_peaks: list = []             # peaks of automatic moments (for spacing)
        self.pending: list = []                # peaks waiting for their reaction to finish
        self.manual: list = []                 # requested "clip that" ranges
        self.moments: list = []                # project ids, filled in by the server
        self.score = np.zeros(0, dtype=np.float32)
        self.stop_flag = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._run = 0
        self._seg_number = 0
        self._moment_n = 0
        self.log_lines: list = []
        self.irc_ok = False
        self.mode = ""                         # "hls" (Clip Radar reads the playlist) or "ffmpeg"
        self.in_ad = False
        self.ads_skipped = 0.0                 # seconds of ads left out of the recording
        self.ad_breaks = 0
        self.signed_in = False
        self._media = 0.0                      # media seconds written (including the piece being written)
        self._anchor: Optional[tuple] = None   # (wall clock, media seconds) when media last arrived
        self._slack = 3.0
        self._scan_from = 0
        self.adjust = None                     # learned from your keeps and skips (set by the server)
        self.events = None
        if events:
            try:
                import audio_events
                if audio_events.available():
                    self.events = audio_events.Scanner()
            except Exception:
                self.events = None

    # ------------------------------------------------------------------ utils
    def set_frequency(self, level: str):
        level = self.ALIASES.get(level, level)
        if level not in self.FREQUENCY:
            level = "normal"
        self.frequency = level
        self.sensitivity = level
        self.threshold, self.min_gap = self.FREQUENCY[level]

    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        self.log_lines = (self.log_lines + [line])[-200:]
        self._log(msg)

    @property
    def recorded(self) -> float:
        return float(len(self.loud))

    def live_edge(self) -> float:
        """Media time of 'now' (what viewers are seeing right now). Ads are left out of the media
        timeline, so this pauses during an ad break."""
        if self._anchor is None:
            return 0.0
        wall, media = self._anchor
        if self.in_ad:
            return media
        return media + min(max(0.0, _now() - wall), self._slack)

    def _note_media(self, seconds: float):
        self._media += seconds
        self._anchor = (_now(), self._media)

    def public(self) -> dict:
        n = len(self.score)
        pts = 900
        if n > pts:
            edges = np.linspace(0, n, pts + 1).astype(int)
            curve = [round(float(self.score[a:b].max()), 2) for a, b in zip(edges[:-1], edges[1:])]
        else:
            curve = [round(float(x), 2) for x in self.score]
        edge = self.live_edge()
        with self.chat_lock:
            recent = sum(1 for m in self.chat[-3000:] if m[0] >= edge - 60 and not m[3])
            total_chat = self.scorer.stats["messages"]
        return {
            "id": self.id, "channel": self.channel, "title": self.title, "source": self.source,
            "status": self.status, "error": self.error, "recorded": self.recorded,
            "edge": edge, "started": self.started, "curve": curve,
            "chat_per_min": recent, "chat_total": total_chat, "chat_connected": self.irc_ok,
            "chatters": self.scorer.chatters, "chat_ignored": self.scorer.summary()["ignored"],
            "moments": list(self.moments), "length": self.length, "sensitivity": self.sensitivity,
            "frequency": self.frequency, "wait_for_live": self.wait_for_live,
            "pending_manual": len(self.manual), "log": self.log_lines[-12:], "dir": self.dir, "seg_dir": self.seg_dir,
            "warming_up": self.recorded < self.warmup, "in_ad": self.in_ad,
            "ads_skipped": round(self.ads_skipped, 1), "ad_breaks": self.ad_breaks, "mode": self.mode,
            "signed_in": self.signed_in, "laughter": self.events is not None,
            "chat_state": self.chat_state(), "chat_delay": self.chat_delay, "delay_sure": self.delay_sure,
            "delay_auto": self.delay_auto, "min_quality": self.min_quality,
            "forming": sorted(self.forming.values(), key=lambda f: f["at"]),
            "latency": self.latency_summary(), "latency_last": dict(self.last_lat), "skipped": dict(self.skipped),
        }

    # ------------------------------------------------------------------ latency and chat health
    def note_latency(self, stage: str, seconds: Optional[float]):
        if seconds is None or stage not in self.lat or not (0 <= seconds < 36000):
            return
        self.lat[stage] = (self.lat[stage] + [round(float(seconds), 2)])[-30:]

    def latency_summary(self) -> dict:
        """Typical (median) seconds each stage takes, over the last moments."""
        return {k: round(float(np.median(v)), 1) for k, v in self.lat.items() if v}

    def _arrival(self, t: float) -> Optional[tuple]:
        """(wall clock it was recorded, media end of its piece, seconds measuring) for media time t."""
        import bisect
        arr = self._arrive
        i = bisect.bisect_left([a[0] for a in arr], t)
        return (arr[i][1], arr[i][0], arr[i][2]) if i < len(arr) else None

    def chat_state(self) -> str:
        """none (no chat for this stream), offline (can't connect), quiet (went silent after being
        busy: probably delayed or dropped) or ok."""
        if not self.channel:
            return "none"
        if not self.irc_ok:
            return "offline"
        if self._busy_chat and self._last_chat is not None and _now() - self._last_chat > self.CHAT_QUIET:
            return "quiet"
        return "ok"

    def chat_usable(self) -> bool:
        return self.chat_state() == "ok"

    # ------------------------------------------------------------------ control
    def start(self):
        threading.Thread(target=self._record_loop, daemon=True, name=f"live-rec-{self.id}").start()
        if self.channel:
            threading.Thread(target=self._chat_loop, daemon=True, name=f"live-chat-{self.id}").start()

    def stop(self):
        self.stop_flag.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                # ask ffmpeg to finish the current segment cleanly
                p.stdin.write(b"q")
                p.stdin.flush()
            except Exception:
                pass
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()

    def clip_now(self, seconds: float = 30.0) -> float:
        end = self.live_edge() if self._anchor else self.recorded
        self.manual.append((max(0.0, end - seconds), end))
        self.log(f"Clip requested: last {int(seconds)}s")
        return end

    # ------------------------------------------------------------------ recording
    def _resolve_ytdlp(self) -> str:
        """A YouTube livestream's playlist address (it expires after some hours; reconnecting asks again)."""
        import sources
        test = os.environ.get("CLIPRADAR_TEST_YT_HLS") if os.environ.get("CLIPRADAR_TEST") else ""
        if test:
            info = {"live_status": "is_live", "url": test, "title": "Test livestream", "uploader": "testchannel"}
        else:
            fmt = f"best[height<={self.max_height}][protocol^=m3u8]/best[height<={self.max_height}]/best"
            exe = engine.which("yt-dlp")
            info, err = None, ""
            if exe:
                cmd = [exe, "-J", "-f", fmt, "--no-warnings", "--ignore-config", "--no-playlist"]
                if self.cookies:
                    cmd += ["--cookies-from-browser", self.cookies]
                p = subprocess.run(cmd + [self.source], capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL,
                                   encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
                if p.returncode == 0 and p.stdout.strip().startswith("{"):
                    try:
                        info = json.loads(p.stdout)
                    except ValueError:
                        info = None
                err = (p.stderr or p.stdout or "").strip()
            else:
                try:
                    import yt_dlp
                    with yt_dlp.YoutubeDL({"format": fmt, "quiet": True, "no_warnings": True, "noplaylist": True}) as y:
                        info = y.extract_info(self.source, download=False)
                except Exception as e:
                    err = str(e)
            if info is None:
                msg = sources.explain(err, "youtube")
                if re.search(r"hasn't started|not live|offline|premieres", msg + err, re.I):
                    raise NotLive("That livestream hasn't started yet.")
                if re.search(r"isn't available|private|removed", msg, re.I):
                    raise NotLive(msg, kind="missing")
                raise RuntimeError(msg)
        ls = info.get("live_status") or ("is_live" if info.get("is_live") else "")
        if ls == "is_upcoming":
            raise NotLive("That livestream hasn't started yet.")
        if ls != "is_live":
            raise NotLive("That video isn't live (it's an ordinary video or the stream has ended). Open it from Past broadcast instead.",
                          kind="ended" if ls in ("was_live", "post_live") else "missing")
        if info.get("uploader") and self.title.endswith("(live)") and not self.segments:
            self.title = f"{info['uploader']} (live)"
            engine._save_meta(self.dir, title=self.title, source=self.source, live=True, streamer=str(info.get("uploader_id") or "").lower(),
                              platform="youtube", kind="live", label="YouTube livestream", uploader=info.get("uploader", ""))
        url = info.get("url") or next((f.get("url") for f in reversed(info.get("requested_formats") or info.get("formats") or [])
                                       if f.get("url") and "m3u8" in str(f.get("protocol", ""))), "")
        if not url:
            raise RuntimeError("couldn't get the livestream's address")
        return url

    def _resolve(self) -> str:
        if self.via_ytdlp:
            return self._resolve_ytdlp()
        if self.direct:
            if self.source.startswith("http"):
                try:
                    with urllib.request.urlopen(urllib.request.Request(self.source, headers={"User-Agent": UA}),
                                                timeout=15) as r:
                        if b"#EXT-X-ENDLIST" in r.read():
                            raise NotLive("The stream has ended.")
                except NotLive:
                    raise
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        raise NotLive("The stream isn't live yet.")
                    raise RuntimeError(f"couldn't load the playlist ({e})")
                except Exception as e:
                    raise RuntimeError(f"couldn't load the playlist ({e})")
            if os.path.isfile(self.source):
                with open(self.source, encoding="utf-8", errors="replace") as f:
                    if "#EXT-X-ENDLIST" in f.read():
                        raise NotLive("The stream has ended.")
            return self.source
        import twitchauth
        url = f"https://www.twitch.tv/{self.channel}"
        fmt = f"best[height<={self.max_height}]/best"
        exe = engine.which("yt-dlp")
        err = ""
        with twitchauth.cookie_file() as cookie:
            self.signed_in = bool(cookie)
            if exe:
                cmd = [exe, "-g", "-f", fmt, "--no-warnings", "--ignore-config", url]
                if cookie:
                    cmd[1:1] = ["--cookies", cookie]
                elif self.cookies:
                    cmd[1:1] = ["--cookies-from-browser", self.cookies]
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                                   encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
                out = [ln for ln in p.stdout.splitlines() if ln.startswith("http")]
                if p.returncode == 0 and out:
                    return out[0]
                err = (p.stderr or p.stdout).strip()
            else:
                try:
                    import yt_dlp
                    opts = {"format": fmt, "quiet": True, "no_warnings": True}
                    if cookie:
                        opts["cookiefile"] = cookie
                    elif self.cookies:
                        opts["cookiesfrombrowser"] = (self.cookies,)
                    with yt_dlp.YoutubeDL(opts) as y:
                        info = y.extract_info(url, download=False)
                    if info.get("url"):
                        return info["url"]
                except Exception as e:
                    err = str(e)
        if re.search(r"not currently live|is offline|offline", err, re.I):
            raise NotLive(f"{self.channel} isn't live right now.")
        if re.search(r"does not exist|404", err, re.I):
            raise NotLive(f"Couldn't find a Twitch channel called {self.channel}.", kind="missing")
        raise RuntimeError(engine._clean_ytdlp_error(err) if err else "couldn't get the stream address")

    def _safe_detect(self, final: bool = False):
        """Finding moments must never stop the recording: log what went wrong and carry on."""
        try:
            self._detect(final)
            if not final:
                self._cleanup()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log(f"Moment detection hit a problem ({str(e)[:120]}); still recording")

    def _record_loop(self):
        try:
            self._record_loop_inner()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.status = "error"
            self.error = f"Recording stopped unexpectedly: {str(e)[:200]}"
            self.log(self.error)

    def _record_loop_inner(self):
        backoff = 3
        use_ffmpeg = False
        while not self.stop_flag.is_set():
            try:
                hls = self._resolve()
            except NotLive as e:
                if self.segments:
                    self.status = "ended"
                    self.log(f"Stream ended. {e}")
                elif self.wait_for_live and e.kind == "offline":
                    # hands-free: sit and wait for the stream to start
                    if self.status != "waiting":
                        self.log(f"{self.channel or 'The stream'} isn't live yet. Checking every {int(self.WAIT_POLL)}s.")
                    self.status = "waiting"
                    t = _now()
                    if self.stop_flag.wait(self.WAIT_POLL):
                        break
                    self.waited += _now() - t
                    continue
                else:
                    self.status = "error"
                    self.error = str(e)
                    self.log(str(e))
                break
            except Exception as e:
                self.status = "reconnecting" if self.segments else "starting"
                self.log(f"Couldn't reach the stream ({str(e)[:120]}), retrying in {backoff}s")
                if self.stop_flag.wait(backoff):
                    break
                backoff = min(60, backoff * 2)
                if not self.segments and _now() - self.started - self.waited > 180:
                    self.status = "error"
                    self.error = f"Couldn't start recording: {str(e)[:200]}"
                    break
                continue
            backoff = 3
            self._run += 1
            how = "fallback" if use_ffmpeg or not hls.startswith("http") else self._run_hls(hls)
            if how == "fallback":
                use_ffmpeg = True
                self._run_ffmpeg(hls)
            if self.stop_flag.is_set():
                break
            if how == "ended":
                self.status = "ended"
                self.log("Stream ended.")
                break
            self.status = "reconnecting"
            self.log("Lost the stream, reconnecting...")
            self.stop_flag.wait(2)
        if self.status not in ("ended", "error"):
            self.status = "stopped"
        self.in_ad = False
        self._ingest_new(final=True)  # pick up the final segment (ffmpeg mode)
        self._safe_detect(final=True)
        if self.temp_buffer:
            self.delete_buffer()                 # every moment is cut out by now; the raw buffer was temporary
        self.log("Recording stopped.")

    # ---- Clip Radar's own HLS reader (skips ads)
    def _get(self, url: str, timeout: float = 15) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def _run_hls(self, url: str) -> str:
        """Record until the stream is lost ("lost"), ends ("ended") or can't be read ("fallback")."""
        self.mode = "hls"
        self._slack = 3.0
        piece = None                  # {"f", "path", "dur"}
        last_seq = None
        last_new = _now()
        errors = 0
        ad_run = 0.0
        self.log("Recording" if self._run == 1 else "Reconnected")

        def close_piece():
            nonlocal piece
            if piece is None:
                return
            piece["f"].close()
            if piece["dur"] > 0.05:
                self._add_segment(piece["path"], piece["dur"])
                self.status = "recording"
                self._safe_detect()
            else:
                try:
                    os.remove(piece["path"])
                except OSError:
                    pass
            piece = None

        try:
            while not self.stop_flag.is_set():
                try:
                    text = self._get(url).decode("utf-8", "replace")
                    errors = 0
                except urllib.error.HTTPError as e:
                    if e.code in (403, 404, 410):
                        return "lost"
                    errors += 1
                    if errors > 6:
                        return "lost"
                    self.stop_flag.wait(1.5)
                    continue
                except Exception:
                    errors += 1
                    if errors > 6:
                        return "lost"
                    self.stop_flag.wait(1.5)
                    continue
                if "#EXTM3U" not in text[:200]:
                    return "fallback"
                pl = parse_playlist(text, url)
                if pl["variants"] and not pl["segments"]:
                    url = pick_variant(pl["variants"], self.max_height)
                    continue
                if pl["unsupported"]:
                    self.log(f"This stream is {pl['unsupported']}; recording it with ffmpeg instead.")
                    return "fallback"
                new = [s for s in pl["segments"] if last_seq is None or s["seq"] > last_seq]
                if last_seq is None and len(new) > 3:
                    new = new[-3:]                       # start close to live, like a player does
                if new and last_seq is not None and new[0]["seq"] > last_seq + 1:
                    close_piece()                        # we missed some: don't glue across the gap
                for s in new:
                    last_seq = s["seq"]
                    last_new = _now()
                    if self.stop_flag.is_set():
                        break
                    if s["ad"]:
                        if not self.in_ad:
                            self.in_ad = True
                            self.ad_breaks += 1
                            ad_run = 0.0
                            close_piece()
                            self.log("Ad break in the stream: leaving it out of the recording")
                        ad_run += s["dur"]
                        self.ads_skipped += s["dur"]
                        continue
                    if self.in_ad:
                        self.in_ad = False
                        self.log(f"Ad break over ({ad_run:.0f}s left out)")
                        self._anchor = (_now(), self._media)
                    if s["disc"]:
                        close_piece()
                    try:
                        data = self._get(s["uri"], timeout=20)
                    except Exception:
                        try:
                            data = self._get(s["uri"], timeout=20)
                        except Exception:
                            close_piece()                # a hole: start a fresh piece after it
                            continue
                    if piece is None:
                        name = f"seg_{self._seg_number:06d}.ts"
                        self._seg_number += 1
                        path = os.path.join(self.seg_dir, name)
                        piece = {"f": open(path, "wb"), "path": path, "dur": 0.0}
                    piece["f"].write(data)
                    piece["dur"] += s["dur"]
                    self._note_media(s["dur"])
                    if self.status != "recording":
                        self.status = "recording"
                    if piece["dur"] >= SEG_SECONDS - 0.05:
                        close_piece()
                if pl["end"]:
                    close_piece()
                    return "ended"
                if _now() - last_new > self.STALL:
                    self.log(f"No new video for {int(self.STALL)}s, reconnecting")
                    return "lost"
                self.stop_flag.wait(max(0.5, min(2.0, pl["target"] / 2)))
            return "stopped"
        finally:
            close_piece()

    def _add_segment(self, path: str, dur: float):
        start = self.segments[-1]["start"] + self.segments[-1]["dur"] if self.segments else 0.0
        self.segments.append({"path": path, "start": start, "dur": dur, "name": os.path.basename(path)})
        t0 = _now()
        self._measure(path, dur)
        self._arrive = (self._arrive + [(start + dur, _now(), _now() - t0)])[-4000:]

    # ---- ffmpeg recorder (for playlists Clip Radar doesn't read itself)
    def _run_ffmpeg(self, hls: str) -> bool:
        self.mode = "ffmpeg"
        self._slack = SEG_SECONDS + 5.0
        cmd = [engine.ffmpeg(), "-hide_banner", "-loglevel", "error"]
        if hls.startswith("http"):
            cmd += ["-rw_timeout", "20000000", "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"]
        cmd += ["-i", hls, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEG_SECONDS), "-reset_timestamps", "1",
                "-segment_start_number", str(self._seg_number),
                os.path.join(self.seg_dir, "seg_%06d.ts")]
        self.log("Recording" if self._run == 1 else "Reconnected")
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=engine.CREATE_NO_WINDOW)
        self._proc = p
        if self._anchor is None:
            self._anchor = (_now(), self._media)
        err_tail: list = []

        def read_err():
            for line in iter(p.stderr.readline, b""):
                err_tail.append(line.decode("utf-8", "replace").strip())
                del err_tail[:-6]
        threading.Thread(target=read_err, daemon=True).start()

        last_growth, last_size = time.time(), -1
        while p.poll() is None and not self.stop_flag.is_set():
            got = self._ingest_new()
            if got:
                self.status = "recording"
                self._safe_detect()
            # watchdog: if no new video arrives for a while, the connection is stuck; reconnect
            size = self._newest_size()
            if size != last_size:
                last_size, last_growth = size, time.time()
            elif time.time() - last_growth > self.STALL:
                self.log(f"No new video for {int(self.STALL)}s, reconnecting")
                p.kill()
                break
            time.sleep(0.5)
        if p.poll() is None:
            self.stop()
        self._ingest_new(final=True)
        if err_tail and not self.stop_flag.is_set():
            self.log("ffmpeg: " + " / ".join(err_tail[-2:])[:200])
        return False

    def _ingest_new(self, final: bool = False) -> int:
        """ffmpeg mode: pick up segments ffmpeg has finished. A segment is finished once the next one
        exists (or ffmpeg has exited). This doesn't depend on ffmpeg's list file, whose flushing differs
        between ffmpeg builds and platforms."""
        if self.mode != "ffmpeg":
            return 0
        try:
            names = [f for f in os.listdir(self.seg_dir) if re.fullmatch(r"seg_\d{6}\.ts", f)]
        except OSError:
            return 0
        nums = sorted(int(f[4:10]) for f in names if int(f[4:10]) >= self._seg_number)
        proc_done = self._proc is None or self._proc.poll() is not None
        if not (final or proc_done) and nums:
            nums = nums[:-1]                  # the newest one is still being written
        added = 0
        for num in nums:
            name = f"seg_{num:06d}.ts"
            seg_path = os.path.join(self.seg_dir, name)
            dur = self._duration(seg_path)
            if dur <= 0.05:
                self._seg_number = num + 1   # empty/broken segment: skip it
                continue
            self._seg_number = num + 1
            self._add_segment(seg_path, dur)
            self._media = self.segments[-1]["start"] + dur
            self._anchor = (_now(), self._media)
            added += 1
        return added

    def _newest_size(self) -> int:
        try:
            files = [f for f in os.listdir(self.seg_dir) if f.endswith(".ts")]
            if not files:
                return 0
            newest = max(files)
            return len(files) * 10**12 + os.path.getsize(os.path.join(self.seg_dir, newest))
        except OSError:
            return 0

    def _duration(self, path: str) -> float:
        try:
            p = subprocess.run([engine.ffprobe(), "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                               creationflags=engine.CREATE_NO_WINDOW)
            return float(p.stdout.strip() or 0)
        except Exception:
            return 0.0

    def _measure(self, seg_path: str, dur: float):
        """Loudness (per second and per 0.1 s) and, when available, laughter / screams for a piece."""
        sr = 32000 if self.events is not None else SR
        cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-i", seg_path, "-map", "0:a:0?", "-vn",
               "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                               creationflags=engine.CREATE_NO_WINDOW)
            pcm = np.frombuffer(p.stdout[: len(p.stdout) // 4 * 4], dtype=np.float32) if p.returncode == 0 \
                else np.zeros(0, dtype=np.float32)
        except Exception:
            pcm = np.zeros(0, dtype=np.float32)
        want = int(round(dur * sr))
        if pcm.size < want * 0.5:              # no audio in this piece (or unreadable)
            pcm = np.zeros(want, dtype=np.float32)
        pcm = np.pad(pcm[:want], (0, max(0, want - pcm.size)))
        if self.events is not None:
            try:
                self.events.feed(pcm)
            except Exception as e:
                self.log(f"Laughter detection stopped: {str(e)[:100]}")
                self.events = None
        buf = np.concatenate([self._pending_audio, pcm])
        blk = sr // engine.FINE_HZ
        whole = buf.size // blk
        if whole:
            pw = np.mean(buf[: whole * blk].reshape(whole, blk) ** 2, axis=1)
            self.fine.extend((10 * np.log10(np.maximum(pw, 1e-10))).tolist())
            secs = len(self.fine) // engine.FINE_HZ
            while len(self.loud) < secs:
                i = len(self.loud)
                p10 = 10 ** (np.asarray(self.fine[i * engine.FINE_HZ:(i + 1) * engine.FINE_HZ]) / 10)
                self.loud.append(float(10 * np.log10(max(float(p10.mean()), 1e-10))))
        self._pending_audio = buf[whole * blk:]

    # ------------------------------------------------------------------ detection
    def _analysis(self, n: int):
        import detect
        loud = np.asarray(self.loud[:n], dtype=np.float32)
        ev = self.events.result(n) if self.events is not None else None
        with self.chat_lock:
            raw = np.asarray(self._raw[: n + 20], dtype=np.float32)
            emo = np.asarray(self._emote_raw[: n + 20], dtype=np.float32) if self.emote_set is not None else None
        if self.delay_auto and n >= 300 and n - self._delay_at >= self.DELAY_EVERY:
            # how late chat reacts, worked out again as the stream goes on
            self._delay_at = n
            lag, sure = detect.estimate_delay(engine._fit(raw[:n], n), engine._audio_z(loud, n), ev, self.chat_delay)
            if sure and (not self.delay_sure or abs(lag - self.chat_delay) >= 1):
                self.log(f"Chat reacts about {lag:.0f} s after the stream")
            if sure:
                self.chat_delay, self.delay_sure = lag, True
        rate = engine._fit(detect.shift(engine._fit(raw, n + 20), self.chat_delay)[:n], n)
        # chat that dropped or went quiet: rank by what's on stream instead of waiting for chat
        use_chat = self.chat_usable()
        cw = self.chat_weight if use_chat else 0.0
        if not use_chat:
            rate = np.zeros(n, dtype=np.float32)
        an = engine.score_signals(loud, [], n, float(n), self.chat_delay,
                                  cw, rate=rate, stats=self.scorer.summary(), events=ev,
                                  fine=np.asarray(self.fine[: n * engine.FINE_HZ], dtype=np.float32))
        an.surge = detect.surge_signal(rate) if an.has_chat else None
        if emo is not None and use_chat:
            # emote waves count for live the same way they do for VODs
            an.emote_rate = engine._fit(detect.shift(engine._fit(emo, n + 20), self.chat_delay)[:n], n)
            an.emote_z = detect.emote_signal(an.emote_rate)
        an.react = detect.react_signal(an)
        an.noise = detect.noise_mult(an)
        an.combine(cw, getattr(self, "weights", None) or detect.preset("any"))
        return an

    def _detect(self, final: bool = False):
        import detect
        n = len(self.loud)
        if n < 5:
            return
        an = self._analysis(n)
        self.score = an.score
        with self.chat_lock:
            chat = list(self.chat)
        # manual "clip that" requests whose end has been recorded
        for req in list(self.manual):
            a, b = req
            if b <= n or final:
                self.manual.remove(req)
                b = min(b, n)
                a = max(0.0, min(a, b - 3))
                peak = (a + b) / 2
                self._make_moment(a, b, peak, "Clipped by you", an, chat, manual=True)
        if n < self.warmup and not final:
            return
        s = an.score
        win = 12
        limit = n if final else n - 2
        lo = max(0, int(self._scan_from))
        gap = max(self.min_gap, 15.0)
        scanned = lo
        edge = min(60.0, self.warmup)          # the first minute: "starting soon", chat saying hi
        for i in range(lo, max(lo, limit)):
            if s[i] < self.threshold or i < edge:
                scanned = i + 1
                continue
            a, b = max(0, i - win), min(n, i + win + 1)
            if b < i + win + 1 and not final:
                # can't tell yet whether this is the top of the peak; show it as forming meanwhile
                j = a + int(np.argmax(s[a:n]))
                if not any(abs(j - p) < max(self.min_gap, self.DUP_GAP) for p in self.auto_peaks + self.pending) \
                        and not any(ts <= j <= te for ts, te in self.taken):
                    prev = next((k for k, f in self.forming.items() if f.get("early") and abs(k - j) <= win), None)
                    since = self.forming.pop(prev)["since"] if prev is not None else _now()
                    self.forming[j] = {"at": float(j), "quality": detect.quality(float(s[j])), "since": since, "early": True}
                break
            scanned = i + 1
            if s[i] < s[a:b].max():
                continue
            if any(abs(i - p) < max(self.min_gap, self.DUP_GAP) for p in self.auto_peaks) or \
                    any(abs(i - p) < gap for p in self.pending) or any(ts <= i <= te for ts, te in self.taken):
                continue
            self.pending.append(i)
            # a candidate: shown right away, cut once the reaction has played out
            early = next((k for k, f in self.forming.items() if f.get("early") and abs(k - i) <= win), None)
            now = self.forming.pop(early)["since"] if early is not None else _now()
            arr = self._arrival(i + 1)
            aired = arr[0] - (arr[1] - i) - arr[2] if arr else None
            self._cand[i] = {"aired": aired, "arrived": arr[0] if arr else None, "found": now}
            self.forming[i] = {"at": float(i), "quality": detect.quality(float(s[i])), "since": now, "early": False}
        # scores near the live edge still change as more arrives, so look at the last minute again next time
        self._scan_from = max(0, scanned - 60)
        chat_t = engine.chat_times(chat)
        # strongest first: when moments compete for the same stretch (or the PC is busy), the best one wins
        for p in sorted(self.pending, key=lambda q: -float(s[min(q, len(s) - 1)])):
            if self.length > 0:
                st, en = engine.fixed_bounds(an, p, self.length, 0.6)
                done = final or en <= n - 1
            else:
                st, en, done = engine.smart_bounds(an, p, avail=n, final=final)
            if not done:
                continue
            self.pending.remove(p)
            self.forming.pop(p, None)
            cand = self._cand.pop(p, {})
            if any(st < te - 2 and en > ts + 2 for ts, te in self.taken) or \
                    any(abs(p - q) < max(self.min_gap, self.DUP_GAP) for q in self.auto_peaks):
                self.skipped["repeat"] += 1
                self.log(f"Skipped {engine.fmt_ts(p)}: same moment as one already clipped")
                continue
            adj = 0.0
            if self.adjust is not None:
                try:
                    adj = float(self.adjust([engine.clip_features(an, st, en, p, chat, chat_t, self.chat_delay)])[0])
                except Exception:
                    adj = 0.0
                if s[p] + adj < self.threshold:
                    self.skipped["learned"] += 1
                    self.log(f"Passed on a moment at {engine.fmt_ts(p)}: not like the clips you usually keep")
                    continue
            if self.chat_usable() and not self._crowd_ok(an, p, chat):
                self.skipped["crowd"] = self.skipped.get("crowd", 0) + 1
                self.log(f"Passed on {engine.fmt_ts(p)}: only chat, and only one or two people")
                continue
            q = detect.quality(float(s[p]) + adj)
            if q < self.min_quality:
                self.skipped["confidence"] += 1
                self.log(f"Passed on a moment at {engine.fmt_ts(p)}: score {q}, under your minimum of {self.min_quality}")
                continue
            now = _now()
            lat = {}
            if cand.get("aired") is not None:
                lat["ingest"] = cand["arrived"] - cand["aired"]
                # spotted as forming before its peak had even arrived: no time lost spotting it
                lat["detect"] = max(0.0, cand["found"] - cand["arrived"])
                lat["context"] = now - max(cand["found"], cand["arrived"])
            for k, v in lat.items():
                self.note_latency(k, v)
            self.auto_peaks.append(p)
            self._make_moment(st, en, float(p), engine._reason(an, p, chat, chat_t, self.chat_delay), an, chat,
                              lat=lat, aired=cand.get("aired"))
        # candidates that were dropped before they became moments
        for p in list(self.forming):
            if p not in self.pending and not (self.forming[p].get("early") and not final):
                self.forming.pop(p, None)
                self._cand.pop(p, None)

    def _crowd_ok(self, an, p: float, chat: list) -> bool:
        """A moment carried by chat alone needs a crowd: more people than usual reacting, not one
        or two spamming."""
        import detect
        bd = detect.breakdown(an, p)
        total = sum(v for k, v in bd.items() if k not in ("noise", "tuned") and v > 0)
        chatty = sum(bd.get(k, 0) for k in ("chat", "emotes", "surge") if bd.get(k, 0) > 0)
        if total <= 0 or chatty < 0.75 * total:
            return True                                  # the stream itself backs it up
        d = self.chat_delay

        def people(a, b):
            return len({m[2] for m in chat if a <= m[0] < b and not m[3]})
        now = people(p + d - 2, p + d + 12)
        base = [people(t, t + 14) for t in np.arange(max(0.0, p - 300), max(0.0, p - 30), 14.0)]
        usual = float(np.median(base)) if base else 0.0
        return now >= usual + 3

    def _make_moment(self, start: float, end: float, peak: float, reason: str, an, chat: list,
                     manual: bool = False, lat: Optional[dict] = None, aired: Optional[float] = None):
        self.taken.append((start, end))
        margin = 12.0                 # room to move the start or end to a sentence boundary later
        segs = [g for g in self.segments if g["start"] + g["dur"] > start - margin and g["start"] < end + margin
                and os.path.exists(g["path"])]
        if not segs:
            self.log("Couldn't cut a moment: its video was already cleared from the buffer.")
            return
        self._moment_n += 1
        name = f"moment_{self._moment_n:04d}.mp4"
        out = os.path.join(self.moment_dir, name)
        lst = os.path.join(self.moment_dir, f".concat_{self._moment_n}.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for g in segs:
                f.write("file '" + g["path"].replace("\\", "/").replace("'", "'\\''") + "'\n")
        lat = dict(lat or {})
        t_cut = _now()
        try:
            engine.run([engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst,
                        "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", out], timeout=120)
        except Exception as e:
            self.log(f"Couldn't cut a moment: {str(e)[:150]}")
            return
        finally:
            try:
                os.remove(lst)
            except OSError:
                pass
        lat["cut"] = _now() - t_cut
        self.note_latency("cut", lat["cut"])
        offset = segs[0]["start"]
        last = segs[-1]["start"] + segs[-1]["dur"]
        mchat = [(m[0] - offset,) + tuple(m[1:]) for m in chat if offset - 30 <= m[0] <= last + 30]
        a, b = int(offset), int(math.ceil(last))
        sub = an.part(a, min(b, len(an.score)))
        feat = engine.clip_features(an, start, end, peak, chat, engine.chat_times(chat), self.chat_delay)
        import detect
        sc = round(float(an.score[min(int(peak), len(an.score) - 1)]), 2)
        clip = engine.Clip(1, round(start - offset, 2), round(end - offset, 2), round(peak - offset, 2),
                           sc, reason, feat, quality=detect.quality(sc), parts=detect.breakdown(an, peak))
        self.log(f"Moment {self._moment_n} at {engine.fmt_ts(peak)}" + (" (yours)" if manual else ""))
        try:
            self.on_moment(self, {"video": out, "chat": mchat, "analysis": sub, "clip": clip, "offset": offset,
                                  "manual": manual, "number": self._moment_n,
                                  "lat": {k: round(v, 2) for k, v in lat.items()}, "aired": aired})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log(f"Couldn't add moment {self._moment_n}: {str(e)[:150]}")

    # ------------------------------------------------------------------ disk
    def _cleanup(self):
        """Keep only the last few minutes of raw segments (moments are already cut out)."""
        n = self.recorded
        keep_from = n - self.buffer_s
        for a, b in self.manual:
            keep_from = min(keep_from, a - 15)
        for p in self.pending:
            keep_from = min(keep_from, p - 75)
        for g in self.segments:
            if g["start"] + g["dur"] < keep_from and os.path.exists(g["path"]):
                try:
                    os.remove(g["path"])
                except OSError:
                    pass

    def delete_buffer(self):
        shutil.rmtree(self.seg_dir, ignore_errors=True)
        if self.temp_buffer:
            try:
                os.rmdir(os.path.dirname(self.seg_dir))
            except OSError:
                pass

    # ------------------------------------------------------------------ chat
    def _irc_target(self):
        env = os.environ.get("CLIPRADAR_IRC")          # "host:port[:plain]" for tests
        if env:
            parts = env.split(":")
            return parts[0], int(parts[1]), not (len(parts) > 2 and parts[2] == "plain")
        return "irc.chat.twitch.tv", 6697, True

    _LINE = re.compile(r"^:([^ !]+)(?:!\S+)? (PRIVMSG|USERNOTICE) #\S+(?: :(.*))?$")

    def _on_chat(self, raw_line: str):
        tags = {}
        line = raw_line
        if line.startswith("@"):
            tagstr, _, line = line[1:].partition(" ")
            for kv in tagstr.split(";"):
                k, _, v = kv.partition("=")
                tags[k] = v
        m = self._LINE.match(line)
        if not m or self._anchor is None:
            return
        prefix, cmd, text = m.groups()
        text = text or ""
        if cmd == "USERNOTICE":
            kind = tags.get("msg-id") or "notice"
            user = tags.get("login", "")
        else:
            kind = "bits" if tags.get("bits") else ""
            user = prefix
        if self.in_ad:
            return            # chat during an ad break is about something not in the recording
        self.add_chat(self.live_edge(), text, user.lower(), kind)

    def add_chat(self, t: float, text: str, user: str, kind: str = ""):
        """A chat message seen at media time t (when it arrived; the delay is applied when scoring)."""
        w = self.scorer.weight(t, text, user, kind)
        now = _now()
        with self.chat_lock:
            self.chat.append((t, text, user, kind))
            if len(self.chat) > 300000:
                del self.chat[:100000]
            self._last_chat = now
            if len(self.chat) >= 30:
                self._busy_chat = True
            if w > 0:
                s = int(t)
                if s >= 0:
                    if s >= len(self._raw):
                        self._raw.extend([0.0] * (s + 1 - len(self._raw)))
                    self._raw[s] += w
            if self.emote_set is not None and t >= 0:
                import detect
                if detect.emote_in(text, user, kind, self.emote_set):
                    lt = self._emote_last.get(user) if user else None
                    if lt is None or t - lt >= 8:
                        if user:
                            self._emote_last[user] = t
                        s = int(t)
                        if s >= len(self._emote_raw):
                            self._emote_raw.extend([0.0] * (s + 1 - len(self._emote_raw)))
                        self._emote_raw[s] += 1.0

    def _chat_loop(self):
        host, port, use_tls = self._irc_target()
        while not self.stop_flag.is_set():
            sock = None
            try:
                raw = socket.create_connection((host, port), timeout=20)
                if use_tls:
                    ctx = ssl.create_default_context()
                    try:
                        import certifi
                        ctx.load_verify_locations(certifi.where())
                    except Exception:
                        pass
                    sock = ctx.wrap_socket(raw, server_hostname=host)
                else:
                    sock = raw
                sock.settimeout(60)
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nPASS SCHMOOPIIE\r\nNICK {nick}\r\n"
                             f"JOIN #{self.channel}\r\n".encode())
                buf = b""
                self.irc_ok = True
                while not self.stop_flag.is_set():
                    try:
                        data = sock.recv(16384)
                    except socket.timeout:
                        sock.sendall(b"PING :tmi.twitch.tv\r\n")
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for raw_line in lines:
                        line = raw_line.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
                            continue
                        try:
                            self._on_chat(line)
                        except Exception:
                            pass
            except Exception as e:
                self.irc_ok = False
                if not self.stop_flag.is_set():
                    self.log(f"Chat disconnected ({str(e)[:60]}), reconnecting")
            finally:
                self.irc_ok = False
                try:
                    if sock:
                        sock.close()
                except Exception:
                    pass
            self.stop_flag.wait(5)
