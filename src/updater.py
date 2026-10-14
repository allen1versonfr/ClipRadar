"""
Updates from GitHub Releases.

Clip Radar checks the latest release of the repository and compares its tag
(vX.Y.Z) with the running version. Installed copies download the new
ClipRadar-Setup.exe and run it silently; the installer closes Clip Radar,
upgrades it in place and opens it again. Portable and source copies just point
to the release page.

CLIPRADAR_UPDATE_API overrides the release URL (tests).
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
import urllib.parse
import urllib.request

import paths
from version import VERSION

REPO = "allen1versonfr/vodclipper"
API = os.environ.get("CLIPRADAR_UPDATE_API", f"https://api.github.com/repos/{REPO}/releases/latest")
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SETUP_ASSET = "ClipRadar-Setup.exe"
OLD_ASSET = "VodClipper-Setup.exe"         # releases also carry the installer under the old name, for VodClipper's updater
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
    tok = get_token()
    v["token"] = bool(tok)
    v["token_hint"] = ("…" + tok[-4:]) if tok else ""
    v["auto"] = prefs()["auto"]
    return v


TOKEN_FILE = paths.DATA_DIR / "github.dat"


def get_token() -> str:
    """GitHub access token for a private repository (stored encrypted), or ''."""
    try:
        from tiktok import _unprotect
        return _unprotect(TOKEN_FILE.read_bytes()).decode("utf-8").strip()
    except Exception:
        return ""


def set_token(tok: str):
    tok = (tok or "").strip()
    if not tok:
        try:
            TOKEN_FILE.unlink()
        except OSError:
            pass
        return
    if not re.fullmatch(r"(ghp_|github_pat_|gho_|ghs_)[A-Za-z0-9_]{20,255}", tok):
        raise ValueError("That doesn't look like a GitHub token. They start with github_pat_ or ghp_.")
    from tiktok import _protect
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_protect(tok.encode("utf-8")))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _request(url: str, timeout: float = 15, accept: str = "application/vnd.github+json", auth: bool = True):
    headers = {"Accept": accept, "User-Agent": f"Clip Radar/{VERSION}", "X-GitHub-Api-Version": "2022-11-28"}
    tok = get_token() if auth else ""
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    if tok:
        try:
            return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not loc:
                raise
            loc = urllib.parse.urljoin(url, loc)
            if urllib.parse.urlsplit(loc).netloc == urllib.parse.urlsplit(url).netloc:
                # same API host: a renamed or moved repository. Keep the token.
                return _request(loc, timeout, accept=accept, auth=True)
            # private assets redirect to a signed download link that must NOT get the token
            return _request(loc, timeout, accept="application/octet-stream", auth=False)
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
        assets = rel.get("assets") or []
        asset = next((a for a in assets if a.get("name") == SETUP_ASSET), None) or \
            next((a for a in assets if a.get("name") == OLD_ASSET), {})
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
        has_tok = bool(get_token())
        if e.code == 401:
            msg = "GitHub didn't accept your access token. Check it (or remove it) under Private repository below."
        elif e.code in (403, 429):
            msg = "GitHub is limiting update checks right now. Try again in an hour."
        elif e.code == 404:
            msg = ("Your access token can't see Clip Radar's releases. Give it read access to the repository's contents."
                   if has_tok else "Couldn't find Clip Radar's releases. The repository is private: add a GitHub "
                   "access token under Private repository below, or make the repository public.")
        else:
            msg = f"Couldn't check for updates (GitHub said {e.code})."
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
        url, size, digest = _asset["browser_download_url"], int(_asset.get("size") or 0), _asset.get("digest") or ""
        api_url = _asset.get("url") or ""
        ver = state["latest"]
        state.update(state="downloading", progress=0.0, error="")
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    for old in [*DOWNLOADS.glob("ClipRadar-Setup-*"), *DOWNLOADS.glob("VodClipper-Setup-*")]:        # earlier updates
        try:
            old.unlink()
        except OSError:
            pass
    dest = DOWNLOADS / f"ClipRadar-Setup-{ver}.exe"
    part = dest.with_suffix(".part")
    try:
        h = hashlib.sha256()
        got = 0
        src = _request(api_url, timeout=60, accept="application/octet-stream") if (api_url and get_token()) \
            else _request(url, timeout=60, auth=False)
        with src as r, open(part, "wb") as f:
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
    """Run Setup silently. It waits for Clip Radar to close, upgrades it, then opens it again."""
    if not installed():
        raise RuntimeError("This copy of Clip Radar wasn't installed with Setup, so it can't update itself.")
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
