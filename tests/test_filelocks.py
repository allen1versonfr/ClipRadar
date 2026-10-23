"""
"Couldn't delete: in use" names the program holding the file (Windows). A child process keeps a
file open; filelocks.who_has() must name that process, a delete that gives up must say so in the
log, and once the child lets go the file is free and deletes.

  python tests/test_filelocks.py          (Windows; elsewhere only the no-op is checked)
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="vc_test_"))

import engine  # noqa: E402
import filelocks  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="vc_locks_")
path = os.path.join(tmp, "moment_0001.mp4")
with open(path, "wb") as f:
    f.write(b"\0" * 4096)
check("a file nobody has open: no one named", filelocks.who_has(path) == [], filelocks.who_has(path))
check("a path that doesn't exist: no one named, no error", filelocks.who_has(os.path.join(tmp, "nope")) == [])

if sys.platform != "win32":
    print("PASSED (Windows-only parts skipped)" if not failures else f"FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)

holder = subprocess.Popen([sys.executable, "-c", "import sys, time; f = open(sys.argv[1], 'rb'); print('open', flush=True); time.sleep(120)",
                           path], stdout=subprocess.PIPE, text=True)
try:
    holder.stdout.readline()
    who = filelocks.who_has(path)
    check("the process holding the file is named, with its pid", any(f"pid {holder.pid})" in w for w in who), who)
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        freed, failed = engine._remove([path])
    check("a delete that gives up reports the file", failed == ["moment_0001.mp4"] and freed == 0, (freed, failed))
    check("and the log says who has it open", f"pid {holder.pid})" in log.getvalue(), log.getvalue())
finally:
    holder.kill()
    holder.wait(timeout=10)
end = time.time() + 5
while filelocks.who_has(path) and time.time() < end:
    time.sleep(0.1)
check("once it lets go, no one is named", filelocks.who_has(path) == [], filelocks.who_has(path))
freed, failed = engine._remove([path])
check("and the file deletes", not failed and not os.path.exists(path), (freed, failed))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
