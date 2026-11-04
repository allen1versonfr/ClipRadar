"""
Clip Radar's notification sounds (4.41): radar and sonar pings, made here from a few sine tones rather than
files shipped with the app. Before, they were Windows' own beeps (MessageBeep), the same as every other program's.

  contact   a clip was added to the queue, or the clip hotkey grabbed one: one short, bright blip
  lock      a render finished: two rising pings, locking on, with the echo coming back
  lost      the clip hotkey couldn't grab a clip: a falling tone, signal lost

Windows can only play a sound from memory and carry on at the same time from a file, so each is written once
to Clip Radar's data folder (a few tens of KB) and played from there. Anywhere else, nothing plays.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import wave

import numpy as np

RATE = 22050
VERSION = 1                 # in the file names: a changed sound is written again, not read from an old file
PEAK = 0.42                 # loud enough to notice over a game, well below a full-scale system sound
NAMES = ("contact", "lock", "lost")
_lock = threading.Lock()


def _tone(freq: float, dur: float, decay: float, to_freq: float = 0.0, attack: float = 0.004) -> np.ndarray:
    """A ping: a sine (sliding to to_freq when given) that starts in a few ms and dies away (decay per second),
    with a quieter octave above it so it rings like a sonar return rather than a test tone."""
    t = np.arange(int(RATE * dur)) / RATE
    f = np.full_like(t, freq) if not to_freq else freq * (to_freq / freq) ** (t / dur)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    env = np.minimum(1.0, t / attack) * np.exp(-decay * t)
    env[-int(RATE * 0.01):] *= np.linspace(1, 0, int(RATE * 0.01))      # no click at the end
    return env * (np.sin(phase) + 0.18 * np.sin(2 * phase + 0.6))


def _place(parts: list, length: float) -> np.ndarray:
    out = np.zeros(int(RATE * length))
    for at, x, gain in parts:
        i = int(RATE * at)
        n = min(len(x), len(out) - i)
        out[i:i + n] += gain * x[:n]
    return out


def _echo(x: np.ndarray, delay: float, gain: float, times: int) -> np.ndarray:
    """The ping coming back off something far away: quieter copies, each a little duller (a short average)."""
    out = x.copy()
    d = int(RATE * delay)
    back = x
    for k in range(1, times + 1):
        back = np.convolve(back, np.ones(3) / 3, mode="same") * gain
        if k * d >= len(out):
            break
        out[k * d:] += back[:len(out) - k * d]
    return out


def render(name: str) -> np.ndarray:
    """The sound as samples, -1..1."""
    if name == "contact":
        x = _place([(0.0, _tone(1320, 0.30, 16, to_freq=1240), 1.0)], 0.45)
        x = _echo(x, 0.11, 0.35, 2)
    elif name == "lock":
        x = _place([(0.0, _tone(990, 0.35, 11), 0.85), (0.13, _tone(1480, 0.9, 5.5), 1.0)], 1.25)
        x = _echo(x, 0.24, 0.32, 3)
    elif name == "lost":
        x = _place([(0.0, _tone(700, 0.42, 6, to_freq=330), 1.0)], 0.55)
    else:
        raise ValueError(f"no sound called {name!r}")
    return x / max(1e-9, float(np.abs(x).max())) * PEAK


def wav_bytes(name: str) -> bytes:
    pcm = (np.clip(render(name), -1, 1) * 32767).astype("<i2").tobytes()
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return b.getvalue()


def path(name: str, folder: str = "") -> str:
    """The sound's file, written the first time it's wanted."""
    if not folder:
        import paths
        folder = str(paths.DATA_DIR / "sounds")
    p = os.path.join(folder, f"radar_{name}_v{VERSION}.wav")
    with _lock:
        if not os.path.isfile(p):
            os.makedirs(folder, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(wav_bytes(name))
            os.replace(tmp, p)
    return p


def play(name: str) -> bool:
    """Play a sound without waiting for it (Windows). Windows' own beep if the file can't be made or played."""
    if sys.platform != "win32":
        return False
    try:
        import winsound
    except ImportError:
        return False
    try:
        winsound.PlaySound(path(name), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND if name == "lost" else winsound.MB_OK)
        except Exception:
            pass
        return False
