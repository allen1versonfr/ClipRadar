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
    if sys.platform == "darwin":
        # Store only a random reference in Clip Radar's files. The secret itself stays in
        # the user's login Keychain via keyring's Security.framework backend.
        import uuid
        keyring = _mac_keyring()

        account = uuid.uuid4().hex
        keyring.set_password("com.clipradar.credentials", account, base64.b64encode(data).decode("ascii"))
        return b"keychain:" + account.encode("ascii")
    if sys.platform != "win32":
        raise RuntimeError("Secure credential storage is unavailable on this platform.")
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
    if kind == b"keychain":
        keyring = _mac_keyring()

        value = keyring.get_password("com.clipradar.credentials", b64.decode("ascii"))
        if value is None:
            raise RuntimeError("This Clip Radar credential is missing from the macOS Keychain. Sign in again in Settings.")
        return base64.b64decode(value)
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


def delete(blob: bytes) -> None:
    """Remove a macOS Keychain value referenced by a credential file, if present."""
    kind, _, account = blob.partition(b":")
    if kind != b"keychain":
        return
    keyring = _mac_keyring()
    try:
        keyring.delete_password("com.clipradar.credentials", account.decode("ascii"))
    except keyring.errors.PasswordDeleteError:
        pass


def _mac_keyring():
    import keyring

    backend = keyring.get_keyring()
    if not backend.__class__.__module__.startswith("keyring.backends.macOS"):
        raise RuntimeError("Clip Radar needs the macOS Keychain to store credentials securely. Install its macOS dependencies and restart.")
    return keyring
