"""
The reliability report (scripts/reliability_report.py) on a sample log: the Outcome lines src/outcome.py writes are
read back, dated from the "Clip Radar started" line (and past midnight), counted per feature and site or platform,
with failures by step; errors.jsonl gives the most common messages and the newest page fingerprint for each upload
step that failed, without counting a failed upload twice. --days leaves older lines out.

  python tests/test_reliability_report.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import outcome  # noqa: E402
import reliability_report as rr  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- the line
L = outcome.line
check("an Outcome line looks like a Timing line", L("upload", "failed", site="tiktok", step="caption")
      == "Outcome upload site=tiktok step=caption result=failed", L("upload", "failed", site="tiktok", step="caption"))
check("a value with a space is quoted", L("upload", "failed", site="tiktok", step="file box")
      == 'Outcome upload site=tiktok step="file box" result=failed')
check("yes and no for true and false; empty fields left out", L("download", "ok", platform="twitch", fresh=True, step="")
      == "Outcome download platform=twitch fresh=yes result=ok")
check("a job step loses its percentage", outcome.step_name("Downloading the VOD 40%") == "Downloading the VOD")
odd = L("upload", "failed", site="tiktok", step='a "quoted" step\nwith a line')
check("an odd value stays on one line and reads back", "\n" not in odd
      and rr.parse_fields(odd.split(" ", 2)[2])["step"] == 'a "quoted" step\nwith a line', odd)

# ---------------------------------------------------------------- a sample log
logdir = tempfile.mkdtemp(prefix="vc_rel_")


def ln(t, *a, **k):
    return f"{t}   {L(*a, **k)}\n"


old_log = [
    "\n===== Clip Radar started 2026-10-01 10:00:00 =====\n",
    ln("10:05:00", "render", "failed", kind="encoder"),                     # older than 14 days
]
log = [
    "  Outcome render result=ok\n",                                          # before any start line: no date
    "\n===== Clip Radar started 2026-11-10 22:00:00 =====\n",
    "22:00:01 Clip Radar 4.52.0\n",
    ln("22:01:00", "download", "ok", platform="twitch", fresh=True),
    ln("22:30:00", "download", "failed", platform="twitch", step="Downloading the VOD"),
    ln("22:40:00", "download", "ok", platform="youtube", fresh=False),
    ln("23:00:00", "upload", "ok", site="tiktok", posted=True, pressed=True),
    ln("23:10:00", "upload", "failed", site="tiktok", step="caption box"),
    ln("23:50:00", "upload", "cancelled", site="tiktok"),
    "23:59:59 Rendering \"a clip\" (30 s)...\n",
    ln("00:00:10", "render", "ok", live=False),                              # past midnight: 2026-11-11
    ln("00:01:00", "render", "failed", kind="stalled", retry=True),
    ln("00:02:00", "render", "ok"),
    ln("00:03:00", "live", "ok", platform="twitch"),
    ln("00:04:00", "discord", "failed", step="send", file=True),
    ln("00:05:00", "discord", "ok", attached=True),
    ln("00:06:00", "upload", "failed", site="youtube", step="file box"),
    "00:07:00   Timing, render \"a clip\": Cutting 1.0 s (all 1.0 s)\n",
]
with open(os.path.join(logdir, "clipradar.old.log"), "w", encoding="utf-8") as f:
    f.writelines(old_log)
with open(os.path.join(logdir, "clipradar.log"), "w", encoding="utf-8") as f:
    f.writelines(log)

FP_OLD = {"site": "tiktok", "selectors": {"caption": {"match": False}}, "path": "/tiktokstudio/upload"}
FP_NEW = {"site": "tiktok", "selectors": {"caption": {"match": True, "visible": False}}, "path": "/tiktokstudio/upload"}
errors = [
    {"at": "2026-10-01 10:05:00", "where": "render:encoder", "message": "Old failure"},
    {"at": "2026-11-10 22:30:00", "where": "job:analyze", "message": "The VOD download stopped for 3 minutes.",
     "context": {"step": "Getting the VOD"}},
    {"at": "2026-11-10 22:31:00", "where": "job:analyze", "message": "Not a download", "context": {"step": "Finding the best moments"}},
    {"at": "2026-11-10 23:05:00", "where": "upload", "message": "The caption didn't go in as written.",
     "context": {"step": "caption box", "site": "tiktok"}, "fingerprint": FP_OLD, "version": "4.52.0"},
    {"at": "2026-11-10 23:10:00", "where": "upload", "message": "The caption didn't go in as written.",
     "context": {"step": "caption box", "site": "tiktok"}, "fingerprint": FP_NEW, "version": "4.52.0"},
    {"at": "2026-11-10 23:10:01", "where": "job:studio", "message": "The caption didn't go in as written. The video is selected",
     "context": {"step": "Typing the caption"}},
    {"at": "2026-11-11 00:01:00", "where": "render:stalled", "message": "Render stalled; trying once more"},
    {"at": "2026-11-11 00:04:00", "where": "autopilot:discord", "message": "Couldn't reach Discord."},
    {"at": "2026-11-11 00:06:00", "where": "job:youtube", "message": "YouTube Studio's upload box didn't appear."},
]
with open(os.path.join(logdir, "errors.jsonl"), "w", encoding="utf-8") as f:
    f.writelines(json.dumps(e) + "\n" for e in errors)

outs = rr.read_logs(logdir)
check("every Outcome line is read, the old log first", len(outs) == 15 and outs[0]["fields"].get("kind") == "encoder", len(outs))
check("the Timing and other lines aren't", all(o["feature"] in rr.FEATURES for o in outs))
check("a line before any start line has no day", outs[1]["day"] is None, outs[1])
by_time = {o["time"]: o for o in outs if o["time"]}
check("lines are dated from the start line", str(by_time["22:01:00"]["day"]) == "2026-11-10", by_time["22:01:00"])
check("and move on a day past midnight", str(by_time["00:00:10"]["day"]) == "2026-11-11", by_time["00:00:10"])
check("quoted steps read back", by_time["23:10:00"]["fields"]["step"] == "caption box", by_time["23:10:00"])

import datetime as dt  # noqa: E402

s = rr.summarize(outs, rr.read_errors(logdir), since=dt.date(2026, 10, 29))
rows = s["rows"]
tt = rows["upload"]["tiktok"]
check("TikTok uploads: 3 attempts, 1 through, 1 failed, 1 cancelled", (tt["attempts"], tt["ok"], tt["failed"], tt["cancelled"])
      == (3, 1, 1, 1), tt)
check("failed at the caption box", dict(tt["steps"]) == {"caption box": 1}, tt["steps"])
check("downloads are split by platform", rows["download"]["twitch"]["attempts"] == 2 and rows["download"]["youtube"]["ok"] == 1,
      dict(rows["download"]))
r = rows["render"][""]
check("renders in the period: a stall counts as a failure, the old and undated ones are left out",
      (r["attempts"], r["ok"], r["failed"]) == (3, 2, 1) and dict(r["steps"]) == {"stalled": 1}, r)
check("live starts and Discord sends are counted", rows["live"]["twitch"]["ok"] == 1 and rows["discord"][""]["failed"] == 1)
m = s["messages"]
check("an upload failure isn't counted twice (the job's line and the upload's own)",
      m["upload"]["The caption didn't go in as written."] == 2 and not any("selected" in k for k in m["upload"]), dict(m["upload"]))
check("a site with no upload record of its own still has its job's message",
      m["upload"]["YouTube Studio's upload box didn't appear."] == 1, dict(m["upload"]))
check("download messages are the download step's only", list(m["download"]) == ["The VOD download stopped for 3 minutes."],
      dict(m["download"]))
check("an old failure is left out", "Old failure" not in m["render"], dict(m["render"]))
check("the newest fingerprint for each upload step", s["fingerprints"][("tiktok", "caption box")]["fingerprint"] == FP_NEW,
      s["fingerprints"])

md = rr.render_markdown(s, dt.date(2026, 10, 29), dt.date(2026, 11, 11))
check("the report has a row per feature and site", "| Upload (tiktok) | 3 | 1 | 1 | 1 | 50% | caption box 1 |" in md
      and "| VOD download (twitch) | 2 | 1 | 1 | 0 | 50% | Downloading the VOD 1 |" in md
      and "| Render | 3 | 2 | 1 | 0 | 33% | stalled 1 |" in md, md)
check("the top messages and the fingerprint", "## Upload: most common messages" in md and '"visible": false' in md, md)

out = os.path.join(logdir, "report.md")
code = rr.main([logdir, "--days", "14", "--today", "2026-11-11", "--out", out])
text = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
check("the script writes the report", code == 0 and "| Render | 3 | 2 | 1 | 0 | 33% |" in text, text[:400])
rr.main([logdir, "--days", "0", "--out", out])
text = open(out, encoding="utf-8").read()
check("--days 0: everything, the old and undated lines too", "| Render | 5 | 3 | 2 | 0 | 40% |" in text, text[:600])
empty = tempfile.mkdtemp(prefix="vc_rel_")
rr.main([empty, "--out", os.path.join(empty, "r.md")])
check("a folder with no logs: a report that says so", "no Outcome lines" in open(os.path.join(empty, "r.md"), encoding="utf-8").read())

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
