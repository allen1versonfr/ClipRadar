"""
Erase song with the song named (5.3): the recording found in the stream's sound and taken out, the talking kept.

A song is made up here (chords and drums), and a "stream" of someone talking over it: the song delayed, EQ'd and
turned down and up the way a streamer's setup does. Checked: the song found and taken out (far quieter between
sentences and under the talking), the talking left nearly as it was, a different song left alone, the sound outside
the stretch untouched sample for sample, and Export VOD's options reading which song each stretch is.

  python tests/test_songref.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import numpy as np  # noqa: E402

import engine  # noqa: E402
import songid  # noqa: E402
import songref  # noqa: E402
import vodexport  # noqa: E402

failures = []
SR = songref.SR


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def db(x) -> float:
    return float(10 * np.log10(np.mean(np.square(x)) + 1e-12))


def song(seed: int, seconds: float) -> np.ndarray:
    """chords that change every half second, a bass, and drums: [2, n]"""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros((2, n), dtype=np.float64)
    beat = int(0.5 * SR)
    for i in range(0, n, beat):
        k = min(beat, n - i)
        tt = t[i:i + k]
        env = np.exp(-3 * (tt - tt[0]))
        root = 110 * 2 ** (rng.integers(0, 12) / 12)
        for j, ratio in enumerate((1, 1.26, 1.5, 2, 2.52)):
            f = root * ratio * 2
            pan = 0.3 + 0.4 * (j % 2)
            tone = np.sin(2 * np.pi * f * tt + rng.random() * 6) * env * 0.12
            out[0, i:i + k] += tone * (1 - pan)
            out[1, i:i + k] += tone * pan
        out[:, i:i + k] += np.sin(2 * np.pi * root / 2 * tt) * env * 0.2
        hit = min(k, int(0.06 * SR))
        drum = rng.standard_normal(hit) * np.exp(-np.arange(hit) / (0.01 * SR)) * 0.4
        out[:, i:i + hit] += drum
    return (out / np.abs(out).max() * 0.7).astype(np.float32)


def talking(seconds: float) -> np.ndarray:
    """a voice: sentences of syllables (a pitch that moves, a few harmonics), a pause between sentences: [2, n]"""
    rng = np.random.default_rng(7)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    pitch = 140 + 25 * np.sin(2 * np.pi * 0.7 * t) + 10 * np.sin(2 * np.pi * 2.3 * t)
    phase = 2 * np.pi * np.cumsum(pitch) / SR
    v = sum(np.sin(h * phase) / h for h in range(1, 12))
    syll = (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) ** 2
    on = np.zeros(n)
    s = 0.5
    while s < seconds:
        a, b = s, min(seconds, s + rng.uniform(2.5, 4.0))
        on[int(a * SR):int(b * SR)] = 1
        s = b + rng.uniform(1.2, 2.0)
    voice = (v * syll * on * 0.25).astype(np.float32)
    return np.stack([voice, voice]), on.astype(bool)


def through_setup(x: np.ndarray, delay: float) -> np.ndarray:
    """what a streamer's setup does to a song: later, darker (highs rolled off), quieter, its volume moving"""
    f = np.fft.rfftfreq(x.shape[1], 1 / SR)
    tone = 1 / np.sqrt(1 + (f / 3000) ** 2) * np.exp(-1j * np.arctan(f / 3000))      # a gentle low-pass, with its phase
    y = np.fft.irfft(np.fft.rfft(x, axis=1) * tone, n=x.shape[1], axis=1).astype(np.float32)
    d = int(delay * SR)
    y = np.concatenate([np.zeros((2, d), dtype=np.float32), y], axis=1)
    ride = 0.45 + 0.15 * np.sin(2 * np.pi * np.arange(y.shape[1]) / SR / 9.0)
    return (y * ride).astype(np.float32)


def write(path: str, x: np.ndarray) -> None:
    p = subprocess.run([engine.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-c:a", "flac", path], input=np.ascontiguousarray(x.T, dtype="<f4").tobytes(), capture_output=True)
    assert p.returncode == 0, p.stderr


def have_ffmpeg() -> bool:
    try:
        engine.ffmpeg()
        return True
    except RuntimeError:
        return False


# the song as released, and the stream: 40 s of talking, the song from 4 s in (it started 3.7 s before the stream's 0
# in its own time: the stream picks it up partway)
rec = song(1, 60)
voice, on = talking(40)
played = through_setup(rec[:, int(3.7 * SR):], 4.0)[:, :voice.shape[1]]
music = np.zeros_like(voice)
music[:, :played.shape[1]] = played
mix = voice + music

out, rep = songref.clean(mix.copy(), rec)
inside = slice(int(5 * SR), int(39 * SR))
pauses = ~on[inside]
res = (out - voice)[:, inside]
mus = music[:, inside]
drop_pause = db(res[:, pauses]) - db(mus[:, pauses])
drop_talk = db(res[:, ~pauses]) - db(mus[:, ~pauses])
a = voice[:, inside][:, ~pauses].ravel()
b = out[:, inside][:, ~pauses].ravel()
al = (a @ b) / (a @ a)
sisdr = db(al * a) - db(b - al * a)
print(f"    song {drop_pause:+.1f} dB between sentences, {drop_talk:+.1f} dB under talking, talking SI-SDR {sisdr:.1f} dB, "
      f"found {rep['found']} s, missed {rep['missed']}")
check("the song is found", rep["found"] >= 30 and not [m for m in rep["missed"] if m[0] >= 5], str(rep))
check("the song is far quieter between sentences (at least 18 dB)", drop_pause <= -18, f"{drop_pause:.1f}")
check("and under the talking (at least 10 dB)", drop_talk <= -10, f"{drop_talk:.1f}")
check("the talking is kept (SI-SDR at least 15 dB)", sisdr >= 15, f"{sisdr:.1f}")

other = song(2, 60)
out2, rep2 = songref.clean(mix.copy(), other)
check("a different song isn't found and nothing is changed",
      rep2["found"] == 0 and float(np.abs(out2 - mix).max()) < 1e-6, str(rep2))

# Export VOD's options: which song each Erase song stretch is
o = vodexport.clean_options({"music": [[10, 30, "voice", "https://www.youtube.com/watch?v=abc"],
                                       [40, 50, "voice", "relative/song.mp3"],
                                       [60, 70, "mute", "https://www.youtube.com/watch?v=x"],
                                       [80, 90, "voice"]]}, 100.0)
check("a link is read as the song of its stretch", o["songs"] == [(10.0, 30.0, "https://www.youtube.com/watch?v=abc")], str(o["songs"]))
check("every stretch keeps what to do with it", [m for _, _, m in o["music"]] == ["voice", "voice", "mute", "voice"], str(o["music"]))
check("a full path is a song file", vodexport.song_source(os.path.abspath(__file__)) == os.path.abspath(__file__)
      and vodexport.song_source("song.mp3") == "")

# recognising the song (no network here: Shazam's answer and YouTube's search are made up)
rows = [{"title": "Midnight City (Live at Coachella)", "channel": "M83", "url": "u-live", "duration": 260},
        {"title": "M83 - Midnight City (Official Video)", "channel": "M83VEVO", "url": "u-video", "duration": 250},
        {"title": "Midnight City", "channel": "M83 - Topic", "url": "u-topic", "duration": 243},
        {"title": "Midnight City (Slowed + Reverb)", "channel": "someone", "url": "u-slowed", "duration": 300},
        {"title": "Some other song", "channel": "M83 - Topic", "url": "u-other", "duration": 200},
        {"title": "Midnight City 10 hours", "channel": "loops", "url": "u-long", "duration": 36000}]
got = [c["url"] for c in songid.candidates({"title": "Midnight City", "artist": "M83"}, search=lambda q: rows)]
check("the artist's own recording (YouTube's Topic upload) is tried first, then the artist's video",
      got[:2] == ["u-topic", "u-video"], str(got))
check("live versions, slowed edits, other songs and 10-hour loops aren't taken for the recording",
      not {"u-live", "u-slowed", "u-other", "u-long"} & set(got), str(got))
check("samples are taken across a stretch, the middle first, each one long enough for Shazam",
      songid.windows(100, 220)[0] == 154.0 and all(100 <= t <= 220 - songid.WINDOW for t in songid.windows(100, 220))
      and songid.windows(100, 105) == [100])

# the scan's recognised songs are used by Segments with nothing set on Export VOD
pr = {"workdir": tempfile.mkdtemp(prefix="songref_pr_"), "video": os.path.abspath(__file__),
      "copyright": {"at": 1.0, "how": "test", "curve": [], "spans": [
          {"start": 10.0, "end": 30.0, "confidence": 0.9, "speech": 0.5, "song": {"title": "A", "artist": "B", "url": "https://www.youtube.com/watch?v=t"}},
          {"start": 50.0, "end": 60.0, "confidence": 0.9, "speech": 0.5, "song": {"title": "C", "artist": "D", "url": ""}},
          {"start": 70.0, "end": 80.0, "confidence": 0.9, "speech": 0.5}]}}
check("Segments takes the recognised recordings out (only those whose recording was found)",
      vodexport.suggested_songs(pr) == [(10.0, 30.0, "https://www.youtube.com/watch?v=t")], str(vodexport.suggested_songs(pr)))
view = vodexport._scan_view(pr["copyright"])
check("a stretch whose recording was found starts as Erase song", view["spans"][0]["suggest"] == "voice", str(view["spans"][0]))
vodexport.save_music(pr, {"at": 1.0, "songs": [{"start": 12.0, "end": 28.0, "act": "voice",
                                               "found": {"title": "A", "artist": "B", "url": "https://www.youtube.com/watch?v=t"}},
                                              {"start": 50.0, "end": 60.0, "act": "voice", "song": "https://youtu.be/mine"}]})
check("edges you moved keep the song recognised for them, and a song you named is used",
      vodexport.suggested_songs(pr) == [(12.0, 28.0, "https://www.youtube.com/watch?v=t"), (50.0, 60.0, "https://youtu.be/mine")],
      str(vodexport.suggested_songs(pr)))
shutil.rmtree(pr["workdir"], ignore_errors=True)

# 5.7: would YouTube still catch it? Each erased stretch heard again in back-to-back samples
heard_at = []
real_ask = songid._ask
songid._ask = lambda x: ({"title": "Song", "artist": "Band"} if float(np.abs(x).max()) > 0.5 else None)
songid.GAP = 0
loud = np.zeros((2, 60 * SR), dtype=np.float32)
loud[:, 20 * SR:35 * SR] = 0.9                  # still loud from 20 s to 35 s of the erased sound
left = songid.still_heard(lambda t, n: loud[:, int(t * SR):int((t + n) * SR)], [(0.0, 50.0)], separate=False)
songid._ask = real_ask
check("what's still recognisable after erasing is found, in the samples it's heard in",
      [(a, b) for a, b, _ in left] == [(12.0, 24.0), (24.0, 36.0)] and left[0][2] == "Band - Song", left)

if not have_ffmpeg():
    print("[skip] no ffmpeg here: the song file and the export's sound aren't made (the render job runs them)")
else:
    tmp = tempfile.mkdtemp(prefix="songref_")
    try:
        ref_file = os.path.join(tmp, "the song.flac")
        write(ref_file, rec)
        stream = os.path.join(tmp, "stream.flac")
        write(stream, mix)
        cache = os.path.join(tmp, "songs")
        got = songref.fetch(ref_file, cache)
        check("a song file on this PC is read and kept", os.path.isfile(got) and got.startswith(cache))
        check("asked again, the kept copy is used", songref.fetch(ref_file, cache) == got)
        try:
            songref.fetch(os.path.join(tmp, "nope.mp3"), cache)
            check("a missing file says so", False)
        except RuntimeError as e:
            check("a missing file says so", "isn't there" in str(e), str(e))

        # in an export: only the stretch (10 s to 30 s) changes, from a start of 2 s for 36 s
        done = os.path.join(tmp, "done.flac")
        r = songref.erase_known(stream, [(10.0, 30.0, ref_file)], done, 2.0, 36.0, cache)
        y = songref.read(done)
        x = songref.read(stream, 2.0, 36.0)
        check("the export's sound is as long as asked", abs(y.shape[1] - 36 * SR) <= SR // 100, str(y.shape))
        n = min(x.shape[1], y.shape[1])
        before, after = slice(0, int(7.9 * SR)), slice(int(28.1 * SR), n)
        check("outside the stretch the sound is untouched",
              float(np.abs(y[:, before] - x[:, before]).max()) < 2e-4 and float(np.abs(y[:, after] - x[:, after]).max()) < 2e-4)
        mid = slice(int(9 * SR), int(27 * SR))                  # 11 s to 29 s of the video
        drop = db(y[:, mid] - voice[:, int(11 * SR):int(29 * SR)]) - db(music[:, int(11 * SR):int(29 * SR)])
        check("inside it the song is taken out", drop <= -10, f"{drop:.1f}")
        check("the report says how much was found", r["found"] >= 15 and not r["missed"], str(r))

        # a long stretch is cleaned a few minutes at a time (5.4): here one piece a block, so the stretch is several
        # blocks, and the result is the same as in one go
        real_block = songref.BLOCK
        songref.BLOCK = songref.PIECE - songref.OVERLAP
        try:
            done2 = os.path.join(tmp, "done_blocks.flac")
            r2 = songref.erase_known(stream, [(10.0, 30.0, ref_file)], done2, 2.0, 36.0, cache)
        finally:
            songref.BLOCK = real_block
        y2 = songref.read(done2)
        drop2 = db(y2[:, mid] - voice[:, int(11 * SR):int(29 * SR)]) - db(music[:, int(11 * SR):int(29 * SR)])
        check("in blocks, the song is taken out as well as in one go", abs(drop2 - drop) <= 1.0 and r2["found"] >= 15, f"{drop2:.1f} vs {drop:.1f}, {r2}")
        check("and its temporary files are gone", not [f for f in os.listdir(tmp) if f.endswith(".f32")], os.listdir(tmp))

        # two exports of one stream (same folder, same process) each keep their own temporary files: one finishing
        # used to delete the other's mid-read (known_<pid>_0.f32, "No such file or directory")
        raws, real_clean = [], songref._clean_stretch
        songref._clean_stretch = lambda video, x0, x1, ref, raw, *a: (raws.append(raw), real_clean(video, x0, x1, ref, raw, *a))[1]
        try:
            for name in ("export_a.flac", "export_b.flac"):
                songref.erase_known(stream, [(10.0, 30.0, ref_file)], os.path.join(tmp, name), 2.0, 36.0, cache)
        finally:
            songref._clean_stretch = real_clean
        check("two exports into one folder never share a temporary file", len(raws) == 2 and raws[0] != raws[1], str(raws))

        # the recording found for a recognised song must be the one in the stream: a wrong upload ranked first is passed over
        wrong_file = os.path.join(tmp, "wrong.flac")
        write(wrong_file, song(2, 60))
        found = songid.find_recording({"title": "Song", "artist": "Band"}, mix[:, 10 * SR:30 * SR], cache, search=lambda q: [
            {"title": "Song", "channel": "Band - Topic", "url": wrong_file, "duration": 60},
            {"title": "Band - Song", "channel": "Band", "url": ref_file, "duration": 60}])
        check("of the recordings found, the one that's really in the stream is taken", (found or {}).get("url") == ref_file, str(found))

        # 5.7: everywhere a recognised song plays, found with its recording (here: twice, once quiet under talking,
        # and not where another song plays)
        dur = 300
        long_voice, _ = talking(dur)
        long_mix = long_voice.copy()

        def put(x, at, gain):
            k = min(x.shape[1], long_mix.shape[1] - int(at * SR))
            long_mix[:, int(at * SR):int(at * SR) + k] += x[:, :k] * gain
        put(through_setup(rec[:, :50 * SR], 0), 40, 1.0)
        put(through_setup(rec[:, 10 * SR:40 * SR], 0), 210, 0.25)
        put(through_setup(song(2, 40), 0), 130, 1.0)
        long_file = os.path.join(tmp, "long.flac")
        write(long_file, long_mix)
        hits = songref.sweep(long_file, dur, {"rec": rec})["rec"]
        inside = lambda t: any(a <= t <= b for a, b in hits)  # noqa: E731
        check("a recognised song is found everywhere it plays, quiet under talking too",
              inside(60) and inside(80) and inside(220), hits)
        check("and not where another song plays", not inside(150), hits)
        pr = {"video": long_file, "workdir": tmp, "duration": dur,
              "copyright": {"at": 1, "how": "t", "curve": [], "spans": [
                  {"start": 45.0, "end": 70.0, "confidence": 0.9, "speech": 0.2, "song": {"title": "Song", "artist": "Band", "url": ref_file}},
                  {"start": 132.0, "end": 160.0, "confidence": 0.9, "speech": 0.2}]}}
        added = vodexport.spread_songs(pr, pr["copyright"])
        sp = pr["copyright"]["spans"]
        check("the music scan's stretches get the places it missed added, as Erase song stretches with the song",
              added > 0 and any(x.get("found") == "everywhere" and x["start"] <= 215 <= x["end"] and x["song"]["url"] == ref_file for x in sp)
              and not any(x["start"] < x2["end"] and x2["start"] < x["end"] for i, x in enumerate(sp) for x2 in sp[i + 1:]),
              [(x["start"], x["end"], (x.get("song") or {}).get("title"), x.get("found")) for x in sp])
        check("a stretch of another song isn't given this one", "song" not in next(x for x in sp if x["start"] == 132.0))

        # recognising a scan's stretches: Shazam's answer made up, everything else real
        asked = []
        real_ask = songid._ask
        songid._ask = lambda x: asked.append(x.shape[1]) or {"title": "Song", "artist": "Band", "key": "1"}
        real_search = songid._search
        songid._search = lambda q: [{"title": "Band - Song", "channel": "Band", "url": ref_file, "duration": 60}]
        try:
            spans = [{"start": 6.0, "end": 38.0}, {"start": 0.0, "end": 4.0}]
            n = songid.name_spans(stream, spans, cache)
        finally:
            songid._ask, songid._search = real_ask, real_search
        check("a stretch is recognised and its recording found", n == 1 and spans[0].get("song", {}).get("url") == ref_file, str(spans))
        check("Shazam is sent 12 s samples, and a stretch too short isn't sent at all",
              asked and all(k >= songid.WINDOW * SR * 0.95 for k in asked) and "song" not in spans[1], str(asked))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{len(failures)} failed" if failures else "\nall ok")
sys.exit(1 if failures else 0)
