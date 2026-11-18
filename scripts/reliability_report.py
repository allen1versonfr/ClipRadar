"""
How reliable Clip Radar is in real use (docs/plans/hardening.md, phase 3): the reliability twin of the speed audit
(docs/audits/efficiency-audit-2026-11-08.md).

It reads the Outcome lines the app writes to clipradar.log (one per TikTok or YouTube upload, VOD download, live
session start, render and Discord send: src/outcome.py) and the failures in errors.jsonl, and writes a Markdown report:
for each feature, the attempts, how many went through, the failure rate, the failures by step, the 5 most common
messages, and, for uploads, the newest page fingerprint for each step that failed.

  python scripts/reliability_report.py LOGDIR                  the last 14 days, printed
  python scripts/reliability_report.py LOGDIR --days 30 --out docs/audits/reliability-2026-12-01.md

LOGDIR is the folder with clipradar.log (Settings › Storage › Logs; usually %LOCALAPPDATA%\\ClipRadar). Run it on the
PC Clip Radar is used on and commit the report under docs/audits/. Nothing in it is personal: the Outcome lines hold
no titles or links, errors.jsonl has secrets blanked and the home folder as ~, and fingerprints hold no captions,
file names or account names.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

FEATURES = ("upload", "download", "live", "render", "discord")
TITLES = {"upload": "Upload", "download": "VOD download", "live": "Live session start", "render": "Render",
          "discord": "Discord send"}
# which field splits a feature into rows: uploads by site, downloads and live sessions by platform
SPLIT = {"upload": "site", "download": "platform", "live": "platform"}

_STARTED = re.compile(r"===== Clip Radar started (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
_LINE = re.compile(r"^(?:(\d{2}:\d{2}:\d{2}) )?\s*Outcome (\w+)((?: \w+=(?:\"(?:[^\"\\]|\\.)*\"|\S+))*)\s*$")
_FIELD = re.compile(r"(\w+)=(\"(?:[^\"\\]|\\.)*\"|\S+)")


def parse_fields(text: str) -> dict:
    out = {}
    for k, v in _FIELD.findall(text or ""):
        if v.startswith('"'):
            try:
                v = json.loads(v)
            except ValueError:
                v = v.strip('"')
        out[k] = v
    return out


def parse_log(lines) -> list:
    """The Outcome lines of one clipradar.log: [{"day", "time", "feature", "result", "fields"}]. The day comes from the
    last "Clip Radar started" line, moved on a day when the time goes back past midnight; None before any."""
    out, day, last = [], None, ""
    for raw in lines:
        raw = raw.rstrip("\n")
        m = _STARTED.search(raw)
        if m:
            day, last = dt.date.fromisoformat(m.group(1)), m.group(2)
            continue
        m = _LINE.match(raw)
        if not m:
            t = raw[:8]
            if day and re.fullmatch(r"\d{2}:\d{2}:\d{2}", t):
                if t < last:
                    day += dt.timedelta(days=1)
                last = t
            continue
        t, feature, rest = m.group(1) or "", m.group(2), m.group(3)
        if day and t:
            if t < last:
                day += dt.timedelta(days=1)
            last = t
        fields = parse_fields(rest)
        out.append({"day": day, "time": t, "feature": feature, "result": fields.pop("result", ""), "fields": fields})
    return out


def read_logs(logdir: str) -> list:
    """Every Outcome line in clipradar.log and its older copies, oldest file first."""
    files = sorted(glob.glob(os.path.join(logdir, "clipradar*.log")), key=lambda p: (".old" not in p, p))
    out = []
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            out += parse_log(f)
    return out


def read_errors(logdir: str) -> list:
    out = []
    for name in ("errors.old.jsonl", "errors.jsonl"):
        try:
            with open(os.path.join(logdir, name), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out


def error_feature(e: dict) -> tuple:
    """(feature, row) an errors.jsonl entry belongs to, or (None, None)."""
    where, ctx = str(e.get("where", "")), e.get("context") or {}
    if where == "upload":
        return "upload", ctx.get("site", "tiktok")
    if where in ("job:studio", "job:youtube"):
        return "upload:job", "tiktok" if where == "job:studio" else "youtube"
    if where.startswith("render:"):
        return "render", ""
    if where in ("live:start", "live:not-live"):
        return "live", ""
    if where == "job:analyze" and re.search(r"Getting the VOD|[Dd]ownload", str(ctx.get("step", ""))):
        return "download", ""
    if "discord" in where.lower() or "Discord" in str(e.get("message", "")):
        return "discord", ""
    return None, None


def _day_of(at: str):
    try:
        return dt.date.fromisoformat(str(at)[:10])
    except ValueError:
        return None


def summarize(outcomes: list, errors: list, since=None) -> dict:
    """{feature: {row: {"attempts", "ok", "failed", "cancelled", "steps": Counter}}}, the top messages per feature,
    and the newest fingerprint per (site, step). since: only from that day on (undated lines are left out then)."""
    rows: dict = defaultdict(lambda: defaultdict(lambda: {"attempts": 0, "ok": 0, "failed": 0, "cancelled": 0,
                                                         "steps": Counter()}))
    for o in outcomes:
        if o["feature"] not in FEATURES or (since and (o["day"] is None or o["day"] < since)):
            continue
        f = o["fields"]
        r = rows[o["feature"]][f.get(SPLIT.get(o["feature"], ""), "") if o["feature"] in SPLIT else ""]
        r["attempts"] += 1
        if o["result"] in ("ok", "failed", "cancelled"):
            r[o["result"]] += 1
        if o["result"] == "failed":
            r["steps"][f.get("step") or f.get("kind") or "?"] += 1
    errors = [e for e in errors if not since or (_day_of(e.get("at")) or dt.date.min) >= since]
    messages: dict = defaultdict(Counter)
    has_upload = {e.get("context", {}).get("site", "tiktok") for e in errors if e.get("where") == "upload"}
    prints: dict = {}
    for e in errors:
        feature, row = error_feature(e)
        if feature == "upload:job":
            if row in has_upload:            # the same failure, recorded by the upload itself with its fingerprint
                continue
            feature = "upload"
        if not feature:
            continue
        msg = re.sub(r"\s+", " ", str(e.get("message", "")))[:160]
        messages[feature][msg] += 1
        if feature == "upload" and e.get("fingerprint"):
            key = (row, (e.get("context") or {}).get("step", "?"))
            if key not in prints or str(e.get("at", "")) >= str(prints[key]["at"]):
                prints[key] = {"at": e.get("at", ""), "version": e.get("version", ""), "fingerprint": e["fingerprint"]}
    return {"rows": rows, "messages": messages, "fingerprints": prints}


def _rate(r: dict) -> str:
    done = r["ok"] + r["failed"]
    return f"{100 * r['failed'] / done:.0f}%" if done else "–"


def render_markdown(s: dict, since=None, until=None, logdir: str = "") -> str:
    out = ["# Reliability report", ""]
    span = f"{since} to {until}" if since and until else "all of the log"
    out += [f"From the Outcome lines in clipradar.log and the failures in errors.jsonl, {span}. "
            "Failure rate is failed / (went through + failed); cancelled attempts don't count either way.", ""]
    out += ["| Feature | Attempts | Went through | Failed | Cancelled | Failure rate | Failures by step |",
            "|---|---:|---:|---:|---:|---:|---|"]
    any_row = False
    for feature in FEATURES:
        for row, r in sorted(s["rows"].get(feature, {}).items()):
            any_row = True
            name = TITLES[feature] + (f" ({row})" if row else "")
            steps = ", ".join(f"{k} {n}" for k, n in r["steps"].most_common()) or "–"
            out.append(f"| {name} | {r['attempts']} | {r['ok']} | {r['failed']} | {r['cancelled']} | {_rate(r)} | {steps} |")
    if not any_row:
        out.append("| (no Outcome lines in this period) | | | | | | |")
    out.append("")
    for feature in FEATURES:
        msgs = s["messages"].get(feature)
        if not msgs:
            continue
        out += [f"## {TITLES[feature]}: most common messages", ""]
        out += [f"{i}. {m} ({n}×)" for i, (m, n) in enumerate(msgs.most_common(5), 1)]
        out.append("")
    if s["fingerprints"]:
        out += ["## Uploads: the newest page fingerprint for each step that failed", ""]
        for (site, step), fp in sorted(s["fingerprints"].items()):
            out += [f"### {site}, {step} ({fp['at']}, Clip Radar {fp['version']})", "", "```json",
                    json.dumps(fp["fingerprint"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logdir", help="the folder with clipradar.log and errors.jsonl")
    ap.add_argument("--days", type=int, default=14, help="how many days back (default 14; 0: everything)")
    ap.add_argument("--today", help=argparse.SUPPRESS)          # tests
    ap.add_argument("--out", help="write the report here instead of printing it")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.logdir):
        print(f"{a.logdir} isn't a folder.")
        return 2
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    since = today - dt.timedelta(days=a.days - 1) if a.days > 0 else None
    s = summarize(read_logs(a.logdir), read_errors(a.logdir), since)
    md = render_markdown(s, since, today if since else None, a.logdir)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
