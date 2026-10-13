"""
Replay a recorded stream through live detection, faster than real time, and score it against the
moments you know are in it. Use it to check a change to live clipping before it goes live.

  python tests/live_replay.py VIDEO [--chat CHAT.json] --truth TRUTH.json [--frequency normal] [--min-quality 0]

TRUTH.json: [{"at": 754.0}, {"at": 1302.5, "name": "the fall"}, ...]  (seconds into the video where
each moment happens). The report has precision (moments that were real), recall (real moments
found), false positives, misses, how often the clip kept the setup and the reaction, and how many
seconds of stream it took to cut each moment.

Used by tests/test_live.py with a made-up stream, so it runs without ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import detect  # noqa: E402
import engine  # noqa: E402
import live  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class ReplaySession(live.LiveSession):
    """A live session fed from arrays instead of a recording; moments are noted, not cut."""

    def __init__(self, workroot: str, chat: bool = True, **kw):
        src = os.path.join(workroot, "replay.m3u8")
        open(src, "w").close()
        super().__init__("replay", src, workroot, events=False, **kw)
        self.channel = "replay" if chat else None
        self.irc_ok = chat
        self.found: list = []

    def _make_moment(self, start, end, peak, reason, an, chat, manual=False, lat=None, aired=None):
        self.taken.append((start, end))
        self._moment_n += 1
        self.found.append({"start": float(start), "end": float(end), "peak": float(peak), "manual": manual,
                           "emitted": float(len(self.loud)), "lat": dict(lat or {}), "reason": reason,
                           "quality": detect.quality(float(an.score[min(int(peak), len(an.score) - 1)]))})


def replay(loud, fine=None, chat=None, step: float = 2.0, chat_on: bool = True, chat_lost_at: float = None,
           clock_rate: float = 1.0, **session):
    """Feed a stream second by second, running detection every `step` seconds like the live reader.
    chat: [(seconds into the stream it was sent, text, user)]. chat_lost_at: chat stops from then on
    (dropped or very delayed). Returns the session (moments in .found)."""
    clock = _Clock()
    old_now = live._now
    live._now = clock
    try:
        work = tempfile.mkdtemp(prefix="vc_replay_")
        s = ReplaySession(work, chat=chat_on, **session)
        loud = np.asarray(loud, dtype=np.float32)
        if fine is None:
            fine = np.repeat(loud, engine.FINE_HZ)
        msgs = sorted(chat or [])
        mi = 0
        n = len(loud)
        t = 0
        while t < n:
            t2 = min(n, t + int(step))
            clock.t += (t2 - t) * clock_rate
            s.loud.extend(loud[t:t2].tolist())
            s.fine.extend(fine[t * engine.FINE_HZ:t2 * engine.FINE_HZ].tolist())
            s._arrive.append((float(t2), clock.t, 0.05))
            s._anchor = (clock.t, float(t2))
            while mi < len(msgs) and msgs[mi][0] < t2:
                m = msgs[mi]
                mi += 1
                if chat_lost_at is not None and m[0] >= chat_lost_at:
                    continue
                s.add_chat(float(m[0]), m[1], m[2], m[3] if len(m) > 3 else "")
                s._last_chat = clock.t - (t2 - m[0]) * clock_rate
            t = t2
            s._detect(final=False)
        s._detect(final=True)
        return s
    finally:
        live._now = old_now


def score(found: list, truth: list, setup: float = 2.0, payoff: float = 4.0) -> dict:
    """Precision, recall, false positives, misses, boundaries that kept setup and reaction, and delay."""
    auto = [f for f in found if not f["manual"]]
    hits, matched, full, delays = set(), 0, 0, []
    for f in auto:
        tr = [i for i, x in enumerate(truth) if f["start"] <= x["at"] <= f["end"]]
        if tr:
            matched += 1
            i = min(tr, key=lambda k: abs(truth[k]["at"] - f["peak"]))
            if i not in hits:
                hits.add(i)
                delays.append(f["emitted"] - truth[i]["at"])
                if f["start"] <= truth[i]["at"] - setup and f["end"] >= truth[i]["at"] + payoff:
                    full += 1
    return {"moments": len(auto), "true": len(truth), "precision": round(matched / len(auto), 3) if auto else 1.0,
            "recall": round(len(hits) / len(truth), 3) if truth else 1.0, "false_positives": len(auto) - matched,
            "missed": [truth[i]["at"] for i in range(len(truth)) if i not in hits],
            "boundaries_ok": round(full / len(hits), 3) if hits else 1.0,
            "delay_median": round(float(np.median(delays)), 1) if delays else None,
            "delay_max": round(float(max(delays)), 1) if delays else None}


def _loudness(video: str):
    sr = live.SR
    pcm = subprocess.run([engine.ffmpeg(), "-v", "error", "-nostdin", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1",
                          "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm[: len(pcm) // 4 * 4], dtype=np.float32)
    blk = sr // engine.FINE_HZ
    k = x.size // blk
    fine = 10 * np.log10(np.maximum(np.mean(x[: k * blk].reshape(k, blk) ** 2, axis=1), 1e-10))
    secs = len(fine) // engine.FINE_HZ
    loud = np.array([10 * np.log10(max(float(np.mean(10 ** (fine[i * 10:(i + 1) * 10] / 10))), 1e-10))
                     for i in range(secs)], dtype=np.float32)
    return loud, fine[: secs * engine.FINE_HZ].astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--chat")
    ap.add_argument("--truth", required=True)
    ap.add_argument("--frequency", default="normal")
    ap.add_argument("--min-quality", type=int, default=0)
    ap.add_argument("--chat-delay", default="auto")
    a = ap.parse_args()
    loud, fine = _loudness(a.video)
    chat = [(m[0], m[1], m[2], m[3] if len(m) > 3 else "") for m in engine.load_chat(a.chat)] if a.chat else []
    s = replay(loud, fine, chat, chat_on=bool(chat), sensitivity=a.frequency, min_quality=a.min_quality,
               chat_delay=a.chat_delay)
    rep = score(s.found, json.load(open(a.truth, encoding="utf-8")))
    rep["chat_delay"] = s.chat_delay
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
