"""
Storage: what Clip Radar has on disk, and cleaning it up (moved from engine.py, hardening phase 4a; no changes).

Only folders Clip Radar made (they contain meta.json) are ever touched, and a local video you pointed Clip Radar
at is never deleted; it lives outside these folders.

  storage_summary(workroot)            how many VODs and clips, and how much room they take
  delete_vods(workroot), delete_clips(workroot)
  recent_projects(workroot, limit)     for Home's recent list

engine.py re-exports these names, so engine.storage_summary and the rest still work. A test that replaces one
replaces it here (storage._remove = fake), where the code that calls it looks it up.
"""

from __future__ import annotations

import json
import os
import shutil
import time


def _project_dirs(workroot: str) -> list:
    if not os.path.isdir(workroot):
        return []
    out = []
    for d in os.listdir(workroot):
        p = os.path.join(workroot, d)
        if os.path.isdir(p) and not os.path.islink(p) and os.path.isfile(os.path.join(p, "meta.json")):
            out.append(p)
    return out


def _vod_files(pdir: str) -> list:
    """Downloaded video in a project folder (or the Downloads location): the VOD (incl. half-finished
    downloads), or for a live session its recording buffer and the cut-out moments."""
    import locations
    out = []
    dl = locations.download_dir(pdir)
    for d in dict.fromkeys([pdir, dl]):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p) and (f.startswith("vod.") or f.startswith("chat_raw")):
                out.append(p)
    for sub in ("segments", "moments"):
        d = os.path.join(pdir, sub)
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
    return out


def _has_vod(pdir: str) -> bool:
    from engine import VIDEO_EXTS
    return any(os.path.splitext(f)[1].lower() in VIDEO_EXTS | {".ts"} for f in _vod_files(pdir))


def _clip_files(pdir: str) -> list:
    """Rendered clips for a project: its "tiktok" folder and, when set, the Clips location."""
    import locations
    out = []
    for tdir in dict.fromkeys([os.path.join(pdir, "tiktok"), locations.clips_dir(pdir)]):
        if os.path.isdir(tdir):
            out += [os.path.join(tdir, f) for f in os.listdir(tdir)
                    if os.path.isfile(os.path.join(tdir, f)) and f.lower().endswith((".mp4", ".mov", ".webm", ".srt", ".ass", ".wav", ".txt"))]
    return out


def _size(files: list) -> int:
    total = 0
    for f in files:
        try:
            total += os.path.getsize(f)
        except OSError:
            pass
    return total


def _dir_size(d: str) -> int:
    total = 0
    for root, _, files in os.walk(d):
        total += _size([os.path.join(root, f) for f in files])
    return total


def storage_summary(workroot: str) -> dict:
    vods, clips = [], []
    other, vod_count = 0, 0
    for pdir in _project_dirs(workroot):
        v = [f for f in _vod_files(pdir) if not f.endswith(".json")]
        vods += v
        vod_count += 1 if _has_vod(pdir) else 0
        clips += [f for f in _clip_files(pdir) if f.lower().endswith(".mp4")]
        other += _dir_size(os.path.join(pdir, "thumbs"))
    return {
        "vods": {"count": vod_count, "bytes": _size(vods)},
        "clips": {"count": len(clips), "bytes": _size(clips)},
        "other_bytes": other,
        "workroot": workroot,
    }


def _remove(paths_: list) -> tuple:
    freed, failed = 0, []
    for p in paths_:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        for attempt in range(3):  # Windows can briefly lock a file that was just being read
            try:
                os.remove(p)
                freed += size
                break
            except FileNotFoundError:
                break
            except OSError as e:
                if attempt == 2:
                    failed.append(os.path.basename(p))
                    # name who has it open (Windows), so a file that stays busy can be traced
                    import filelocks
                    who = filelocks.who_has(p)
                    print(f"  Couldn't delete {os.path.basename(p)}: {e.strerror or e}"
                          + (f"; open in {', '.join(who)}" if who else ""), flush=True)
                else:
                    time.sleep(0.4)
    return freed, failed


def _prune(pdir: str):
    """Remove a project folder once it holds neither a VOD nor any clips."""
    if not _has_vod(pdir) and not _clip_files(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
    else:
        import locations
        for tdir in dict.fromkeys([os.path.join(pdir, "tiktok"), locations.clips_dir(pdir), locations.download_dir(pdir)]):
            if tdir != pdir and os.path.isdir(tdir) and not os.listdir(tdir):
                try:
                    os.rmdir(tdir)
                except OSError:
                    pass


def delete_vods(workroot: str) -> dict:
    """Delete downloaded VODs (+ their chat and thumbnails). Rendered clips are kept."""
    files, count = [], 0
    for pdir in _project_dirs(workroot):
        v = _vod_files(pdir)
        count += 1 if _has_vod(pdir) else 0
        files += v
        for name in ("chat.json", "analysis.npz"):
            if os.path.isfile(os.path.join(pdir, name)):
                files.append(os.path.join(pdir, name))
        tdir = os.path.join(pdir, "thumbs")
        if os.path.isdir(tdir):
            files += [os.path.join(tdir, f) for f in os.listdir(tdir)]
    freed, failed = _remove(files)
    for pdir in _project_dirs(workroot):
        for sub in ("thumbs", "segments", "moments"):
            d = os.path.join(pdir, sub)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        _prune(pdir)
    return {"deleted": count, "freed": freed, "failed": failed}


def delete_clips(workroot: str) -> dict:
    """Delete every rendered TikTok clip. Downloaded VODs are kept."""
    files = []
    for pdir in _project_dirs(workroot):
        files += _clip_files(pdir)
    count = len([f for f in files if f.lower().endswith(".mp4")])
    freed, failed = _remove(files)
    for pdir in _project_dirs(workroot):
        _prune(pdir)
    return {"deleted": count, "freed": freed, "failed": failed}


def recent_projects(workroot: str, limit: int = 8) -> list:
    from engine import project_video
    out = []
    if not os.path.isdir(workroot):
        return out
    for d in os.listdir(workroot):
        meta_p = os.path.join(workroot, d, "meta.json")
        if not os.path.exists(meta_p):
            continue
        try:
            m = json.load(open(meta_p, encoding="utf-8"))
        except Exception:
            continue
        src = m.get("source", "")
        if m.get("live"):
            continue
        if not src or (m.get("local") and not os.path.exists(src)):
            continue
        if not m.get("local") and not project_video(os.path.join(workroot, d)):
            continue
        out.append({"title": m.get("title", d), "source": src, "updated": m.get("updated", 0),
                    "duration": m.get("duration", 0), "review": m.get("review")})
    out.sort(key=lambda x: -x["updated"])
    return out[:limit]
