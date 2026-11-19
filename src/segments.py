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
import subprocess
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


def join(parts: list, out_path: str, check=lambda: None) -> None:
    """The parts, one after another, in one file. They were all made with the same settings, so they're joined
    as they are; if FFmpeg won't (an encoder fell back partway), they're made again into one."""
    work = os.path.dirname(os.path.abspath(out_path))
    lst = os.path.join(work, f"seg_{uuid.uuid4().hex[:8]}.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("\\", "/").replace("'", r"'\''") + "'\n")
    base = [engine.ffmpeg(), "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst]
    try:
        for codec in (["-c", "copy"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]):
            check()
            r = subprocess.run(base + codec + ["-movflags", "+faststart", out_path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", creationflags=engine.CREATE_NO_WINDOW)
            if r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                return
        raise RuntimeError("Couldn't join the segments:\n" + render._ffmpeg_reason(r.stderr[-4000:]))
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass


def _out_name(name: str, outdir: str) -> str:
    base = exports.safe(name or "Segments")
    out, n = f"{base}.mp4", 2
    while os.path.exists(os.path.join(outdir, out)):
        out = f"{base} ({n}).mp4"
        n += 1
    return out


def _make(S, job: dict, pr: dict, o: dict, plan: list, path: str, tmp: str, band: tuple, label) -> dict:
    """The stretches in plan [(segment, start, end)], one after another, in one file at path. Each is made the way
    Export VOD makes a video, then they're joined without being made again. A subtitle file goes next to it when
    asked for. band: the share of the job's bar this takes; label(i): what each step says first."""
    total = sum(b - a for _, a, b in plan)
    lo, hi = band
    made, words, ro, at = [], [], None, 0.0
    try:
        for i, (s, a, b) in enumerate(plan):
            S.job_check(job)()
            part = os.path.join(tmp, f"part_{job['id']}_{uuid.uuid4().hex[:6]}_{i:03d}.mp4")
            done = sum(y - x for _, x, y in plan[:i])
            r = vodexport.render_range(S, job, pr, dict(o, gain_db=s["gain_db"]), a, b, part, whole=True, part=True,
                                       band=(lo + (hi - lo) * 0.97 * done / total, lo + (hi - lo) * 0.97 * (done + b - a) / total),
                                       label=label(i), words=caption_words(s))
            made.append(part)
            ro = r["ro"]
            words += [dict(w, start=w["start"] - a + at, end=w["end"] - a + at) for w in r["words"]]
            at += b - a
        if len(made) == 1:
            os.replace(made[0], path)
        else:
            S.job_step(job)("Joining " + os.path.basename(path))
            join(made, path, S.job_check(job))
    finally:
        for p in made:
            try:
                os.remove(p)
            except OSError:
                pass
    srt = ""
    if o["captions"] in ("srt", "both") and words and ro is not None:
        srt = os.path.splitext(path)[0] + ".srt"
        render.write_srt(words, ro, srt, total)
    return {"path": path, "name": os.path.basename(path), "srt": os.path.basename(srt) if srt else "", "length": round(total, 1)}


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
    total = sum(b - a for _, g in groups for _, a, b in g)
    o = vodexport.clean_options(body, dur)
    if body.get("cleanup", True) is not False:
        o["music"], o["word_spans"] = vodexport.suggested(pr)
    outdir = locations.segments_dir()      # Settings › Storage › Segment exports, apart from the clips (5.0)
    if mode == "each" and not one:
        outdir = os.path.join(outdir, exports.safe(e["name"] or "Segments"))   # an edit's segments kept together
    os.makedirs(outdir, exist_ok=True)
    render_queue._check_space(outdir, total * 0.6)
    tmp = locations.temp_dir(os.path.basename(pr["workdir"]), "segments")
    log = S.job_logger(job)
    t0, files = time.time(), []
    if mode == "join":
        plan = [row for _, g in groups for row in g]
        path = os.path.join(outdir, _out_name(e["name"], outdir))
        files.append(_make(S, job, pr, o, plan, path, tmp, (0.0, 1.0),
                           lambda i: f"{plan[i][0]['name'] or 'Segment'} ({i + 1} of {len(plan)}): "))
    else:
        done = 0.0
        for k, (s, g) in enumerate(groups):
            length = sum(b - a for _, a, b in g)
            name = s["name"] or f"Segment {e['segments'].index(s) + 1}"
            path = os.path.join(outdir, _out_name(name, outdir))
            files.append(_make(S, job, pr, o, g, path, tmp, (done / total, (done + length) / total),
                               lambda i, n=name: f"{n} ({k + 1} of {len(groups)}): "))
            done += length
    took = time.time() - t0
    log(f"Saved {len(files)} video{'s' if len(files) != 1 else ''} ({engine.fmt_ts(total)} in all) in {engine.fmt_ts(took)}.")
    first = files[0]
    return {"path": first["path"], "name": first["name"], "srt": first["srt"], "folder": outdir, "files": files,
            "mode": mode, "length": round(total, 1), "parts": sum(len(g) for _, g in groups), "took": round(took, 1),
            "warning": job.get("warning", "")}


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
    return {"edits": out}
