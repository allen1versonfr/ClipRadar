"""
TikTok results (4.51): how your posted clips actually did, from TikTok Studio's own export, and ranking that learns
from it. From the first user report: drama between streamers got most of the views and shares, while solo moments
stalled at 5-10K, but ranking only knew what you kept, skipped and posted, not what people watched.

  1. In TikTok Studio, Analytics › Content › Export data (a .csv or .xlsx file).
  2. Settings › TikTok results › Import. The file is read here; nothing is sent anywhere.
  3. Each video is matched to the clip it came from: by its TikTok link when you pasted it in Exports, otherwise by
     its caption and title, and when it was posted.
  4. Ranking: a posted clip already counts as a "keep" for its streamer (learning.py). A clip that did much better
     than that streamer's usual counts several times as much; one that flopped counts as a skip.
  5. The panel shows views by streamer, by series, for arguments against the rest, and every video matched.

TikTok's export changes its column names now and then, and comes in several languages, so columns are found by
what they're called in any of those (views/plays, likes, comments, shares, title/caption/description, link, time).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import statistics
import threading
import time
import zipfile
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

import paths
import persist

_lock = threading.RLock()
HIT = 2.0                  # views over this many times the streamer's median: a hit
FLOP = 0.4                 # under this many times: a flop (counts as a skip)
MIN_FOR_MEDIAN = 3         # clips of a streamer before its median means anything

COLUMNS = {
    "title": ("video title", "title", "caption", "description", "video description", "post", "video", "título",
              "titre", "titel", "descripción"),
    "link": ("video link", "link", "url", "video url", "share link", "enlace", "lien"),
    "id": ("video id", "item id", "id"),
    "posted": ("post time", "posted", "posted on", "date posted", "publish time", "create time", "created", "date",
               "time", "fecha", "datum"),
    "views": ("total views", "video views", "views", "plays", "total plays", "visualizaciones", "vues", "aufrufe"),
    "likes": ("total likes", "likes", "me gusta", "j'aime"),
    "comments": ("total comments", "comments", "comentarios", "commentaires", "kommentare"),
    "shares": ("total shares", "shares", "compartidos", "partages", "geteilt"),
}


def _path() -> str:
    return os.path.join(str(paths.DATA_DIR), "results.json")


def load() -> dict:
    with _lock:
        d = persist.read_json(_path(), {})
        return d if isinstance(d, dict) and isinstance(d.get("videos"), list) else {"videos": [], "imported": 0}


def _save(d: dict):
    persist.write_json(_path(), d)


def forget() -> dict:
    with _lock:
        _save({"videos": [], "imported": 0})
    return view()


# ---------------------------------------------------------------- reading the export

def _rows_csv(data: bytes) -> list:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _rows_xlsx(data: bytes) -> list:
    """The first sheet of an Excel file, as rows of text (no Excel library needed)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise ValueError("That Excel file has no sheet in it.")
        root = ET.fromstring(z.read(sheets[0]))
    rows = []
    for r in root.iter(f"{{{ns['m']}}}row"):
        cells: dict = {}
        for c in r.findall("m:c", ns):
            t, v = c.get("t"), c.find("m:v", ns)
            if t == "s" and v is not None:
                val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None and v.text is not None else ""
            cells[_col(c.get("r", "A1"))] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return [r for r in rows if any(str(c).strip() for c in r)]


def _num(v) -> Optional[float]:
    """"12,345", "1.2K", "3M", "4.5%" and plain numbers."""
    s = str(v or "").strip().replace(" ", "").replace(" ", "")
    if not s or s in ("-", "--"):
        return None
    m = re.fullmatch(r"([\d.,]+)([KkMmBb]?)%?", s)
    if not m:
        return None
    n = m.group(1)
    if "," in n and "." in n:
        n = n.replace(",", "")
    elif "," in n:
        n = n.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", n) else n.replace(",", ".")
    try:
        x = float(n)
    except ValueError:
        return None
    return x * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2).lower(), 1)


def _time(v) -> float:
    s = str(v or "").strip()
    if not s:
        return 0.0
    x = _num(s)
    if x is not None and 20000 < x < 80000:              # an Excel date
        return (x - 25569) * 86400
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace("/", "-")).timestamp()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _video_id(s: str) -> str:
    m = re.search(r"/video/(\d{8,})", str(s or "")) or re.fullmatch(r"\s*(\d{12,})\s*", str(s or ""))
    return m.group(1) if m else ""


def parse(data: bytes, name: str = "") -> list:
    """The videos in a TikTok Studio export: [{title, link, id, posted, views, likes, comments, shares}]."""
    is_xlsx = data[:2] == b"PK" or name.lower().endswith((".xlsx", ".xlsm"))
    rows = _rows_xlsx(data) if is_xlsx else _rows_csv(data)
    # the header is the first row that names a views column (Studio puts a title line above it in some exports)
    for hi, row in enumerate(rows[:10]):
        heads = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in row]
        if any(h in COLUMNS["views"] for h in heads):
            break
    else:
        found = ", ".join(str(c) for c in (rows[0] if rows else [])[:12]) or "nothing"
        raise ValueError(f"Couldn't find a views column in that file (its first row has: {found}). Export "
                         "Analytics › Content from TikTok Studio, then import that file.")
    where = {}
    for key, names in COLUMNS.items():
        for i, h in enumerate(heads):
            if h in names and i not in where.values():
                where[key] = i
                break
    out = []
    for row in rows[hi + 1:]:
        cell = lambda k: row[where[k]] if k in where and where[k] < len(row) else ""  # noqa: E731
        views = _num(cell("views"))
        if views is None:
            continue
        link = str(cell("link")).strip()
        out.append({"title": str(cell("title")).strip()[:300], "link": link[:300],
                    "id": _video_id(link) or _video_id(cell("id")), "posted": _time(cell("posted")),
                    "views": int(views), "likes": int(_num(cell("likes")) or 0),
                    "comments": int(_num(cell("comments")) or 0), "shares": int(_num(cell("shares")) or 0)})
    if not out:
        raise ValueError("That file has a views column but no videos under it.")
    return out


# ---------------------------------------------------------------- matching videos to clips

_STOP = {"the", "a", "an", "and", "of", "to", "in", "on", "is", "it", "this", "that", "for", "with", "at", "pt"}


def _words(text: str) -> set:
    text = re.sub(r"#\w+|@\w+|https?://\S+", " ", str(text or "").lower())
    return {w for w in re.findall(r"[a-z0-9']+", text) if w not in _STOP and len(w) > 1}


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def match(video: dict, items: list) -> Optional[dict]:
    """The rendered clip a TikTok video came from: the same TikTok link pasted in Exports, else the most alike
    title and caption (most of the words of the shorter one), rendered before it was posted."""
    if video.get("id"):
        for it in items:
            if _video_id(it.get("post_url", "")) == video["id"]:
                return it
    best, score = None, 0.0
    for it in items:
        if video.get("posted") and it.get("created") and it["created"] > video["posted"] + 3600:
            continue                                    # rendered after the video went up
        s = max(_similar(video["title"], it.get("post", "")), _similar(video["title"], it.get("title", "")))
        if s > score:
            best, score = it, s
    return best if score >= 0.6 else None


def _learn_ref(it: dict) -> dict:
    """The clip's learning entry (key and streamer): kept on the queue item since 4.51, else read from the project
    it came from, where the clip's signals were saved."""
    if it.get("learn"):
        return it["learn"]
    try:
        with open(os.path.join(it.get("workdir") or "", "project.json"), encoding="utf-8") as f:
            pj = json.load(f)
        d = next((c for c in pj.get("clips", []) if c.get("id") == it.get("clip_id")), None)
        if d and d.get("feat"):
            at = d.get("at", d.get("peak", 0))
            return {"key": f"{pj.get('source', '')}|{int(at)}", "streamer": pj.get("streamer", "")}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def import_file(data: bytes, name: str, items: list) -> dict:
    """Read an export, match each video to a clip, store the results (newer numbers replace older ones for the same
    video) and tell ranking. Returns the screen's view with what this import did."""
    import learning
    vids = parse(data, name)
    with _lock:
        d = load()
        have = {(v.get("id") or v.get("title")): v for v in d["videos"]}
        matched = 0
        for v in vids:
            it = match(v, items)
            if it:
                matched += 1
                v["clip"] = {"queue_id": it.get("id"), "pid": it.get("pid"), "clip_id": it.get("clip_id"),
                             "title": it.get("title", ""), "source": it.get("source_title", ""),
                             "streamer": it.get("streamer") or (it.get("name_fields") or {}).get("streamer", ""),
                             "series": (it.get("series") or {}).get("name", ""), "vibe": it.get("vibe", ""),
                             "conflict": bool(it.get("conflict")), "learn": _learn_ref(it)}
            have[v.get("id") or v.get("title")] = v
        d["videos"] = list(have.values())
        d["imported"] = time.time()
        _save(d)
        learned = teach(d["videos"], learning)
    out = view()
    out["last"] = {"videos": len(vids), "matched": matched, "learned": learned}
    return out


def _streamer(v: dict) -> str:
    c = v.get("clip") or {}
    return c.get("streamer") or (c.get("learn") or {}).get("streamer") or ""


def teach(videos: list, learning) -> int:
    """Each matched video's clip, re-weighted in ranking by how it did against its streamer's median."""
    by: dict = {}
    for v in videos:
        if v.get("clip"):
            by.setdefault(_streamer(v).lower(), []).append(v)
    n = 0
    for vs in by.values():
        if len(vs) < MIN_FOR_MEDIAN:
            continue
        med = statistics.median(v["views"] for v in vs) or 1
        for v in vs:
            ref = (v["clip"] or {}).get("learn") or {}
            if ref.get("key") and learning.performance(ref.get("streamer", ""), ref["key"], v["views"] / med):
                n += 1
    return n


# ---------------------------------------------------------------- what the screen shows

def _group(videos: list, key) -> list:
    groups: dict = {}
    for v in videos:
        k = key(v)
        if k:
            groups.setdefault(k, []).append(v)
    out = []
    for k, vs in groups.items():
        views = [v["views"] for v in vs]
        out.append({"name": k, "videos": len(vs), "views": sum(views), "median": int(statistics.median(views)),
                    "like_rate": round(sum(v["likes"] for v in vs) / max(1, sum(views)) * 100, 2),
                    "share_rate": round(sum(v["shares"] for v in vs) / max(1, sum(views)) * 100, 2),
                    "comments_per_1k": round(sum(v["comments"] for v in vs) / max(1, sum(views)) * 1000, 2)})
    return sorted(out, key=lambda g: -g["median"])


def view() -> dict:
    d = load()
    vids = d["videos"]
    m = [v for v in vids if v.get("clip")]
    return {
        "imported": d.get("imported", 0), "videos": len(vids), "matched": len(m),
        "total": _group(vids, lambda v: "All videos"),
        "by_streamer": _group(m, lambda v: _streamer(v) or "Unknown"),
        "by_series": _group(m, lambda v: (v["clip"] or {}).get("series") or "Not in a series"),
        "by_kind": _group(m, lambda v: "Arguments and drama" if (v["clip"] or {}).get("conflict") else "Everything else"),
        "list": sorted(({"title": v["title"][:120], "views": v["views"], "likes": v["likes"], "comments": v["comments"],
                         "shares": v["shares"], "posted": v.get("posted", 0), "link": v.get("link", ""),
                         "clip": (v.get("clip") or {}).get("title", ""), "streamer": _streamer(v)} for v in vids),
                       key=lambda x: -x["views"])[:200],
    }
