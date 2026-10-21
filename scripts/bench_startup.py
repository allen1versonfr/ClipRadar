"""
Startup cost of the app server, measured the way the window uses it: import the
server, start it, then ask for /api/status once (the window does this on load
and every few seconds after).

  python scripts/bench_startup.py            # one run, prints JSON
  python scripts/bench_startup.py --runs 5   # median of five fresh processes

Each run is a fresh Python process with throwaway data and work folders, so
nothing on this PC is touched. Numbers are from this machine only; compare
runs on the same machine.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")

CHILD = r"""
import json, os, sys, time, urllib.request
t0 = time.perf_counter()
sys.path.insert(0, sys.argv[1])
def rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        import ctypes, ctypes.wintypes as w
        class PMC(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [(n, ctypes.c_size_t) for n in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        c = PMC(); c.cb = ctypes.sizeof(c)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.WorkingSetSize / 2**20
    except Exception:
        return 0.0
import server
t_import = time.perf_counter()
srv, url = server.start(int(sys.argv[2]))
t_start = time.perf_counter()
mem_started = rss_mb()
urllib.request.urlopen(url + "/api/status", timeout=60).read()
t_status = time.perf_counter()
urllib.request.urlopen(url + "/api/status", timeout=60).read()
t_status2 = time.perf_counter()
print(json.dumps({
    "import_ms": round((t_import - t0) * 1000),
    "start_ms": round((t_start - t_import) * 1000),
    "first_status_ms": round((t_status - t_start) * 1000),
    "next_status_ms": round((t_status2 - t_status) * 1000, 1),
    "ready_ms": round((t_status - t0) * 1000),
    "rss_started_mb": round(mem_started, 1),
    "rss_after_status_mb": round(rss_mb(), 1),
}))
server.SHUTTING_DOWN[0] = True
os._exit(0)
"""


def one(port: int) -> dict:
    env = dict(os.environ, CLIPRADAR_DATA=tempfile.mkdtemp(prefix="vc_bench_"),
               CLIPRADAR_WORK=tempfile.mkdtemp(prefix="vc_bench_"), CLIPRADAR_UPDATE_API="http://127.0.0.1:9/none")
    out = subprocess.run([sys.executable, "-c", CHILD, SRC, str(port)], env=env, capture_output=True, text=True, timeout=180)
    line = [x for x in out.stdout.splitlines() if x.startswith("{")]
    if not line:
        raise SystemExit(out.stdout + out.stderr)
    return json.loads(line[-1])


def main():
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 1
    rs = [one(7960 + i) for i in range(runs)]
    med = {k: statistics.median(r[k] for r in rs) for k in rs[0]}
    print(json.dumps({"runs": runs, "median": med}, indent=1))


if __name__ == "__main__":
    main()
