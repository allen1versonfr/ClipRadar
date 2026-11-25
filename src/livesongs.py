"""
Songs ready while recording (5.8): the copyright scan and song recognition done during a live session, so a saved
stream opens in Export VOD with its music already found and its songs already named.

Export VOD used to start from nothing on a saved stream: listen to all of it for music (the copyright scan), then
send samples of each music stretch to Shazam and fetch each song's recording. But the live session already runs the
sound model on every piece it records (for laughter), and that model hears music too. So:

  1. The music, always: when the whole stream is saved, the music the session heard, second by second, is saved with
     it as its copyright scan (hand_over). Free: the model had already run. Needs the sound model (Settings ›
     Downloads and tools) and the whole stream saved.
  2. The songs, with "Recognise songs while recording" on: each music stretch is recognised as soon as it ends (or
     once it has played for a minute), in the background, from the recorded pieces: a few 12-second samples to
     Shazam, then the song's recording found on YouTube (songid.py, the same as Export VOD's Recognise songs). That's
     network waiting more than work for the processor, so recording and finding moments aren't held up. The song
     eraser model isn't used to pull the music out of the talking first (it's heavy on the processor while
     recording); a stretch it would have needed is recognised in Export VOD as before.

The erasing itself still happens on export: which stretches are erased, muted or left is chosen there.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

HOW = "heard while recording"
OPEN_AFTER = 60.0        # seconds a stretch still playing has played before it's recognised anyway
QUIET_AFTER = 8.0        # seconds with no music after a stretch before it counts as over (the scan's gap, and some)
MIN_LEN = 8.0            # stretches shorter than this aren't recognised (as songid.name_spans)
DRAIN_WAIT = 30 * 60.0   # seconds the last stretches may take to recognise after the stream ends

_noop = lambda *a, **k: None  # noqa: E731


def available() -> bool:
    try:
        import songid
        return songid.available()
    except Exception:       # noqa: BLE001
        return False


def spans_from(music, speech, duration: float) -> list:
    """The copyright scan's stretches from per-second music (and speech) values, as Export VOD finds them."""
    import vodexport
    return vodexport.music_spans(music, duration, speech=speech)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


class LiveSongs:
    """Recognises a live session's music stretches in the background.

    pieces() -> the session's recorded pieces now ([{path, start, dur}]); a piece already let go is skipped.
    """

    def __init__(self, pieces: Callable[[], list], folder: str, log: Callable = _noop,
                 identify: Optional[Callable] = None, find: Optional[Callable] = None, read: Optional[Callable] = None):
        import songid
        import songref
        self._pieces = pieces
        self.folder = folder
        self.log = log
        self._identify = identify or songid.identify
        self._find = find or songid.find_recording
        self._read = read or songref.read
        self.video = ""                       # the saved stream, once it is: read from it instead of the pieces
        self.done: list = []                  # {"start", "end", "song" or None}
        self._asked: list = []                # (start, end) handed to the worker
        self._cache: dict = {}                # (title, artist) -> recording found, or None
        self._q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- the sound
    def sound(self, t: float, n: float) -> np.ndarray:
        """Stereo 44.1 kHz sound of media seconds [t, t + n]: from the saved stream, or the recorded pieces."""
        if self.video:
            return self._read(self.video, t, n)
        import songref
        parts, first = [], None
        for g in list(self._pieces()):
            if g["start"] + g["dur"] <= t or g["start"] >= t + n or not os.path.exists(g["path"]):
                continue
            if first is not None and abs(g["start"] - (first + sum(p.shape[1] for p in parts) / songref.SR)) > 0.5:
                break                          # a piece missing in between: what's before it only
            x = self._read(g["path"])
            if x.ndim != 2 or not x.shape[1]:
                break
            if first is None:
                first = g["start"]
            parts.append(x)
        if not parts:
            return np.zeros((2, 0), dtype=np.float32)
        x = np.concatenate(parts, axis=1)
        a = max(0, int(round((t - first) * songref.SR)))
        return x[:, a:a + int(round(n * songref.SR))]

    # ---------------------------------------------------------------- what to recognise
    def update(self, music, speech, n: int, final: bool = False) -> None:
        """After each recorded piece: hand the stretches that are over (or have played a minute) to the worker."""
        if music is None or not len(music) or self._stop.is_set():
            return
        for s in spans_from(music, speech, float(n)):
            a, b = float(s["start"]), float(s["end"])
            if b - a < MIN_LEN:
                continue
            over = final or n - b >= QUIET_AFTER
            if not over and b - a < OPEN_AFTER:
                continue
            with self._lock:
                if any(_overlap(a, b, p, q) > 0 for p, q in self._asked):
                    continue
                self._asked.append((a, b))
                self._idle.clear()
                self._q.put((a, b))
                if self._thread is None:       # the worker runs only while there's something to recognise
                    self._thread = threading.Thread(target=self._work, daemon=True, name="live-songs")
                    self._thread.start()

    def _work(self) -> None:
        while True:
            with self._lock:
                try:
                    a, b = self._q.get_nowait()
                except queue.Empty:
                    self._thread = None
                    self._idle.set()
                    return
            song = None
            if not self._stop.is_set():
                try:
                    song = self._one(a, b)
                except Exception as e:       # noqa: BLE001  never stops the recording; the stretch is left unnamed
                    if not self._stop.is_set():
                        self.log(f"Couldn't recognise the song at {_ts(a)}: {str(e)[:120]}")
            with self._lock:
                self.done.append({"start": a, "end": b, "song": song})

    def _one(self, a: float, b: float) -> Optional[dict]:
        check = self._check
        got = self._identify(self.sound, a, b, check=check, separate=False)
        if not got:
            return None
        k = (got["title"].lower(), got.get("artist", "").lower())
        if k not in self._cache:
            mid = max(a, (a + b) / 2 - 10)
            self._cache[k] = self._find(got, self.sound(mid, min(20.0, b - mid)), self.folder, check, self.log)
        rec = self._cache[k]
        self.log(f"Song at {_ts(a)}: {got.get('artist', '')} - {got['title']}"
                 + ("" if rec else " (its recording wasn't found on YouTube)"))
        return dict(got, url=rec["url"] if rec else "")

    def _check(self) -> None:
        if self._stop.is_set():
            raise RuntimeError("stopped")

    def wait(self, timeout: float) -> bool:
        """Wait for the stretches handed over so far. True when they're all done."""
        return self._idle.wait(timeout)

    def stop(self) -> None:
        self._stop.set()

    def results(self) -> list:
        with self._lock:
            return list(self.done)

    def pending(self) -> int:
        with self._lock:
            return len(self._asked) - len(self.done)


def _ts(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def name(spans: list, results: list) -> int:
    """Give each stretch the song recognised where it most overlaps. Returns how many were named."""
    n = 0
    for s in spans:
        best, most = None, 0.0
        for r in results:
            o = _overlap(float(s["start"]), float(s["end"]), r["start"], r["end"])
            if r.get("song") and o > most:
                best, most = r["song"], o
        if best:
            s["song"] = dict(best)
            n += 1
    return n


def scan_for(music, speech, duration: float, results: Optional[list] = None, complete: bool = False) -> dict:
    """The copyright scan of a saved stream (what vodexport.load_scan reads back): its music stretches, the music
    curve for the timeline, and the songs recognised so far. complete: every stretch was tried, so Export VOD
    doesn't offer to recognise them again."""
    dur = int(max(0, duration))
    m = np.nan_to_num(np.asarray(music if music is not None else [], dtype=np.float32), nan=0.0)[:dur]
    sp = None if speech is None else np.asarray(speech, dtype=np.float32)[:dur]
    spans = spans_from(m, sp, float(duration))
    named = name(spans, results or []) if results is not None else 0
    return {"spans": spans, "at": time.time(), "how": HOW, "curve": [round(float(x), 2) for x in m],
            "named": time.time() if results is not None and complete else 0, "everywhere": 0,
            "recognised": named}


def write(workdir: str, video: str, scan: dict) -> None:
    import vodexport
    vodexport._write_json(os.path.join(workdir, "copyright.json"), dict(scan, video=vodexport._sig({"video": video})))


def merge_later(workdir: str, video: str, songs: LiveSongs, music, speech, duration: float,
                log: Callable = _noop) -> threading.Thread:
    """After the stream is saved: wait for the last stretches to be recognised (from the saved video now), then put the
    songs into its copyright scan, unless Export VOD has scanned it again since."""
    import vodexport

    def run():
        done = songs.wait(DRAIN_WAIT)
        songs.stop()
        old = vodexport.load_scan({"video": video, "workdir": workdir})
        if not old or old.get("how") != HOW or old.get("named"):
            return                              # scanned again in Export VOD meanwhile: that one stands
        scan = scan_for(music, speech, duration, songs.results(), complete=done)
        write(workdir, video, scan)
        if scan["spans"]:
            log(f"Recognised {scan['recognised']} of {len(scan['spans'])} music stretch"
                f"{'es' if len(scan['spans']) != 1 else ''} in the saved stream.")

    t = threading.Thread(target=run, daemon=True, name="live-songs-merge")
    t.start()
    return t
