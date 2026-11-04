"""
A clip never ends past the end of its file (issue 42). A live moment's analysis can run up to ~1 s past
its MP4; a cut moved to a sentence's end, or trimmed by hand, was clamped to the analysis only, and the
Review player then reached the file's end instead of looping.

  python tests/test_media_end.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def project(analysis: float, file: float) -> dict:
    return {"analysis": types.SimpleNamespace(duration=analysis), "info": {"duration": file}}


check("the file's length wins when it's shorter", server._media_end(project(45.9, 45.0)) == 45.0)
check("the analysis wins when it's shorter", server._media_end(project(30.0, 31.0)) == 30.0)
check("a file of unknown length uses the analysis", server._media_end(project(30.0, 0)) == 30.0)
check("no analysis: the file", server._media_end({"info": {"duration": 12.5}}) == 12.5)
check("nothing known: no limit", server._media_end({}) is None)

pr = project(45.9, 45.0)
d = {"start": 30.0, "end": 44.0}
server._apply_clip_state(pr, d, {"start": 31.0, "end": 45.8})
check("a hand trim past the file's end stops at the file's end", d["end"] == 45.0, str(d))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
