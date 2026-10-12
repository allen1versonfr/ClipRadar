"""
Updates from GitHub Releases.

VodClipper checks the latest release of the repository and compares its tag
(vX.Y.Z) with the running version. Installed copies download the new
VodClipper-Setup.exe and run it silently; the installer closes VodClipper,
upgrades it in place and opens it again. Portable and source copies just point
to the release page.

VODCLIPPER_UPDATE_API overrides the release URL (tests).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import paths
from version import VERSION

REPO = "ali1qbal/vodclipper"
API = os.environ.get("VODCLIPPER_UPDATE_API", f"https://api.github.com/repos/{REPO}/releases/latest")
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SETUP_ASSET = "VodClipper-Setup.exe"
AUTO_EVERY = 6 * 3600                      # background check interval
PREFS = paths.DATA_DIR / "update.json"
DOWNLOADS = paths.DATA_DIR / "updates"

_lock = threading.Lock()
state = {
    "current": VERSION,
    "state": "idle",          # idle | checking | downloading | ready | installing | error
    "available": False,
    "latest": "",
    "notes": "",
    "page": RELEASES_PAGE,
    "published": "",
    "size": 0,
    "progress": 0.0,
    "error": "",
    "checked": 0.0,
}
_asset: dict = {}
_file = [""]


def parse_version(v: str) -> tuple:
    """'v2.10.1' -> (2, 10, 1). Anything after the numbers (-beta) is ignored."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def newer(a: str, b: str) -> bool:
    """True when version a is newer than version b."""
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def installed() -> bool:
    """Installed with Setup (so it can update itself): the uninstaller sits next to the exe."""
    return paths.FROZEN and sys.platform == "win32" and any(
        (paths.APP_DIR / n).is_file() for n in ("unins000.exe", "unins001.exe"))


def prefs() -> dict:
    try:
        p = json.loads(PREFS.read_text("utf-8"))
        return {"auto": bool(p.get("auto", True))}
    except Exception:
        return {"auto": True}


def set_prefs(auto: bool):
    try:
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps({"auto": bool(auto)}), "utf-8")
    except OSError:
        pass


def view() -> dict:
    with _lock:
        v = dict(state)
    v["can_install"] = installed()
    v["auto"] = prefs()["auto"]
    return v


def _request(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"VodClipper/{VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def _plain_notes(md: str) -> str:
    """Release notes for display: drop the download footer and markdown emphasis."""
    md = (md or "").split("\n---", 1)[0]
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    md = re.sub(r"`([^`]+)`", r"\1", md)
    md = re.sub(r"^#+\s*", "", md, flags=re.M)
    return md.strip()


def check(log=print) -> dict:
    """Ask GitHub for the latest release. Never raises; problems land in state['error']."""
    with _lock:
        if state["state"] in ("checking", "downloading", "installing"):
            return dict(state)
        state.update(state="checking", error="")
    try:
        with _request(API) as r:
            rel = json.load(r)
        tag = rel.get("tag_name") or ""
        if not parse_version(tag):
            raise ValueError(f"unexpected release tag {tag!r}")
        asset = next((a for a in rel.get("assets") or [] if a.get("name") == SETUP_ASSET), {})
        avail = newer(tag, VERSION)
        with _lock:
            _asset.clear()
            _asset.update(asset)
            state.update(state="ready" if (avail and _file[0] and _file[0].endswith(f"-{tag.lstrip('v')}.exe")) else "idle",
                         available=avail, latest=tag.lstrip("v"), notes=_plain_notes(rel.get("body", "")),
                         page=rel.get("html_url") or RELEASES_PAGE, published=rel.get("published_at") or "",
                         size=int(asset.get("size") or 0), checked=time.time(), error="")
        log(f"Update check: latest {tag}, running {VERSION}{' (update available)' if avail else ''}")
    except urllib.error.HTTPError as e:
        msg = "GitHub is limiting update checks right now. Try again in an hour." if e.code in (403, 429) \
            else "No releases found." if e.code == 404 else f"Couldn't check for updates (GitHub said {e.code})."
        with _lock:
            state.update(state="error", error=msg, checked=time.time())
    except ValueError as e:
        log(f"Update check: {e}")
        with _lock:
            state.update(state="error", available=False, checked=time.time(),
                         error="The latest release on GitHub doesn't have a version number, so it was skipped.")
    except Exception as e:
        log(f"Update check failed: {e}")
        with _lock:
            state.update(state="error", error="Couldn't reach GitHub to check for updates. Check your internet connection.",
                         checked=time.time())
    return view()


def download(log=print) -> str:
    """Download the new installer (resumes nothing; small enough to fetch again). Returns its path."""
    with _lock:
        if not state["available"] or not _asset.get("browser_download_url"):
            raise RuntimeError("There's no update to download.")
        if state["state"] in ("downloading", "installing"):
            raise RuntimeError("The update is already downloading.")
        url, size, digest = _asset["browser_download_url"], int(_asset.get("size") or 0), _asset.get("digest") or ""
        ver = state["latest"]
        state.update(state="downloading", progress=0.0, error="")
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    for old in DOWNLOADS.glob("VodClipper-Setup-*"):        # earlier updates
        try:
            old.unlink()
        except OSError:
            pass
    dest = DOWNLOADS / f"VodClipper-Setup-{ver}.exe"
    part = dest.with_suffix(".part")
    try:
        h = hashlib.sha256()
        got = 0
        with _request(url, timeout=60) as r, open(part, "wb") as f:
            total = size or int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if total:
                    with _lock:
                        state["progress"] = min(got / total, 1.0)
        if size and got != size:
            raise RuntimeError(f"the download was incomplete ({got} of {size} bytes)")
        if digest.startswith("sha256:") and h.hexdigest() != digest.split(":", 1)[1].lower():
            raise RuntimeError("the download didn't match GitHub's checksum")
        os.replace(part, dest)
    except Exception as e:
        try:
            part.unlink()
        except OSError:
            pass
        log(f"Update download failed: {e}")
        with _lock:
            state.update(state="error", error=f"Couldn't download the update: {e}" if isinstance(e, RuntimeError)
                         else "Couldn't download the update. Check your internet connection and try again.")
        raise RuntimeError(state["error"]) from None
    _file[0] = str(dest)
    with _lock:
        state.update(state="ready", progress=1.0)
    log(f"Update {ver} downloaded to {dest}")
    return str(dest)


def launch_installer(path: str, log=print):
    """Run Setup silently. It waits for VodClipper to close, upgrades it, then opens it again."""
    if not installed():
        raise RuntimeError("This copy of VodClipper wasn't installed with Setup, so it can't update itself.")
    args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1",
            f"/LOG={DOWNLOADS / 'update-install.log'}"]
    flags = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags, cwd=str(DOWNLOADS))
    with _lock:
        state.update(state="installing")
    log(f"Started the installer for {state['latest']}")


def start_background(log=print):
    """Check shortly after start-up, then every few hours, while automatic checks are on."""
    def loop():
        time.sleep(8)
        while True:
            if prefs()["auto"]:
                check(log)
            time.sleep(AUTO_EVERY)
    threading.Thread(target=loop, daemon=True, name="update-check").start()
