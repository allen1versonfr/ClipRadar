"""macOS release selection and safe app-bundle staging checks."""
from __future__ import annotations

import os
import sys
import tempfile
import hashlib
import json
import stat
import threading
import zipfile
import plistlib
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import updater  # noqa: E402

updater.PLATFORM = "darwin"
failures: list[str] = []


def check(name: str, ok: bool):
    print(f"[{'ok' if ok else 'FAIL'}] {name}", flush=True)
    if not ok:
        failures.append(name)


with tempfile.TemporaryDirectory(prefix="clipradar-update-") as tmp:
    root = Path(tmp)
    updater.DOWNLOADS = root / "updates"
    updater.state["latest"] = "4.19.0"
    mac = {"name": updater.MAC_ASSET, "browser_download_url": "https://example.test/mac.zip"}
    win = {"name": updater.SETUP_ASSET, "browser_download_url": "https://example.test/win.exe"}
    check("macOS chooses the arm64 app archive", updater._pick_asset([win, mac]) == mac)
    check("macOS does not select a Windows installer alone", not updater._pick_asset([win]))

    bundle = root / "ClipRadar.app"
    (bundle / "Contents/MacOS").mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.clipradar.app",
                                                                  "CFBundleExecutable": "ClipRadar",
                                                                  "CFBundleName": "ClipRadar",
                                                                  "CFBundlePackageType": "APPL"}))
    exe = bundle / "Contents/MacOS/ClipRadar"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    os.symlink("ClipRadar", bundle / "Contents/MacOS/ClipRadar-alias")
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    archive = root / "valid.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for file in bundle.rglob("*"):
            if file.is_file() and not file.is_symlink():
                z.write(file, file.relative_to(root).as_posix())
        link = zipfile.ZipInfo("ClipRadar.app/Contents/MacOS/ClipRadar-alias")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "ClipRadar")
    staged = Path(updater._stage_macos_bundle(str(archive)))
    check("macOS stages a complete app bundle", (staged / "Contents/Info.plist").is_file()
          and os.access(staged / "Contents/MacOS/ClipRadar", os.X_OK)
          and (staged / "Contents/MacOS/ClipRadar-alias").is_symlink())

    unsafe = root / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as z:
        z.writestr("../outside", "bad")
    try:
        updater._stage_macos_bundle(str(unsafe))
        rejected = False
    except RuntimeError as e:
        rejected = "unexpected file path" in str(e)
    check("macOS rejects ZIP path traversal", rejected and not (root.parent / "outside").exists())

    payload = archive.read_bytes()

    class ReleaseHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/releases/latest":
                data = json.dumps({"tag_name": "v4.19.0", "body": "Mac update",
                                   "assets": [{"name": updater.MAC_ASSET, "size": len(payload),
                                               "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                                               "browser_download_url": f"http://127.0.0.1:{self.server.server_port}/download"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/download":
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_error(404)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), ReleaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    prior_api, prior_version, prior_installed = updater.API, updater.VERSION, updater.installed
    updater.API = f"http://127.0.0.1:{server.server_port}/releases/latest"
    updater.VERSION = "4.18.2"
    updater.installed = lambda: True
    updater._asset.clear()
    updater._file[0] = ""
    updater.state.update(state="idle", available=False, latest="", error="")
    try:
        status = updater.check(log=lambda _: None)
        check("a newer GitHub release exposes the macOS app update", status["available"] and status["can_install"]
              and status["latest"] == "4.19.0")
        downloaded = updater.download(log=lambda _: None)
        check("the update download passes size and SHA-256 checks", Path(downloaded).read_bytes() == payload
              and updater.downloaded_for("4.19.0") == downloaded)
        updater._asset.clear()
        check("an unfinished macOS release does not offer a broken install button",
              not updater.view()["can_install"] and not updater.can_install_update())
    finally:
        updater.API, updater.VERSION, updater.installed = prior_api, prior_version, prior_installed
        server.shutdown()
        server.server_close()

    updater._file[0] = str(root / "ClipRadar-macOS-arm64-4.19.0.zip")
    Path(updater._file[0]).write_bytes(b"zip")
    check("the updater reuses the matching cached archive", updater.downloaded_for("4.19.0") == updater._file[0])
    check("the updater rejects a cached archive for another version", not updater.downloaded_for("4.19.1"))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
