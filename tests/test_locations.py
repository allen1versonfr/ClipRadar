"""
Unit tests for 3.11 storage locations: defaults that change nothing on upgrade, checking a folder
can be used, a drive that isn't plugged in, older project folders still found, downloads and clips
in their own folders, clearing temporary files safely, and memory readings.

  python tests/test_locations.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
BASE = tempfile.mkdtemp(prefix="vc_loc_")
os.environ["VODCLIPPER_DATA"] = os.path.join(BASE, "data")
os.environ["VODCLIPPER_WORK"] = os.path.join(BASE, "projects")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402
import locations as L  # noqa: E402
import paths  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


paths.ensure_dirs()
check("defaults: where things always went", L.path("projects") == str(paths.WORKROOT) and L.path("downloads") == ""
      and L.path("clips") == "" and L.path("logs") == str(paths.DATA_DIR))
wd = os.path.join(L.path("projects"), "vod_1")
check("by default the video and clips stay in the project folder",
      L.download_dir(wd) == wd and L.clips_dir(wd) == os.path.join(wd, "tiktok"))

# checking folders
check("a writable folder is fine", L.check(os.path.join(BASE, "new", "place"))["ok"] and os.path.isdir(os.path.join(BASE, "new", "place")))
check("a relative path is refused", not L.check("clips")["ok"])
ro = os.path.join(BASE, "ro")
os.makedirs(ro)
os.chmod(ro, 0o500)
if os.name != "nt" and os.geteuid() != 0:
    check("a folder it can't write to is refused", not L.check(ro)["ok"] and "can't write" in L.check(ro)["error"])
check("free space is reported", (L.check(BASE)["free"] or 0) > 0)

# saving
dl, cl = os.path.join(BASE, "dl"), os.path.join(BASE, "clips")
r = L.save({"downloads": dl, "clips": cl})
check("downloads and clips can have their own folders", r["ok"] and L.download_dir(wd) == os.path.join(dl, "vod_1")
      and L.clips_dir(wd) == os.path.join(cl, "vod_1"))
r = L.save({"temp": "relative/path"})
check("a bad folder is refused, the rest kept", not r["ok"] and "temp" in r["errors"] and L.path("downloads") == dl)
L.save({"clips": ""})
check("back to the default", L.path("clips") == "")

# a drive that isn't there
real = L._drive_present
L._drive_present = lambda p: not p.startswith(os.path.join(BASE, "usb"))
L.save({"clips": os.path.join(BASE, "fine")})
L._drive_present = lambda p: not p.startswith(os.path.join(BASE, "fine"))
r = L.resolve("clips")
check("a drive that's unplugged: default for now, and says so", r["path"] == "" and not r["ok"] and "isn't available" in r["problem"]
      and L.problems() and L.problems()[0]["key"] == "clips")
L._drive_present = real
check("plugged back in: used again", L.resolve("clips")["ok"] and L.path("clips") == os.path.join(BASE, "fine"))
L.save({"clips": ""})

# moving the Projects folder: older projects are still found
old_root = L.path("projects")
os.makedirs(os.path.join(old_root, "old_project"), exist_ok=True)
new_root = os.path.join(BASE, "projects2")
L.save({"projects": new_root})
check("projects folder moved; the old one is still searched", L.path("projects") == new_root
      and L.project_roots()[:2] == [new_root, old_root], L.project_roots())

# downloads location: found by the engine and counted in storage
pdir = os.path.join(new_root, "yt_abc")
os.makedirs(pdir)
engine._save_meta(pdir, title="x", source="https://youtu.be/abcdefghijk")
os.makedirs(L.download_dir(pdir), exist_ok=True)
with open(os.path.join(L.download_dir(pdir), "vod.mp4"), "wb") as f:
    f.write(b"\0" * 5000)
check("the engine finds a video in the downloads folder", engine.project_video(pdir) == os.path.join(dl, "yt_abc", "vod.mp4"))
st = engine.storage_summary(new_root)
check("storage counts downloads kept elsewhere", st["vods"]["count"] == 1 and st["vods"]["bytes"] == 5000, st)
res = engine.delete_vods(new_root)
check("delete VODs removes them there too", res["deleted"] == 1 and not os.path.exists(os.path.join(dl, "yt_abc", "vod.mp4")), res)

# clearing temporary files
t1 = L.temp_dir("proj", "preview")
open(os.path.join(t1, "p.mp4"), "wb").write(b"\0" * 1000)
busy = L.temp_dir("live_now", "segments")
open(os.path.join(busy, "seg.ts"), "wb").write(b"\0" * 1000)
r = L.clear_temp(keep=(os.path.dirname(busy),))
check("clear temporary files: previews go, a running live buffer stays", r["freed"] >= 1000 and not os.path.exists(t1)
      and os.path.exists(os.path.join(busy, "seg.ts")), r)
part = os.path.join(dl, "yt_old")
os.makedirs(part, exist_ok=True)
pf = os.path.join(part, "vod.mp4.part")
open(pf, "wb").write(b"x")
os.utime(pf, (time.time() - 3 * 86400, time.time() - 3 * 86400))
fresh = os.path.join(part, "vod.f1.mp4.part")
open(fresh, "wb").write(b"x")
check("old half-finished downloads are found, recent ones left", L.stale_partials([dl]) == [pf], L.stale_partials([dl]))

v = L.view(sizes=True)
check("storage view lists every folder with free space", [x["key"] for x in v["locations"]] == list(L.KEYS)
      and all(x["free"] for x in v["locations"]))
m = L.memory()
check("memory is read", m["total"] is None or (m["total"] > 0 and 0 <= (m["free"] or 0) <= m["total"]), m)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
