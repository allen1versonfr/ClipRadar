"""
Settings › TikTok (studio.json): Press Post for me is saved and read back even when Windows has the file open
for a moment, the way a save racing a read failed with PermissionError (a 500 from /api/studio).

  python tests/test_studio_settings.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import persist  # noqa: E402
import studio_auto  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


check("Press Post for me is off by default", studio_auto.load_settings() == {"press_post": False})
check("saving returns what was saved", studio_auto.save_settings(True) == {"press_post": True})
check("and it's read back", studio_auto.load_settings() == {"press_post": True})
check("no temporary file is left behind",
      not [f for f in os.listdir(os.path.dirname(studio_auto.settings_path())) if f.endswith(".tmp")])

# a save whose os.replace meets the file held open (Windows) twice, then gets through
real_replace = os.replace
held = {"n": 2}


def busy_replace(a, b):
    if held["n"] > 0:
        held["n"] -= 1
        raise PermissionError(5, "Access is denied")
    return real_replace(a, b)


persist.os.replace = busy_replace
try:
    err = None
    try:
        studio_auto.save_settings(False)
    except PermissionError as e:
        err = e
    check("a save that meets the file held open tries again instead of failing", err is None, repr(err))
    check("and the setting is saved", studio_auto.load_settings() == {"press_post": False})
finally:
    persist.os.replace = real_replace

# a read that meets a save replacing the file: it tries again rather than reading the defaults
studio_auto.save_settings(True)
real_open = open
blocked = {"n": 2}


def busy_open(path, *a, **k):
    if path == studio_auto.settings_path() and blocked["n"] > 0:
        blocked["n"] -= 1
        raise PermissionError(13, "Permission denied")
    return real_open(path, *a, **k)


studio_auto.open = busy_open          # module-level name lookup: shadows the builtin in studio_auto only
try:
    check("a read that meets the file held open still reads Press Post for me as on",
          studio_auto.load_settings() == {"press_post": True})
finally:
    del studio_auto.open

# saves and reads at the same time, as Settings does while an upload reads the setting
errors = []


def saver():
    for i in range(40):
        try:
            studio_auto.save_settings(i % 2 == 0)
        except Exception as e:      # noqa: BLE001
            errors.append(repr(e))


def reader():
    for _ in range(200):
        try:
            v = studio_auto.load_settings()
            if set(v) != {"press_post"}:
                errors.append(repr(v))
        except Exception as e:      # noqa: BLE001
            errors.append(repr(e))


ts = [threading.Thread(target=saver), threading.Thread(target=saver), threading.Thread(target=reader)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("saves and reads at the same time never fail", not errors, "; ".join(errors[:3]))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
