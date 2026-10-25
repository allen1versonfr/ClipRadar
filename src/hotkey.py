"""
A system-wide "clip that" key (Ctrl+Alt+C) for live mode on Windows and macOS.

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

KEYS = "Ctrl+Option+C" if sys.platform == "darwin" else "Ctrl+Alt+C"
PREFS = paths.DATA_DIR / "hotkey.json"
WM_HOTKEY, WM_APP = 0x0312, 0x8000
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_C = 0x0001, 0x0002, 0x4000, 0x43

state = {"supported": sys.platform == "win32", "enabled": True, "registered": False, "keys": KEYS,
         "error": "", "presses": 0}
if sys.platform == "darwin":
    state["supported"] = True
_tid = [0]
_ready = threading.Event()
_callback: list = [lambda: None]
_listener = [None]
_mac_pressed: set = set()


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
    if sys.platform == "darwin":
        if state["enabled"] and _listener[0] is None:
            _start_macos()
        elif not state["enabled"] and _listener[0] is not None:
            _listener[0].stop()
            _listener[0] = None
            _mac_pressed.clear()
            state.update(registered=False, error="")
    elif _tid[0]:
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(_tid[0], WM_APP, 0, 0)
        _ready.clear()
        _ready.wait(2)


def simulate() -> bool:
    """Post a hotkey message as if the keys were pressed (tests)."""
    if sys.platform == "darwin":
        if not state["enabled"] or not state["registered"]:
            return False
        _callback[0]()
        return True
    if not _tid[0]:
        return False
    import ctypes
    return bool(ctypes.windll.user32.PostThreadMessageW(_tid[0], WM_HOTKEY, 1, 0))


def beep(ok: bool = True):
    if sys.platform == "darwin":
        try:
            import AppKit
            AppKit.NSBeep()
        except Exception:
            pass
        return
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
    if sys.platform == "darwin":
        _start_macos()
        return
    if not state["supported"]:
        state["error"] = "The clip hotkey is only available on Windows."
        return
    threading.Thread(target=_loop, daemon=True, name="clip-hotkey").start()
    _ready.wait(3)


def _start_macos():
    """Listen for Ctrl+Option+C with macOS's global keyboard event monitor."""
    if not state["enabled"]:
        state.update(registered=False, error="")
        return
    try:
        from pynput import keyboard

        def on_press(key):
            repeated = key in _mac_pressed
            _mac_pressed.add(key)
            ctrl = keyboard.Key.ctrl_l in _mac_pressed or keyboard.Key.ctrl_r in _mac_pressed
            alt = keyboard.Key.alt_l in _mac_pressed or keyboard.Key.alt_r in _mac_pressed
            is_c = getattr(key, "vk", None) == 8 or (getattr(key, "char", "") or "").lower() == "c"
            if not repeated and state["enabled"] and ctrl and alt and is_c:
                state["presses"] += 1
                try:
                    _callback[0]()
                except Exception:
                    pass

        def on_release(key):
            _mac_pressed.discard(key)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        _listener[0] = listener
        listener.start()
        trusted = getattr(listener, "IS_TRUSTED", getattr(keyboard.Listener, "IS_TRUSTED", True))
        if trusted is False:
            listener.stop()
            _listener[0] = None
            state.update(registered=False,
                         error="Allow Clip Radar under System Settings › Privacy & Security › Accessibility, then reopen it.")
        else:
            state.update(registered=state["enabled"], error="")
    except Exception:
        _listener[0] = None
        state.update(registered=False,
                     error="macOS could not start the clip hotkey. Allow Clip Radar under System Settings › Privacy & Security › Accessibility, then reopen it.")


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
