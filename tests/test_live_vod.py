"""
Save the whole stream (live clipping): every recorded piece is kept instead of the rolling buffer, and when the
session ends they're joined into one MP4 (stream copy) in a project folder of its own, which Export VOD lists and
opens like a downloaded VOD. The raw pieces go after. A nearly full disk stops the keeping (what was kept is still
saved), a failed join keeps the pieces, and the server checks for room before it starts.

  python tests/test_live_vod.py      (or: pytest tests/test_live_vod.py)
"""

from __future__ import annotations

import collections
import hashlib
import os
import sys
import tempfile
import types

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
import paths  # noqa: E402
import vodexport  # noqa: E402

WORK = str(paths.WORKROOT)
Usage = collections.namedtuple("Usage", "total used free")


def session(save_vod: bool, **kw) -> live.LiveSession:
    s = live.LiveSession(os.urandom(4).hex(), "twitch.tv/vodtester", WORK, temp_root=tempfile.mkdtemp(prefix="vc_tmp_"),
                         events=False, save_vod=save_vod, **kw)
    s.buffer_s = 0.5                 # trim as hard as possible: anything kept is kept on purpose
    return s


def piece(s: live.LiveSession, secs: float = 2.0) -> str:
    """A real MPEG-TS piece, recorded the way the HLS reader writes them (seg_000000.ts, ...)."""
    path = os.path.join(s.seg_dir, f"seg_{s._seg_number:06d}.ts")
    s._seg_number += 1
    engine.run([engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(secs),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", path], timeout=60)
    s._add_segment(path, secs)
    s._cleanup()
    return path


def fake_server(projects: dict):
    return types.SimpleNamespace(
        WORKROOT=WORK, projects=projects, _load_transcripts=lambda pr: None,
        _pid_for=lambda wd: hashlib.sha1(os.path.normcase(os.path.realpath(wd)).encode()).hexdigest()[:8])


def test_keeps_every_piece_and_saves_one_video():
    s = session(True)
    ps = [piece(s) for _ in range(3)]
    assert all(os.path.exists(p) for p in ps), "every piece is kept while the whole stream is saved"
    assert s.public()["vod"]["state"] == "keeping" and s.public()["save_vod"] is True
    s.status = "ended"
    s._finish_recording()
    v = s.vod
    assert v["state"] == "saved", v
    assert os.path.basename(v["path"]) == "vod.mp4" and os.path.isfile(v["path"])
    assert abs(engine.probe(v["path"])["duration"] - 6.0) < 0.6
    assert not os.path.exists(s.seg_dir), "the raw pieces are deleted once the video is saved"
    wd = os.path.dirname(v["path"])            # no Downloads location set: the video sits in its project folder
    meta = engine._meta(wd)
    assert meta.get("local") and meta.get("source") == v["path"] and not meta.get("live") and meta.get("recorded_live")
    assert meta["title"].startswith("vodtester stream ")
    # opening the file again (Home, recent) lands in the same project, not a new one
    assert os.path.normcase(engine.local_workdir(WORK, v["path"])) == os.path.normcase(wd)
    # Export VOD lists it and opens it like any downloaded VOD
    S = fake_server({})
    row = next((r for r in vodexport.library(S) if r["title"] == meta["title"]), None)
    assert row, vodexport.library(S)
    pid = vodexport.open_video(S, pid=row["pid"])
    assert vodexport.usable(S.projects[pid]) and S.projects[pid]["video"] == v["path"]


def test_rolling_buffer_without_the_setting():
    s = session(False)
    ps = [piece(s) for _ in range(3)]
    assert not os.path.exists(ps[0]), "only the last few minutes are kept, as before"
    s.status = "stopped"
    s._finish_recording()
    assert s.vod["state"] == "off" and not os.path.exists(s.seg_dir)


def test_disk_nearly_full_stops_keeping_but_saves_what_was_kept():
    s = session(True)
    real = live.shutil.disk_usage
    piece(s)
    piece(s)
    live.shutil.disk_usage = lambda p: Usage(10**12, 10**12 - 10**8, 10**8)     # 100 MB left
    try:
        late = [piece(s), piece(s), piece(s)]           # the newest one is always inside the buffer
    finally:
        live.shutil.disk_usage = real
    # the check runs after each piece arrives, so the piece that found the disk full is still kept
    assert s.vod["state"] == "stopped" and s.vod["upto"] == 6.0, s.vod
    assert os.path.exists(late[0]) and not os.path.exists(late[1]), "later pieces go back to the rolling buffer"
    assert any("disk is nearly full" in ln for ln in s.log_lines)
    s.status = "ended"
    s._finish_recording()
    assert s.vod["state"] == "saved" and abs(engine.probe(s.vod["path"])["duration"] - 6.0) < 0.6


def test_failed_join_keeps_the_pieces():
    s = session(True)
    ps = [piece(s), piece(s)]
    real = engine.run

    def broken(cmd, **kw):
        if "concat" in cmd:
            raise RuntimeError("disk error")
        return real(cmd, **kw)
    engine.run = broken
    try:
        s.status = "error"
        s._finish_recording()
    finally:
        engine.run = real
    assert s.vod["state"] == "failed" and "disk error" in s.vod["note"] and s.seg_dir in s.vod["note"]
    assert all(os.path.exists(p) for p in ps), "the stream isn't lost when joining fails"
    assert not any(f.startswith("vod") for _r, _d, fs in os.walk(WORK) for f in fs if "saving" in f)


def test_server_checks_for_room_first():
    import server
    assert server.live_vod_setting({"save_vod": False}) == (False, "")
    real = server._check_space
    server._check_space = lambda d, secs: (_ for _ in ()).throw(OSError(28, "No space left on device (900 MB free, about 5600 MB needed)"))
    try:
        on, note = server.live_vod_setting({})
    finally:
        server._check_space = real
    assert on is False and note.startswith("Not saving the full stream: No space left on device (900 MB free")
    s = session(on, save_vod_note=note)
    assert s.public()["vod"] == dict(s.vod) and s.vod["state"] == "off" and s.vod["note"] == note


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
