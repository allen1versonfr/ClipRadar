"""macOS foundation checks that need no video or network fixtures.

Run with: python tests/test_macos_foundation.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import paths  # noqa: E402
import secret  # noqa: E402
import tools_setup  # noqa: E402
try:
    import engine  # noqa: E402
except ImportError:
    engine = None

failures: list[str] = []


def check(name: str, condition: bool):
    print(f"[{'ok' if condition else 'FAIL'}] {name}", flush=True)
    if not condition:
        failures.append(name)


def _raises(call, message: str) -> bool:
    try:
        call()
    except RuntimeError as exc:
        return message in str(exc)
    return False


with tempfile.TemporaryDirectory(prefix="clipradar_mac_") as tmp:
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = tmp
    old_frozen, old_mac, old_win = paths.FROZEN, paths.IS_MAC, paths.IS_WIN
    try:
        paths.FROZEN, paths.IS_MAC, paths.IS_WIN = True, True, False
        home = Path(tmp)
        (home / "Movies").mkdir()
        check("frozen app data uses macOS Application Support", paths._data_dir() == home / "Library/Application Support/ClipRadar")
        check("macOS projects default under Movies", paths._work_root() == home / "Movies/Clip Radar")
        (home / "Movies").rmdir()
        check("missing Movies folder is created for exports", paths._work_root() == home / "Movies/Clip Radar"
              and (home / "Movies").is_dir())
        paths.IS_MAC, paths.IS_WIN = False, True
        os.environ["LOCALAPPDATA"] = str(home / "Local")
        check("Windows data path remains LocalAppData", paths._data_dir() == home / "Local/ClipRadar")
    finally:
        paths.FROZEN, paths.IS_MAC, paths.IS_WIN = old_frozen, old_mac, old_win
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        os.environ.pop("LOCALAPPDATA", None)

items: dict[tuple[str, str], str] = {}
fake_keyring = types.SimpleNamespace(
    set_password=lambda service, account, value: items.__setitem__((service, account), value),
    get_password=lambda service, account: items.get((service, account)),
    delete_password=lambda service, account: items.pop((service, account), None),
    errors=types.SimpleNamespace(PasswordDeleteError=type("PasswordDeleteError", (Exception,), {})),
)
_MacKeyring = type("Keyring", (), {})
_MacKeyring.__module__ = "keyring.backends.macOS"
fake_keyring.get_keyring = lambda: _MacKeyring()
sys.modules["keyring"] = fake_keyring
old_platform = sys.platform
try:
    sys.platform = "darwin"
    marker = secret.protect(b"twitch-token")
    check("macOS stores a Keychain reference rather than the token", marker.startswith(b"keychain:") and b"twitch-token" not in marker)
    check("Keychain reference round trips", secret.unprotect(marker) == b"twitch-token")
    check("missing Keychain item fails with a sign-in instruction",
          _raises(lambda: secret.unprotect(b"keychain:missing"), "Sign in again"))
    secret.delete(marker)
    check("deleting a credential removes its Keychain item", not items)
finally:
    sys.platform = old_platform

check("legacy plain credential values remain readable", secret.unprotect(b"plain:dG9rZW4=") == b"token")

assets = [
    {"name": "TwitchDownloaderCLI-1.0-MacOSArm64.zip"},
    {"name": "TwitchDownloaderCLI-1.0-MacOS-x64.zip"},
    {"name": "TwitchDownloaderCLI-1.0-Windows-x64.zip"},
]
check("tool setup selects the arm64 TwitchDownloader release", tools_setup._twitchdownloader_asset(assets, True, "arm64") == assets[0])
check("tool setup selects the Intel Mac TwitchDownloader release", tools_setup._twitchdownloader_asset(assets, True, "x86_64") == assets[1])
check("tool setup keeps the existing Windows TwitchDownloader release", tools_setup._twitchdownloader_asset(assets, False, "AMD64") == assets[2])
with tempfile.NamedTemporaryFile(prefix="clipradar_hash_") as sample:
    sample.write(b"signed media tool")
    sample.flush()
    check("pinned FFmpeg checksums use SHA-256", len(tools_setup.FFMPEG_MAC_SHA256["ffmpeg"]) == 64
          and tools_setup._sha256(sample.name) != tools_setup.FFMPEG_MAC_SHA256["ffmpeg"])

if engine is None:
    print("[skip] executable discovery checks need the project NumPy dependency")
else:
    with tempfile.TemporaryDirectory(prefix="clipradar_tools_") as tmp:
        folder = Path(tmp)
        native, windows = folder / "ClipRadarProbe", folder / "ClipRadarProbe.exe"
        win_only = folder / "ClipRadarWindowsOnly.exe"
        native.write_text("#!/bin/sh\n")
        native.chmod(0o755)
        windows.write_text("test executable\n")
        win_only.write_text("test executable\n")
        prior_dirs, prior_win = paths.tools_dirs, paths.IS_WIN
        paths.tools_dirs = lambda: [folder]
        try:
            paths.IS_WIN = False
            check("macOS discovery prefers native tools and ignores Windows executables",
                  engine.which("ClipRadarProbe") == str(native))
            check("a Windows-only executable is not discovered on macOS", engine.which("ClipRadarWindowsOnly") is None)
            paths.IS_WIN = True
            check("Windows discovery still finds .exe tools", engine.which("ClipRadarProbe") == str(windows))
        finally:
            paths.tools_dirs, paths.IS_WIN = prior_dirs, prior_win
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
