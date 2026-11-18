"""
1. acquire: getting the video and its chat (moved from engine.py, hardening phase 4b; no changes). A link is downloaded
with yt-dlp (Twitch VODs' chat with TwitchDownloader), a file on this PC is used where it is; either way the result is
a Source (the video, its chat, title, project folder and streamer).

  acquire(source, workroot, …)      the video and chat for a link or a file
  probe_url(url)                     what a link is, before anything is downloaded
  load_chat(path), chat_times(chat)  a chat replay, from any of the formats Clip Radar reads

engine.py re-exports every name here, so engine.acquire and the rest still work. The shared helpers (run, which,
ffmpeg, ffprobe, Cancelled…) stay in engine.py and are imported from it: engine imports this module once they're
defined (imported on its own, this module has engine import it first). A test that replaces a download name replaces it here
(acquire.DOWNLOAD_STALL_S = 4); one that replaces a helper for the download code replaces acquire.<helper> too,
since this module holds its own reference to it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import procjob

if "engine" not in sys.modules:
    # imported before engine: engine imports this module itself once the helpers below are defined, so engine is
    # imported first and this copy steps aside (the import then gives back the copy engine loaded in full)
    del sys.modules[__name__]
    import engine  # noqa: E402,F401
from engine import (  # noqa: E402  engine imports this module after these are defined
    CREATE_NO_WINDOW, Cancelled, Check, Log, Progress, VIDEO_EXTS, _noop, _save_meta, _slug, _video_in, ffmpeg, ffprobe, fmt_ts, project_video, run, which,
)


@dataclass
class Source:
    video: str
    chat: list  # [(seconds, text, user, kind)] sorted
    title: str
    workdir: str
    streamer: str = ""      # channel name when known (for learning what you like per streamer)
    start: float = 0.0      # where the link said to start (YouTube ?t=, Twitch ?t=)
    info: dict = field(default_factory=dict)   # what the source is (sources.describe)
    # the stream's sound already listened to while the video downloaded: a wait() giving measure_audio's
    # (loud, fine, events), or None when that didn't work out (analyze then listens to the video itself)
    sound: Optional[Callable] = None


def local_workdir(workroot: str, video: str) -> str:
    """The project folder for a video file on disk. Named after the file, except a file called vod.* (every Clip Radar
    download is), which is named after its folder. A folder that already belongs to another video gets a short suffix
    from this file's path: before 4.49 every vod.mp4 opened as a file shared one folder, and opening one replaced the
    other project's meta.json (efficiency audit 2026-11-08, F7). The same file opened again finds its own project."""
    p = Path(video)
    name = p.parent.name if p.stem.lower() == "vod" and p.parent.name else p.stem
    workdir = os.path.join(workroot, _slug(name))
    owner = str(_meta(workdir).get("source") or "")
    if owner and os.path.normcase(os.path.abspath(owner)) != os.path.normcase(os.path.abspath(video)):
        import hashlib
        workdir += "_" + hashlib.sha1(os.path.normcase(os.path.abspath(video)).encode("utf-8")).hexdigest()[:6]
    return workdir


def _meta(workdir: str) -> dict:
    try:
        return json.load(open(os.path.join(workdir, "meta.json"), encoding="utf-8"))
    except Exception:
        return {}


def acquire(source: str, workroot: str, chat_file: str = "", max_height: int = 1080,
            cookies_browser: str = "", log: Log = _noop, progress: Progress = _noop,
            step: Log = _noop, check: Check = _noop, cookie_file: str = "",
            early_sound: bool = False, events: bool = True) -> Source:
    """early_sound: for a Twitch VOD not downloaded yet, also download its sound-only version and listen to it
    (loudness, and laughter etc. when events) while the video downloads; the result is Source.sound."""
    source = source.strip().strip('"').strip("'")
    chat_file = (chat_file or "").strip().strip('"').strip("'")
    if chat_file and not os.path.isfile(chat_file):
        raise RuntimeError(f"Chat file not found: {chat_file}")

    # ---- local file
    if os.path.isfile(source):
        video = os.path.abspath(source)
        if os.path.splitext(video)[1].lower() not in VIDEO_EXTS:
            log("That file type isn't a usual video format, trying anyway.")
        title = Path(video).stem
        workdir = local_workdir(workroot, video)
        os.makedirs(workdir, exist_ok=True)
        log(f"Using local file: {video}")
        info: dict = {}
        if chat_file:
            if os.path.abspath(chat_file) != os.path.abspath(os.path.join(workdir, "chat.json")):
                shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
            chat = load_chat(chat_file, info)
            log(f"Loaded {len(chat):,} chat messages.")
        else:
            chat = _sidecar_chat(video, workdir, log, info)
        streamer = info.get("streamer") or _meta(workdir).get("streamer", "")
        _save_meta(workdir, title=title, source=video, local=True, streamer=streamer,
                   **({"streamer_id": info["streamer_id"]} if info.get("streamer_id") else {}))
        progress(1.0)
        return Source(video, chat, title, workdir, streamer)

    import sources
    c = sources.classify(source)
    if c["platform"] == "local":
        if "\\" in source or "/" in source or re.match(r"^[a-zA-Z]:", source):
            raise RuntimeError(f"File not found: {source}")
        raise RuntimeError("Paste a Twitch, YouTube or X video link, or pick a video file on your PC.")
    if c["kind"] == "channel":
        raise RuntimeError({"twitch": "That's a channel link. Open the channel's Videos tab and paste the link to a past broadcast "
                                      "(twitch.tv/videos/…), or use Live clipping to clip it as it streams.",
                            "youtube": "That's a channel link. Paste the link to one of its videos, or use Live clipping for its "
                                       "livestream."}.get(c["platform"], "That's a channel link. Paste the link to a video."))

    vod_id = c["id"] if c["platform"] == "twitch" and c["kind"] == "vod" else None
    url = c["url"]
    workdir = os.path.join(workroot, sources.workdir_name(c, _slug(source)[:40]))
    os.makedirs(workdir, exist_ok=True)

    desc: dict = {}
    meta = None
    if not vod_id and not project_video(workdir):
        # YouTube, X and others: find out what it is first (a video, a finished stream, a live one...)
        step("Checking the link")
        meta, err = probe_url(url, cookies_browser, cookie_file, _video_format(max_height))
        if meta is None and err:
            raise RuntimeError(sources.explain(err, c["platform"]))
        desc = sources.describe(meta or {}, c)
        if desc["live"]:
            raise RuntimeError(f"That's a {desc['label'].lower()} happening now. Use Live clipping for it, "
                               "or wait until it ends and paste the link again.")
        if desc["problem"]:
            raise RuntimeError(desc["problem"])
        _check_download_space(workdir, _size_of(meta))
        low = low_quality(meta, max_height, c["platform"])
        if low:
            log(low)
        log(f"{desc['label']}: {desc['title'] or url}" + (f" by {desc['uploader']}" if desc["uploader"] else "")
            + (f", {fmt_ts(desc['duration'])}" if desc["duration"] else ""))
        _save_meta(workdir, platform=c["platform"], kind=desc["kind"], label=desc["label"], uploader=desc["uploader"],
                   uploaded=desc["uploaded"], category=desc.get("category", ""),
                   **({"streamer": desc["uploader_id"].lower()} if desc["uploader_id"] else {}))
    elif vod_id:
        _save_meta(workdir, platform="twitch", kind="vod", label="Twitch past broadcast")

    fresh = not project_video(workdir)
    if vod_id and fresh:
        step("Checking the VOD")
        tmeta, _err = probe_url(url, cookies_browser, cookie_file, _video_format(max_height))
        _check_download_space(workdir, _size_of(tmeta))
    # Chat replay and the stream's sound don't need the video: get them while it downloads. A 9-hour VOD
    # (measured): the video takes 18 minutes, its chat 1 minute after it, then listening to the sound 2.5
    # minutes more. Twitch's sound-only version (about 1 GB) downloads in under a minute.
    # Neither may outlive this call with cookie_file: it's the Twitch sign-in, deleted when this returns, and
    # yt-dlp writes its cookies back when it exits. Twitch chat comes from TwitchDownloaderCLI (no cookies);
    # YouTube chat gets the browser's cookies only; the early sound download is stopped before returning.
    # A YouTube video: the link check already has everything the downloads need, so they start from it
    # (--load-info-json) instead of asking YouTube again, which takes a few seconds each time (its player has
    # to be worked out). The sound comes down on its own and is listened to while the video downloads, then
    # handed to the video's download as its sound part, so it isn't fetched twice. Comments, captions and
    # Most replayed are read alongside too: before, all of that waited for the download, then for each other.
    # The background work gets the browser's cookies only, not cookie_file (the Twitch sign-in): see above.
    info_file = _write_info(workdir, meta) if meta and which("yt-dlp") else ""
    extras = None
    if c["platform"] == "youtube" and meta:
        import ytextra
        extras = _Soon.start(("ytextra", workdir), lambda s: ytextra.gather(
            url, workdir, meta, log, ["--cookies-from-browser", cookies_browser] if cookies_browser else []))
    info: dict = {}
    chat_soon = None
    vclips = None
    if vod_id:
        # the clips viewers made from it (twitchclips): one quick request, alongside everything else
        import twitchclips
        vclips = _Soon.start(("vclips", workdir), lambda s: twitchclips.refresh(workdir, vod_id, log))
    if not chat_file and c["platform"] == "twitch":
        chat_soon = _Soon.start(("chat", workdir), lambda s: _with_info(lambda inf: _download_chat(url, vod_id, workdir, log, inf)))
    elif not chat_file and c["platform"] == "youtube" and _meta(workdir).get("kind") == "vod":
        chat_soon = _Soon.start(("chat", workdir), lambda s: (_youtube_chat(url, workdir, log, cookies_browser), {}))
    sound = None
    handoff = ""
    part = _sound_part(meta) if c["platform"] == "youtube" else None
    if early_sound and vod_id and fresh:
        sound = _Soon.start(("sound", workdir), lambda s: _early_sound(s, url, workdir, cookies_browser, cookie_file,
                                                                       events, log, check))
    elif early_sound and part and fresh:
        import locations
        handoff = os.path.join(locations.download_dir(workdir), f"vod.f{part[0]}.{part[1]}")
        sound = _Soon.start(("sound", workdir), lambda s: _early_sound(s, url, workdir, cookies_browser, "", events, log,
                                                                       check, fmt=part[0], info_file=info_file,
                                                                       handoff=handoff))
    step("Downloading the VOD" if c["platform"] == "twitch" else "Downloading the video")
    got_video = False
    try:
        video, title = _download_video(url, vod_id, workdir, max_height, cookies_browser,
                                       log, progress, step, check, cookie_file, info_file)
        got_video = True
    except Cancelled:
        raise
    except RuntimeError as e:
        if c["platform"] == "twitch":
            raise
        raise RuntimeError(sources.explain(str(e), c["platform"])) from None
    finally:
        if sound is not None:
            if not got_video:
                sound.cancel.set()            # the video failed or was stopped: so does the sound
            elif not sound.mark.wait(SOUND_GRACE_S):
                # the video is here and the sound-only copy still isn't: it's stuck. It used to be waited
                # for with no limit, so a wedged sound download held the whole job at "Downloading".
                # The video itself is listened to instead.
                log("The sound-only download is taking too long; listening to the video instead.")
                sound.cancel.set()
            sound.mark.wait()                 # its download is over either way (see cookie_file above)
        if got_video and handoff and os.path.exists(handoff):
            try:
                os.remove(handoff)            # the sound came down after the video was joined: not needed
            except OSError:
                pass
        if info_file:
            try:
                os.remove(info_file)
            except OSError:
                pass
    check()
    if extras is not None:
        # no chat on an ordinary video: what YouTube knows about it (Most replayed, comments, captions),
        # read while the video downloaded
        if not extras.done():
            step("Reading YouTube's Most replayed graph, comments and captions")
        extras.wait()
        check()
    if vclips is not None:
        try:
            vclips.wait()
        except Exception as e:                # optional: ranking goes on without it
            log(f"Couldn't read the clips viewers made ({str(e)[:80]}).")
    step("Getting chat replay")
    if chat_file:
        shutil.copyfile(chat_file, os.path.join(workdir, "chat.json"))
        chat = load_chat(chat_file, info)
    elif chat_soon is not None:
        chat, got = chat_soon.wait()
        info.update(got)
        if not chat and c["platform"] == "youtube":
            log("No chat replay for this video, ranking by audio.")
    elif c["platform"] == "youtube":
        chat = []
        log("No chat replay for this video, ranking by audio.")
    else:
        chat = []
        log("No chat for this video, ranking by audio.")
    streamer = _meta(workdir).get("streamer") or info.get("streamer", "")
    _save_meta(workdir, title=title, source=url, local=False, streamer=streamer, start=c["start"],
               **({"streamer_id": info["streamer_id"]} if info.get("streamer_id") else {}),
               **({"game": info["game"]} if info.get("game") else {}), **({"games": info["games"]} if info.get("games") else {}))
    m = _meta(workdir)
    return Source(video, chat, title, workdir, streamer, start=c["start"],
                  info={k: m.get(k, "") for k in ("platform", "kind", "label", "uploader", "uploaded")},
                  sound=sound.wait if sound is not None else None)


LOW_HEIGHT = 480        # a download this tall or less makes blurry 1080x1920 clips


def low_quality(meta: Optional[dict], max_height: int, platform: str) -> str:
    """A warning when the download will be low resolution although more was asked for, '' otherwise (4.45).
    YouTube sometimes offers only 360p to a client it doesn't trust (no sign-in, an old yt-dlp): the whole
    VOD then downloads, is ranked and rendered before the blur shows. The link check already lists what's on
    offer, so this costs nothing extra; it warns and carries on, since some videos really are 360p."""
    m = meta or {}
    rf = m.get("requested_formats") or [m]
    got = max([int(f.get("height") or 0) for f in rf if isinstance(f, dict)] or [0])
    if not got or got > LOW_HEIGHT or (0 < max_height <= LOW_HEIGHT):
        return ""
    best = max([int(f.get("height") or 0) for f in (m.get("formats") or []) if isinstance(f, dict)
                and f.get("vcodec") not in (None, "none")] or [got])
    if best > LOW_HEIGHT:
        return ""                     # better is on offer but wasn't picked: the format choice, not the site
    msg = f"Only {got}p is available for this video, so clips will look soft on a phone."
    if platform == "youtube":
        msg += (" If it's HD on YouTube, YouTube is holding back the better versions: choose your browser under Settings › "
                "Downloads and tools › Use cookies from, or press Update downloader there, then open it again.")
    return msg


def _video_format(max_height: int) -> str:
    """yt-dlp's format choice: H.264 up to max_height (every browser and FFmpeg build plays it), else the best.
    max_height 0 (Download quality › Best available): the best there is, whatever its size or codec."""
    if max_height <= 0:
        return "bv*+ba/b"
    return (f"bv*[height<={max_height}][vcodec^=avc1]+ba/"
            f"b[height<={max_height}][vcodec^=avc1]/"
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b")


def _write_info(workdir: str, meta: dict) -> str:
    """The link check's metadata as a file yt-dlp can start a download from (--load-info-json), or '' if it
    can't be written. A download that can't use it (links gone stale) looks the video up again by itself."""
    p = os.path.join(workdir, "probe.info.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, default=str)
        return p
    except (OSError, TypeError, ValueError):
        return ""


def _sound_part(meta: Optional[dict]) -> Optional[tuple]:
    """(format id, ext) of the sound a download will fetch as its own file next to the video (YouTube sends
    them apart), or None when it's one file with both."""
    rf = (meta or {}).get("requested_formats") or []
    if len(rf) != 2:
        return None
    v, a = rf
    if v.get("vcodec") in (None, "none") or a.get("vcodec") not in (None, "none") or a.get("acodec") in (None, "none"):
        return None
    fid, ext = str(a.get("format_id") or ""), str(a.get("ext") or "")
    if not re.fullmatch(r"[\w.-]+", fid) or not re.fullmatch(r"\w+", ext):
        return None
    return fid, ext


def _size_of(meta: Optional[dict]) -> float:
    """Bytes a download will take, from yt-dlp's metadata (0 if it can't tell). Twitch gives no size, only the
    bitrate: bitrate x length (9-hour VOD: 32.75 GB estimated, 32.7 GB downloaded)."""
    m = meta or {}
    size = float(m.get("filesize") or m.get("filesize_approx") or 0)
    if not size and m.get("tbr") and m.get("duration"):
        size = float(m["tbr"]) * 1000 / 8 * float(m["duration"])
    return size


class _Soon:
    """Work started in the background now and collected later. One per key at a time: a paused download that's
    resumed picks up its chat download still running instead of starting a second one into the same file."""
    _running: dict = {}
    _lock = threading.Lock()

    def __init__(self, fn):
        self._box: dict = {}
        self.cancel = threading.Event()        # asks fn to stop
        self.mark = threading.Event()          # fn's own milestone (_early_sound: its download is over)
        self._t = threading.Thread(target=self._run, args=(fn,), daemon=True)

    def _run(self, fn):
        try:
            self._box["v"] = fn(self)
        except BaseException as e:
            self._box["e"] = e
        finally:
            self.mark.set()

    @classmethod
    def start(cls, key, fn) -> "_Soon":
        with cls._lock:
            s = cls._running.get(key)
            if s is None or not s._t.is_alive():
                s = cls._running[key] = cls(fn)
                s._t.start()
            return s

    def done(self) -> bool:
        return not self._t.is_alive()

    def wait(self):
        self._t.join()
        if "e" in self._box:
            raise self._box["e"]
        return self._box.get("v")


def _check_download_space(workdir: str, need: float):
    """Stop before a download that can't fit (Settings › Storage › Downloaded videos)."""
    import locations
    dl = locations.download_dir(workdir)
    free = locations.free_space(dl)
    if free is None:
        return
    # x2: yt-dlp's last step (joining a Twitch download's pieces, or a video and its sound) writes a new copy
    # before it deletes the old one (measured: 32.7 GB + 31.6 GB on disk at once for a 9-hour VOD).
    # A download picked up again (paused, or stopped by a crash) already has some of it on disk: that part
    # isn't needed twice. Before, resuming a big VOD on a nearly full drive was refused.
    have = min(float(_download_bytes(dl)), need) if need else 0.0
    need = (need - have) + need * 1.1 + 300e6 if need else 2e9   # unknown size: a stream download needs a few GB
    if free < need:
        raise RuntimeError(f"Not enough space to download this: {free / 1e9:.1f} GB free on that drive, about "
                           f"{need / 1e9:.1f} GB needed. Free up space (Settings › Storage) or choose another folder for "
                           "downloaded videos.")


def probe_url(url: str, cookies_browser: str = "", cookie_file: str = "", fmt: str = "") -> tuple:
    """yt-dlp's metadata for a link, without downloading: (info or None, error text).
    fmt: the format the download will pick, so the size in the result is that format's."""
    exe = which("yt-dlp")
    if exe:
        cmd = [exe, "-J", "--skip-download", "--no-warnings", "--ignore-config", "--no-playlist"]
        if fmt:
            cmd += ["-f", fmt]
        if cookie_file:
            cmd += ["--cookies", cookie_file]
        elif cookies_browser:
            cmd += ["--cookies-from-browser", cookies_browser]
        try:
            p = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            return None, str(e)
        if p.returncode == 0 and p.stdout.strip().startswith("{"):
            try:
                return json.loads(p.stdout), ""
            except ValueError:
                pass
        return None, (p.stderr or p.stdout or "").strip()
    try:
        import yt_dlp
    except ImportError:
        return None, "yt-dlp isn't installed"
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    if fmt:
        opts["format"] = fmt
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False) or {}, ""
    except Exception as e:
        return None, str(e)


def _youtube_chat(url: str, workdir: str, log: Log, cookies_browser: str = "", cookie_file: str = "") -> list:
    """A finished YouTube livestream's chat replay (yt-dlp's live_chat), when it has one."""
    out = os.path.join(workdir, "chat.json")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        try:
            chat = load_chat(out)
            log(f"Chat already downloaded ({len(chat):,} messages), reusing it.")
            return chat
        except Exception:
            pass
    exe = which("yt-dlp")
    if not exe:
        return []
    log("Downloading the chat replay (long streams take a while)...")
    cmd = [exe, "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-warnings", "--ignore-config",
           "-o", os.path.join(workdir, "chat_yt.%(ext)s")]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    try:
        subprocess.run(cmd + [url], capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL,
                       encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except Exception:
        return []
    for f in os.listdir(workdir):
        if f.startswith("chat_yt") and f.endswith(".json"):
            shutil.move(os.path.join(workdir, f), out)
            try:
                chat = load_chat(out)
            except Exception:
                return []
            log(f"Got {len(chat):,} chat messages.")
            return chat
    return []


def _download_video(url: str, vod_id: Optional[str], workdir: str, max_height: int,
                    cookies_browser: str, log: Log, progress: Progress, step: Log, check: Check,
                    cookie_file: str = "", info_file: str = ""):
    import locations
    dl = locations.download_dir(workdir)              # Settings › Storage › Downloaded videos
    os.makedirs(dl, exist_ok=True)
    existing = _video_in(dl) or _video_in(workdir)
    if existing:
        existing = _reusable(existing, log, step, check)
    meta_p = os.path.join(workdir, "meta.json")
    if existing:
        log("VOD already downloaded, reusing it.")
        progress(1.0)
        title = "vod"
        if os.path.exists(meta_p):
            try:
                title = json.load(open(meta_p, encoding="utf-8")).get("title", "vod")
            except Exception:
                pass
        return existing, title

    fmt = _video_format(max_height)
    errs = []

    # 1) standalone yt-dlp.exe (kept up to date by the app, so Twitch changes don't need a rebuild)
    exe = which("yt-dlp")
    if exe:
        try:
            return _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file, out_dir=dl,
                              info_file=info_file)
        except Cancelled:
            raise
        except Exception as e:
            errs.append(str(e))
            log(f"yt-dlp couldn't download it: {e}")

    # 2) yt-dlp Python module
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None
    if yt_dlp:
        try:
            return _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file, out_dir=dl)
        except Cancelled:
            raise
        except Exception as e:
            if "Cancelled" in type(e).__name__ or "Cancelled" in str(e):
                raise Cancelled()
            msg = _clean_ytdlp_error(str(e))
            errs.append(msg)
            log(f"yt-dlp couldn't download it: {msg}")
    elif not exe:
        errs.append("yt-dlp isn't installed")

    err = "\n".join(dict.fromkeys(errs)) if errs else None

    # fallback: TwitchDownloaderCLI
    tdl = which("TwitchDownloaderCLI")
    if tdl and vod_id:
        step("Downloading the VOD (TwitchDownloader)")
        log("Trying TwitchDownloaderCLI instead...")
        # TwitchDownloader's FFmpeg writes its output file in place, so a download stopped part way left a
        # vod.mp4 with no index that the next open reused as finished. It writes to TDL_PART now and the
        # file only becomes vod.mp4 once TwitchDownloader says it's done.
        part = os.path.join(dl, TDL_PART)
        try:
            os.remove(part)
        except OSError:
            pass
        cmd = [tdl, "videodownload", "--id", vod_id, "-o", part, "-t", str(FRAGMENTS)]
        if which("ffmpeg"):
            cmd += ["--ffmpeg-path", ffmpeg()]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW, **({} if os.name == "nt" else {"start_new_session": True}))
        procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
        tail = []
        last = [time.monotonic()]
        stalled = _watch_stall(p, dl, last)     # it had no stall check: a stuck one waited for ever
        try:
            for line in p.stdout:
                last[0] = time.monotonic()
                tail = (tail + [line.strip()])[-8:]
                m = re.search(r"(\d{1,3})%", line)
                if m:
                    progress(min(0.999, int(m.group(1)) / 100))
                try:
                    check()
                except Cancelled:
                    kill_tree(p)               # TwitchDownloaderCLI runs ffmpeg: stop that too
                    raise
        finally:
            p.wait()
        if p.returncode == 0 and os.path.isfile(part) and os.path.getsize(part) > 1024:
            os.replace(part, os.path.join(dl, "vod.mp4"))
        video = _video_in(dl)
        if stalled[0] and not video:
            tail.append(f"stopped after {DOWNLOAD_STALL_S // 60} minutes with nothing written")
        if p.returncode == 0 and video:
            title = f"Twitch VOD {vod_id}"
            _save_meta(workdir, title=title)
            progress(1.0)
            return video, title
        err = (err or "") + "\nTwitchDownloader: " + " / ".join(t for t in tail if t)[-300:]

    if not vod_id and "twitch.tv" not in url:
        raise RuntimeError(err or "yt-dlp is not installed")     # explained by sources.explain in acquire
    raise RuntimeError(
        "Couldn't download that VOD.\n"
        f"{err or 'yt-dlp is not installed'}\n"
        "If it's subscriber-only, sign in under Settings › Twitch, or choose the browser where you're logged in to "
        "Twitch under Settings › Downloads and tools › Use cookies from. If Twitch changed something, press Update "
        "downloader there.")



class _Stepper:
    """Turns raw download numbers into progress + a friendly step line."""

    # A download in pieces (Twitch) is joined into one file after the last piece, which took a third of the
    # time (measured, 9-hour VOD: 687 s of pieces, then 355 s joining), so the bar leaves that much for it
    PIECES_SHARE = 0.66
    # YouTube and others send the video and its sound as two files, one after the other: each went 0-100%,
    # so the bar ran to the end and started again. Each file now gets its part of one bar, by size (or, when
    # the site doesn't say, the video 90%: its sound is about a tenth of the bytes). Joining the two is a
    # quick copy, not a Twitch-style rewrite (with +faststart off: see NO_FASTSTART).
    VIDEO_SHARE = 0.9

    def __init__(self, progress: Progress, step: Log, log: Log):
        self.progress, self.step, self.log = progress, step, log
        self.t = 0.0
        self.pct = -1
        self.frac = 0.0
        self.share = 1.0
        self.weights = [1.0]
        self.part = 0
        self.last_done = None
        self.shown = 0.0

    def parts(self, sizes: list):
        """The files yt-dlp will download for this video (their sizes in bytes, 0 = unknown)."""
        n = len(sizes)
        if n < 2:
            return
        sizes = [float(s or 0) for s in sizes]
        if all(s > 0 for s in sizes):
            self.weights = [s / sum(sizes) for s in sizes]
        else:
            self.weights = [self.VIDEO_SHARE] + [(1 - self.VIDEO_SHARE) / (n - 1)] * (n - 1)

    def _show(self, v: float):
        # never backwards: a bar that drops makes the time left meaningless
        self.shown = max(self.shown, min(0.999, max(0.0, v)))
        self.progress(self.shown)

    def update(self, done, total, frag_i, frag_n, speed, eta):
        if (done is not None and self.last_done is not None and done < self.last_done * 0.5
                and self.part + 1 < len(self.weights)):
            self.part += 1                     # the next file (the sound after the video)
        if done is not None:
            self.last_done = done
        frac = None
        if total:
            frac = done / total if done is not None else None
        elif frag_n:
            frac = (frag_i or 0) / frag_n
        if frag_n and len(self.weights) == 1:
            self.share = self.PIECES_SHARE
        elif len(self.weights) > 1:
            self.share = 0.98
        if frac is not None:
            frac = sum(self.weights[:self.part]) + self.weights[self.part] * min(1.0, max(0.0, frac))
            self.frac = frac
            self._show(frac * self.share)
        now = time.time()
        if now - self.t > 1.0:
            self.t = now
            bits = ["Downloading the VOD"]
            if speed:
                bits.append(f"{speed / 1e6:.1f} MB/s")
            if eta:
                bits.append(f"about {fmt_ts(eta)} left")
            self.step(", ".join(bits))
            if frac is not None and int(frac * 10) > self.pct:
                self.pct = int(frac * 10)
                self.log(f"Download {int(frac * 100)}%")

    def joined(self, frac: float):
        """How far the join after the download has got, 0..1."""
        self._show(self.share + (1 - self.share) * max(0.0, frac))
        self.step(f"Putting the video together, {int(frac * 100)}%")


def _finish_download(workdir: str, title: str, duration, progress: Progress, log: Log, streamer: str = "", out_dir: str = ""):
    _save_meta(workdir, title=title, duration=duration or 0, **({"streamer": streamer} if streamer else {}))
    video = _video_in(out_dir or workdir)
    if not video:
        raise RuntimeError("the download finished but no video file was found")
    progress(1.0)
    log(f"Downloaded: {title}")
    return video, title


def _ytdlp_cli(exe, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file="", out_dir="",
               info_file=""):
    """info_file: the link check's metadata (_write_info), so yt-dlp doesn't look the video up again. When its
    links don't work any more, yt-dlp looks it up by itself ("trying with URL")."""
    out_dir = out_dir or workdir
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    cmd = [exe, "--newline", "--progress", "--no-colors", "--no-simulate", "--ignore-config",
           "--print", "before_dl:VCINFO %(.{title,is_live,duration,uploader_id,uploader})j",
           "--print", "before_dl:VCPARTS %(requested_formats.:.{filesize,filesize_approx})j",
           "--progress-template",
           "download:VCPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
           "%(progress.total_bytes_estimate)s %(progress.fragment_index)s "
           "%(progress.fragment_count)s %(progress.speed)s %(progress.eta)s",
           "-f", fmt, "-o", os.path.join(out_dir, "vod.%(ext)s"), "--merge-output-format", "mp4",
           "-N", str(FRAGMENTS), "--throttled-rate", "100K", "--socket-timeout", "30", "--retries", "10", "--fragment-retries", "20",
           "--match-filters", "!is_live", *NO_FASTSTART]
    ff = which("ffmpeg")
    if ff:
        cmd += ["--ffmpeg-location", os.path.dirname(ff)]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd += ["--load-info-json", info_file] if info_file and os.path.isfile(info_file) else [url]
    log("Downloading the VOD. Long streams take a while.")
    st = _Stepper(progress, step, log)
    info = {}
    errors, tail = [], []
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
                         **({} if os.name == "nt" else {"start_new_session": True}))
    procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
    last_output = [time.monotonic()]
    last_line = [time.monotonic()]
    stalled = [False]

    def watch_download():
        # yt-dlp normally prints progress often, but after 100% it can go quiet for minutes while it
        # joins or remuxes a long VOD (an 18 GB, 6-hour stream: about 3 minutes). A download file that
        # is still growing counts as progress; only real silence is a stall. A stalled download is
        # stopped with its whole process tree: yt-dlp.exe is a launcher, and killing only it left the
        # real download running in the background.
        size, told = -1, False
        while p.poll() is None:
            now = _download_bytes(out_dir)
            if now != size:
                if size >= 0:
                    last_output[0] = time.monotonic()
                size = now
            # the join has begun: every part reached 100%, or the joined file has appeared. A part that was
            # on disk already (the sound fetched to listen ahead) never reports progress, so the parts
            # alone stopped at ~97% and the bar sat still for the whole join (4.41.2)
            if not told and (st.frac >= 0.99 or _join_progress(out_dir) is not None)                     and time.monotonic() - last_line[0] > 5:
                told = True
                step("Putting the video together (a few minutes for a long stream)")
                log("Download finished; joining it into one video.")
            if told:
                j = _join_progress(out_dir)
                if j is not None:
                    st.joined(j)
            if time.monotonic() - last_output[0] > DOWNLOAD_STALL_S:
                stalled[0] = True
                kill_tree(p)
                return
            time.sleep(2)

    threading.Thread(target=watch_download, daemon=True).start()
    try:
        for line in p.stdout:
            last_output[0] = last_line[0] = time.monotonic()
            line = line.strip()
            if line.startswith("VCPROG "):
                v = [num(x) for x in line.split()[1:8]]
                v += [None] * (7 - len(v))
                st.update(v[0], v[1] or v[2], v[3], v[4], v[5], v[6])
            elif line.startswith("VCPARTS "):
                try:
                    fs = json.loads(line[8:])
                    if isinstance(fs, list):
                        st.parts([(f or {}).get("filesize") or (f or {}).get("filesize_approx") or 0 for f in fs])
                except Exception:
                    pass
            elif line.startswith("VCINFO "):
                try:
                    info = json.loads(line[7:])
                except Exception:
                    pass
            elif line:
                tail = (tail + [line])[-6:]
                if line.startswith("ERROR"):
                    errors.append(_clean_ytdlp_error(line))
            try:
                check()
            except Cancelled:
                kill_tree(p)
                raise
    finally:
        p.wait()
    if stalled[0] and not (p.returncode == 0 and _video_in(out_dir)):
        raise RuntimeError(f"The VOD download stopped for {DOWNLOAD_STALL_S // 60} minutes with nothing written. "
                           "Check your connection and retry.")
    if p.returncode != 0 or not _video_in(out_dir):
        if info.get("is_live") or any("is_live" in t for t in tail):
            raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
        raise RuntimeError(errors[-1] if errors else (tail[-1] if tail else f"yt-dlp exited with code {p.returncode}"))
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log,
                            str(info.get("uploader_id") or info.get("uploader") or "").lower(), out_dir)


# pieces of a download fetched at once (yt-dlp -N, TwitchDownloader -t). Nothing limits a download's
# speed: no rate limit is ever set, and --throttled-rate gets a fresh link when a site (YouTube) slows
# one down to under 100 KB/s instead of letting it crawl.
FRAGMENTS = 16
DOWNLOAD_STALL_S = 180       # no output and no file growth for this long: the download is stuck
SOUND_GRACE_S = 180          # the video is down: how much longer its sound-only copy may take

# yt-dlp joins a Twitch download's pieces with FFmpeg and +faststart, which writes the whole MP4 and then
# reads and rewrites all of it again to put the index first: only useful for playing over the internet.
# Clip Radar plays the VOD from disk (the index is read from the end with a range request; checked in the
# app's player). Measured, 9-hour VOD: the join read and wrote 65 GB for a 32.7 GB file.
# yt-dlp's Merger (YouTube's video and sound joined into one MP4) adds +faststart too. Its second pass
# rewrites the whole file without it getting any bigger, so on a 9-hour, 18 GB YouTube VOD the stall check
# saw 3 minutes of no growth and stopped a join that was nearly done (4.41.2).
NO_FASTSTART = ["--ppa", "FixupM3u8:-movflags -faststart", "--ppa", "Merger:-movflags -faststart"]


TDL_PART = "vod.tdl-part.mp4"     # TwitchDownloader's output until it's finished (its stem isn't "vod")


def _watch_stall(p: subprocess.Popen, folder: str, last: list) -> list:
    """Stop p, with everything it started, when it has printed nothing (last[0], a monotonic time the
    caller moves on every line) and its files in folder haven't grown for DOWNLOAD_STALL_S.
    Returns [stalled], set when it had to."""
    stalled = [False]

    def watch():
        size = -1
        while p.poll() is None:
            now = _download_bytes(folder)
            if now != size:
                if size >= 0:
                    last[0] = time.monotonic()
                size = now
            if time.monotonic() - last[0] > DOWNLOAD_STALL_S:
                stalled[0] = True
                kill_tree(p)
                return
            time.sleep(2)
    threading.Thread(target=watch, daemon=True).start()
    return stalled


def _container(path: str) -> Optional[str]:
    """FFprobe's format name for a file ("mov,mp4,m4a,3gp,3g2,mj2", "mpegts"...), '' when FFprobe can't read
    it as a video at all (an MP4 stopped part way has no index), None when that can't be told."""
    try:
        p = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "format=format_name,duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except Exception:
        return None
    if p.returncode != 0:
        return ""
    try:
        fmt = json.loads(p.stdout or "{}").get("format") or {}
    except ValueError:
        return None
    return str(fmt.get("format_name") or "")


def _reusable(video: str, log: Log, step: Log = _noop, check: Check = _noop) -> Optional[str]:
    """A VOD found on disk from an earlier download, made usable, or None to download it again.

    yt-dlp joins a Twitch download in place: the pieces become vod.mp4 (really MPEG-TS), then FFmpeg
    writes vod.temp.mp4 and swaps it in. On a 9-hour VOD that join takes about 6 minutes, and a download
    paused, cancelled, stalled or closed during it left the MPEG-TS vod.mp4 behind, which every later open
    reused as finished: the preview wouldn't play it, seeking was off and clips could come out blank.
    That file has the whole VOD in it, so it's finished here (the same copy yt-dlp makes). An MP4 FFprobe
    can't read at all (one stopped while being written, before 4.45 TwitchDownloader's) is removed so the
    download starts again instead of failing later at every step."""
    folder = os.path.dirname(video)
    for f in os.listdir(folder):
        if f.startswith("vod.temp."):                 # a join that was stopped: only half a file
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
    kind = _container(video)
    if kind is None:
        return video                                   # can't tell (no FFprobe): as before
    if kind == "":
        log("The video downloaded before is unreadable (its download was stopped part way). Downloading it again.")
        try:
            os.remove(video)
        except OSError:
            return video
        return None
    if "mpegts" in kind and video.lower().endswith((".mp4", ".m4v")):
        step("Putting the video together (its download was stopped while it was joined)")
        log("The VOD downloaded before was never joined into one video. Joining it now.")
        tmp = os.path.join(folder, "vod.temp" + os.path.splitext(video)[1])
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-map", "0", "-dn", "-ignore_unknown",
               "-c", "copy", "-bsf:a", "aac_adtstoasc", "-f", "mp4", tmp]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW, **({} if os.name == "nt" else {"start_new_session": True}))
        procjob.adopt(p)
        try:
            while p.poll() is None:
                try:
                    check()
                except Cancelled:
                    kill_tree(p)
                    raise
                time.sleep(0.5)
        finally:
            p.wait()
            err = (p.stderr.read() or b"").decode("utf-8", "replace") if p.stderr else ""
            if p.returncode != 0:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if p.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 1024:
            os.replace(tmp, video)
            return video
        log(f"Couldn't join it ({err.strip().splitlines()[-1][:160] if err.strip() else 'FFmpeg failed'}); "
            "using it as it is.")
    return video


def _download_bytes(folder: str) -> int:
    """Bytes in the download's files so far (vod.*: parts, fragments, the file being joined)."""
    total = 0
    try:
        for f in os.listdir(folder):
            if f.startswith("vod.") or f.startswith("vod-"):
                try:
                    total += os.path.getsize(os.path.join(folder, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _join_progress(folder: str) -> Optional[float]:
    """While yt-dlp joins a download into vod.temp.<ext>: how much of it is written, 0..1 (None if it isn't
    joining). The joined file comes out about as big as what goes in (32.7 GB in, 31.7 GB out)."""
    try:
        sizes = {f: os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder) if f.startswith("vod.")}
    except OSError:
        return None
    out = sum(s for f, s in sizes.items() if f.startswith("vod.temp."))
    src = sum(s for f, s in sizes.items() if not f.startswith("vod.temp.") and not f.endswith((".part", ".ytdl")))
    if not out or not src:
        return None
    return min(1.0, out / src)


def kill_tree(p: subprocess.Popen):
    """Stop a process and everything it started (yt-dlp.exe is a launcher; ffmpeg runs under it)."""
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        p.kill()
    except OSError:
        pass


def _ytdlp_module(yt_dlp, url, workdir, fmt, cookies_browser, log, progress, step, check, cookie_file="", out_dir=""):
    out_dir = out_dir or workdir
    st = _Stepper(progress, step, log)

    def hook(d):
        check()
        fs = (d.get("info_dict") or {}).get("requested_formats")
        if fs and len(st.weights) == 1:
            st.parts([f.get("filesize") or f.get("filesize_approx") or 0 for f in fs])
        if d.get("status") == "downloading":
            st.update(d.get("downloaded_bytes"), d.get("total_bytes") or d.get("total_bytes_estimate"),
                      d.get("fragment_index"), d.get("fragment_count"), d.get("speed"), d.get("eta"))

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(out_dir, "vod.%(ext)s"),
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": FRAGMENTS,
        "throttledratelimit": 100_000,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 10,
        "extractor_retries": 3,
        "fragment_retries": 20,
        "file_access_retries": 3,
        "continuedl": True,
        "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
        "postprocessor_args": {"fixupm3u8": ["-movflags", "-faststart"],      # see NO_FASTSTART
                               "merger": ["-movflags", "-faststart"]},
    }
    ff = which("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = os.path.dirname(ff)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    log("Downloading the VOD. Long streams take a while.")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    if info.get("is_live"):
        raise RuntimeError("That stream is live. Wait until it ends and use the VOD link.")
    return _finish_download(workdir, info.get("title") or "vod", info.get("duration"), progress, log,
                            str(info.get("uploader_id") or info.get("uploader") or "").lower(), out_dir)


def _clean_ytdlp_error(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = s.replace("ERROR: ", "").strip()
    return s.splitlines()[0][:300] if s else "unknown error"


def _download_chat(url: str, vod_id: Optional[str], workdir: str, log: Log, info: Optional[dict] = None) -> list:
    out = os.path.join(workdir, "chat.json")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        try:
            chat = load_chat(out, info)
            log(f"Chat already downloaded ({len(chat):,} messages), reusing it.")
            return chat
        except Exception:
            pass

    tdl = which("TwitchDownloaderCLI")
    if tdl and vod_id:
        log("Downloading chat replay with TwitchDownloaderCLI...")
        try:
            if os.path.exists(out):
                os.remove(out)
            # ended with Clip Radar like the video's download (it ran on for up to an hour after a crash)
            part = os.path.join(workdir, "chat.part.json")      # TwitchDownloader picks the format by extension
            p = subprocess.Popen([tdl, "chatdownload", "--id", vod_id, "-o", part, "-t", "8"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 creationflags=CREATE_NO_WINDOW)
            procjob.adopt(p)
            try:
                tail = p.communicate(timeout=3600)[0].decode("utf-8", "replace").strip()
            except subprocess.TimeoutExpired:
                kill_tree(p)
                raise RuntimeError("TwitchDownloaderCLI timed out after an hour")
            if p.returncode != 0 or not os.path.isfile(part):
                raise RuntimeError(tail or f"TwitchDownloaderCLI exited with code {p.returncode}")
            # written whole, then put in place: a chat download stopped part way left a cut-off chat.json
            os.replace(part, out)
            chat = load_chat(out, info)
            log(f"Got {len(chat):,} chat messages.")
            return chat
        except Exception as e:
            log(f"Chat download failed: {str(e).splitlines()[-1][:160]}")

    try:
        import yt_dlp
        opts = {"skip_download": True, "writesubtitles": True, "subtitleslangs": ["rechat"],
                "outtmpl": os.path.join(workdir, "chat_raw"), "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for f in os.listdir(workdir):
            if f.startswith("chat_raw") and f.endswith(".json"):
                shutil.move(os.path.join(workdir, f), out)
                chat = load_chat(out, info)
                log(f"Got {len(chat):,} chat messages.")
                return chat
    except Exception:
        pass

    log("No chat replay available, ranking by audio only. "
        "(Run install.bat to add TwitchDownloaderCLI for chat.)")
    return []


def _with_info(fn) -> tuple:
    """(fn(info), info) for a chat download in the background: a resumed download that picks it up still
    gets the streamer's name and id it found."""
    info: dict = {}
    return fn(info), info


def _early_sound(soon: "_Soon", url: str, workdir: str, cookies_browser: str, cookie_file: str, events: bool,
                 log: Log, check: Check, fmt: str = "Audio_Only/ba", info_file: str = "",
                 handoff: str = "") -> Optional[tuple]:
    """Download a Twitch VOD's sound-only version and listen to it, while the video downloads alongside.
    Returns measure_audio's (loud, fine, events), or None if anything went wrong (the video is listened to
    instead, as before). soon.mark is set as soon as the download part is over.
    A YouTube video (fmt: its sound's format id): handoff is where the video's own download looks for that
    sound part (vod.f<id>.<ext>). A copy goes there as soon as it's down, and yt-dlp, finding it, joins it
    instead of downloading it again. The video comes first and is about nine tenths of the bytes, so the
    sound is nearly always there in time; if yt-dlp got to it first, it's left alone."""
    from analysis import measure_audio     # analysis.py imports engine, which imports this module
    d = os.path.join(workdir, "sound_first")
    exe = which("yt-dlp")

    def stop():
        if soon.cancel.is_set():
            raise Cancelled()
        check()
    try:
        if not exe:
            return None
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
        cmd = [exe, "--ignore-config", "--no-progress", "--no-warnings", "-f", fmt, "-N", str(FRAGMENTS), *NO_FASTSTART,
               "--socket-timeout", "30", "--retries", "10", "--fragment-retries", "20",
               "-o", os.path.join(d, "sound.%(ext)s")]
        ff = which("ffmpeg")
        if ff:
            cmd += ["--ffmpeg-location", os.path.dirname(ff)]
        if cookie_file:
            cmd += ["--cookies", cookie_file]
        elif cookies_browser:
            cmd += ["--cookies-from-browser", cookies_browser]
        t0 = time.monotonic()
        errlog = os.path.join(d, "yt-dlp.log")
        with open(errlog, "wb") as ef:
            p = subprocess.Popen(cmd + (["--load-info-json", info_file] if info_file else [url]),
                                 stdout=subprocess.DEVNULL, stderr=ef, stdin=subprocess.DEVNULL,
                                 creationflags=CREATE_NO_WINDOW, **({} if os.name == "nt" else {"start_new_session": True}))
        procjob.adopt(p)
        try:
            while p.poll() is None:
                try:
                    stop()
                except Cancelled:
                    kill_tree(p)
                    raise
                time.sleep(0.2 if handoff else 0.5)       # the video's download may want it soon
        finally:
            p.wait()
            if not handoff:
                soon.mark.set()
        names = [f for f in os.listdir(d) if f.startswith("sound.") and not f.endswith((".part", ".ytdl"))
                 and ".part-Frag" not in f and ".temp." not in f]
        if p.returncode != 0 or not names:
            with open(errlog, encoding="utf-8", errors="replace") as ef:
                why = [ln.strip() for ln in ef if ln.strip()]
            raise RuntimeError(_clean_ytdlp_error(why[-1]) if why else f"yt-dlp exited with code {p.returncode}")
        sound = os.path.join(d, names[0])
        if handoff:
            _hand_over(sound, handoff, log)
            soon.mark.set()
        dur = float(run([ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                         sound], timeout=60).stdout.strip() or 0)
        if dur <= 0:
            return None
        took = time.monotonic() - t0
        res = measure_audio(sound, dur, events, check=stop, log=log)
        log(f"Listened to the sound while the video downloads (sound-only copy in {took:.0f} s, "
            f"listening {time.monotonic() - t0 - took:.0f} s).")
        return res
    except Cancelled:
        return None
    except Exception as e:
        log(f"Listening ahead didn't work ({str(e).splitlines()[0][:120] if str(e) else type(e).__name__}); "
            "listening to the video once it's here.")
        return None
    finally:
        soon.mark.set()
        shutil.rmtree(d, ignore_errors=True)


def _hand_over(sound: str, target: str, log: Log) -> bool:
    """Put a copy of the sound where the video's download will look for its sound part, unless that download
    has already started on it (or has it). A copy, not a move: this file is listened to next, and the
    download deletes its parts once they're joined."""
    if os.path.exists(target) or os.path.exists(target + ".part") or os.path.exists(target + ".ytdl") \
            or _video_in(os.path.dirname(target)):
        return False
    tmp = os.path.join(os.path.dirname(target), "sound-handoff.tmp")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(sound, tmp)
        if os.path.exists(target + ".part"):          # it started while the copy was made: it'll have its own
            os.remove(tmp)
            return False
        os.replace(tmp, target)
        log("The sound is down already, so the video's download won't fetch it again.")
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _sidecar_chat(video: str, workdir: str, log: Log, info: Optional[dict] = None) -> list:
    base = os.path.splitext(video)[0]
    for cand in (base + ".chat.json", base + "_chat.json", base + ".json",
                 os.path.join(os.path.dirname(video), "chat.json"),
                 os.path.join(workdir, "chat.json")):
        if os.path.exists(cand):
            try:
                chat = load_chat(cand, info)
            except Exception:
                continue
            log(f"Found chat file {os.path.basename(cand)} ({len(chat):,} messages).")
            return chat
    log("No chat file given, ranking by audio only.")
    return []


def load_chat(path: str, info: Optional[dict] = None) -> list:
    """Parse TwitchDownloader / yt-dlp rechat / generic chat JSON into [(sec, text, user, kind)].
    kind is "" for normal chat, or the Twitch notice type ("sub", "subgift", "raid", "bits"...).
    If info is given, it gets the streamer's channel name when the file has it, and the game (4.36): "game",
    and "games" [{start, end, game}] when the stream changed game (TwitchDownloader's chapters)."""
    import gzip
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()
    msgs = []

    def take(c):
        if not isinstance(c, dict):
            return
        t = c.get("content_offset_seconds", c.get("offset", c.get("time", c.get("t"))))
        msg = c.get("message", c.get("text", c.get("body", "")))
        kind = ""
        if isinstance(msg, dict):
            notice = msg.get("user_notice_params") or {}
            if isinstance(notice, dict) and notice.get("msg-id"):
                kind = str(notice.get("msg-id"))
            elif (msg.get("bits_spent") or 0) > 0:
                kind = "bits"
            msg = msg.get("body") or " ".join(
                fr.get("text", "") for fr in msg.get("fragments", []) or [] if isinstance(fr, dict))
        who = c.get("commenter") or c.get("author") or c.get("user") or c.get("name") or ""
        if isinstance(who, dict):
            who = who.get("name") or who.get("login") or who.get("display_name") or ""
        try:
            msgs.append((float(t), str(msg or ""), str(who).lower(), kind))
        except (TypeError, ValueError):
            pass

    try:
        data = json.loads(raw)
        items = data.get("comments", data) if isinstance(data, dict) else data
        if isinstance(data, dict) and info is not None:
            s = data.get("streamer") or {}
            if isinstance(s, dict) and s.get("name"):
                info["streamer"] = str(s["name"]).lower()
            if isinstance(s, dict) and s.get("id"):
                info["streamer_id"] = str(s["id"])
            info.update(_chat_games(data.get("video")))
        for c in items or []:
            take(c)
    except json.JSONDecodeError:
        for line in raw.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and "replayChatItemAction" in obj:
                msgs.extend(_yt_chat(obj))          # YouTube chat replay (yt-dlp live_chat)
            else:
                take(obj)
    msgs.sort(key=lambda m: m[0])
    return msgs


def _chat_games(video) -> dict:
    """{"game", "games"} from a TwitchDownloader chat file's "video": the game it was in, and each chapter's
    (seconds) when it changed game. Empty when the file doesn't say."""
    out: dict = {}
    if not isinstance(video, dict):
        return out
    games = []
    for ch in video.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        g = str(ch.get("gameDisplayName") or ch.get("description") or "").strip()
        try:
            a = float(ch.get("startMilliseconds") or 0) / 1000.0
            b = a + float(ch.get("lengthMilliseconds") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if g and b > a:
            games.append({"start": round(a, 1), "end": round(b, 1), "game": g[:60]})
    g = video.get("game") or video.get("gameDisplayName")
    if isinstance(g, dict):
        g = g.get("displayName") or g.get("name")
    if g:
        out["game"] = str(g).strip()[:60]
    elif games:
        out["game"] = max(games, key=lambda c: c["end"] - c["start"])["game"]      # the one played longest
    if len(games) > 1:
        out["games"] = games
    return out


def game_at(meta: dict, t: float) -> str:
    """The game being played at t seconds into the video ('' when unknown)."""
    for ch in meta.get("games") or []:
        if isinstance(ch, dict) and ch.get("start", 0) <= t < ch.get("end", 0):
            return str(ch.get("game") or "")
    return str(meta.get("game") or "")


def _yt_chat(obj: dict) -> list:
    """Messages from one line of a YouTube chat replay (yt-dlp's live_chat.json)."""
    r = obj.get("replayChatItemAction") or {}
    try:
        # a replay has the offset inside; yt-dlp's live-chat recording puts it next to it
        t = float(r.get("videoOffsetTimeMsec") or obj.get("videoOffsetTimeMsec") or 0) / 1000
    except (TypeError, ValueError):
        return []
    out = []
    for a in r.get("actions") or []:
        item = ((a or {}).get("addChatItemAction") or {}).get("item") or {}
        for key, kind in (("liveChatTextMessageRenderer", ""), ("liveChatPaidMessageRenderer", "superchat"),
                          ("liveChatMembershipItemRenderer", "sub"), ("liveChatSponsorshipsGiftPurchaseAnnouncementRenderer", "subgift")):
            m = item.get(key)
            if not isinstance(m, dict):
                continue
            runs = ((m.get("message") or m.get("headerSubtext") or {}).get("runs")) or []
            text = "".join(x.get("text") or _yt_emoji(x.get("emoji") or {}) for x in runs if isinstance(x, dict))
            who = ((m.get("authorName") or {}).get("simpleText")) or ""
            out.append((t, text, str(who).lower(), kind))
    return out


def _yt_emoji(e: dict) -> str:
    """A YouTube chat emoji as text: the real character for standard emoji (so 😂 counts as a laugh,
    like on Twitch), the name for a channel's own emoji (":_hahaLaugh:" -> "hahaLaugh")."""
    if not isinstance(e, dict):
        return ""
    if not e.get("isCustomEmoji") and e.get("emojiId") and len(str(e["emojiId"])) <= 8:
        return str(e["emojiId"])
    sc = (e.get("shortcuts") or [""])[0] or ""
    name = sc.strip(":").lstrip("_")
    return f" {name} " if name else ""


def chat_times(chat: list) -> list:
    return [m[0] for m in chat]
