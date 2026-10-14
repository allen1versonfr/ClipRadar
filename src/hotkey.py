"""
A system-wide "clip that" key (Ctrl+Alt+C) for live mode on Windows.

It works while you're watching the stream in another window or playing a game:
Clip Radar hears the key, clips the last 30 seconds and plays a short sound.
Elsewhere (or if another app already owns the shortcut) it simply reports that
it isn't available.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Callable

import paths

KEYS = "Ctrl+Alt+C"
PREFS = paths.DATA_DIR / "hotkey.json"
WM_HOTKEY, WM_APP = 0x0312, 0x8000
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_C = 0x0001, 0x0002, 0x4000, 0x43

state = {"supported": sys.platform == "win32", "enabled": True, "registered": False, "keys": KEYS,
         "error": "", "presses": 0}
_tid = [0]
_ready = threading.Event()
_callback: list = [lambda: None]


def _load_enabled() -> bool:
    try:
        return bool(json.loads(PREFS.read_text("utf-8")).get("enabled", True))
    except Exception:
        return True


def status() -> dict:
    return dict(state)


def set_enabled(on: bool):
    state["enabled"] = bool(on)
    try:
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps({"enabled": bool(on)}), "utf-8")
    except OSError:
        pass
    if _tid[0]:
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(_tid[0], WM_APP, 0, 0)
        _ready.clear()
        _ready.wait(2)


def simulate() -> bool:
    """Post a hotkey message as if the keys were pressed (tests)."""
    if not _tid[0]:
        return False
    import ctypes
    return bool(ctypes.windll.user32.PostThreadMessageW(_tid[0], WM_HOTKEY, 1, 0))


def beep(ok: bool = True):
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
    except Exception:
        pass


def start(on_press: Callable):
    _callback[0] = on_press
    state["enabled"] = _load_enabled()
    if not state["supported"]:
        state["error"] = "The clip hotkey is only available on Windows."
        return
    threading.Thread(target=_loop, daemon=True, name="clip-hotkey").start()
    _ready.wait(3)


def _loop():
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    msg = wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)    # create this thread's message queue
    _tid[0] = kernel32.GetCurrentThreadId()

    def apply():
        if state["enabled"] and not state["registered"]:
            if user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_C):
                state.update(registered=True, error="")
            else:
                state.update(registered=False, error=f"Another app is already using {KEYS}.")
        elif not state["enabled"] and state["registered"]:
            user32.UnregisterHotKey(None, 1)
            state.update(registered=False, error="")
        _ready.set()

    apply()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            state["presses"] += 1
            try:
                _callback[0]()
            except Exception:
                pass
        elif msg.message == WM_APP:
            apply()
