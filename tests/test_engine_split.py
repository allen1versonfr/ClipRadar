"""
engine.py is being split into modules, one section at a time (docs/plans/hardening.md, phase 4). This checks the moves
so far kept their promises:

- engine.py still re-exports every moved name, as the same object, so callers and scripts/ keep working;
- engine.py no longer defines any of them itself (one copy of each);
- no test, script or module replaces a moved name through engine (engine.X = fake, setattr(engine, "X", …)): that
  stops working once the code calling X lives in the new module, which looks X up there. Patch the new module;
- engine.py imports each new module at its top level, so PyInstaller's analysis (scripts/build_exe.bat and
  .github/workflows/build.yml, which freeze src/app.py) finds it without a --hidden-import.

When a section moves, add its module and names to MOVED.

  python tests/test_engine_split.py
"""

from __future__ import annotations

import glob
import importlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(ROOT, "src"))

import engine  # noqa: E402

# the sections moved out of engine.py so far: module -> the names it took
MOVED = {
    "storage": ["storage_summary", "delete_vods", "delete_clips", "recent_projects", "_project_dirs", "_vod_files",
                "_has_vod", "_clip_files", "_size", "_dir_size", "_remove", "_prune"],
}

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


with open(os.path.join(ROOT, "src", "engine.py"), encoding="utf-8") as f:
    engine_src = f.read()
files = sorted(glob.glob(os.path.join(ROOT, "tests", "*.py")) + glob.glob(os.path.join(ROOT, "scripts", "*.py"))
               + glob.glob(os.path.join(ROOT, "src", "*.py")))
texts = {}
for p in files:
    with open(p, encoding="utf-8", errors="replace") as f:
        texts[os.path.relpath(p, ROOT)] = f.read()

for module, names in MOVED.items():
    mod = importlib.import_module(module)
    missing = [n for n in names if not hasattr(mod, n)]
    check(f"{module}.py has every name it took", not missing, missing)
    not_same = [n for n in names if getattr(engine, n, None) is not getattr(mod, n, object())]
    check(f"engine re-exports {module}'s names, the same objects", not not_same, not_same)
    still = [n for n in names if re.search(rf"^(def|class) {re.escape(n)}\b|^{re.escape(n)} *=", engine_src, re.M)]
    check(f"engine.py doesn't define {module}'s names any more", not still, still)
    check(f"engine.py imports {module} at its top level (PyInstaller finds it)",
          re.search(rf"^(from {module} import|import {module}\b)", engine_src, re.M) is not None)
    alt = "|".join(re.escape(n) for n in names)
    patch = re.compile(rf"\bengine\.({alt})\s*=(?!=)|setattr\(\s*engine\s*,\s*['\"]({alt})['\"]")
    found = [f"{path}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}"
             for path, text in texts.items() for m in patch.finditer(text)]
    check(f"nothing replaces one of {module}'s names through engine (patch {module}.<name> instead)", not found,
          "; ".join(found))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
