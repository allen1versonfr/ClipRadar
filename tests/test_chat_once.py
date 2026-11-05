"""
Chat is weighed once per open (4.45.1). Before, every message was scored again by detect.enrich after
analyze had already done it, and again on every reopen of a saved analysis: about 3 s a pass for
300,000 messages, in plain Python.

  - the signals come out exactly as when enrich weighs the chat itself
  - opening weighs each message once; reopening a saved analysis weighs none
  - an analysis saved by an older version (no chat rate in it) still works: chat is weighed once, as before

  python tests/test_chat_once.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import chatsignal  # noqa: E402
import detect  # noqa: E402
import engine  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


random.seed(4)
dur = 1800
words = ["KEKW", "LUL", "no way", "what", "POG", "gg", "hi chat", "?????", "hahaha", "clip it", "W", "bro"]
chat = sorted((random.uniform(0, dur), " ".join(random.choices(words, k=random.randint(1, 3))), f"u{random.randint(0, 400)}", "")
              for _ in range(20_000))
for t0 in (300, 900, 1500):                      # three real moments
    chat += [(t0 + random.uniform(0, 8), "KEKW KEKW", f"m{i}", "") for i in range(300)]
chat.sort()
n = dur + 1
loud = np.full(n, -30.0, dtype=np.float32) + np.random.default_rng(1).normal(0, 1, n).astype(np.float32)

fed = [0]
real_feed = chatsignal.ChatScorer.feed


def counting_feed(self, msgs, talk=None):
    fed[0] += len(msgs)
    return real_feed(self, msgs, talk)


chatsignal.ChatScorer.feed = counting_feed

# opening: analyze's own pass, then _finish
fed[0] = 0
an = engine.score_signals(loud, chat, n, float(dur), 6.0, 60)
engine._finish(an, chat, None, None, 60)
check("opening weighs each message once", fed[0] == len(chat), (fed[0], len(chat)))

# the same signals as enrich weighing the chat itself (what it did before)
fed[0] = 0
ref = engine.score_signals(loud, chat, n, float(dur), 6.0, 60)
detect.enrich(ref, chat, ref.chat_delay, None)
ref.combine(60, detect.preset("any"))
for name in ("surge", "emote_rate", "react", "noise", "talk", "score"):
    a, b = getattr(an, name), getattr(ref, name)
    same = (a is None and b is None) or (a is not None and b is not None and np.allclose(a, b))
    check(f"{name}: the same as before", same)

# reopening a saved analysis weighs nothing
d = tempfile.mkdtemp(prefix="vc_once_")
path = os.path.join(d, "analysis.npz")
engine._save_analysis(path, "k", an)
fed[0] = 0
back = engine._load_analysis(path, "k")
engine._finish(back, chat, None, None, 60)
check("reopening a saved analysis weighs no message again", fed[0] == 0, fed[0])
check("and ranks the same", np.allclose(back.score, an.score))

# an analysis saved by an older version: no chat rate, so chat is weighed once, as before
old = dict(np.load(path, allow_pickle=False))
old.pop("chat_rate")
np.savez(path, **old)
fed[0] = 0
back_old = engine._load_analysis(path, "k")
engine._finish(back_old, chat, None, None, 60)
check("an older saved analysis still opens, weighing the chat once", back_old.chat_rate is None and fed[0] == len(chat), fed[0])
check("and ranks the same", np.allclose(back_old.score, an.score))

# no chat: nothing kept, nothing weighed
fed[0] = 0
quiet = engine.score_signals(loud, [], n, float(dur), 6.0, 60)
engine._finish(quiet, [], None, None, 60)
check("no chat: no chat rate, nothing weighed", quiet.chat_rate is None and fed[0] == 0)
check("a part of the analysis keeps it (live moments)", an.part(100, 200).chat_rate is not None)

chatsignal.ChatScorer.feed = real_feed
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
