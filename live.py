"""
Live mode: clip a Twitch stream while it's still going.

  recorder   yt-dlp finds the stream's HLS playlist, ffmpeg saves it as 10-second
             segments (stream copy, so it costs almost no CPU). If the connection
             drops it reconnects; when the channel goes offline the session ends.
  chat       reads the channel's chat anonymously over Twitch IRC (no login needed).
  detector   after every segment it scores loudness + chat exactly like a VOD,
             and turns every strong peak into a moment. There is no limit.
  moments    each moment is cut from the segments into a small MP4 ("snippet")
             that the rest of VodClipper treats like a tiny VOD: preview, titles,
             captions and rendering all work on it.
  buffer     only the last few minutes of raw segments are kept, so a 10-hour
             stream doesn't fill the disk.
"""

from __future__ import annotations

import csv
import io
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
from typing import Callable, Optional

import numpy as np

import engine

SEG_SECONDS = 10
SR = 8000


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
    pass


class LiveSession:
    SENSITIVITY = {"low": 4.0, "normal": 3.0, "high": 2.2}

    def __init__(self, sid: str, source: str, workroot: str, *, length: float = 30, sensitivity: str = "normal",
                 chat_delay: float = 6.0, chat_weight: float = 60, max_height: int = 1080, cookies: str = "",
                 warmup: float = 90, buffer_minutes: float = 15,
                 on_moment: Callable = None, log: Callable = None):
        self.id = sid
        self.source = source.strip()
        self.channel = parse_channel(self.source)
        self.direct = bool(re.search(r"\.m3u8(\?|$)", self.source)) or os.path.isfile(self.source)
        if not self.channel and not self.direct:
            raise ValueError("Enter a Twitch channel name or link, like twitch.tv/yourname.")
        label = self.channel or "stream"
        self.title = f"{label} (live)"
        stamp = time.strftime("%Y-%m-%d %H-%M")
        self.dir = os.path.join(workroot, f"live_{label}_{stamp}")
        self.seg_dir = os.path.join(self.dir, "segments")
        self.moment_dir = os.path.join(self.dir, "moments")
        os.makedirs(self.seg_dir, exist_ok=True)
        os.makedirs(self.moment_dir, exist_ok=True)
        engine._save_meta(self.dir, title=self.title, source=self.source, live=True)

        self.length = float(length)
        self.threshold = self.SENSITIVITY.get(sensitivity, 3.0)
        self.sensitivity = sensitivity
        self.chat_delay = float(chat_delay)
        self.chat_weight = float(chat_weight)
        self.max_height = int(max_height)
        self.cookies = cookies
        self.warmup = float(warmup)
        self.buffer_s = max(1.0, float(buffer_minutes)) * 60
        self.on_moment = on_moment or (lambda *a, **k: None)
        self._log = log or (lambda m: None)

        self.status = "starting"          # starting | recording | reconnecting | ended | stopped | error
        self.error = ""
        self.started = _now()
        self.t0_wall: Optional[float] = None   # wall-clock time of media second 0
        self.segments: list = []               # {path, start, dur, name}
        self.loud: list = []                   # dB per second
        self._pending_audio = np.zeros(0, dtype=np.float32)
        self.chat: list = []                   # (media seconds, text)
        self.chat_lock = threading.Lock()
        self.taken: list = []                  # (start, end) already clipped
        self.manual: list = []                 # requested "clip that" end times
        self.moments: list = []                # project ids, filled in by the server
        self.score = np.zeros(0, dtype=np.float32)
        self.stop_flag = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._run = 0
        self._seg_number = 0
        self._moment_n = 0
        self.log_lines: list = []
        self.irc_ok = False

    # ------------------------------------------------------------------ utils
    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        self.log_lines = (self.log_lines + [line])[-200:]
        self._log(msg)

    @property
    def recorded(self) -> float:
        return float(len(self.loud))

    def live_edge(self) -> float:
        """Media time of 'now' (what viewers are seeing right now)."""
        if self.t0_wall is None:
            return 0.0
        return max(0.0, _now() - self.t0_wall)

    def public(self) -> dict:
        n = len(self.score)
        pts = 900
        if n > pts:
            edges = np.linspace(0, n, pts + 1).astype(int)
            curve = [round(float(self.score[a:b].max()), 2) for a, b in zip(edges[:-1], edges[1:])]
        else:
            curve = [round(float(x), 2) for x in self.score]
        with self.chat_lock:
            recent = sum(1 for t, _ in self.chat[-2000:] if t >= self.live_edge() - 60)
            total_chat = len(self.chat)
        return {
            "id": self.id, "channel": self.channel, "title": self.title, "source": self.source,
            "status": self.status, "error": self.error, "recorded": self.recorded,
            "edge": self.live_edge(), "started": self.started, "curve": curve,
            "chat_per_min": recent, "chat_total": total_chat, "chat_connected": self.irc_ok,
            "moments": list(self.moments), "length": self.length, "sensitivity": self.sensitivity,
            "pending_manual": len(self.manual), "log": self.log_lines[-12:], "dir": self.dir,
            "warming_up": self.recorded < self.warmup,
        }

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
        end = self.live_edge() if self.t0_wall else self.recorded
        self.manual.append((max(0.0, end - seconds), end))
        self.log(f"Clip requested: last {int(seconds)}s")
        return end

    # ------------------------------------------------------------------ recording
    def _resolve(self) -> str:
        if self.direct:
            if os.path.isfile(self.source):
                with open(self.source, encoding="utf-8", errors="replace") as f:
                    if "#EXT-X-ENDLIST" in f.read():
                        raise NotLive("The stream has ended.")
            return self.source
        url = f"https://www.twitch.tv/{self.channel}"
        fmt = f"best[height<={self.max_height}]/best"
        exe = engine.which("yt-dlp")
        err = ""
        if exe:
            cmd = [exe, "-g", "-f", fmt, "--no-warnings", "--ignore-config", url]
            if self.cookies:
                cmd[1:1] = ["--cookies-from-browser", self.cookies]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
            out = [l for l in p.stdout.splitlines() if l.startswith("http")]
            if p.returncode == 0 and out:
                return out[0]
            err = (p.stderr or p.stdout).strip()
        else:
            try:
                import yt_dlp
                opts = {"format": fmt, "quiet": True, "no_warnings": True}
                if self.cookies:
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
            raise NotLive(f"Couldn't find a Twitch channel called {self.channel}.")
        raise RuntimeError(engine._clean_ytdlp_error(err) if err else "couldn't get the stream address")

    def _record_loop(self):
        backoff = 3
        while not self.stop_flag.is_set():
            try:
                hls = self._resolve()
            except NotLive as e:
                if self.segments:
                    self.status = "ended"
                    self.log(f"Stream ended. {e}")
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
                if not self.segments and _now() - self.started > 180:
                    self.status = "error"
                    self.error = f"Couldn't start recording: {str(e)[:200]}"
                    break
                continue
            backoff = 3
            self._run += 1
            ok = self._run_ffmpeg(hls)
            if self.stop_flag.is_set():
                break
            if not ok:
                self.status = "reconnecting"
                self.log("Lost the stream, reconnecting...")
                self.stop_flag.wait(2)
        if self.status not in ("ended", "error"):
            self.status = "stopped"
        self._ingest_new()           # pick up the final segment
        self._detect(final=True)
        self.log("Recording stopped.")

    def _run_ffmpeg(self, hls: str) -> bool:
        list_path = os.path.join(self.seg_dir, f"list_{self._run}.csv")
        cmd = [engine.ffmpeg(), "-hide_banner", "-loglevel", "error"]
        if hls.startswith("http"):
            cmd += ["-rw_timeout", "20000000", "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"]
        cmd += ["-i", hls, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEG_SECONDS), "-reset_timestamps", "1",
                "-segment_start_number", str(self._seg_number),
                "-segment_list", list_path, "-segment_list_type", "csv",
                os.path.join(self.seg_dir, "seg_%06d.ts")]
        self.log("Recording" if self._run == 1 else "Reconnected")
        self._list_path = list_path
        self._list_seen = 0
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=engine.CREATE_NO_WINDOW)
        self._proc = p
        if self.t0_wall is None:
            self.t0_wall = _now() - self.recorded
        err_tail: list = []

        def read_err():
            for line in iter(p.stderr.readline, b""):
                err_tail.append(line.decode("utf-8", "replace").strip())
                del err_tail[:-6]
        threading.Thread(target=read_err, daemon=True).start()

        while p.poll() is None and not self.stop_flag.is_set():
            got = self._ingest_new()
            if got:
                self.status = "recording"
                self._detect()
                self._cleanup()
            time.sleep(0.5)
        if p.poll() is None:
            self.stop()
        self._ingest_new()
        if err_tail and not self.stop_flag.is_set():
            self.log("ffmpeg: " + " / ".join(err_tail[-2:])[:200])
        return False

    def _ingest_new(self) -> int:
        """Read newly finished segments from ffmpeg's list and measure their audio."""
        path = getattr(self, "_list_path", None)
        if not path or not os.path.exists(path):
            return 0
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                rows = list(csv.reader(f))
        except OSError:
            return 0
        new = rows[getattr(self, "_list_seen", 0):]
        added = 0
        for row in new:
            if len(row) < 3:
                break
            name = row[0]
            try:
                dur = max(0.0, float(row[2]) - float(row[1]))
            except ValueError:
                dur = SEG_SECONDS
            seg_path = os.path.join(self.seg_dir, name)
            start = self.segments[-1]["start"] + self.segments[-1]["dur"] if self.segments else 0.0
            self.segments.append({"path": seg_path, "start": start, "dur": dur, "name": name})
            self._seg_number = max(self._seg_number, int(re.sub(r"\D", "", name) or 0) + 1)
            self._measure(seg_path, dur)
            self._list_seen += 1
            added += 1
        return added

    def _measure(self, seg_path: str, dur: float):
        cmd = [engine.ffmpeg(), "-v", "error", "-nostdin", "-i", seg_path, "-map", "0:a:0?", "-vn",
               "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                               creationflags=engine.CREATE_NO_WINDOW)
            pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) if p.returncode == 0 else np.zeros(0)
        except Exception:
            pcm = np.zeros(0)
        want = int(round(dur * SR))
        if pcm.size < want * 0.5:              # no audio in this segment (or unreadable)
            pcm = np.zeros(want, dtype=np.float32)
        buf = np.concatenate([self._pending_audio, pcm[:max(want, 1)]])
        whole = buf.size // SR
        if whole:
            arr = buf[: whole * SR].reshape(whole, SR)
            rms = np.sqrt(np.mean(arr * arr, axis=1)) / 32768.0
            self.loud.extend((20 * np.log10(np.maximum(rms, 1e-5))).tolist())
        self._pending_audio = buf[whole * SR:]

    # ------------------------------------------------------------------ detection
    def _detect(self, final: bool = False):
        n = len(self.loud)
        if n < 5:
            return
        with self.chat_lock:
            chat = list(self.chat)
        an = engine.score_signals(np.asarray(self.loud), chat, n, float(n), self.chat_delay, self.chat_weight)
        self.score = an.score
        L = self.length
        lead = 0.6
        # manual "clip that" requests whose end has been recorded
        for req in list(self.manual):
            a, b = req
            if b <= n or final:
                self.manual.remove(req)
                b = min(b, n)
                a = max(0.0, min(a, b - 3))
                peak = (a + b) / 2
                self._make_moment(a, b, peak, "Clipped by you", an, manual=True)
        if n < self.warmup:
            return
        tail = L * (1 - lead) + 4
        limit = n if final else int(n - tail)
        s = an.score
        win = max(3, int(L / 2))
        lo = max(0, int(getattr(self, "_scan_from", 0)))
        for i in range(lo, max(lo, limit)):
            v = s[i]
            if v < self.threshold:
                continue
            a, b = max(0, i - win), min(n, i + win + 1)
            if v < s[a:b].max():
                continue
            start = max(0.0, i - L * lead)
            end = min(float(n), start + L)
            if any(start < te - 2 and end > ts + 2 for ts, te in self.taken):
                continue
            self._make_moment(start, end, float(i), engine._reason(an, i, chat, [t for t, _ in chat], self.chat_delay), an)
        self._scan_from = max(0, limit - int(L))

    def _make_moment(self, start: float, end: float, peak: float, reason: str, an, manual: bool = False):
        self.taken.append((start, end))
        margin = 6.0
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
        offset = segs[0]["start"]
        with self.chat_lock:
            chat = [(t - offset, m) for t, m in self.chat if offset - 30 <= t <= segs[-1]["start"] + segs[-1]["dur"] + 30]
        a, b = int(offset), int(math.ceil(segs[-1]["start"] + segs[-1]["dur"]))
        sub = engine.Analysis(float(b - a), an.chat_z[a:b].copy(), an.audio_z[a:b].copy(),
                              an.has_chat, an.has_audio)
        sub.combine(self.chat_weight)
        clip = engine.Clip(1, round(start - offset, 2), round(end - offset, 2), round(peak - offset, 2),
                           round(float(an.score[min(int(peak), len(an.score) - 1)]), 2), reason)
        self.log(f"Moment {self._moment_n} at {engine.fmt_ts(peak)}" + (" (yours)" if manual else ""))
        self.on_moment(self, {"video": out, "chat": chat, "analysis": sub, "clip": clip, "offset": offset,
                              "manual": manual, "number": self._moment_n})

    # ------------------------------------------------------------------ disk
    def _cleanup(self):
        """Keep only the last few minutes of raw segments (moments are already cut out)."""
        n = self.recorded
        keep_from = n - self.buffer_s
        for a, b in self.manual:
            keep_from = min(keep_from, a - 10)
        for g in self.segments:
            if g["start"] + g["dur"] < keep_from and os.path.exists(g["path"]):
                try:
                    os.remove(g["path"])
                except OSError:
                    pass

    def delete_buffer(self):
        shutil.rmtree(self.seg_dir, ignore_errors=True)

    # ------------------------------------------------------------------ chat
    def _irc_target(self):
        env = os.environ.get("VODCLIPPER_IRC")          # "host:port[:plain]" for tests
        if env:
            parts = env.split(":")
            return parts[0], int(parts[1]), not (len(parts) > 2 and parts[2] == "plain")
        return "irc.chat.twitch.tv", 6697, True

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
                sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
                buf = b""
                self.irc_ok = True
                while not self.stop_flag.is_set():
                    try:
                        data = sock.recv(8192)
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
                        m = re.match(r"^(?:@\S+ )?:\S+ PRIVMSG #\S+ :(.*)$", line)
                        if m and self.t0_wall is not None:
                            t = _now() - self.t0_wall
                            with self.chat_lock:
                                self.chat.append((t, m.group(1)))
                                if len(self.chat) > 400000:
                                    del self.chat[:100000]
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
