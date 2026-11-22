"""
Segments: parts of a video picked on the Export VOD screen, edited together and exported as one video.

  - An edit belongs to one video and holds its segments in the order they'll play. Each segment has a name,
    a start and an end, the stretches cut out of it, a volume, whether it's in the export (ticked), and,
    once you've changed them, its own caption lines.
  - Edits are kept in segments.json in Clip Radar's data folder, so an unfinished one is there next time.
    Every save carries the version it was made from: an edit changed somewhere else since (a segment sent
    from Export VOD while the Segments screen had it open) isn't overwritten, the screen loads it again.
  - The export: each segment is a video of its own (5.0.1), or the ticked ones can be joined into one. Each
    stretch that's kept is made the way Export VOD makes a video (music erased or muted as set there, flagged
    words muted, captions burned in), then a segment's stretches are joined without being made again. No
    length limit (5.0: it was capped at 30 minutes).
  - Saved in Settings › Storage › Segment exports, a folder of its own, not with the clips (5.0).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
import uuid
from typing import Optional

import engine

import render
import whisper
import exports
import locations
import paths
import vodexport
import render_queue

MIN_LEN = 0.5            # a segment (or what's left of it) shorter than this is nothing
NAME_MAX = 80
MAX_SEGMENTS = 200
GAIN = (-12.0, 12.0)     # dB, as render.audio_filter allows

_lock = threading.Lock()
_edits: Optional[list] = None
_jobs: dict = {}          # edit id -> {"export": job id, "hear": job id}


def _file() -> str:
    return str(paths.DATA_DIR / "segments.json")


def _load() -> list:
    global _edits
    if _edits is None:
        try:
            with open(_file(), encoding="utf-8") as f:
                data = json.load(f)
            _edits = [e for e in data.get("edits", []) if isinstance(e, dict) and e.get("id")]
        except (OSError, ValueError, AttributeError):
            _edits = []
    return _edits


def _save() -> None:
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"edits": _load()}, f, ensure_ascii=False)
    os.replace(tmp, _file())


def _find(eid: str) -> dict:
    e = next((e for e in _load() if e["id"] == eid), None)
    if not e:
        raise LookupError("That edit isn't there any more.")
    return e


def _num(v, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


# ---------------------------------------------------------------- what a segment keeps

def kept(seg: dict) -> list:
    """The stretches of a segment that play, in order: start to end with the cut stretches taken out."""
    out, at = [], seg["start"]
    for a, b in sorted(seg.get("cuts") or []):
        if b <= at:
            continue
        if a > at:
            out.append((at, min(a, seg["end"])))
        at = max(at, b)
        if at >= seg["end"]:
            break
    if at < seg["end"]:
        out.append((at, seg["end"]))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a >= MIN_LEN]


def seg_len(seg: dict) -> float:
    return sum(b - a for a, b in kept(seg))


def edit_len(edit: dict) -> float:
    """How long the export would be: the ticked segments, cuts left out."""
    return sum(seg_len(s) for s in edit["segments"] if s.get("on", True))


def line_words(line: dict) -> list:
    """A caption line typed by hand ({"start", "end", "text"}) as timed words, the shape the renderer burns in:
    [{"start", "end", "word"}]. The line keeps its own start and end; the words share that time out."""
    words = line["text"].split()
    if not words:
        return []
    # time shared out by length, so a long word stays up longer than "a"; the +1 gives short words a fair beat
    a, b = float(line["start"]), float(line["end"])
    weights = [len(w) + 1 for w in words]
    total, at, out = sum(weights), a, []
    for w, k in zip(words, weights):
        end = at + (b - a) * k / total
        out.append({"start": round(at, 3), "end": round(end, 3), "word": w})
        at = end
    out[-1]["end"] = round(b, 3)        # rounding never leaves the last word short of the line's end
    return out


def caption_words(seg: dict) -> Optional[list]:
    """The words for a segment's captions when its lines were edited by hand (absolute times), or None to
    use what was heard."""
    lines = seg.get("lines")
    if lines is None:
        return None
    return [w for ln in lines for w in line_words(ln)]


# ---------------------------------------------------------------- checking what the screen sent

def _clean_lines(rows, a: float, b: float) -> Optional[list]:
    if rows is None:
        return None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        x, y = max(a, _num(r.get("start"))), min(b, _num(r.get("end")))
        text = " ".join(str(r.get("text") or "").split())[:300]
        if y - x >= 0.1:
            out.append({"start": round(x, 3), "end": round(y, 3), "text": text})
    return sorted(out, key=lambda r: r["start"])


def clean_segment(s: dict, duration: float) -> Optional[dict]:
    """A segment as the screen sent it, checked: times inside the video and in order, cuts inside the segment."""
    if not isinstance(s, dict):
        return None
    dur = duration or math.inf
    a = max(0.0, min(_num(s.get("start")), dur))
    b = max(0.0, min(_num(s.get("end")), dur))
    if b - a < MIN_LEN:
        return None
    cuts = []
    for c in s.get("cuts") or []:
        try:
            x, y = max(a, _num(c[0])), min(b, _num(c[1]))
        except (TypeError, IndexError, KeyError):
            continue
        if y - x >= 0.1:
            cuts.append([round(x, 3), round(y, 3)])
    return {
        "id": str(s.get("id") or uuid.uuid4().hex[:8])[:16],
        "name": " ".join(str(s.get("name") or "").split())[:NAME_MAX],
        "start": round(a, 3), "end": round(b, 3), "cuts": sorted(cuts),
        "gain_db": max(GAIN[0], min(GAIN[1], round(_num(s.get("gain_db")), 1))),
        "on": s.get("on", True) is not False,
        "lines": _clean_lines(s.get("lines"), a, b),
    }


# ---------------------------------------------------------------- edits

def add(S, pid: str, start: float, end: float, name: str = "", edit_id: str = "") -> dict:
    """A part of a video sent from Export VOD: added to the end of an edit of that video (edit_id, or a new
    edit when it's empty). Returns the edit."""
    pr = S.projects.get(pid)
    if not vodexport.usable(pr):
        raise LookupError("That video isn't open any more. Pick it again in Export VOD.")
    dur = vodexport._duration(pr)
    seg = clean_segment({"start": start, "end": end, "name": name}, dur)
    if not seg:
        raise ValueError("Pick a part at least a second long: set where it starts and where it ends.")
    with _lock:
        if edit_id:
            e = _find(edit_id)
            if e["pid"] != pid:
                raise ValueError("That edit is of another video. An edit holds parts of one video.")
        else:
            n = sum(1 for x in _load() if x["pid"] == pid) + 1
            title = pr.get("title") or os.path.basename(pr["video"])
            e = {"id": uuid.uuid4().hex[:8], "name": f"{title} edit" + (f" {n}" if n > 1 else ""), "pid": pid,
                 "video": pr["video"], "title": title, "duration": round(dur, 1), "segments": [], "ver": 0,
                 "created": time.time()}
            _load().insert(0, e)
        if len(e["segments"]) >= MAX_SEGMENTS:
            raise ValueError(f"An edit holds at most {MAX_SEGMENTS} segments.")
        if not seg["name"]:
            seg["name"] = f"Segment {len(e['segments']) + 1}"
        e["segments"].append(seg)
        e["ver"] += 1
        e["updated"] = time.time()
        _save()
        return e


def add_many(S, pid: str, parts: list, edit_id: str = "", name: str = "") -> dict:
    """Parts found by Export VOD's Find segments (5.0.3), each a segment, added in order to a new edit named `name`
    (or to edit_id). Returns the edit."""
    rows = [p for p in parts if isinstance(p, dict)][:MAX_SEGMENTS]
    if not rows:
        raise ValueError("Tick at least one part to send.")
    e = None
    for p in rows:
        e = add(S, pid, p.get("start"), p.get("end"), str(p.get("name") or ""), edit_id or (e["id"] if e else ""))
    if name and not edit_id:
        with _lock:
            e["name"] = " ".join(str(name).split())[:NAME_MAX] or e["name"]
            e["ver"] += 1
            _save()
    return e


def save(body: dict) -> dict:
    """The edit as the Segments screen has it now: its name and segments (order, trims, cuts, volume,
    captions). ver: the version it was loaded at; if the edit has moved on since, nothing is saved (Conflict)."""
    with _lock:
        e = _find(str(body.get("id") or ""))
        if int(_num(body.get("ver"), -1)) != e["ver"]:
            raise Conflict("This edit changed in another place. It has been loaded again.")
        segs = [clean_segment(s, e.get("duration") or 0) for s in (body.get("segments") or [])[:MAX_SEGMENTS]]
        name = " ".join(str(body.get("name") or "").split())[:NAME_MAX]
        e["name"] = name or e["name"]
        e["segments"] = [s for s in segs if s]
        e["ver"] += 1
        e["updated"] = time.time()
        _save()
        return e


class Conflict(Exception):
    pass


def delete(eid: str) -> None:
    with _lock:
        e = _find(eid)
        _load().remove(e)
        _jobs.pop(eid, None)
        _save()


def ensure_open(S, e: dict) -> dict:
    """The edit's video, opened (an edit made last time points at a video that isn't open after a restart)."""
    pr = S.projects.get(e["pid"])
    if vodexport.usable(pr):
        return pr
    try:
        pid = vodexport.open_video(S, pid=e["pid"])
    except RuntimeError:
        if not os.path.isfile(e.get("video") or ""):
            raise RuntimeError("This edit's video was deleted, so it can't be played or exported.") from None
        pid = vodexport.open_video(S, path=e["video"])
    if pid != e["pid"]:
        with _lock:
            e["pid"] = pid
            _save()
    return S.projects[pid]


# ---------------------------------------------------------------- jobs

def _job(S, eid: str, key: str) -> Optional[dict]:
    return S.jobs.get((_jobs.get(eid) or {}).get(key, ""))


def _start(S, eid: str, key: str, kind: str, fn) -> dict:
    old = _job(S, eid, key)
    if old and old["status"] == "running":
        return old
    job = S.new_job(kind)
    _jobs.setdefault(eid, {})[key] = job["id"]
    S.run_job(job, lambda: fn(job))
    return job


def start_hear(S, eid: str, seg_id: str, body: dict) -> dict:
    """Listen to one segment, so its captions can be read and changed."""
    e = _find(eid)
    seg = next((s for s in e["segments"] if s["id"] == seg_id), None)
    if not seg:
        raise LookupError("That segment isn't in this edit any more.")
    pr = ensure_open(S, e)
    model, lang = str(body.get("whisper_model") or "small")[:40], str(body.get("language") or "")[:10]
    a, b = seg["start"], seg["end"]

    def go(job):
        if not whisper.whisper_available() and not (pr.get("yt") or {}).get("words"):
            raise RuntimeError("Captions need the speech model (Whisper). Get it in Settings › Downloads and tools.")
        words = vodexport.hear_all(S, pr, model, lang, a, b, S.job_progress(job, 0, 1), S.job_check(job), S.job_step(job))
        return {"start": a, "end": b, "words": len(words), "segment": seg_id}
    return _start(S, eid, "hear", "seghear", go)


def join(parts: list, out_path: str, check=lambda: None, length: float = 0.0, progress=lambda f: None,
         step=lambda m: None, warn=lambda m: None) -> None:
    """The parts, one after another, in one file. They were all made with the same settings, so they're joined
    as they are; if FFmpeg won't (an encoder fell back partway), they're made again into one. With the bar moving
    and Stop working throughout (5.4: before, both ran blind, and the re-making could sit at 100% for as long as the
    whole export took, every core busy)."""
    work = os.path.dirname(os.path.abspath(out_path))
    lst = os.path.join(work, f"seg_{uuid.uuid4().hex[:8]}.txt")
    want_audio = _has_audio(parts[0]) if parts else False
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("\\", "/").replace("'", r"'\''") + "'\n")
    base = [engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-f", "concat", "-safe", "0", "-i", lst]
    err = ""
    try:
        for i, codec in enumerate((["-c", "copy"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                                                    *render.RENDER_THREADS])):
            check()
            if i:
                step("The parts wouldn't join as they are: making them into one video again")
            try:
                vodexport._run_ffmpeg(base + codec + [*vodexport.movflags(length or 1e9), out_path], work, length or 1.0, progress, check)
            except RuntimeError as e:
                err = str(e)
                if vodexport._disk_full(e):
                    break
                continue
            # 5.7.1: joined as they are, it must also come out as long as the parts, with sound when they have it (a
            # join of parts whose timestamps don't line up can come out short, or with the sound dropped): if not, they're
            # made into one again; what that makes is kept either way, and the export says if it's still off
            bad = vodexport.check_made(out_path, length, want_audio) if length else ("" if os.path.isfile(out_path) else "nothing")
            if not bad or (i and os.path.isfile(out_path) and os.path.getsize(out_path) > 1000):
                if bad:
                    warn(f"The joined video was made, but {bad}. Watch it through before you upload it.")
                return
            err = f"Joined as they are, {bad}."
            step("The joined video came out wrong: making the parts into one video again")
        raise RuntimeError("Couldn't join the segments:\n" + err.replace("ffmpeg failed:\n", ""))
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass


def _has_audio(path: str) -> bool:
    try:
        return bool(engine.probe(path).get("has_audio"))
    except Exception:       # noqa: BLE001
        return False


def _out_name(name: str, outdir: str, taken: Optional[set] = None) -> str:
    """A file name in outdir not used yet, nor in taken (paths picked already for this export, before they're made)."""
    base = exports.safe(name or "Segments")
    out, n = f"{base}.mp4", 2
    taken = taken or set()
    while os.path.exists(os.path.join(outdir, out)) or os.path.normcase(os.path.join(outdir, out)) in taken:
        out = f"{base} ({n}).mp4"
        n += 1
    return out


AT_ONCE_PARTS = 2       # stretches made at the same time (5.7.3): one FFmpeg can't keep every core busy


class _Share:
    """The server, as seen by one stretch made alongside others (5.7.3): its bar goes into its own share of the job's,
    and Stop (or another stretch failing) stops it. Everything else is the server's own."""

    def __init__(self, S, setter, stopped):
        self._S, self._set, self._stopped = S, setter, stopped

    def __getattr__(self, k):
        return getattr(self._S, k)

    def job_progress(self, job, lo, hi):
        return lambda x: self._set(lo + (hi - lo) * max(0.0, min(1.0, x)))

    def job_check(self, job):
        real = self._S.job_check(job)

        def c():
            if self._stopped():
                raise engine.Cancelled()
            real()
        return c


def _make_all(S, job: dict, pr: dict, o: dict, files: list, tmp: str) -> list:
    """Every file of an export: files [{"path", "plan": [(segment, start, end)], "copy", "label": fn(i), "band"}].
    Each stretch is made the way Export VOD makes a video, AT_ONCE_PARTS at a time across all the files (5.7.3: one
    after the other before, the CPU half idle), and each file is joined as soon as its stretches are made, without
    making them again. A subtitle file goes next to one when asked for."""
    from concurrent.futures import ThreadPoolExecutor
    workers = AT_ONCE_PARTS if (os.cpu_count() or 1) >= 4 else 1
    parts = [(k, i) for k, f in enumerate(files) for i in range(len(f["plan"]))]
    lock = threading.Lock()
    share, done = {}, {}
    for k, f in enumerate(files):
        lo, hi = f["band"]
        total = sum(b - a for _, a, b in f["plan"]) or 1.0
        for i, (_, a, b) in enumerate(f["plan"]):
            share[(k, i)] = (hi - lo) * 0.97 * (b - a) / total
        share[(k, "join")] = (hi - lo) * 0.03
    to_bar = S.job_progress(job, 0.0, 1.0)
    stop = {"v": False}

    def moved(key, v):
        with lock:
            done[key] = max(done.get(key, 0.0), v)
            to_bar(min(0.999, sum(share[x] * done.get(x, 0.0) for x in share)))

    def one(k, i):
        f = files[k]
        s, a, b = f["plan"][i]
        path = os.path.join(tmp, f"part_{job['id']}_{uuid.uuid4().hex[:6]}_{k:03d}_{i:03d}.mp4")
        sh = _Share(S, lambda v, key=(k, i): moved(key, v), lambda: stop["v"])
        try:
            r = vodexport.render_range(sh, job, pr, dict(o, gain_db=s["gain_db"]), a, b, path, whole=True, part=True,
                                       label=f["label"](i), words=caption_words(s), copy=f["copy"], keep_models=True)
        except BaseException:
            _rm(path)
            raise
        return path, r

    out, futs, made = [], {}, []
    pool = ThreadPoolExecutor(workers)
    nxt = 0

    def ahead(upto: int):
        nonlocal nxt
        while nxt < len(parts) and nxt <= upto:
            futs[parts[nxt]] = pool.submit(one, *parts[nxt])
            nxt += 1
    try:
        at = 0
        for k, f in enumerate(files):
            got, words, ro, t = [], [], None, 0.0
            for i, (_, a, b) in enumerate(f["plan"]):
                ahead(at + workers)              # a few stretches ahead, never the whole export at once (the disk)
                fut = futs.pop((k, i))
                while True:
                    S.job_check(job)()
                    try:
                        path, r = fut.result(timeout=0.5)
                        break
                    except TimeoutError:
                        continue
                got.append(path)
                made.append(path)
                ro = r["ro"]
                words += [dict(w, start=w["start"] - a + t, end=w["end"] - a + t) for w in r["words"]]
                t += b - a
                at += 1
            if len(got) == 1:
                # the parts are made in the Segments folder itself (5.4), so this is a rename; shutil.move still copies
                # if it ever has to cross drives (5.0.2: "The system cannot move the file to a different disk drive")
                shutil.move(got[0], f["path"])
            else:
                S.job_step(job)("Joining " + os.path.basename(f["path"]))
                join(got, f["path"], S.job_check(job), t, lambda v, k=k: moved((k, "join"), v), S.job_step(job),
                     warn=lambda m: vodexport._warn(job, m))
            moved((k, "join"), 1.0)
            for p_ in got:
                _rm(p_)
            srt = ""
            if o["captions"] in ("srt", "both") and words and ro is not None:
                srt = os.path.splitext(f["path"])[0] + ".srt"
                render.write_srt(words, ro, srt, t)
            out.append({"path": f["path"], "name": os.path.basename(f["path"]), "srt": os.path.basename(srt) if srt else "",
                        "length": round(t, 1)})
    finally:
        stop["v"] = True
        pool.shutdown(wait=True, cancel_futures=True)
        for p_ in made:
            _rm(p_)
        import songerase
        import voicekeep
        songerase.release()
        voicekeep.release()
    return out


def _rm(p: str) -> None:
    try:
        os.remove(p)
    except OSError:
        pass


def run_export(S, job: dict, eid: str, body: dict) -> dict:
    """Export an edit (5.0.1): each segment as a video of its own (mode "each", the default), or only the one
    named in body["segment"], or the ticked segments joined into one video (mode "join", as before).
    Segments are saved in Settings › Storage › Segment exports: one segment's video straight there, an edit's in
    a folder named after the edit."""
    e = _find(eid)
    pr = ensure_open(S, e)
    dur = vodexport._duration(pr)
    one = str(body.get("segment") or "")
    mode = "join" if body.get("mode") == "join" and not one else "each"
    segs = [s for s in e["segments"] if (s["id"] == one if one else s.get("on", True))]
    if one and not segs:
        raise RuntimeError("That segment isn't in this edit any more.")
    groups = [(s, [(s, a, b) for a, b in kept(s)]) for s in segs]
    groups = [(s, g) for s, g in groups if g]
    if not groups:
        raise RuntimeError("Nothing to export: tick at least one segment." if not one else "This segment is all cut out.")
    o = vodexport.clean_options(body, dur)
    # 5.1.1: with nothing drawn on the picture (no burned-in captions, the video's own size) each stretch's picture is
    # copied as it is and only the sound is made again: many times faster. A copied picture starts on a keyframe, so
    # a segment starts on the keyframe just before where you set it (a second or two more, nothing lost), and after a
    # cut on the keyframe just after (a little more cut). Burned-in captions mean every frame is made again.
    copied = set()
    if vodexport.can_copy(o, engine.probe(pr["video"])):
        kf = vodexport.keyframes(pr["video"], [a for _, g in groups for _, a, _ in g])
        snapped = []
        for s, g in groups:
            new = vodexport.snap_to_keyframes([(a, b) for _, a, b in g], kf)
            if all(new):
                snapped.append((s, [(s, a, b) for a, b in new]))
                copied.add(s["id"])
            else:
                snapped.append((s, g))          # a stretch with no keyframe near: this segment is made the slow way
        groups = snapped
        if mode == "join" and len(copied) != len(groups):     # one file: all copied, or all made again
            groups, copied = [(s, [(s, a, b) for a, b in kept(s)]) for s, _ in groups], set()
    total = sum(b - a for _, g in groups for _, a, b in g)
    if body.get("cleanup", True) is not False:
        o["music"], o["word_spans"] = vodexport.suggested(pr)
        o["songs"] = vodexport.suggested_songs(pr)
    outdir = locations.segments_dir()      # Settings › Storage › Segment exports, apart from the clips (5.0)
    if mode == "each" and not one:
        outdir = os.path.join(outdir, exports.safe(e["name"] or "Segments"))   # an edit's segments kept together
    os.makedirs(outdir, exist_ok=True)
    render_queue._check_space(outdir, total * 0.6)
    # the parts are made next to where they end up (a hidden folder in it), not in the temp folder: putting the finished
    # video in place is then a rename, never a copy of gigabytes across drives with the bar at 100% (5.4)
    tmp = os.path.join(outdir, f".parts-{job['id']}")
    for old in os.listdir(outdir):          # parts left by an export Clip Radar was closed in the middle of
        d = os.path.join(outdir, old)
        try:
            if old.startswith(".parts-") and d != tmp and time.time() - os.path.getmtime(d) > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:                     # gone already, or another export's (5.7.1: that failed the export)
            pass
    os.makedirs(tmp, exist_ok=True)
    log = S.job_logger(job)
    try:
        t0, todo = time.time(), []
        if mode == "join":
            plan = [row for _, g in groups for row in g]
            todo.append({"path": os.path.join(outdir, _out_name(e["name"], outdir)), "plan": plan, "copy": bool(copied),
                         "band": (0.0, 1.0), "label": lambda i, plan=plan: f"{plan[i][0]['name'] or 'Segment'} ({i + 1} of {len(plan)}): "})
        else:
            done = 0.0
            taken: set = set()
            for k, (s, g) in enumerate(groups):
                length = sum(b - a for _, a, b in g)
                name = s["name"] or f"Segment {e['segments'].index(s) + 1}"
                path = os.path.join(outdir, _out_name(name, outdir, taken))
                taken.add(os.path.normcase(path))
                todo.append({"path": path, "plan": g, "copy": s["id"] in copied, "band": (done / total, (done + length) / total),
                             "label": lambda i, n=name, k=k: f"{n} ({k + 1} of {len(groups)}): "})
                done += length
        files = _make_all(S, job, pr, o, todo, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    took = time.time() - t0
    log(f"Saved {len(files)} video{'s' if len(files) != 1 else ''} ({engine.fmt_ts(total)} in all) in {engine.fmt_ts(took)}"
        + (", the picture copied as it is" if copied else "") + ".")
    first = files[0]
    return {"path": first["path"], "name": first["name"], "srt": first["srt"], "folder": outdir, "files": files,
            "mode": mode, "copied": len(copied), "length": round(total, 1), "parts": sum(len(g) for _, g in groups), "took": round(took, 1),
            "warning": job.get("warning", "")}


_yt: dict = {}            # an exported segment's file -> what was done with it on YouTube (5.1.3)


def to_youtube(S, eid: str, body: dict) -> dict:
    """An exported segment to YouTube (5.1.3), as Exports sends clips: Upload to YouTube (Clip Radar's own browser
    window, with Settings › YouTube's choices) or Open YouTube Studio (the pass-through). body: {file: the exported
    file's path, title, description, auto}. Only a file Segments saved is sent."""
    import ytclips
    _find(eid)
    path = os.path.abspath(str(body.get("file") or ""))
    root = os.path.abspath(locations.segments_dir())
    if not (os.path.normcase(path).startswith(os.path.normcase(root) + os.sep) and os.path.isfile(path)):
        raise RuntimeError("That segment's file isn't there any more. Export it again first.")
    it = _yt.setdefault(path, {"path": path, "title": os.path.splitext(os.path.basename(path))[0]})
    it["yt_text"] = {"title": str(body.get("title") or it["title"])[:200], "description": str(body.get("description") or "")[:5000]}
    return ytclips.start_upload(S, it) if body.get("auto") else ytclips.pass_through(it)


def youtube_states() -> dict:
    """What was done on YouTube with each exported segment file: {path: {"at", "url", "auto", "error", "left"}}."""
    return {p: it["yt"] for p, it in _yt.items() if it.get("yt")}


def start_export(S, eid: str, body: dict) -> dict:
    _find(eid)
    return _start(S, eid, "export", "segexport", lambda job: run_export(S, job, eid, body))


def view(S) -> dict:
    """The Segments screen: every edit, newest first, with the jobs running or last run on it."""
    out = []
    for e in sorted(_load(), key=lambda x: -float(x.get("updated") or x.get("created") or 0)):
        pr = S.projects.get(e["pid"])
        job = lambda key: vodexport._job_view(S, _job(S, e["id"], key))     # noqa: E731
        out.append(dict(e, length=round(edit_len(e), 1), open=vodexport.usable(pr),
                        exists=os.path.isfile(e.get("video") or ""),
                        export_job=job("export"), hear_job=job("hear")))
    return {"edits": out, "youtube": youtube_states()}
