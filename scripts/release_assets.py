"""Enforce release branding and retire duplicate legacy installers on Clip Radar releases."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path


def legacy_executable(name: str) -> bool:
    return name.casefold().startswith("vodclipper") and name.casefold().endswith(".exe")


def cleanup_candidates(release: dict) -> list[dict]:
    # Preserve genuine pre-rebrand archives and never remove a release's only installer.
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", release.get("tag_name", ""))
    assets = release.get("assets", [])
    if release.get("draft") or not match or int(match[1]) < 4:
        return []
    if not any(a.get("name") == "ClipRadar-Setup.exe" for a in assets):
        return []
    return [a for a in assets if legacy_executable(a.get("name", ""))]


def validate(version: str, files: list[str]) -> None:
    expected = {"ClipRadar-Setup.exe", "ClipRadar-portable.zip", f"ClipRadar-{version}-source.zip"}
    names = [Path(file).name for file in files]
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError(f"Release files must be exactly {sorted(expected)}; got {names}")
    for file in files:
        if not Path(file).is_file() or Path(file).stat().st_size == 0:
            raise ValueError(f"Missing or empty release file: {file}")


def cleanup() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "allen1versonfr/ClipRadar":
        raise ValueError("Legacy installer cleanup is restricted to the main ClipRadar repository")
    token = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]

    def request(path: str, method: str = "GET"):
        req = urllib.request.Request("https://api.github.com" + path, method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ClipRadar-release-maintenance"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response) if method == "GET" else None

    removed = 0
    page = 1
    while True:
        releases = request(f"/repos/{repository}/releases?per_page=100&page={page}")
        if not releases:
            break
        for release in releases:
            # Release responses can contain only the first page of assets.
            assets = []
            asset_page = 1
            while True:
                chunk = request(f"/repos/{repository}/releases/{release['id']}/assets?per_page=100&page={asset_page}")
                assets.extend(chunk)
                if len(chunk) < 100:
                    break
                asset_page += 1
            for asset in cleanup_candidates(dict(release, assets=assets)):
                request(f"/repos/{repository}/releases/assets/{int(asset['id'])}", "DELETE")
                removed += 1
                print(f"Removed {release['tag_name']}: {asset['name']}", flush=True)
        page += 1
    print(f"Removed {removed} duplicate legacy installer(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "cleanup"))
    parser.add_argument("--version")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    if args.command == "cleanup":
        cleanup()
    else:
        validate(args.version, args.files)
