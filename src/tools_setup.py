"""
Downloads and updates the helper programs Clip Radar uses (Windows):
  ffmpeg / ffprobe       cutting, reframing and encoding video
  TwitchDownloaderCLI    chat replay (and a backup VOD downloader)
  yt-dlp                 VOD downloader, updated whenever Twitch changes things

Everything goes into paths.TOOLS_WRITE, so the built .exe never needs rebuilding
just to get newer tools. Can also be run directly:  python tools_setup.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

import paths

Log = Callable[[str], None]
Progress = Callable[[float], None]
Check = Callable[[], None]

# FFmpeg is pinned: one release, checked against its SHA-256. A floating "latest" once swapped a
# 100 MB release build for a 162 MB build of FFmpeg's development branch with nobody noticing.
# The main download is BtbN's shared build of FFmpeg 9.0.2: ffmpeg.exe and ffprobe.exe are small
# and share one set of DLLs (about 180 MB for both) instead of two static programs that each hold
# everything (about 200 MB). BtbN keeps the last build of each month for two years, so this is one.
# The mirror is gyan.dev's static "essentials" build of the same release, from its GitHub releases,
# which are kept. Either way, the files land together in the tools folder (see _install_ffmpeg).
FFMPEG_ZIP = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/"
              "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-shared-9.0.zip")
FFMPEG_SHA256 = "1d9823c35c7a7e2f54d1438cf61030d6efabe3278de01c191309d1e828bb74df"
FFMPEG_ZIP_MIRROR = ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
                     "ffmpeg-9.0.2-essentials_build.zip")
FFMPEG_MIRROR_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
# FFmpeg's own libraries, as a shared build names them (avcodec-63.dll, swscale-10.dll, ...)
FFMPEG_DLL = re.compile(r"^(avcodec|avdevice|avfilter|avformat|avutil|postproc|swresample|swscale)-\d+\.dll$",
                        re.IGNORECASE)
TDL_API = "https://api.github.com/repos/lay295/TwitchDownloader/releases/latest"
YTDLP_EXE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
UA = {"User-Agent": "ClipRadar/1.0 (+https://github.com)"}


def _noop(*_a, **_k):
    pass


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # bundled with yt-dlp[default]; helps on PCs with stale root certs
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _get_json(url: str) -> dict:
    headers = {**UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def _latest_tag(repo: str) -> str:
    """Latest release tag via the github.com redirect (no API rate limit)."""
    req = urllib.request.Request(f"https://github.com/{repo}/releases/latest", headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
        final = r.geturl()
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if not tag or tag == "latest":
        raise RuntimeError("couldn't find the latest release")
    return tag


def download(url: str, dest: str, progress: Progress = _noop, check: Check = _noop) -> str:
    req = urllib.request.Request(url, headers=UA)
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            check()
            chunk = r.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(min(0.999, done / total))
    if total and done < total:
        os.remove(tmp)
        raise RuntimeError(f"Download was cut off ({done // 1_000_000} of {total // 1_000_000} MB).")
    os.replace(tmp, dest)
    progress(1.0)
    return dest


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: str, sha256: str) -> None:
    got = sha256_of(path)
    if got != sha256.lower():
        raise RuntimeError(f"The download didn't match its checksum (got {got[:16]}..., "
                           f"expected {sha256[:16]}...).")


def extract_named(zip_path: str, names: list, out_dir: str) -> list:
    """Pull specific files (by base name) out of a zip, wherever they sit inside it."""
    os.makedirs(out_dir, exist_ok=True)
    wanted = {n.lower() for n in names}
    found = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = os.path.basename(info.filename)
            if base.lower() in wanted and not info.is_dir():
                target = os.path.join(out_dir, base)
                with z.open(info) as src, open(target + ".new", "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(target + ".new", target)
                found.append(base.lower())
                wanted.discard(base.lower())
    if wanted:
        raise RuntimeError(f"The archive didn't contain {', '.join(sorted(wanted))}.")
    return found


def have(name: str) -> Optional[str]:
    import engine
    return engine.which(name)


def status() -> dict:
    return {
        "ffmpeg": bool(have("ffmpeg") and have("ffprobe")),
        "twitchdownloader": bool(have("TwitchDownloaderCLI")),
        "ytdlp_exe": bool(have("yt-dlp")),
        "can_download": paths.IS_WIN,
    }


def _install_ffmpeg(zip_path: str, out: str) -> list:
    """ffmpeg.exe, ffprobe.exe and, from a shared build, FFmpeg's DLLs into `out`, in an order that
    never leaves a broken pair: everything is unpacked to a folder beside `out` first, then the DLLs
    move in, then the two programs (a static ffmpeg.exe ignores DLLs next to it, and a shared one only
    arrives once its DLLs are there), and only then are FFmpeg DLLs the new programs don't use removed:
    another version's, or all of them after a static build. Returns the file names installed."""
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        members = {}
        for info in z.infolist():
            base = os.path.basename(info.filename)
            if info.is_dir() or not base:
                continue
            if base.lower() in ("ffmpeg.exe", "ffprobe.exe") or FFMPEG_DLL.match(base):
                members.setdefault(base.lower(), (base, info))
        missing = [n for n in ("ffmpeg.exe", "ffprobe.exe") if n not in members]
        if missing:
            raise RuntimeError(f"The archive didn't contain {', '.join(missing)}.")
        stage = tempfile.mkdtemp(prefix=".ffmpeg-", dir=out)
        try:
            for base, info in members.values():
                with z.open(info) as src, open(os.path.join(stage, base), "wb") as dst:
                    shutil.copyfileobj(src, dst)
            names = [b for b, _ in members.values()]
            dlls = [b for b in names if FFMPEG_DLL.match(b)]
            for base in dlls + [b for b in names if b not in dlls]:
                os.replace(os.path.join(stage, base), os.path.join(out, base))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    keep = {d.lower() for d in dlls}
    for f in os.listdir(out):
        if FFMPEG_DLL.match(f) and f.lower() not in keep:
            try:
                os.remove(os.path.join(out, f))
            except OSError:
                pass                     # in use: harmless, the new programs don't load it
    return names


def get_ffmpeg(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    out = str(paths.TOOLS_WRITE)
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "ffmpeg.zip")
        try:
            log("Downloading ffmpeg 9.0.2 (about 85 MB)...")
            download(FFMPEG_ZIP, z, progress, check)
            verify(z, FFMPEG_SHA256)
        except Exception as e:
            if "Cancelled" in str(e):
                raise
            log(f"Main ffmpeg source failed ({str(e)[:80]}), trying a mirror...")
            download(FFMPEG_ZIP_MIRROR, z, progress, check)
            verify(z, FFMPEG_MIRROR_SHA256)
        _install_ffmpeg(z, out)
    log("ffmpeg ready.")


def get_twitchdownloader(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    out = str(paths.TOOLS_WRITE)
    log("Finding the latest TwitchDownloaderCLI...")
    name = url = None
    try:
        rel = _get_json(TDL_API)
        asset = next((a for a in rel.get("assets", [])
                      if a["name"].startswith("TwitchDownloaderCLI") and "Windows-x64" in a["name"]
                      and a["name"].endswith(".zip")), None)
        if asset:
            name, url = asset["name"], asset["browser_download_url"]
    except Exception as e:
        log(f"  GitHub API unavailable ({str(e)[:60]}), using the releases page instead.")
    if not url:
        tag = _latest_tag("lay295/TwitchDownloader")
        name = f"TwitchDownloaderCLI-{tag}-Windows-x64.zip"
        url = f"https://github.com/lay295/TwitchDownloader/releases/download/{tag}/{name}"
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "tdl.zip")
        log(f"Downloading {name}...")
        for attempt in range(3):
            try:
                download(url, z, progress, check)
                break
            except Exception as e:
                if "Cancelled" in str(e) or attempt == 2:
                    raise
                log(f"  retrying ({str(e)[:60]})")
                time.sleep(3 * (attempt + 1))
        extract_named(z, ["TwitchDownloaderCLI.exe"], out)
    log("TwitchDownloaderCLI ready.")


def get_ytdlp(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    target = os.path.join(str(paths.TOOLS_WRITE), "yt-dlp.exe")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    log("Downloading the latest yt-dlp...")
    download(YTDLP_EXE, target, progress, check)
    log("yt-dlp is up to date.")


def setup(want: list, log: Log = _noop, progress: Progress = _noop, check: Check = _noop) -> dict:
    """want: any of 'ffmpeg', 'twitchdownloader', 'ytdlp'."""
    if not paths.IS_WIN:
        raise RuntimeError("Automatic tool downloads are for Windows. On this system install "
                           "ffmpeg and yt-dlp with your package manager.")
    paths.ensure_dirs()
    steps = [(k, f) for k, f in (("ffmpeg", get_ffmpeg), ("twitchdownloader", get_twitchdownloader),
                                 ("ytdlp", get_ytdlp)) if k in want]
    failed = []
    for i, (key, fn) in enumerate(steps):
        lo, hi = i / len(steps), (i + 1) / len(steps)
        try:
            fn(log, lambda x: progress(lo + (hi - lo) * x), check)
        except Exception as e:
            if "Cancelled" in type(e).__name__ or str(e) == "Cancelled":
                raise
            failed.append(key)
            log(f"Couldn't get {key}: {str(e)[:200]}")
    progress(1.0)
    if failed and len(failed) == len(steps):
        raise RuntimeError("Couldn't download the tools. Check your internet connection and try again.")
    return status()


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    want = sys.argv[1:] or ["ffmpeg", "twitchdownloader", "ytdlp"]
    if "--dest" in want:
        i = want.index("--dest")
        paths.TOOLS_WRITE = Path(want[i + 1]).resolve()
        want = want[:i] + want[i + 2:]
    last = [-1]

    def prog(x):
        p = int(x * 100)
        if p // 10 != last[0] // 10:
            print(f"    {p}%", flush=True)
        last[0] = p

    try:
        setup(want or ["ffmpeg", "twitchdownloader", "ytdlp"], log=lambda m: print("  " + m, flush=True),
              progress=prog)
    except Exception as e:
        print(f"  {e}")
        sys.exit(1)
