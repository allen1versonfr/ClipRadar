"""
Songs ready while recording (5.8, livesongs.py): the live session's music, heard by the sound model as it recorded, is
saved as the saved stream's copyright scan, so Export VOD doesn't listen to the whole stream again; and with
"Recognise songs while recording" on, each music stretch is recognised in the background once it's over (or has
played a minute), so the scan opens with its songs named.

Shazam and YouTube are stood in for: what's checked is what's asked, when, from which sound, and what's kept.

  python tests/test_live_songs.py      (or: pytest tests/test_live_songs.py)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="vc_test_"))
os.environ.setdefault("CLIPRADAR_WORK", tempfile.mkdtemp(prefix="vc_test_"))
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402
import live  # noqa: E402
import livesongs  # noqa: E402
import paths  # noqa: E402
import songref  # noqa: E402
import vodexport  # noqa: E402

WORK = str(paths.WORKROOT)
SR = songref.SR
SONG = {"title": "Song", "artist": "Band"}


def music(n: int, on: list) -> np.ndarray:
    x = np.zeros(n, dtype=np.float32)
    for a, b in on:
        x[a:b] = 0.9
    return x


class Fakes:
    """Shazam and the YouTube search, stood in for."""

    def __init__(self, answer=SONG, gate: threading.Event = None):
        self.asked, self.found, self.answer, self.gate = [], [], answer, gate

    def identify(self, get, a, b, check=None, separate=True, tick=None):
        if self.gate:
            self.gate.wait(10)
        self.asked.append((a, b, separate, get(a, 12.0).shape))
        return dict(self.answer) if self.answer else None

    def find(self, song, sound, folder, check=None, log=None):
        self.found.append(song["title"])
        return {"url": "https://www.youtube.com/watch?v=abcdefghijk", "name": song["title"]}


def songs_with(f: Fakes, pieces=lambda: [], read=None) -> livesongs.LiveSongs:
    return livesongs.LiveSongs(pieces, tempfile.mkdtemp(prefix="vc_songs_"), identify=f.identify, find=f.find,
                               read=read or (lambda path, t=0.0, n=0.0: np.zeros((2, int((n or 10) * SR)), np.float32)))


def test_a_stretch_is_recognised_once_it_is_over():
    f = Fakes()
    ls = songs_with(f)
    m = music(40, [(5, 25)])
    ls.update(m[:28], None, 28)                       # 3 s after the music: it could still go on
    assert ls.wait(5) and not f.asked, "a stretch still within the scan's gap isn't asked about yet"
    ls.update(m[:40], None, 40)                       # 15 s of quiet: over
    assert ls.wait(5) and len(f.asked) == 1, f.asked
    a, b, separate, shape = f.asked[0]
    assert a <= 5 and b >= 25 and separate is False, "the eraser model isn't run while recording"
    ls.update(m[:40], None, 40)
    ls.update(m[:40], None, 40, final=True)
    assert ls.wait(5) and len(f.asked) == 1, "and only once"
    r = ls.results()
    assert r[0]["song"]["title"] == "Song" and r[0]["song"]["url"].startswith("https://www.youtube.com/"), r


def test_a_long_stretch_is_recognised_while_it_still_plays():
    f = Fakes()
    ls = songs_with(f)
    ls.update(music(50, [(0, 50)]), None, 50)
    assert ls.wait(5) and not f.asked, "50 s of music still playing: not yet"
    ls.update(music(70, [(0, 70)]), None, 70)
    assert ls.wait(5) and len(f.asked) == 1, "a minute in: recognised without waiting for it to end"
    ls.update(music(200, [(0, 200)]), None, 200, final=True)
    assert ls.wait(5) and len(f.asked) == 1, "the same stretch isn't asked about again as it grows"


def test_short_music_and_the_end_of_the_stream():
    f = Fakes(answer=None)
    ls = songs_with(f)
    ls.update(music(60, [(10, 15)]), None, 60)
    assert ls.wait(5) and not f.asked, "a jingle isn't recognised"
    ls.update(music(60, [(40, 58)]), None, 60, final=True)
    assert ls.wait(5) and len(f.asked) == 1, "the stream ended: a stretch still playing is recognised too"
    assert ls.results()[0]["song"] is None and not f.found, "not recognised: no recording looked for"


def test_the_same_song_is_looked_for_on_youtube_once():
    f = Fakes()
    ls = songs_with(f)
    ls.update(music(200, [(0, 20), (60, 80), (120, 140)]), None, 200, final=True)
    assert ls.wait(5) and len(f.asked) == 3 and f.found == ["Song"], (f.asked, f.found)


def test_a_failure_is_logged_and_recording_goes_on():
    logs = []

    def boom(*a, **k):
        raise RuntimeError("Shazam is down")
    ls = livesongs.LiveSongs(lambda: [], tempfile.mkdtemp(), log=logs.append, identify=boom, find=Fakes().find,
                             read=lambda *a: np.zeros((2, 0), np.float32))
    ls.update(music(60, [(0, 20)]), None, 60)
    assert ls.wait(5) and ls.results() == [{"start": ls.results()[0]["start"], "end": ls.results()[0]["end"], "song": None}]
    assert any("Shazam is down" in m for m in logs), logs


def test_the_sound_comes_from_the_recorded_pieces():
    folder = tempfile.mkdtemp(prefix="vc_pieces_")
    pieces = []
    for i in range(4):
        p = os.path.join(folder, f"seg_{i}.ts")
        open(p, "w").close()
        pieces.append({"path": p, "start": i * 10.0, "dur": 10.0})

    def read(path, t=0.0, n=0.0):            # each piece's sound is its number
        i = int(os.path.basename(path)[4])
        return np.full((2, 10 * SR), float(i), np.float32)
    ls = livesongs.LiveSongs(lambda: pieces, folder, identify=Fakes().identify, find=Fakes().find, read=read)
    x = ls.sound(15.0, 12.0)
    assert x.shape == (2, 12 * SR) and x[0, 0] == 1.0 and x[0, -1] == 2.0, "pieces joined, from the right place"
    os.remove(pieces[2]["path"])
    x = ls.sound(15.0, 20.0)
    assert x.shape == (2, 5 * SR) and set(np.unique(x)) == {1.0}, "a piece already let go: only what's before it"
    assert ls.sound(100.0, 12.0).shape[1] == 0
    ls.video = "saved.mp4"
    seen = []
    ls._read = lambda path, t=0.0, n=0.0: seen.append((path, t, n)) or np.zeros((2, 1), np.float32)
    ls.sound(15.0, 12.0)
    assert seen == [("saved.mp4", 15.0, 12.0)], "once the stream is saved, read from it"


def test_names_go_to_the_stretch_they_overlap_most():
    spans = [{"start": 0.0, "end": 30.0}, {"start": 50.0, "end": 90.0}, {"start": 100.0, "end": 120.0}]
    res = [{"start": 1.0, "end": 29.0, "song": {"title": "A", "artist": "", "url": "u"}},
           {"start": 48.0, "end": 70.0, "song": {"title": "B", "artist": "", "url": ""}},
           {"start": 100.0, "end": 120.0, "song": None}]
    assert livesongs.name(spans, res) == 2
    assert spans[0]["song"]["title"] == "A" and spans[1]["song"]["title"] == "B" and "song" not in spans[2]


# ---------------------------------------------------------------- with a live session and its saved stream

class Scanner:
    def __init__(self, m):
        self.m = m

    def result(self, n=None):
        n = len(self.m) if n is None else n
        return {"music": self.m[:n], "speech": np.zeros(n, np.float32), "laughter": np.zeros(n, np.float32)}


def session(**kw) -> live.LiveSession:
    s = live.LiveSession(os.urandom(4).hex(), "twitch.tv/songtester", WORK, temp_root=tempfile.mkdtemp(prefix="vc_tmp_"),
                         events=False, save_vod=True, **kw)
    s.buffer_s = 0.5
    return s


def piece(s: live.LiveSession, secs: float = 4.0) -> str:
    path = os.path.join(s.seg_dir, f"seg_{s._seg_number:06d}.ts")
    s._seg_number += 1
    engine.run([engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(secs),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", path], timeout=60)
    s._add_segment(path, secs)
    s._cleanup()
    return path


def saved_scan(s: live.LiveSession):
    wd = engine.local_workdir(s.workroot, s.vod["path"])
    pr = {"video": s.vod["path"], "workdir": wd}
    return vodexport.load_scan(pr), wd


def test_the_saved_stream_keeps_the_music_heard_while_recording():
    s = session()
    assert s.songs is None and s.public()["songs"] == {"on": False, "named": 0, "pending": 0}
    for _ in range(6):
        piece(s)
    s.events = Scanner(music(len(s.loud), [(4, 18)]))
    s.status = "ended"
    s._finish_recording()
    assert s.vod["state"] == "saved", s.vod
    scan, _ = saved_scan(s)
    assert scan and scan["how"] == livesongs.HOW, "Export VOD reads it back as this video's scan: nothing to listen to"
    assert len(scan["spans"]) == 1 and scan["spans"][0]["start"] <= 4 and scan["spans"][0]["end"] >= 18, scan["spans"]
    assert len(scan["curve"]) == len(s.loud) and not scan["named"], "the songs weren't recognised: Export VOD offers to"


def test_songs_recognised_while_recording_are_in_the_scan():
    s = session()
    gate = threading.Event()
    f = Fakes(gate=gate)
    s.songs = songs_with(f, pieces=lambda: s.segments, read=songref.read)
    m = music(60, [(2, 14), (34, 46)])
    for _ in range(12):
        piece(s)                                    # 48 s
        s.events = Scanner(m)
        s._songs_tick()
    assert s.public()["songs"]["on"] is True
    gate.set()
    assert s.songs.wait(30) and len(f.asked) == 1, "the first stretch, over while recording, from the pieces"
    assert f.asked[0][3] == (2, 12 * SR), "its sound read from the recorded pieces"
    assert s.public()["songs"] == {"on": True, "named": 1, "pending": 0}
    s.status = "ended"
    s._safe_detect(final=True)                     # the second one was still playing when it ended
    s._finish_recording()
    assert s.vod["state"] == "saved", s.vod
    assert s.songs.wait(30)
    deadline = 50
    scan = None
    for _ in range(deadline):
        scan, wd = saved_scan(s)
        if scan and scan.get("named"):
            break
        threading.Event().wait(0.2)
    assert scan["named"], "every stretch tried: Export VOD doesn't offer to recognise them again"
    assert [x.get("song", {}).get("title") for x in scan["spans"]] == ["Song", "Song"], scan["spans"]
    assert len(f.asked) == 2 and f.found == ["Song"]
    view = vodexport._scan_view(scan)
    assert all(x["suggest"] == "voice" for x in view["spans"]), "a song with its recording: erased by default"
    assert not os.path.exists(s.seg_dir), "the pieces still go once it's saved"


def test_a_scan_made_in_export_vod_meanwhile_stands():
    gate = threading.Event()
    f = Fakes(gate=gate)
    s = session()
    s.songs = songs_with(f, pieces=lambda: s.segments)
    for _ in range(6):
        piece(s)
    s.events = Scanner(music(len(s.loud), [(4, 18)]))
    s.status = "ended"
    s._safe_detect(final=True)
    s._finish_recording()
    scan, wd = saved_scan(s)
    assert scan["how"] == livesongs.HOW
    newer = dict(scan, how="heard now", spans=[])
    vodexport._write_json(os.path.join(wd, "copyright.json"), dict(newer, video=vodexport._sig({"video": s.vod["path"]})))
    gate.set()
    assert s.songs.wait(30)
    threading.Event().wait(0.5)
    with open(os.path.join(wd, "copyright.json"), encoding="utf-8") as fh:
        assert json.load(fh)["how"] == "heard now", "the merge doesn't overwrite it"


def test_the_setting_reaches_the_session():
    import server
    seen = {}
    real = live.LiveSession

    class Probe(real):
        def __init__(self, *a, **k):
            seen.update(k)
            raise RuntimeError("stop here")
    live.LiveSession = Probe
    try:
        for body, want in (({}, True), ({"prep_songs": False}, False)):
            try:
                server.start_live_session("twitch.tv/songtester", dict(body, save_vod=False))
            except RuntimeError:
                pass
            assert seen.get("prep_songs") is want, (body, seen)
    finally:
        live.LiveSession = real
    s = session(prep_songs=True)
    assert s.songs is None, "no sound model, no music to recognise (events off here)"


if __name__ == "__main__":
    failures = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[ok] {name}", flush=True)
            except Exception as e:
                failures.append(name)
                print(f"[FAIL] {name}: {type(e).__name__}: {e}", flush=True)
    print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)
