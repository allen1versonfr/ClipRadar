"""
Unit tests for automatic cleanup: a VOD only goes when you're finished with it (everything reviewed,
every kept clip rendered, nothing failed that a retry would need, not open, not recently used); its
rendered clips, review and thumbnails always stay; VOD cleanup is off until you turn it on; recent
temporary files stay; skipped clips only lose files that rendered on their own; and a file that
can't be removed is reported, not a crash.

  python tests/test_cleanup.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = WORK = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import cleanup  # noqa: E402
import locations  # noqa: E402

failures = []
OLD = time.time() - 30 * 86400


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def project(name, states, live=False, age=OLD):
    d = os.path.join(WORK, name)
    os.makedirs(os.path.join(d, "tiktok"), exist_ok=True)
    os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
    open(os.path.join(d, "vod.mp4"), "wb").write(b"x" * 5000)
    open(os.path.join(d, "analysis.npz"), "wb").write(b"a")
    open(os.path.join(d, "tiktok", "Kept clip #Tag.mp4"), "wb").write(b"clip")
    open(os.path.join(d, "thumbs", "1.jpg"), "wb").write(b"t")
    json.dump({"title": name, "live": live}, open(os.path.join(d, "meta.json"), "w"))
    json.dump({"clips": [{"id": i + 1, "state": st} for i, st in enumerate(states)]}, open(os.path.join(d, "project.json"), "w"))
    for f in ("meta.json", "project.json"):
        os.utime(os.path.join(d, f), (age, age))
    return d


def done(d, cid, status="done"):
    return {"workdir": d, "clip_id": cid, "status": status, "video": os.path.join(d, "vod.mp4")}


# ---- why a VOD stays
a = project("finished", ["kept", "skipped", "skipped"])
q = [done(a, 1)]
check("finished with: may go", cleanup.vod_verdict(a, q, set(), 7) == "")
b = project("reviewing", ["kept", "todo"])
check("clips left to review: stays", cleanup.vod_verdict(b, [done(b, 1)], set(), 7) == "clips left to review")
c = project("failed", ["kept", "skipped"])
check("a failed render (a retry needs the source): stays", cleanup.vod_verdict(c, [done(c, 1, "error")], set(), 7).startswith("renders"))
check("a render still waiting: stays", cleanup.vod_verdict(c, [done(c, 1, "queued")], set(), 7).startswith("renders"))
e = project("unrendered", ["kept", "kept"])
check("a kept clip never rendered: stays", cleanup.vod_verdict(e, [done(e, 1)], set(), 7) == "kept clips not rendered")
f = project("recent", ["skipped"], age=time.time() - 3600)
check("opened recently: stays", cleanup.vod_verdict(f, [], set(), 7).startswith("opened in the last"))
g = project("open", ["skipped"])
check("open right now: stays", cleanup.vod_verdict(g, [], {cleanup._norm(g)}, 7) == "open")
h = project("live", ["skipped"], live=True)
check("a live recording: stays", cleanup.vod_verdict(h, [], set(), 7) == "live recording")
i = project("untouched", [])
check("never reviewed: stays", cleanup.vod_verdict(i, [], set(), 7) == "not reviewed")

# ---- off by default
check("VOD cleanup is off by default", cleanup.load()["vods"] is False)
rep = cleanup.sweep([WORK], q, set())
check("so nothing is deleted", os.path.exists(os.path.join(a, "vod.mp4")) and not rep["removed"], rep)

# ---- turned on: only the finished VOD goes, and only its video
cleanup.save({"vods": True, "vod_days": 7})
rep = cleanup.sweep([WORK], q + [done(c, 1, "error"), done(b, 1), done(e, 1)], {cleanup._norm(g)})
check("turned on: the finished VOD is deleted", not os.path.exists(os.path.join(a, "vod.mp4")) and rep["removed"] == ["finished"], rep)
check("its rendered clip stays", os.path.exists(os.path.join(a, "tiktok", "Kept clip #Tag.mp4")))
check("its review and thumbnails stay", os.path.exists(os.path.join(a, "project.json")) and os.path.exists(os.path.join(a, "thumbs", "1.jpg")))
check("every other VOD stays", all(os.path.exists(os.path.join(x, "vod.mp4")) for x in (b, c, e, f, g, h, i)))
check("and the report says why each stayed", {k["project"] for k in rep["kept"]} >= {"reviewing", "failed", "unrendered", "recent", "open", "live"}, rep["kept"])
check("the space freed is counted", rep["freed"] >= 5000, rep["freed"])

# ---- a file that can't be removed: reported, no crash
if os.name != "nt" and os.geteuid() != 0:
    j = project("locked", ["skipped"])
    os.chmod(j, 0o555)
    rep = cleanup.sweep([WORK], [], set())
    os.chmod(j, 0o755)
    check("a file that can't be removed is reported", rep["failed"], rep)
else:
    import engine
    real = engine._remove
    engine._remove = lambda files: (0, [os.path.basename(x) for x in files])
    j = project("locked", ["skipped"])
    rep = cleanup.sweep([WORK], [], set())
    engine._remove = real
    check("a file that can't be removed is reported", rep["failed"] and os.path.exists(os.path.join(j, "vod.mp4")), rep)
import engine  # noqa: E402
real_dirs = engine._project_dirs
engine._project_dirs = lambda root: (_ for _ in ()).throw(OSError("drive unplugged"))
rep = cleanup.sweep([WORK], [], set())
engine._project_dirs = real_dirs
check("an unexpected problem stops cleanup with a report, not a crash", any("drive unplugged" in str(x) for x in rep["failed"]), rep)

# ---- temporary files: recent ones stay
tmp = locations.path("temp")
os.makedirs(os.path.join(tmp, "old_preview"), exist_ok=True)
open(os.path.join(tmp, "old_preview", "p.mp4"), "wb").write(b"p" * 100)
os.utime(os.path.join(tmp, "old_preview", "p.mp4"), (OLD, OLD))
os.utime(os.path.join(tmp, "old_preview"), (OLD, OLD))
os.makedirs(os.path.join(tmp, "fresh_framing"), exist_ok=True)
open(os.path.join(tmp, "fresh_framing", "f.npz"), "wb").write(b"f")
cleanup.sweep([WORK], [], set())
check("old temporary files are cleared", not os.path.exists(os.path.join(tmp, "old_preview")))
check("recent ones (still useful) stay", os.path.exists(os.path.join(tmp, "fresh_framing", "f.npz")))

# ---- stalled downloads: only Clip Radar's own, even when Projects is a whole drive (D:\)
drive = tempfile.mkdtemp(prefix="vc_drive_")
mine_p = os.path.join(drive, "vod_123")
other_p = os.path.join(drive, "Someone else's downloads")
for d in (mine_p, other_p):
    os.makedirs(d)
json.dump({"title": "vod_123"}, open(os.path.join(mine_p, "meta.json"), "w"))
stale = [os.path.join(mine_p, "vod.mp4.part"), os.path.join(mine_p, "vod.f1080p60.mp4.part-Frag12"),
         os.path.join(other_p, "movie.mkv.part"), os.path.join(other_p, "vod.mp4.part"), os.path.join(mine_p, "notes.part")]
for p in stale:
    open(p, "wb").write(b"p")
    os.utime(p, (OLD, OLD))
found = {os.path.relpath(p, drive) for p in locations.stale_partials([drive])}
check("stalled downloads in a project are found", found == {os.path.join("vod_123", "vod.mp4.part"),
                                                            os.path.join("vod_123", "vod.f1080p60.mp4.part-Frag12")}, found)
real_listdir = os.listdir
os.listdir = lambda p: (_ for _ in ()).throw(PermissionError(5, "Access is denied", p)) if p == other_p else real_listdir(p)
try:
    ok = locations.stale_partials([drive])
except OSError as e:
    ok = e
os.listdir = real_listdir
check("an unreadable folder (System Volume Information) is skipped, not the end of cleanup", isinstance(ok, list) and len(ok) == 2, ok)

# ---- skipped clips
out = tempfile.mkdtemp()
def rendered(name, auto):
    p = os.path.join(out, name)
    open(p, "wb").write(b"v")
    open(os.path.splitext(p)[0] + ".srt", "w").write("1")
    return {"path": p, "status": "done", "auto": auto}
mine = rendered("mine.mp4", False)
hands = rendered("hands.mp4", True)
check("skip cleanup is off by default", cleanup.skipped_render(hands) is False and os.path.exists(hands["path"]))
cleanup.save({"skipped": True})
check("a clip you rendered yourself is never removed on skip", cleanup.skipped_render(mine) is False and os.path.exists(mine["path"]))
check("a hands-free render you skipped is removed, with its .srt", cleanup.skipped_render(hands) is True
      and not os.path.exists(hands["path"]) and not os.path.exists(os.path.join(out, "hands.srt")))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
