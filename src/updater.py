"""
Updates from GitHub Releases.

Windows installed copies download and run ClipRadar-Setup.exe. Installed
macOS .app copies download the Apple Silicon ZIP, stage a replacement bundle,
then use a detached helper to replace and reopen the app after it exits.
Portable and source copies point to the release page.

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
PLATFORM = sys.platform
API = os.environ.get("CLIPRADAR_UPDATE_API", f"https://api.github.com/repos/{REPO}/releases/latest")
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SETUP_ASSET = "ClipRadar-Setup.exe"
OLD_ASSET = "VodClipper-Setup.exe"         # releases also carry the installer under the old name, for VodClipper's updater
MAC_ASSET = "ClipRadar-macOS-arm64.zip"
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
    """Whether this packaged copy can replace itself in its current location."""
    if not paths.FROZEN:
        return False
    if PLATFORM == "win32":
        return any((paths.APP_DIR / n).is_file() for n in ("unins000.exe", "unins001.exe"))
    if PLATFORM == "darwin":
        bundle = getattr(paths, "APP_BUNDLE", None)
        return bool(bundle and bundle.is_dir() and os.access(bundle.parent, os.W_OK))
    return False


def _download_name(version: str) -> str:
    return f"ClipRadar-macOS-arm64-{version}.zip" if PLATFORM == "darwin" else f"ClipRadar-Setup-{version}.exe"


def downloaded_for(version: str) -> str:
    path = _file[0]
    return path if path and os.path.isfile(path) and os.path.basename(path) == _download_name(version) else ""


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
        has_asset = bool(_asset.get("browser_download_url"))
    v["can_install"] = installed() and (has_asset if v["available"] else True)
    tok = get_token()
    v["token"] = bool(tok)
    v["token_hint"] = ("…" + tok[-4:]) if tok else ""
    v["auto"] = prefs()["auto"]
    return v


def can_install_update() -> bool:
    with _lock:
        return installed() and bool(state["available"] and _asset.get("browser_download_url"))


TOKEN_FILE = paths.DATA_DIR / "github.dat"


def get_token() -> str:
    """GitHub access token for a private repository (stored encrypted), or ''."""
    try:
        from secret import unprotect
        return unprotect(TOKEN_FILE.read_bytes()).decode("utf-8").strip()
    except Exception:
        return ""


def set_token(tok: str):
    tok = (tok or "").strip()
    if not tok:
        try:
            from secret import delete
            delete(TOKEN_FILE.read_bytes())
        except Exception:
            pass
        try:
            TOKEN_FILE.unlink()
        except OSError:
            pass
        return
    if not re.fullmatch(r"(ghp_|github_pat_|gho_|ghs_)[A-Za-z0-9_]{20,255}", tok):
        raise ValueError("That doesn't look like a GitHub token. They start with github_pat_ or ghp_.")
    from secret import delete, protect
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    old = TOKEN_FILE.read_bytes() if TOKEN_FILE.exists() else b""
    new = protect(tok.encode("utf-8"))
    tmp = TOKEN_FILE.with_suffix(".tmp")
    try:
        tmp.write_bytes(new)
        os.replace(tmp, TOKEN_FILE)
    except Exception:
        delete(new)
        raise
    if old and old != new:
        delete(old)


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
        asset = _pick_asset(rel.get("assets") or [])
        avail = newer(tag, VERSION)
        with _lock:
            _asset.clear()
            _asset.update(asset)
            state.update(state="ready" if (avail and downloaded_for(tag.lstrip("v"))) else "idle",
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


_versions = {"at": 0.0, "list": []}
LIST_API = API.rsplit("/latest", 1)[0] + "?per_page=100"


def _pick_asset(assets: list) -> dict:
    if PLATFORM == "darwin":
        return next((a for a in assets if a.get("name") == MAC_ASSET), {})
    return next((a for a in assets if a.get("name") == SETUP_ASSET), None) or \
        next((a for a in assets if a.get("name") == OLD_ASSET), {})


def versions(refresh: bool = False, log=print) -> dict:
    """Every release on GitHub, newest first (Settings › Updates › All versions). Cached for 10 minutes."""
    if refresh or time.time() - _versions["at"] > 600 or not _versions["list"]:
        try:
            with _request(LIST_API) as r:
                rels = json.load(r)
            out = []
            for rel in rels if isinstance(rels, list) else []:
                tag = rel.get("tag_name") or ""
                if not parse_version(tag) or rel.get("draft"):
                    continue                        # the laughter-model download (models-v1) isn't a version
                a = _pick_asset(rel.get("assets") or [])
                out.append({"version": tag.lstrip("v"), "name": rel.get("name") or tag, "published": rel.get("published_at") or "",
                            "notes": _plain_notes(rel.get("body", "")), "page": rel.get("html_url") or RELEASES_PAGE,
                            "setup": a, "archive": not a})
            out.sort(key=lambda x: parse_version(x["version"]), reverse=True)
            _versions.update(at=time.time(), list=out, error="")
        except Exception as e:
            log(f"Couldn't list versions: {e}")
            _versions["error"] = "Couldn't get the list of versions from GitHub. Check your internet connection."
    can = installed()
    rows = []
    for x in _versions["list"]:
        rows.append({k: v for k, v in x.items() if k != "setup"} | {
            "current": x["version"] == VERSION, "newer": newer(x["version"], VERSION),
            "installable": can and newer(x["version"], VERSION) and bool(x["setup"].get("browser_download_url"))})
    return {"current": VERSION, "versions": rows, "error": _versions.get("error", ""), "can_install": can}


def choose(version: str):
    """Install this newer version instead of the latest (Settings › Updates › All versions). Going back isn't offered."""
    if not newer(version, VERSION):
        raise ValueError("You can only install a version newer than the one you have.")
    if not _versions["list"]:
        versions(refresh=True)
    rel = next((x for x in _versions["list"] if x["version"] == version), None)
    if not rel or not rel["setup"].get("browser_download_url"):
        raise ValueError(f"Version {version} can't be installed from here (it has no installer).")
    with _lock:
        _asset.clear()
        _asset.update(rel["setup"])
        state.update(available=True, latest=version, notes=rel["notes"], page=rel["page"], published=rel["published"],
                     size=int(rel["setup"].get("size") or 0), state="idle", error="")
        if _file[0] and os.path.basename(_file[0]) != _download_name(version):
            _file[0] = ""


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
    for old in [*DOWNLOADS.glob("ClipRadar-Setup-*"), *DOWNLOADS.glob("VodClipper-Setup-*"),
                *DOWNLOADS.glob("ClipRadar-macOS-arm64-*")]:
        try:
            old.unlink()
        except OSError:
            pass
    dest = DOWNLOADS / _download_name(ver)
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
    """Start the platform installer/replacer and return so the app can shut down."""
    if PLATFORM == "darwin":
        if not installed():
            raise RuntimeError("Move Clip Radar to a folder you can write to (such as ~/Applications) before updating.")
        bundle = getattr(paths, "APP_BUNDLE", None)
        staged = _stage_macos_bundle(path)
        script = DOWNLOADS / "replace-macos-app.sh"
        script.write_text(_macos_replace_script(), encoding="utf-8")
        script.chmod(0o700)
        log_path = DOWNLOADS / "update-install.log"
        subprocess.Popen(["/bin/sh", str(script), str(os.getpid()), str(bundle), staged, str(log_path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True, cwd=str(DOWNLOADS))
        with _lock:
            state.update(state="installing")
        log(f"Started the macOS app replacement for {state['latest']}")
        return
    if not installed():
        raise RuntimeError("This copy of Clip Radar wasn't installed with Setup, so it can't update itself.")
    args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1",
            f"/LOG={DOWNLOADS / 'update-install.log'}"]
    flags = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags, cwd=str(DOWNLOADS))
    with _lock:
        state.update(state="installing")
    log(f"Started the installer for {state['latest']}")


def _stage_macos_bundle(archive: str) -> str:
    """Safely extract only the expected app bundle from a release ZIP."""
    import shutil
    import plistlib
    import stat
    import zipfile
    from pathlib import PurePosixPath

    if not zipfile.is_zipfile(archive):
        raise RuntimeError("The macOS update file is not a valid ZIP archive.")
    target = DOWNLOADS / f"staged-{state['latest']}"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            total = 0
            for info in zf.infolist():
                name = PurePosixPath(info.filename)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    raise RuntimeError("The macOS update ZIP contains an unexpected file path.")
                if name.parts[0] == "__MACOSX" or name.name.startswith("._"):
                    continue
                if name.parts[0] != "ClipRadar.app":
                    raise RuntimeError("The macOS update ZIP contains an unexpected file path.")
                total += info.file_size
                if total > 3 * 1024 ** 3:
                    raise RuntimeError("The macOS update ZIP is unexpectedly large.")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    link = zf.read(info).decode("utf-8", errors="strict")
                    resolved = PurePosixPath(os.path.normpath(str(name.parent / link)))
                    if PurePosixPath(link).is_absolute() or ".." in resolved.parts or not resolved.parts \
                            or resolved.parts[0] != "ClipRadar.app":
                        raise RuntimeError("The macOS update ZIP contains an unsafe symbolic link.")
        # ditto preserves the app's framework symlinks, executable modes and
        # macOS resource forks, which a plain ZIP extraction would discard.
        subprocess.run(["/usr/bin/ditto", "-x", "-k", archive, str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    bundle = target / "ClipRadar.app"
    if not (bundle / "Contents/Info.plist").is_file() or not (bundle / "Contents/MacOS/ClipRadar").is_file() \
            or not os.access(bundle / "Contents/MacOS/ClipRadar", os.X_OK):
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError("The update ZIP doesn't contain a complete Clip Radar app.")
    try:
        with (bundle / "Contents/Info.plist").open("rb") as f:
            bundle_id = plistlib.load(f).get("CFBundleIdentifier")
        if bundle_id != "com.clipradar.app":
            raise RuntimeError("The update ZIP is not a Clip Radar app bundle.")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError("macOS could not verify the updated Clip Radar app bundle.") from e
    return str(bundle)


def _macos_replace_script() -> str:
    """Detached helper waits for the app to exit, then swaps and relaunches the bundle."""
    return '''#!/bin/sh
set -eu
pid="$1"; bundle="$2"; staged="$3"; log="$4"
parent=$(dirname "$bundle")
candidate="$parent/.ClipRadar.app.update.$$"
backup="$parent/.ClipRadar.app.old.$$"
fail() { printf '%s\\n' "$*" >> "$log"; exit 1; }
while /bin/kill -0 "$pid" 2>/dev/null; do /bin/sleep 0.5; done
[ -w "$parent" ] || fail "The app folder is not writable; move Clip Radar to ~/Applications and retry."
/usr/bin/ditto "$staged" "$candidate" || fail "Could not prepare the updated app bundle."
/bin/mv "$bundle" "$backup" || fail "Could not move the old app bundle."
if /bin/mv "$candidate" "$bundle"; then
  /bin/rm -rf "$backup"
  /usr/bin/open "$bundle" || fail "The update was installed but Clip Radar did not reopen."
  /bin/rm -rf "$(dirname "$staged")"
  printf '%s\\n' "Installation process succeeded" >> "$log"
else
  /bin/mv "$backup" "$bundle"
  fail "Could not install the updated app; restored the previous version."
fi
'''


def start_background(log=print):
    """Check shortly after start-up, then every few hours, while automatic checks are on."""
    def loop():
        time.sleep(8)
        while True:
            if prefs()["auto"]:
                check(log)
            time.sleep(AUTO_EVERY)
    threading.Thread(target=loop, daemon=True, name="update-check").start()
