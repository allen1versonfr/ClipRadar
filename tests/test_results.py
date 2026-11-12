"""
TikTok results (results.py, 4.51): TikTok Studio's Content export read as CSV or Excel (columns found by name, a
title line above them, "1.2K" numbers), each video matched to the clip it came from (by link, else by caption),
ranking re-weighted by how each did against its streamer's usual, and the groups the screen shows.

  python tests/test_results.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import learning  # noqa: E402
import results  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


CSV = """Content performance Oct 12 - Nov 10
Video title,Video link,Post time,Total views,Total likes,Total comments,Total shares
"Jason × Emily Pt. 1: Emily steals his car Who's right here? #jasonxemily",https://www.tiktok.com/@x/video/7400000000000000001,2026-11-04,"5,328",85,4,0
Adin fries Wendy Ortiz on stream,https://www.tiktok.com/@x/video/7400000000000000002,2026-10-18,184.5K,9967,120,461
Jason drops phone on Vespa,https://www.tiktok.com/@x/video/7400000000000000003,2026-11-03,5177,31,1,0
Jason breaks Marlon's trophy,,2026-10-21,6019,60,2,0
Something nobody rendered here,,2026-10-22,1000,10,0,0
"""
v = results.parse(CSV.encode())
check("reads the videos under the header, past a title line", len(v) == 5, len(v))
check("numbers with commas and K", v[0]["views"] == 5328 and v[1]["views"] == 184500, [x["views"] for x in v])
check("the TikTok video id from the link", v[1]["id"] == "7400000000000000002")
check("the post time", time.strftime("%Y-%m-%d", time.localtime(v[0]["posted"])) == "2026-11-04", v[0]["posted"])
try:
    results.parse(b"Name,Size\nx,1\n")
    check("a file without views says what it has", False)
except ValueError as e:
    check("a file without views says what it has", "Name, Size" in str(e), str(e))


def xlsx(rows):
    """A minimal Excel file: shared strings for text, numbers as numbers."""
    strings, xml_rows = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row):
            ref = chr(65 + c) + str(r)
            if isinstance(val, (int, float)):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                strings.append(val)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    return b.getvalue()


xv = results.parse(xlsx([["Title", "Views", "Likes", "Comments", "Shares"], ["Meily catches a crazy clip", 25496, 1759, 30, 33]]),
                   "content.xlsx")
check("reads an Excel export too", xv and xv[0]["title"] == "Meily catches a crazy clip" and xv[0]["views"] == 25496, xv)

# ---- matching to clips
now = time.time()
items = [
    {"id": "q1", "title": "Jason × Emily Pt. 1: Emily steals his car", "post": "Jason × Emily Pt. 1\nWho's right here? #jasonxemily",
     "created": 0, "streamer": "Jason", "series": {"name": "Jason × Emily"}, "conflict": True,
     "learn": {"key": "src|101", "streamer": "jason"}},
    {"id": "q2", "title": "Adin fries Wendy", "post": "", "post_url": "https://www.tiktok.com/@x/video/7400000000000000002",
     "created": 0, "streamer": "Adin", "conflict": True},
    {"id": "q3", "title": "Jason drops phone on Vespa", "post": "Wait for it", "created": 0, "streamer": "Jason",
     "learn": {"key": "src|303", "streamer": "jason"}},
    {"id": "q4", "title": "Jason breaks Marlon's trophy", "post": "", "created": 0, "streamer": "Jason",
     "learn": {"key": "src|404", "streamer": "jason"}},
]
check("matched by TikTok link first", results.match(v[1], items)["id"] == "q2")
check("else by caption and title", results.match(v[0], items)["id"] == "q1" and results.match(v[2], items)["id"] == "q3")
check("a video no clip is like isn't matched", results.match(v[4], items) is None)
late = dict(items[2], created=v[2]["posted"] + 86400)
check("not a clip rendered after the video was posted", results.match(v[2], [late]) is None)

# ---- ranking: each matched posted clip, against its streamer's median
for k in ("src|101", "src|303", "src|404"):
    learning.record("jason", k, {f: 0.5 for f in learning.FEATURES}, "post")
out = results.import_file(CSV.encode(), "content.csv", items)
check("the import says what it did", out["last"] == {"videos": 5, "matched": 4, "learned": 3}, out.get("last"))
samples = {s["k"]: s for s in learning._load()["profiles"]["jason"]["samples"]}
check("the clips that did about as well as usual stay a keep", samples["src|303"]["y"] == 1.0 and samples["src|404"]["y"] == 1.0,
      samples)
check("ranking was told how each did", all("perf" in samples[k] for k in ("src|101", "src|303", "src|404")), samples)
learning.performance("jason", "src|303", 3.0)
learning.performance("jason", "src|404", 0.2)
samples = {s["k"]: s for s in learning._load()["profiles"]["jason"]["samples"]}
check("a hit counts several times as much as a post", samples["src|303"]["w"] == 6.0 and samples["src|303"]["y"] == 1.0)
check("a flop counts as a skip", samples["src|404"]["y"] == 0.0)
check("a clip ranking never stored is skipped quietly", learning.performance("jason", "nope|1", 2.0) is False)

# ---- what the screen shows
vw = results.view()
check("all videos counted", vw["videos"] == 5 and vw["matched"] == 4 and vw["total"][0]["views"] == 5328 + 184500 + 5177 + 6019 + 1000,
      vw["total"])
by = {g["name"]: g for g in vw["by_streamer"]}
check("by streamer, best median first", list(by) == ["Adin", "Jason"] and by["Jason"]["videos"] == 3, list(by))
check("by series", {g["name"] for g in vw["by_series"]} == {"Jason × Emily", "Not in a series"})
check("arguments against the rest", {g["name"] for g in vw["by_kind"]} == {"Arguments and drama", "Everything else"})
check("the list, most viewed first, with the clip each came from", vw["list"][0]["views"] == 184500 and vw["list"][0]["clip"] == "Adin fries Wendy")

# a newer export of the same video replaces its numbers instead of counting it twice
results.import_file(CSV.replace("184.5K", "190K").encode(), "content.csv", items)
check("importing again updates, doesn't add", results.view()["videos"] == 5 and results.view()["list"][0]["views"] == 190000)

# a clip queued before 4.51 (no learning key on it): read from its project
wd = tempfile.mkdtemp(prefix="vc_res_")
with open(os.path.join(wd, "project.json"), "w", encoding="utf-8") as f:
    json.dump({"source": "old", "streamer": "Jason", "clips": [{"id": 5, "at": 77.9, "feat": {"chat": 1}}]}, f)
check("an older clip's learning key comes from its project", results._learn_ref({"workdir": wd, "clip_id": 5}) == {"key": "old|77", "streamer": "Jason"})

print(f"\n{len(failures)} failed" if failures else "\nPASSED")
sys.exit(1 if failures else 0)
