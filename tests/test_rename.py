"""
Unit tests for 4.0 (VodClipper is now Clip Radar): the old folders move to the new names on first
start, saved paths follow them, a folder that's in use is left for next time, and settings made
with the old names (VODCLIPPER_* variables) still work.

  python tests/test_rename.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["VODCLIPPER_SOMETHING"] = "old-style setting"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import paths  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


check("old-style VODCLIPPER_ settings still apply", os.environ.get("CLIPRADAR_SOMETHING") == "old-style setting")

base = Path(tempfile.mkdtemp(prefix="vc_rename_"))
local, videos = base / "Local", base / "Videos"
old_data, old_clips = local / "VodClipper", videos / "VodClipper"
(old_data / "updates").mkdir(parents=True)
(old_clips / "vod_1" / "tiktok").mkdir(parents=True)
(old_data / "export.json").write_text('{"preset": "x"}', encoding="utf-8")
clip = old_clips / "vod_1" / "tiktok" / "Old clip.mp4"
clip.write_text("fake", encoding="utf-8")
(old_clips / "vod_1" / "meta.json").write_text(json.dumps({"title": "t", "source": str(old_clips / "vod_1" / "vod.mp4")}), encoding="utf-8")
(old_data / "queue.json").write_text(json.dumps({"v": 1, "items": [{"id": "a", "path": str(clip), "workdir": str(old_clips / "vod_1")}]}),
                                     encoding="utf-8")

paths.FROZEN = True
data = paths._rename_target(local, paths.DATA_NAME)
clips = paths._rename_target(videos, paths.CLIPS_NAME)
check("data folder moved to ClipRadar", data == local / "ClipRadar" and (data / "export.json").is_file() and not old_data.exists())
check("clips folder moved to 'Clip Radar'", clips == videos / "Clip Radar" and (clips / "vod_1" / "tiktok" / "Old clip.mp4").is_file()
      and not old_clips.exists())
paths.DATA_DIR, paths.WORKROOT = data, clips
n = paths.fix_moved_paths()
q = json.loads((data / "queue.json").read_text(encoding="utf-8"))
check("saved paths follow the moved folders", n >= 2 and q["items"][0]["path"] == str(clips / "vod_1" / "tiktok" / "Old clip.mp4")
      and os.path.isfile(q["items"][0]["path"]), q)
m = json.loads((clips / "vod_1" / "meta.json").read_text(encoding="utf-8"))
check("project files too", m["source"].startswith(str(clips)), m)
check("settings kept", json.loads((data / "export.json").read_text(encoding="utf-8"))["preset"] == "x")

# a second start: nothing to move, nothing changes
paths.MIGRATION.clear()
check("next start: nothing to do", paths._rename_target(local, paths.DATA_NAME) == data and paths.fix_moved_paths() == 0)

# already have both (installed fresh, then an old folder appears): the new one wins, the old is left alone
(local / "VodClipper").mkdir()
check("both exist: keeps the new one", paths._rename_target(local, paths.DATA_NAME) == data and (local / "VodClipper").exists())

# in use: keep using the old folder, move it next time
base2 = Path(tempfile.mkdtemp(prefix="vc_rename2_"))
(base2 / "VodClipper").mkdir()
real = paths._move_folder
paths._move_folder = lambda old, new, wait=30.0: False
got = paths._rename_target(base2, paths.DATA_NAME)
check("folder in use: keeps using it for now", got == base2 / "VodClipper" and paths.MIGRATION.get(str(base2 / "VodClipper")) == "")
paths._move_folder = real
check("and moves it next start", paths._rename_target(base2, paths.DATA_NAME) == base2 / "ClipRadar")

paths.FROZEN = False
check("running from source: nothing moves", paths._rename_target(base2, "Other") == base2 / "Other")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
