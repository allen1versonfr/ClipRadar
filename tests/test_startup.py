"""
Unit tests for startup cost: the window asks /api/status the moment it opens and every few
seconds after, so answering it must not load Whisper, yt-dlp or the laughter detector's runtime.

  python tests/test_startup.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import importlib.util  # noqa: E402
import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


HEAVY = ("faster_whisper", "ctranslate2", "yt_dlp", "onnxruntime", "av")
check("importing the server loads none of the heavy packages", not [m for m in HEAVY if m in sys.modules],
      [m for m in HEAVY if m in sys.modules])

srv, url = server.start(7990)
st = json.loads(urllib.request.urlopen(url + "/api/status", timeout=30).read())
check("status answers", st.get("version") == server.VERSION, st)
check("answering status loads none of them either", not [m for m in HEAVY if m in sys.modules],
      [m for m in HEAVY if m in sys.modules])
check("whisper is reported as installed when it is", st["whisper"] == (importlib.util.find_spec("faster_whisper") is not None))
check("a missing package is reported missing", server._has("no_such_package_xyz", load=False) is False)
check("a loaded package counts without looking", server._has("json", load=False) is True)

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
