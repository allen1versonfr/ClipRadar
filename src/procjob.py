"""
Background tools end with Clip Radar (Windows).

FFmpeg, yt-dlp and TwitchDownloader run as separate processes. If Clip Radar crashes or is ended
from Task Manager, Windows leaves them running: a render keeps writing a file nobody will use, a
recording keeps the drive busy, and the leftovers can't be cleaned up on the next start because
they're still open.

adopt(p) puts one such process in a job object that Windows closes when Clip Radar's process ends,
for any reason, and closing it ends everything in it (KILL_ON_JOB_CLOSE). Processes they start
themselves (yt-dlp's FFmpeg) are in the job too. Clip Radar itself is deliberately NOT in the job:
the update installer, File Explorer and the browser windows it opens must outlive it.

Elsewhere, and if Windows refuses (very old versions), adopt() does nothing: a tool then just isn't
tied to the app, as before.
"""

from __future__ import annotations

import sys
import threading

_job = [None]
_lock = threading.Lock()


def _make_job():
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # ExtendedLimitInformation
        k32.CloseHandle(job)
        return None
    # never closed: Windows closes it when this process ends, which is the point
    return (k32, job)


def adopt(p) -> bool:
    """Tie a started subprocess.Popen to Clip Radar's lifetime. True if it was."""
    if sys.platform != "win32" or p is None:
        return False
    try:
        with _lock:
            if _job[0] is None:
                _job[0] = _make_job() or False
        if not _job[0]:
            return False
        k32, job = _job[0]
        return bool(k32.AssignProcessToJobObject(job, int(p._handle)))
    except Exception:
        return False


# ---------------------------------------------------------------- watched pipes (5.7.2)

STUCK = 120.0       # seconds a read from (or a write to) a tool may wait before the tool counts as hung


class Watch:
    """A tool's pipes watched from the side, so Stop works and a hung tool can't hold a job at one percent for good.

    Reading a pipe blocks until the tool writes, and the loop around it only checks for Stop between reads: an FFmpeg
    that stalled (a disk asleep, a network drive gone, a file it chokes on) kept the music scan, the song eraser or a
    recognised song's erase at the same percent forever, with Stop doing nothing (5.7.2). This thread checks for Stop
    every second and ends the tools at once; and when a read or write has waited STUCK seconds it ends them too.
    Only time spent waiting on the tool counts (`with w.waiting():` around each read and write), never the time Clip
    Radar spends working between reads, however long a block through the model takes.

        w = Watch([dec, enc], check)
        try:
            with w.waiting():
                data = dec.stdout.read(n)
            ...
        except Exception:
            w.raise_if()          # Stop, or the tool hung: said as that, not as the broken pipe it caused
            raise
        finally:
            w.close()
        w.raise_if()              # a tool ended early by the watch reads as the end of its output
    """

    def __init__(self, procs: list, check=lambda: None, stuck: float = 0.0, what: str = "FFmpeg"):
        import time as _t
        self._t = _t
        self.procs = [p for p in procs if p is not None]
        self.check, self.stuck, self.what = check, float(stuck or STUCK), what
        self.since = 0.0
        self.why = ""
        self.cancelled = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import engine
        while not self._stop.wait(1.0):
            try:
                self.check()
            except engine.Cancelled:
                self.cancelled = True
                self._kill()
                return
            except Exception:        # noqa: BLE001  a check that fails is no reason to stop the tool
                pass
            since = self.since
            if since and self._t.monotonic() - since > self.stuck:
                self.why = (f"{self.what} stopped responding (nothing for {int(self.stuck)} seconds) and was stopped. "
                            "If this keeps happening, check the drive the video is on.")
                self._kill()
                return

    def _kill(self):
        for p in self.procs:
            try:
                if p.poll() is None:
                    p.kill()
            except OSError:
                pass

    class _Waiting:
        def __init__(self, w):
            self.w = w

        def __enter__(self):
            self.w.since = self.w._t.monotonic()

        def __exit__(self, *exc):
            self.w.since = 0.0
            return False

    def waiting(self):
        return Watch._Waiting(self)

    def raise_if(self):
        if self.cancelled:
            import engine
            raise engine.Cancelled()
        if self.why:
            raise RuntimeError(self.why)

    def close(self):
        self._stop.set()
