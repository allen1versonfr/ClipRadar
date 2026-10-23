"""
Which programs have a file open (Windows), so "couldn't delete: in use" can say by whom.

Uses Windows' Restart Manager, the same API Explorer's "The action can't be completed because the
file is open in ..." message comes from. It only reads; nothing is closed or ended.
Elsewhere, or if Windows refuses, who_has() returns [] and the caller says what it said before.
"""

from __future__ import annotations

import os
import sys


def who_has(path: str, limit: int = 8) -> list:
    """['ffmpeg.exe (pid 1234)', ...] for the processes holding `path` open; [] if none or unknown."""
    if sys.platform != "win32" or not path:
        return []
    try:
        return _who_has(os.path.abspath(path), limit)
    except Exception:
        return []


def _who_has(path: str, limit: int) -> list:
    import ctypes
    from ctypes import wintypes

    class UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", UNIQUE_PROCESS), ("strAppName", wintypes.WCHAR * 256),
                    ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]

    rm = ctypes.WinDLL("rstrtmgr")
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)                          # CCH_RM_SESSION_KEY + 1
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if rc not in (0, 234) or not needed.value:                  # 234: ERROR_MORE_DATA, the normal answer here
            return []
        infos = (PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        out = []
        for info in infos[:min(count.value, limit)]:
            name = info.strAppName or info.strServiceShortName or "a program"
            out.append(f"{name} (pid {info.Process.dwProcessId})")
        return out
    finally:
        rm.RmEndSession(session)
