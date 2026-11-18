"""
How well Clip Radar picks a VOD's moments, scored against the clips viewers made of it
(docs/plans/ranking-eval.md). A developer tool, not shipped.

  python scripts/eval_ranking.py score CORPUS --out rank_out                    # this checkout
  python scripts/eval_ranking.py score CORPUS --baseline main --out rank_out    # A/B against a git ref
  python scripts/eval_ranking.py score CORPUS --ablate --out rank_out           # each signal switched off in turn

CORPUS is a folder of project folders, each with video.mp4, meta.json, chat.json and the viewers' clips frozen in
twitch_clips.json (times from the start of the stretch), and optionally emotes.json ({service: [names]}, the
channel's 7TV/BTTV/FFZ emotes frozen when it was built, so the score doesn't depend on those services today).

For each project, `score` finds the moments the way the app does with its default settings (the "Any stream"
preset, Normal, 15-120 s clips, chat delay worked out): engine.analyze_cached, then engine.pick_clips twice, with
a fixed count (--count, 20) and on Automatic. Two things are different from the app, on purpose:
  - the viewers' clips are the answer key, so they are never a ranking input: the analysis is made in a cache
    folder of its own (CORPUS/<project>/rankcache/<side>/) where there is no twitch_clips.json to load, the
    signal is cleared again after, and a project whose score still has a "viewers" part is refused;
  - nothing learned from what you kept is used (adjust=None, no trim offsets), so the score doesn't depend on
    whose PC ran it.
The metrics (scripts/rankmetrics.py) are always this checkout's, for both sides of an A/B. Truth moments whose
payoff is in the first or last EDGE_S seconds of a stretch are left out: pick_clips never takes a peak there,
and a stretch's edges are where it was cut, not where the stream started or ended.

It writes OUT/report.md (headline numbers, per VOD, the biggest misses with links to watch them, and the
baseline or ablation tables) and OUT/report.json.

What this measures: did it find the moments people noticed on Twitch. Not what will do well on TikTok, and big
streams get far more clips than small ones. Every report says so.
"""

from __future__ import annotations

import argparse
import datetime
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="eval_ranking_"))   # the user's settings untouched
sys.path.insert(0, HERE)

import rankmetrics as rm  # noqa: E402

PRESET = "any"
SENSITIVITY = "normal"
COUNT = 20               # the fixed count: recall@5/10/20 are the first 5, 10 and 20 of this list
EDGE_S = 60              # pick_clips skips peaks this close to either end (min(60, 3% of the video))
MISSES = 15              # misses listed in the report
CAVEAT = ("This measures whether Clip Radar found the moments viewers noticed and clipped on Twitch. It isn't a "
          "measure of what will do well on TikTok, and big streams get far more clips than small ones.")
# score parts --ablate switches off one at a time: (name, how). "weight": the preset's weight set to 0;
# "array": the signal itself set to 0 (chat and audio are mixed by the chat %, not a weight); "talk": no dampening
ABLATE = [("chat", "array"), ("audio", "array"), ("laugh", "weight"), ("scream", "weight"), ("cheer", "weight"),
          ("emotes", "weight"), ("surge", "weight"), ("react", "weight"), ("agree", "weight"), ("talk", "talk")]


# ---------------------------------------------------------------------------------------------- the projects
def _read(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def projects(corpus: str, only=None) -> list:
    """Project folders with a video and frozen viewers' clips, by name."""
    out = []
    for name in sorted(os.listdir(corpus)):
        wd = os.path.join(corpus, name)
        if only and name not in only:
            continue
        if os.path.isfile(os.path.join(wd, "video.mp4")) and os.path.isfile(os.path.join(wd, "twitch_clips.json")):
            out.append(wd)
    return out


def _clips(cs: list) -> list:
    return [{"start": round(float(c.start), 2), "end": round(float(c.end), 2), "peak": float(c.peak),
             "score": float(c.score), "reason": str(getattr(c, "reason", "") or "")} for c in cs]


def _pick(an, count: int, chat: list, p: dict) -> list:
    """engine.pick_clips with the app's defaults and nothing learned; only the arguments this side's version takes."""
    import engine
    kw = {"count": count, "chat": chat, "chat_delay": an.chat_delay, "adjust": None, "min_len": float(p["min"]),
          "max_len": float(p["max"]), "sensitivity": SENSITIVITY, "back": float(p.get("back", 25)), "offsets": (0.0, 0.0)}
    takes = inspect.signature(engine.pick_clips).parameters
    return _clips(engine.pick_clips(an, **{k: v for k, v in kw.items() if k in takes}))


def _viewers_off(an, p: dict):
    if getattr(an, "vclips", None) is not None:
        an.vclips = an.vclip_people = an.vclip_views = None
        an.combine(an.chat_weight_pct, p)
    if "viewers" in (getattr(an, "parts", None) or {}):
        raise RuntimeError("the viewers' clips are still in the score: the test would read its own answers")


@contextmanager
def _without(an, part: str, how: str, p: dict):
    """The analysis with one score part switched off, put back after."""
    import numpy as np
    pct = an.chat_weight_pct
    saved = None
    w = dict(p)
    if how == "weight":
        w[part] = 0.0
    elif how == "array":
        attr = {"chat": "chat_z", "audio": "audio_z"}[part]
        saved = (attr, getattr(an, attr))
        setattr(an, attr, np.zeros_like(saved[1]))
    elif how == "talk":
        saved = ("talk", getattr(an, "talk", None))
        an.talk = None
    an.combine(pct, w)
    try:
        yield an
    finally:
        if saved is not None:
            setattr(an, saved[0], saved[1])
        an.combine(pct, p)


def run_side(corpus: str, tag: str, ablate: bool = False, count: int = COUNT, fresh: bool = False, only=None,
             log=print) -> dict:
    """Clip Radar's clips for every project, made by whichever src/ is first on the path."""
    import audio_events
    import detect
    import emotes
    import engine
    p = detect.preset(PRESET)
    out = {"tag": tag, "events_model": bool(audio_events.available()), "preset": PRESET, "count": count, "projects": []}
    for wd in projects(corpus, only):
        name = os.path.basename(wd)
        t0 = time.time()
        cache = os.path.join(wd, "rankcache", tag)
        if fresh and os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)
        cpath = os.path.join(wd, "chat.json")
        chat = engine.load_chat(cpath, {}) if os.path.exists(cpath) else []
        names = _read(os.path.join(wd, "emotes.json"))
        es = emotes.EmoteSet(names) if isinstance(names, dict) else None
        an = engine.analyze_cached(cache, os.path.join(wd, "video.mp4"), chat, chat_delay="auto",
                                   chat_weight_pct=float(p["chat"]), events=True, emote_set=es, weights=p)
        _viewers_off(an, p)
        rec = {"name": name, "duration": float(an.duration), "events": bool(an.has_events), "chat": len(chat),
               "emotes": len(es) if es else 0, "chat_delay": float(an.chat_delay),
               "parts": sorted(getattr(an, "parts", {}) or {}),
               "fixed": _pick(an, count, chat, p), "auto": _pick(an, 0, chat, p)}
        if ablate:
            rec["ablate"] = {}
            for part, how in ABLATE:
                if part not in rec["parts"]:
                    continue
                with _without(an, part, how, p):
                    rec["ablate"][part] = {"fixed": _pick(an, count, chat, p), "auto": _pick(an, 0, chat, p)}
        out["projects"].append(rec)
        log(f"  {name}: {len(rec['fixed'])} clips, {len(rec['auto'])} on Automatic ({time.time() - t0:.0f} s)")
    return out


# ---------------------------------------------------------------------------------------------- a git ref
def _git(*a) -> str:
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True, check=True).stdout.strip()


def _ref_src(ref: str) -> str:
    """src/ of a git ref, unpacked into a temporary folder, with this checkout's laughter detector (it isn't in git)."""
    import io
    import tarfile
    out = tempfile.mkdtemp(prefix="eval_rank_base_")
    tar = subprocess.run(["git", "-C", ROOT, "archive", ref, "src"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(out, filter="data")
    src = os.path.join(out, "src")
    models = os.path.join(ROOT, "src", "models")
    if os.path.isdir(models):
        for f in os.listdir(models):
            if f.endswith(".onnx") and not os.path.exists(os.path.join(src, "models", f)):
                os.makedirs(os.path.join(src, "models"), exist_ok=True)
                shutil.copy2(os.path.join(models, f), os.path.join(src, "models", f))
    return src


def _side(src: str, tag: str, args, out_json: str) -> dict:
    cmd = [sys.executable, os.path.abspath(__file__), "_dump", args.corpus, out_json, "--src", src, "--tag", tag,
           "--count", str(args.count)]
    if args.fresh:
        cmd.append("--fresh")
    for o in args.only or []:
        cmd += ["--only", o]
    env = dict(os.environ, CLIPRADAR_DATA=tempfile.mkdtemp(prefix="eval_ranking_"))
    subprocess.run(cmd, check=True, env=env)
    return _read(out_json, {})


# ---------------------------------------------------------------------------------------------- scoring
def truth_of(wd: str, duration: float) -> tuple:
    """(viewers' clips, moments that aren't at the stretch's edges, how many were at the edges)."""
    vc = (_read(os.path.join(wd, "twitch_clips.json"), {}) or {}).get("clips") or []
    ms = rm.moments(vc)
    edge = min(EDGE_S, duration * 0.03)
    keep = [m for m in ms if m["payoff"][1] > edge and m["payoff"][0] < duration - edge]
    return vc, keep, len(ms) - len(keep)


def evaluate(corpus: str, side: dict) -> dict:
    """Every metric per VOD and across the corpus, for one side's clips."""
    per, miss = [], []
    for rec in side.get("projects", []):
        wd = os.path.join(corpus, rec["name"])
        vc, ms, at_edge = truth_of(wd, rec["duration"])
        s = rm.score_vod(rec["fixed"], vc, rec["auto"], ms=ms)
        meta = _read(os.path.join(wd, "meta.json"), {}) or {}
        s.update(name=rec["name"], at_edge=at_edge, events=rec["events"], vod=str(meta.get("vod") or ""),
                 offset=float(meta.get("start") or 0), category=str(meta.get("category") or meta.get("game") or ""))
        per.append(s)
        for m in rm.misses(rec["fixed"], rm.truth(ms))[:MISSES]:
            miss.append(dict(m, name=rec["name"], vod=s["vod"], offset=s["offset"]))
        if "ablate" in rec:
            s["ablate"] = {k: rm.score_vod(v["fixed"], vc, v["auto"], ms=ms) for k, v in rec["ablate"].items()}
    miss.sort(key=lambda m: (-m["views"], -m["clippers"]))
    return {"per_vod": per, "summary": rm.summarize(per, rm.METRICS), "misses": miss[:MISSES]}


def ablation(ev: dict) -> list:
    """Per part: the corpus means with it off, against the full score."""
    rows = []
    for part, _ in ABLATE:
        offs = [dict(v["ablate"][part], name=v["name"]) for v in ev["per_vod"] if part in (v.get("ablate") or {})]
        if not offs:
            continue
        sm = rm.summarize(offs, rm.METRICS)
        # against the full score of the same VODs, so a part only some VODs have is compared fairly
        names = {o["name"] for o in offs}
        base = rm.summarize([v for v in ev["per_vod"] if v["name"] in names], rm.METRICS)
        rows.append({"part": part, "vods": len(offs), "off": sm, "full": base, "diff": rm.diff(sm, base)})
    return rows


# ---------------------------------------------------------------------------------------------- the report
def _ts(s: float) -> str:
    s = int(max(0, s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _link(vod: str, t: float) -> str:
    t = int(max(0, t))
    return f"https://www.twitch.tv/videos/{vod}?t={t // 3600}h{t // 60 % 60}m{t % 60}s" if vod else ""


def _f(x, pct: bool = True) -> str:
    if x is None:
        return "–"
    return f"{x:.2f}" if pct else f"{x:.1f}"


def _sd(x, pct: bool = True) -> str:
    return "–" if x is None else (f"±{x:.2f}" if pct else f"±{x:.1f}")


HEAD = [("recall@10", "Recall@10 (headline)", True), ("wrecall@10", "Weighted recall@10", True),
        ("recall@5", "Recall@5", True), ("recall@20", "Recall@20", True), ("precision@10", "Precision@10", True),
        ("payoff_kept", "Payoff kept", True), ("first_hit", "First hit rank", False),
        ("auto_count", "Automatic: clips suggested", False), ("auto_recall", "Automatic: recall", True)]


def report(args, ev: dict, side: dict, base_ev=None) -> str:
    per = ev["per_vod"]
    with_truth = [v for v in per if v["truth"]]
    L = [f"# Ranking score, {datetime.date.today().isoformat()}", "",
         f"_{CAVEAT}_", "",
         f"- Checkout: `{_describe()}`" + (f", against `{args.baseline}`" if args.baseline else ""),
         f"- Corpus: {len(per)} VODs, {len(with_truth)} with truth moments; "
         f"{sum(v['truth'] for v in per)} truth moments (two or more clippers, or 500+ views)",
         f"- Settings: the \"Any stream\" preset, Normal, a fixed count of {side.get('count', COUNT)} and Automatic; "
         "viewers' clips off, nothing learned",
         f"- Laughter detector: {'on' if side.get('events_model') else '**off** (src/models/audio_events.onnx is missing)'}"
         f" ({sum(1 for v in per if v['events'])} of {len(per)} VODs analysed with it)", ""]
    if not with_truth:
        L += ["No VOD has truth moments, so there is nothing to score.", ""]
    L += ["## Across the corpus", "", "Each VOD counts once. Mean, spread (sd), lowest and highest, and how many VODs.", "",
          "| Metric | Mean | sd | Min | Max | VODs |", "|---|---|---|---|---|---|"]
    for k, label, pct in HEAD:
        s = ev["summary"].get(k) or {}
        L.append(f"| {label} | {_f(s.get('mean'), pct)} | {_sd(s.get('sd'), pct)} | {_f(s.get('min'), pct)} | "
                 f"{_f(s.get('max'), pct)} | {s.get('n', 0)} |")
    never = sum(1 for v in with_truth if v["first_hit"] is None)
    L += ["", f"First hit rank is over the VODs where the biggest moment was found at all; it wasn't in {never} of "
          f"{len(with_truth)}.", ""]
    if base_ev is not None:
        d = rm.diff(ev["summary"], base_ev["summary"])
        L += [f"## Against `{args.baseline}`", "", "| Metric | Baseline | This checkout | Change |", "|---|---|---|---|"]
        for k, label, pct in HEAD:
            b, n = (base_ev["summary"].get(k) or {}).get("mean"), (ev["summary"].get(k) or {}).get("mean")
            ch = d.get(k)
            L.append(f"| {label} | {_f(b, pct)} | {_f(n, pct)} | {'–' if ch is None else f'{ch:+.3f}'} |")
        bper = {v["name"]: v for v in base_ev["per_vod"]}
        L += ["", "| VOD | Recall@10 | | Weighted recall@10 | | Payoff kept | |", "|---|---|---|---|---|---|---|",
              "| | baseline → new | change | baseline → new | change | baseline → new | change |"]
        for v in per:
            b = bper.get(v["name"]) or {}
            cells = []
            for k in ("recall@10", "wrecall@10", "payoff_kept"):
                x, y = b.get(k), v.get(k)
                cells += [f"{_f(x)} → {_f(y)}", "–" if x is None or y is None else f"{y - x:+.2f}"]
            L.append(f"| {v['name']} | " + " | ".join(cells) + " |")
        L.append("")
    L += ["## Per VOD", "",
          "| VOD | Category | Truth | R@5 | R@10 | R@20 | wR@10 | P@10 | Payoff kept | First hit | Automatic / truth |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in per:
        L.append(f"| {v['name']} | {v['category'] or '–'} | {v['truth']}" + (f" (+{v['at_edge']} at edges)" if v["at_edge"] else "") +
                 f" | {_f(v['recall@5'])} | {_f(v['recall@10'])} | {_f(v['recall@20'])} | {_f(v['wrecall@10'])} | "
                 f"{_f(v['precision@10'])} | {_f(v['payoff_kept'])} | {v['first_hit'] or '–'} | "
                 f"{v.get('auto_count', '–')} / {v['truth']} |")
    L += ["", "## Biggest misses", "",
          "Truth moments none of the top 10 found, most viewed first. Times are in the VOD; the link opens it there.", "",
          "| VOD | Payoff at | Views | Clippers | Came | Viewer's title |", "|---|---|---|---|---|---|"]
    for m in ev["misses"]:
        at = m["offset"] + m["payoff"][0]
        when = f"[{_ts(at)}]({_link(m['vod'], at - 20)})" if m["vod"] else _ts(at)
        title = m["title"].replace("|", "/")
        L.append(f"| {m['name']} | {when} | {m['views']:,} | {m['clippers']} | {m['rank'] or 'not in the list'} | {title} |")
    if not ev["misses"]:
        L.append("| – | | | | | |")
    rows = ablation(ev)
    if rows:
        L += ["", "## Each part switched off", "",
              "The score again with one part of it at 0 (chat and audio: the signal itself; talk: no dampening; the rest: "
              "the preset's weight). A negative change means the part helps; about 0 means it does nothing here.", "",
              "| Part | VODs with it | Weighted recall@10 | Change | Recall@10 | Change | Payoff kept | Change |",
              "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            cells = []
            for k in ("wrecall@10", "recall@10", "payoff_kept"):
                ch = r["diff"].get(k)
                cells += [_f((r["off"].get(k) or {}).get("mean")), "–" if ch is None else f"{ch:+.3f}"]
            L.append(f"| {r['part']} | {r['vods']} | " + " | ".join(cells) + " |")
    L.append("")
    return "\n".join(L)


def _describe() -> str:
    try:
        return _git("describe", "--always", "--dirty")
    except Exception:
        return "unknown"


def score(args) -> int:
    if not os.path.isdir(args.corpus):
        print(f"No folder {args.corpus}")
        return 2
    if not projects(args.corpus, args.only):
        print(f"No projects in {args.corpus} (each needs video.mp4 and twitch_clips.json)")
        return 2
    os.makedirs(args.out, exist_ok=True)
    base_side = base_ev = None
    if args.baseline:
        sha = _git("rev-parse", "--short=10", args.baseline)
        print(f"baseline: {args.baseline} ({sha})", flush=True)
        base_side = _side(_ref_src(args.baseline), f"ref-{sha}", args, os.path.join(args.out, "baseline.json"))
        base_ev = evaluate(args.corpus, base_side)
    print("this checkout", flush=True)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    side = run_side(args.corpus, "this", ablate=args.ablate, count=args.count, fresh=args.fresh, only=args.only)
    ev = evaluate(args.corpus, side)
    md = report(args, ev, side, base_ev)
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"date": datetime.date.today().isoformat(), "checkout": _describe(), "baseline": args.baseline or None,
                   "caveat": CAVEAT, "side": side, "eval": ev, "ablation": ablation(ev),
                   "baseline_side": base_side, "baseline_eval": base_ev}, f, indent=1)
    s = ev["summary"]
    line = "  ".join(f"{k} {_f((s.get(k) or {}).get('mean'), k not in ('first_hit', 'auto_count'))}"
                     for k in ("recall@10", "wrecall@10", "precision@10", "payoff_kept", "first_hit", "auto_count"))
    print(line)
    if base_ev is not None:
        d = rm.diff(s, base_ev["summary"])
        print("change: " + "  ".join(f"{k} {d[k]:+.3f}" for k in ("recall@10", "wrecall@10", "payoff_kept") if d.get(k) is not None))
    print(f"Wrote {os.path.join(args.out, 'report.md')} and report.json")
    return 0


# ---------------------------------------------------------------------------------------------- the command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="score this checkout (or A/B against --baseline) on a corpus")
    sc.add_argument("corpus")
    sc.add_argument("--out", default="rank_out")
    sc.add_argument("--baseline", metavar="REF", help="a git ref to compare with, run in its own process")
    sc.add_argument("--ablate", action="store_true", help="also score with each part of the score switched off in turn")
    sc.add_argument("--count", type=int, default=COUNT, help="the fixed count (recall@20 needs 20)")
    sc.add_argument("--fresh", action="store_true", help="analyse again instead of using the cached analysis")
    sc.add_argument("--only", action="append", help="only this project folder (repeatable)")
    dp = sub.add_parser("_dump", help=argparse.SUPPRESS)
    dp.add_argument("corpus")
    dp.add_argument("json")
    dp.add_argument("--src", required=True)
    dp.add_argument("--tag", required=True)
    dp.add_argument("--count", type=int, default=COUNT)
    dp.add_argument("--fresh", action="store_true")
    dp.add_argument("--only", action="append")
    a = ap.parse_args(argv)
    if a.cmd == "_dump":
        sys.path.insert(0, os.path.abspath(a.src))
        side = run_side(a.corpus, re.sub(r"[^0-9A-Za-z_.-]", "_", a.tag), count=a.count, fresh=a.fresh, only=a.only)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(side, f, indent=1)
        return 0
    if a.ablate and a.baseline:
        ap.error("--ablate runs on this checkout only: run it without --baseline")
    return score(a)


if __name__ == "__main__":
    sys.exit(main())
