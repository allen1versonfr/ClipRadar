"""
Unit tests for 4.1 All versions: the list of every release (newest first, archives marked, the
model download left out), installing a particular newer version, and refusing to go back.

  python tests/test_versions.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, HERE)
import mock_github  # noqa: E402

srv = mock_github.start(0, "v99.0.0")
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = f"http://127.0.0.1:{srv.server_address[1]}/repos/allen1versonfr/vodclipper/releases/latest"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import updater  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


updater.VERSION = "4.1.0"
updater.installed = lambda: True
v = updater.versions(refresh=True, log=lambda m: None)
vs = [x["version"] for x in v["versions"]]
check("every version, newest first, without the model download", vs == ["99.0.0", "98.5.0", "3.11.0", "1.0.0"], vs)
by = {x["version"]: x for x in v["versions"]}
check("newer versions can be installed", by["99.0.0"]["installable"] and by["98.5.0"]["installable"] and by["98.5.0"]["newer"])
check("older versions can't (no going back)", not by["3.11.0"]["installable"] and not by["3.11.0"]["newer"])
check("versions without an installer are marked as archive", by["1.0.0"]["archive"] and not by["1.0.0"]["installable"])
check("notes come along", by["98.5.0"]["notes"] == "Notes for v98.5.0.")

updater.choose("98.5.0")
st = updater.view()
check("picking a newer version sets it up to install", st["available"] and st["latest"] == "98.5.0"
      and updater._asset.get("browser_download_url", "").endswith("ClipRadar-Setup.exe"), st)
path = updater.download(log=lambda m: None)
check("and downloads that version's installer", path.endswith("ClipRadar-Setup-98.5.0.exe") and os.path.getsize(path) > 0)
for bad in ("3.11.0", "4.1.0", "1.0.0"):
    try:
        updater.choose(bad)
        check(f"refuses {bad}", False)
    except ValueError as e:
        check(f"refuses {bad}", "newer" in str(e) or "installer" in str(e), str(e))

updater.installed = lambda: False
check("a portable copy sees the list but can't install", not any(x["installable"] for x in updater.versions()["versions"]))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
