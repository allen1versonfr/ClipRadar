"""
The songs and music, faster and never stuck (5.7.2):

  - procjob.Watch: Stop ends a tool that has gone quiet, and a read that waits too long ends it and says so; time Clip
    Radar spends working between reads doesn't count.
  - Shazam asked a few at a time (songid._each): in order, faster than one by one, and Stop ends it.
  - Recognising a stretch asks the sample as it is first, and only pulls the music out of it (the slow model pass)
    when that isn't recognised; the YouTube check pulls the music out of a run of samples in one pass.
  - The recognised-song erase's fit: the same result as before, worked out faster.
  - The bar: the song eraser and a recognised song's erase move it to the end of the sound track, not only to the
    last song (where it sat at 99% / 90% while the rest of a long video's sound was read and written).

  python tests/test_song_speed.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="vc_songspeed_"))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import procjob  # noqa: E402
import songerase  # noqa: E402
import songid  # noqa: E402
import songref  # noqa: E402

failures = []
SR = songref.SR


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- a tool watched from the side
py = sys.executable
p = subprocess.Popen([py, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE)
stop_at = time.time() + 1.0


def stopper():
    if time.time() > stop_at:
        raise engine.Cancelled()


w = procjob.Watch([p], stopper)
t0 = time.time()
try:
    with w.waiting():
        p.stdout.read()
    w.raise_if()
    got = "nothing"
except engine.Cancelled:
    got = "stopped"
finally:
    w.close()
check("Stop ends a tool that has gone quiet, at once", got == "stopped" and time.time() - t0 < 6, (got, time.time() - t0))

p = subprocess.Popen([py, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE)
w = procjob.Watch([p], lambda: None, stuck=2.0)
t0 = time.time()
try:
    with w.waiting():
        p.stdout.read()
    w.raise_if()
    why = ""
except RuntimeError as e:
    why = str(e)
finally:
    w.close()
check("a read that waits too long ends the tool, and says so", "stopped responding" in why and time.time() - t0 < 8, why)

p = subprocess.Popen([py, "-c", "import sys,time\nfor _ in range(3):\n sys.stdout.write('x'); sys.stdout.flush(); time.sleep(0.2)"],
                     stdout=subprocess.PIPE)
w = procjob.Watch([p], lambda: None, stuck=2.0)
try:
    while True:
        with w.waiting():
            b = p.stdout.read(1)
        if not b:
            break
        time.sleep(1.5)          # working between reads: longer than nothing at all, but it isn't waiting on the tool
    w.raise_if()
    ok = True
except RuntimeError:
    ok = False
finally:
    w.close()
    p.wait()
check("time spent working between reads isn't counted as the tool hanging", ok)

# ---- Shazam asked a few at a time
songid.GAP = 0


def slow(x):
    time.sleep(0.3)
    return x * 10
t0 = time.time()
got = songid._each(slow, list(range(9)))
took = time.time() - t0
check("asked a few at a time, the answers in order", got == [x * 10 for x in range(9)], got)
check("and faster than one by one", took < 9 * 0.3 * 0.6, round(took, 2))
n = [0]


def stop_after_one():
    n[0] += 1
    if n[0] > 3:
        raise engine.Cancelled()
t0 = time.time()
try:
    songid._each(lambda x: time.sleep(0.5), list(range(40)), stop_after_one)
    stopped = False
except engine.Cancelled:
    stopped = True
check("Stop ends it, the rest not asked", stopped and time.time() - t0 < 5, time.time() - t0)

# ---- the model pass only when the sample as it is isn't recognised
pulled = []
real_pull, real_ask = songid.pull_music, songid._ask
songid.pull_music = lambda x: pulled.append(x.shape[1]) or x * 0.5
songid._ask = lambda x: {"title": "Song", "artist": "Band", "key": "1"} if float(np.abs(x).max()) > 0.6 else None
loud = np.full((2, 40 * SR), 0.9, dtype=np.float32)
got = songid.identify(lambda t, k: loud[:, int(t * SR):int((t + k) * SR)], 0.0, 40.0)
check("a sample recognised as it is: the music isn't pulled out of it", got and not pulled, (got, pulled))

# the YouTube check: what's left of the music pulled out of the samples, and heard there
real_window = songid.pull_window
erased = np.full((2, 60 * SR), 0.5, dtype=np.float32)      # not recognised as it is (0.5), recognised pulled out?
songid.pull_window = lambda x: pulled.append(x.shape[1]) or x * 1.6
del pulled[:]
left = songid.still_heard(lambda t, k: erased[:, int(t * SR):int((t + k) * SR)], [(0.0, 48.0)])
check("the YouTube check finds a song only heard with the music pulled out", [(a, b) for a, b, _ in left] ==
      [(0.0, 12.0), (12.0, 24.0), (24.0, 36.0), (36.0, 48.0)], left)
check("each sample pulled out once, 12 s each", len(pulled) == 4 and set(pulled) == {12 * SR}, pulled)
del pulled[:]
songid._ask = lambda x: {"title": "Song", "artist": "Band", "key": "1"}
left = songid.still_heard(lambda t, k: erased[:, int(t * SR):int((t + k) * SR)], [(0.0, 48.0)])
check("samples recognised as they are aren't pulled out at all", len(left) == 4 and not pulled, (left, pulled))

# 5.7.8: an hour of erased music no longer recognisable: the model over one sample in PROBE, not all of it again
hour = np.full((2, 3700 * SR), 0.5, dtype=np.float32)
songid._ask = lambda x: {"title": "Song", "artist": "Band", "key": "1"} if float(np.abs(x).max()) > 0.6 else None
songid.pull_window = lambda x: pulled.append(x.shape[1]) or x
del pulled[:]
left = songid.still_heard(lambda t, k: hour[:, int(t * SR):int((t + k) * SR)], [(0.0, 3600.0)])
check("an hour with nothing left: a quarter of the samples pulled out", not left and len(pulled) == 75, len(pulled))

# a song left recognisable for a minute in the middle: found, and every sample of it, the rest not pulled out
song = np.full((2, 600 * SR), 0.5, dtype=np.float32)
song[:, 120 * SR:180 * SR] = 0.6                            # recognised only pulled out, and only there (0.96 > 0.9)
songid._ask = lambda x: {"title": "Song", "artist": "Band", "key": "1"} if float(np.abs(x).max()) > 0.9 else None
songid.pull_window = lambda x: pulled.append(x.shape[1]) or x * 1.6
del pulled[:]
left = songid.still_heard(lambda t, k: song[:, int(t * SR):int((t + k) * SR)], [(0.0, 600.0)])
check("a minute still recognisable: all of it found", [(a, b) for a, b, _ in left] ==
      [(120.0, 132.0), (132.0, 144.0), (144.0, 156.0), (156.0, 168.0), (168.0, 180.0)], left)
check("and the samples around it, not the rest", len(pulled) < 50 / 4 + 5 + 3, len(pulled))

# a sample recognised as it is: the ones beside it pulled out too, where the song goes on more quietly
song = np.full((2, 600 * SR), 0.5, dtype=np.float32)
song[:, 60 * SR:72 * SR] = 0.95                             # 60-72 heard as it is
song[:, 48 * SR:60 * SR] = 0.6                              # 48-60 and 72-84 only pulled out
song[:, 72 * SR:84 * SR] = 0.6
del pulled[:]
left = songid.still_heard(lambda t, k: song[:, int(t * SR):int((t + k) * SR)], [(0.0, 600.0)])
check("beside a sample heard as it is, the quieter rest of the song found", [(a, b) for a, b, _ in left] ==
      [(48.0, 60.0), (60.0, 72.0), (72.0, 84.0)], left)

# 5.7.8: Shazam not answering at all: the check gives up after a few requests, not one 30 s wait per sample for hours
def no_answer(x):
    songid._fails[0] += 1
    time.sleep(0.05)
    return None
songid._ask = no_answer
t0 = time.time()
try:
    songid.still_heard(lambda t, k: hour[:, int(t * SR):int((t + k) * SR)], [(0.0, 3600.0)])
    gave_up = None
except RuntimeError as ex:
    gave_up = str(ex)
check("Shazam not answering: the check stops, and says why", gave_up == "Shazam isn't answering" and time.time() - t0 < 10,
      (gave_up, round(time.time() - t0, 1)))
songid._ask = lambda x: songid._fails.__setitem__(1, songid._fails[1] + 1)      # answered: nothing heard
left = songid.still_heard(lambda t, k: hour[:, int(t * SR):int((t + 120) * SR)][:, :int(k * SR)], [(0.0, 120.0)], separate=False)
check("answered with nothing heard isn't giving up", left == [], left)

# where the probes go: every PROBE samples in a row hold one, short runs and the end of a run included
for n in (1, 2, 3, 4, 5, 6, 7, 9, 13, 50):
    ws = [(12.0 * i, 12.0 * i + 12) for i in range(n)]
    nx = {w: v for w, v in zip(ws, ws[1:])}
    got = {ws.index(w) for w in songid.probes(ws, nx)}
    gaps = all(any(i <= j < i + songid.PROBE for j in got) for i in range(max(1, n - songid.PROBE + 1)))
    check(f"probes over {n} samples: every {songid.PROBE} in a row hold one", gaps and len(got) <= -(-n // songid.PROBE) + 1,
          sorted(got))
apart = [(0.0, 12.0), (100.0, 112.0)]
check("samples not back to back are each their own run", len(songid.probes(apart, {})) == 2)
songid.pull_music, songid.pull_window, songid._ask = real_pull, real_window, real_ask
check("no sound is nothing to recognise, never an error", songid._ask(np.zeros((2, 0), dtype=np.float32)) is None)

# ---- the fit: the same as before (the reference below is 5.7.1's), faster


def fit_before(X, R, iters=3, smooth=songref.G_SMOOTH):
    T = X.shape[2]
    G = np.ones(T)
    w = np.ones(X.shape)
    est = R
    k = np.ones(smooth) / smooth
    for _ in range(iters):
        RG = R * G[None, None, :]
        E = (w * X * np.conj(RG)).sum(2) / ((w * np.abs(RG) ** 2).sum(2) + 1e-9)
        RE = R * E[..., None]
        g = (w * (X * np.conj(RE)).real).sum((0, 1)) / ((w * np.abs(RE) ** 2).sum((0, 1)) + 1e-9)
        G = np.maximum(np.convolve(g, k, "same"), 0.0)
        est = RE * G[None, None, :]
        res = X - est
        w = np.abs(est) ** 2 / (np.abs(est) ** 2 + np.abs(res) ** 2 + 1e-12)
    return est


rng = np.random.default_rng(3)
r = rng.standard_normal((2, 8 * SR)).astype(np.float32) * 0.2
talk = rng.standard_normal((2, 8 * SR)).astype(np.float32) * 0.05
X, R = songref.stft(r * 0.7 + talk), songref.stft(r)
t0 = time.perf_counter()
a = fit_before(X.astype(np.complex128), R.astype(np.complex128))
t_before = time.perf_counter() - t0
t0 = time.perf_counter()
b = songref.fit(X, R)
t_now = time.perf_counter() - t0
rel = float(np.abs(a - b).max() / (np.abs(a).max() + 1e-12))
check("the fit comes out the same as before", rel < 1e-3, rel)
check("in less time", t_now < t_before, (round(t_before, 2), round(t_now, 2)))
x = rng.standard_normal((2, 5 * SR)).astype(np.float32)
check("the spectrogram there and back gives the sound back", float(np.abs(songref.istft(songref.stft(x), x.shape[1]) - x).max()) < 1e-4)
check("and the eraser's too", float(np.abs(songerase.istft(songerase.stft(x), x.shape[1]) - x).max()) < 1e-4)

# ---- the bar, to the end of the sound track
if shutil.which("ffmpeg") or os.path.isfile(engine.ffmpeg()):
    tmp = tempfile.mkdtemp(prefix="vc_songbar_")
    try:
        video = os.path.join(tmp, "vod.mp4")
        subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=64x36:r=5:d=90",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=90", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", "-shortest", video], check=True)

        class Quiet:            # stands in for the song eraser model: everything is music
            def run(self, spec):
                out = np.zeros((1, 6) + spec.shape[2:], dtype=np.float32)
                out[0, 2] = 1.0
                return out
        songerase.session_impl[0] = Quiet()
        seen = []
        songerase.erase(video, [(0.0, 12.0)], os.path.join(tmp, "e.flac"), progress=seen.append)
        before_end = [x for x in seen if x < 1.0]
        check("the song eraser's bar goes on past the last song, to the end of the sound",
              before_end and max(before_end) > 0.97 and len([x for x in before_end if 0.86 < x < 0.97]) >= 3, before_end[-8:])
        check("and only forward", all(b >= a for a, b in zip(seen, seen[1:])), seen)
        songerase.session_impl[0] = None

        seen = []
        songref.erase_known(video, [(5.0, 20.0, os.path.join(tmp, "not there.flac"))], os.path.join(tmp, "k.flac"), 0.0, 90.0,
                            os.path.join(tmp, "songs"), progress=seen.append)
        check("a recognised song's erase moves the bar through its pass over the whole sound, not stuck at 90%",
              len([x for x in seen if 0.9 < x < 1.0]) >= 3, seen[-6:])

        # the whole-video search reads the sound once and finds the song where it plays
        song = (rng.standard_normal((2, 30 * SR)) * 0.2).astype(np.float32)
        mixx = (rng.standard_normal((2, 240 * SR)) * 0.02).astype(np.float32)
        mixx[:, 150 * SR:180 * SR] += song * 0.7
        f = os.path.join(tmp, "long.flac")
        subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", f],
                       input=mixx.T.tobytes(), check=True)
        launches = []
        real_popen = subprocess.Popen

        def counting(cmd, *a, **k):
            launches.append(cmd)
            return real_popen(cmd, *a, **k)
        songref.subprocess.Popen = counting
        try:
            hits = songref.sweep(f, 240.0, {"s": song}).get("s", [])
        finally:
            songref.subprocess.Popen = real_popen
        check("the whole-video search finds the song where it plays", hits and any(a <= 150 and b >= 180 for a, b in hits), hits)
        check("reading the sound once, not seeking for each minute", len(launches) == 1, len(launches))
    finally:
        songerase.session_impl[0] = None
        shutil.rmtree(tmp, ignore_errors=True)
else:
    print("[skip] no ffmpeg here")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
