"""
Small things several parts of Clip Radar share for talking to the outside world safely:

  protect / unprotect   sign-in tokens (Twitch, GitHub) are stored encrypted for your Windows user
                        account (DPAPI). The format ("dpapi:" or "plain:" + base64) is the same one
                        used before 4.4, so saved sign-ins keep working.
  ssl_context           HTTPS with certifi's certificates as well, for PCs with stale root certificates
"""

from __future__ import annotations

import base64
import ssl
import sys


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return b"plain:" + base64.b64encode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    src = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Clip Radar", None, None, None, 0x1,
                                                  ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return b"dpapi:" + base64.b64encode(ctypes.string_at(out.pbData, out.cbData))
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(blob: bytes) -> bytes:
    kind, _, b64 = blob.partition(b":")
    raw = base64.b64decode(b64)
    if kind == b"plain":
        return raw
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    src = BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1,
                                                    ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
