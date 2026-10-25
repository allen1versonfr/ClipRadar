"""macOS desktop adapters; the keyboard listener is stubbed to avoid real permissions/input."""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hotkey  # noqa: E402
import studio_auto  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool):
    print(f"[{'ok' if ok else 'FAIL'}] {name}", flush=True)
    if not ok:
        failures.append(name)


if sys.platform != "darwin":
    print("SKIP: macOS desktop tests")
else:
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    with patch("studio_auto.os.path.isfile", side_effect=lambda p: p == chrome), \
         patch("studio_auto.os.access", return_value=True):
        check("macOS browser discovery finds an installed Chrome app", studio_auto.find_browser() == chrome)

    class Key:
        ctrl_l, ctrl_r, alt_l, alt_r = object(), object(), object(), object()

    class Listener:
        stopped = False

        def __init__(self, on_press, on_release):
            self.on_press, self.on_release = on_press, on_release

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    class CharKey:
        char = "c"
        vk = 8

    fake_keyboard = types.SimpleNamespace(Key=Key, Listener=Listener)
    fake_pynput = types.ModuleType("pynput")
    fake_pynput.keyboard = fake_keyboard
    previous = sys.modules.get("pynput")
    sys.modules["pynput"] = fake_pynput
    with tempfile.TemporaryDirectory(prefix="clipradar-hotkey-") as tmp:
        old_prefs, old_listener = hotkey.PREFS, hotkey._listener[0]
        old_state = dict(hotkey.state)
        hotkey.PREFS = Path(tmp) / "hotkey.json"
        hotkey._listener[0] = None
        calls = []
        try:
            hotkey._callback[0] = lambda: calls.append("clip")
            hotkey.state.update(enabled=True, registered=False, error="", presses=0)
            hotkey._start_macos()
            listener = hotkey._listener[0]
            listener.on_press(Key.ctrl_l)
            listener.on_press(Key.alt_l)
            listener.on_press(CharKey())
            check("Ctrl+Option+C invokes the live clip callback", calls == ["clip"] and hotkey.state["registered"])
            listener.on_release(Key.ctrl_l)
            listener.on_release(Key.alt_l)
            hotkey.set_enabled(False)
            check("the macOS hotkey can be disabled in settings", not hotkey.state["enabled"]
                  and listener.stopped and not hotkey.simulate())
            hotkey.set_enabled(True)
            check("the macOS hotkey can be re-enabled", hotkey.state["registered"])
        finally:
            hotkey.PREFS, hotkey._listener[0] = old_prefs, old_listener
            hotkey.state.clear()
            hotkey.state.update(old_state)
            if previous is None:
                sys.modules.pop("pynput", None)
            else:
                sys.modules["pynput"] = previous

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
