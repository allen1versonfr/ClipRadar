"""
Downloads and updates the helper programs Clip Radar uses (Windows):
  ffmpeg / ffprobe       cutting, reframing and encoding video
  TwitchDownloaderCLI    chat replay (and a backup VOD downloader)
  yt-dlp                 VOD downloader, updated whenever Twitch changes things

Everything goes into paths.TOOLS_WRITE, so the built .exe never needs rebuilding
just to get newer tools. Can also be run directly:  python tools_setup.py
"""

from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import ssl
import subprocess
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

FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_ZIP_MIRROR = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                     "ffmpeg-master-latest-win64-gpl.zip")
TDL_API = "https://api.github.com/repos/lay295/TwitchDownloader/releases/latest"
YTDLP_EXE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_MAC = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
FFMPEG_MAC_RELEASE = "9.0.2"
FFMPEG_MAC_URLS = {
    "ffmpeg": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
    "ffprobe": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffprobe.zip",
}
FFMPEG_MAC_SHA256 = {
    "ffmpeg": "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
    "ffprobe": "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
}
FFMPEG_MAC_TEAM_ID = "KU3N25YGLU"
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
                if sys.platform == "darwin":
                    os.chmod(target, os.stat(target).st_mode | 0o111)
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
        "can_download": paths.IS_WIN or paths.IS_MAC,
    }


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_macos_signature(path: str) -> None:
    """Require the FFmpeg vendor's Developer ID signature before installing a binary."""
    verify = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", path],
                            capture_output=True, text=True, timeout=30)
    if verify.returncode:
        raise RuntimeError(f"macOS rejected the FFmpeg signature for {os.path.basename(path)}.")
    details = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", path],
                             capture_output=True, text=True, timeout=30)
    identity = (details.stdout or "") + (details.stderr or "")
    if details.returncode or f"TeamIdentifier={FFMPEG_MAC_TEAM_ID}" not in identity:
        raise RuntimeError(f"FFmpeg has an unexpected signing identity for {os.path.basename(path)}.")


def _twitchdownloader_asset(assets: list, is_mac: bool, machine: str) -> Optional[dict]:
    if is_mac:
        suffix = "MacOSArm64" if machine.lower() in ("arm64", "aarch64") else "MacOS-x64"
        return next((a for a in assets if a.get("name", "").startswith("TwitchDownloaderCLI")
                     and a.get("name", "").endswith(f"-{suffix}.zip")), None)
    return next((a for a in assets if a.get("name", "").startswith("TwitchDownloaderCLI")
                 and "Windows-x64" in a.get("name", "") and a.get("name", "").endswith(".zip")), None)


def get_ffmpeg(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    if paths.IS_MAC:
        if platform.machine().lower() not in ("arm64", "aarch64"):
            raise RuntimeError("Automatic FFmpeg setup currently supports Apple Silicon Macs. Install a native FFmpeg build on this Intel Mac.")
        log(f"Downloading signed FFmpeg {FFMPEG_MAC_RELEASE} for Apple Silicon...")
        out = str(paths.TOOLS_WRITE)
        os.makedirs(out, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            names = list(FFMPEG_MAC_URLS)
            verified = {}
            for i, name in enumerate(names):
                archive = os.path.join(td, f"{name}.zip")
                lo, hi = i / len(names), (i + 1) / len(names)
                download(FFMPEG_MAC_URLS[name], archive,
                         lambda f, a=lo, b=hi: progress(a + (b - a) * f), check)
                if _sha256(archive) != FFMPEG_MAC_SHA256[name]:
                    raise RuntimeError(f"The downloaded {name} failed its SHA-256 check. Please try again.")
                extracted = os.path.join(td, "verified")
                extract_named(archive, [name], extracted)
                binary = os.path.join(extracted, name)
                _check_macos_signature(binary)
                try:
                    p = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=30)
                except Exception as e:
                    raise RuntimeError(f"The downloaded {name} couldn't run: {e}") from None
                if p.returncode or f"{FFMPEG_MAC_RELEASE}" not in (p.stdout + p.stderr):
                    raise RuntimeError(f"The downloaded {name} failed its version check.")
                verified[name] = binary
            # Keep a known-good existing install until both new executables pass hash, signature and version checks.
            for name, binary in verified.items():
                staged = os.path.join(out, name + ".new")
                shutil.copy2(binary, staged)
                os.replace(staged, os.path.join(out, name))
        import engine
        if not (engine.which("ffmpeg") and engine.which("ffprobe")):
            raise RuntimeError("FFmpeg setup finished, but Clip Radar still can't find ffmpeg and ffprobe.")
        encoders = subprocess.run([engine.which("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True,
                                  text=True, timeout=30)
        filters = subprocess.run([engine.which("ffmpeg"), "-hide_banner", "-filters"], capture_output=True,
                                 text=True, timeout=30)
        protocols = subprocess.run([engine.which("ffmpeg"), "-hide_banner", "-protocols"], capture_output=True,
                                   text=True, timeout=30)
        caps = (encoders.stdout + filters.stdout + protocols.stdout)
        if "h264_videotoolbox" not in caps or "libx264" not in caps or "subtitles" not in caps or "https" not in caps:
            raise RuntimeError("FFmpeg is missing a required encoder, caption filter, or network protocol.")
        log("Signed FFmpeg and ffprobe are installed and their required capabilities passed checks.")
        progress(1.0)
        return
    out = str(paths.TOOLS_WRITE)
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "ffmpeg.zip")
        try:
            log("Downloading ffmpeg (about 100 MB)...")
            download(FFMPEG_ZIP, z, progress, check)
        except Exception as e:
            if "Cancelled" in str(e):
                raise
            log(f"Main ffmpeg source failed ({str(e)[:80]}), trying a mirror...")
            download(FFMPEG_ZIP_MIRROR, z, progress, check)
        extract_named(z, ["ffmpeg.exe", "ffprobe.exe"], out)
    log("ffmpeg ready.")


def get_twitchdownloader(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    out = str(paths.TOOLS_WRITE)
    log("Finding the latest TwitchDownloaderCLI...")
    name = url = None
    try:
        rel = _get_json(TDL_API)
        if paths.IS_MAC:
            asset = _twitchdownloader_asset(rel.get("assets", []), True, platform.machine())
        else:
            asset = _twitchdownloader_asset(rel.get("assets", []), False, platform.machine())
        if asset:
            name, url = asset["name"], asset["browser_download_url"]
    except Exception as e:
        log(f"  GitHub API unavailable ({str(e)[:60]}), using the releases page instead.")
    if not url:
        tag = _latest_tag("lay295/TwitchDownloader")
        suffix = ("MacOSArm64" if platform.machine().lower() in ("arm64", "aarch64") else "MacOS-x64") if paths.IS_MAC else "Windows-x64"
        name = f"TwitchDownloaderCLI-{tag}-{suffix}.zip"
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
        extract_named(z, ["TwitchDownloaderCLI" if paths.IS_MAC else "TwitchDownloaderCLI.exe"], out)
    log("TwitchDownloaderCLI ready.")


def get_ytdlp(log: Log = _noop, progress: Progress = _noop, check: Check = _noop):
    target = os.path.join(str(paths.TOOLS_WRITE), "yt-dlp" if paths.IS_MAC else "yt-dlp.exe")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    log("Downloading the latest yt-dlp...")
    download(YTDLP_MAC if paths.IS_MAC else YTDLP_EXE, target, progress, check)
    if paths.IS_MAC:
        os.chmod(target, os.stat(target).st_mode | 0o111)
    log("yt-dlp is up to date.")


def setup(want: list, log: Log = _noop, progress: Progress = _noop, check: Check = _noop) -> dict:
    """want: any of 'ffmpeg', 'twitchdownloader', 'ytdlp'."""
    if not (paths.IS_WIN or paths.IS_MAC):
        raise RuntimeError("Automatic tool setup is available for Windows and macOS. Install FFmpeg and yt-dlp with your system package manager.")
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
