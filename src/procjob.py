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
