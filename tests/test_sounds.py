"""
Notification sounds (4.41): radar pings made by Clip Radar, written once as small WAV files and played from there.

  python tests/test_sounds.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import sounds  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def pitch(x, a, b):
    s = x[int(a * sounds.RATE):int(b * sounds.RATE)]
    return np.argmax(np.abs(np.fft.rfft(s * np.hanning(len(s))))) * sounds.RATE / len(s)


folder = tempfile.mkdtemp(prefix="vc_sounds_")
for name in sounds.NAMES:
    x = sounds.render(name)
    check(f"{name}: short, and not louder than meant", 0.3 < len(x) / sounds.RATE < 1.5 and abs(x).max() <= sounds.PEAK + 1e-6,
          (len(x) / sounds.RATE, abs(x).max()))
    check(f"{name}: ends in silence (no click)", abs(x[-50:]).max() < 0.01, abs(x[-50:]).max())
    p = sounds.path(name, folder)
    with wave.open(p) as w:
        check(f"{name}: a WAV Windows can play", (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, sounds.RATE))
    t = os.path.getmtime(p)
    check(f"{name}: written once", sounds.path(name, folder) == p and os.path.getmtime(p) == t)
lock, lost = sounds.render("lock"), sounds.render("lost")
check("a finished render locks on: a second, higher ping", pitch(lock, 0.02, 0.1) < pitch(lock, 0.2, 0.3) - 300,
      (pitch(lock, 0.02, 0.1), pitch(lock, 0.2, 0.3)))
check("a missed hotkey clip falls away: signal lost", pitch(lost, 0.02, 0.07) > pitch(lost, 0.34, 0.39) + 150)
try:
    sounds.render("nope")
    check("an unknown sound is an error", False)
except ValueError:
    check("an unknown sound is an error", True)
if sys.platform != "win32":
    check("nothing plays outside Windows", sounds.play("contact") is False)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
