"""
How well Clip Radar picks a VOD's moments, scored against the clips viewers made of it
(docs/plans/ranking-eval.md). A developer tool, not shipped.

  python scripts/eval_ranking.py build CORPUS --manifest scripts/ranking_corpus.txt   # once: download and freeze
  python scripts/eval_ranking.py score CORPUS --out rank_out                    # this checkout
  python scripts/eval_ranking.py score CORPUS --baseline main --out rank_out    # A/B against a git ref
  python scripts/eval_ranking.py score CORPUS --ablate --out rank_out           # each signal switched off in turn

CORPUS is a folder of project folders, each with video.mp4, meta.json, chat.json and the viewers' clips frozen in
twitch_clips.json (times from the start of the stretch), and optionally emotes.json ({service: [names]}, the
channel's 7TV/BTTV/FFZ emotes frozen when it was built, so the score doesn't depend on those services today).

`build` reads the manifest (one VOD a line, see scripts/ranking_corpus.txt) and, for each VOD, reuses
scripts/title_corpus.py: its chat from Twitch's GQL, the stretch's video at 160p (yt-dlp), and the viewers'
clips (twitchclips.fetch) with who made each one. A VOD without a stretch in the manifest gets one at random
(seeded by --seed and the VOD's id, so the same every time): 60 to 120 minutes, or the whole VOD when it's under
3 hours. Not the stretch with the most clips, which would pick the easy part. The clips and the channel's emotes
are frozen when the project is made and never fetched again, since viewers keep clipping and the answer key
mustn't move. A run that stops can be started again: what's there is kept. No Whisper (ranking doesn't need
words). --write-manifest puts each stretch, the build date and the counts back into the manifest. Keep CORPUS
outside the repository: chat and video are other people's.

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
import math
import os
import random
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
MIN_TRUTH = 8            # a VOD needs this many truth moments (main set, away from the edges) to be scored
STRETCH_MIN, STRETCH_MAX = 60 * 60, 120 * 60    # a stretch is 60 to 120 minutes...
WHOLE_UNDER = 3 * 3600                          # ...or the whole VOD when it's shorter than this
SEED = 97
STRETCH_SHORTEST = 30 * 60                      # where Twitch can't list every clip of 60 minutes, at least this
PAGE = 100                                      # clips Twitch gives an anonymous client per list
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
        if name.endswith(".part"):              # build hasn't finished it
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


def evaluate(corpus: str, side: dict, min_truth: int = MIN_TRUTH) -> dict:
    """Every metric per VOD and across the corpus, for one side's clips. A VOD with fewer than min_truth truth
    moments is listed but left out of the means, the misses and the ablation."""
    per, miss = [], []
    for rec in side.get("projects", []):
        wd = os.path.join(corpus, rec["name"])
        vc, ms, at_edge = truth_of(wd, rec["duration"])
        s = rm.score_vod(rec["fixed"], vc, rec["auto"], ms=ms)
        meta = _read(os.path.join(wd, "meta.json"), {}) or {}
        s.update(name=rec["name"], at_edge=at_edge, events=rec["events"], vod=str(meta.get("vod") or ""),
                 offset=float(meta.get("start") or 0), category=str(meta.get("category") or meta.get("game") or ""),
                 holdout=bool(meta.get("holdout")), scored=s["truth"] >= min_truth)
        per.append(s)
        if not s["scored"]:
            continue
        for m in rm.misses(rec["fixed"], rm.truth(ms))[:MISSES]:
            miss.append(dict(m, name=rec["name"], vod=s["vod"], offset=s["offset"]))
        if "ablate" in rec:
            s["ablate"] = {k: rm.score_vod(v["fixed"], vc, v["auto"], ms=ms) for k, v in rec["ablate"].items()}
    miss.sort(key=lambda m: (-m["views"], -m["clippers"]))
    scored = [v for v in per if v["scored"]]
    cats = sorted({v["category"] for v in scored})
    return {"per_vod": per, "min_truth": min_truth, "summary": rm.summarize(scored, rm.METRICS),
            "by_category": {c: rm.summarize([v for v in scored if v["category"] == c], rm.METRICS) for c in cats},
            "misses": miss[:MISSES]}


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


HEAD = [("recall@10", "Recall@10 (headline)", True), ("ceiling@10", "Recall@10 at best (10 clips, 10 different moments)", True),
        ("wrecall@10", "Weighted recall@10", True),
        ("recall@5", "Recall@5", True), ("recall@20", "Recall@20", True), ("precision@10", "Precision@10", True),
        ("payoff_kept", "Payoff kept", True), ("first_hit", "First hit rank", False),
        ("auto_count", "Automatic: clips suggested", False), ("auto_recall", "Automatic: recall", True)]


def report(args, ev: dict, side: dict, base_ev=None) -> str:
    per = ev["per_vod"]
    with_truth = [v for v in per if v["scored"]]
    L = [f"# Ranking score, {datetime.date.today().isoformat()}", "",
         f"_{CAVEAT}_", "",
         f"- Checkout: `{_describe()}`" + (f", against `{args.baseline}`" if args.baseline else ""),
         f"- Corpus: {len(per)} VODs, {len(with_truth)} scored (with at least {ev['min_truth']} truth moments); "
         f"{sum(v['truth'] for v in with_truth)} truth moments between them (two or more clippers, or 500+ views)",
         f"- Settings: the \"Any stream\" preset, Normal, a fixed count of {side.get('count', COUNT)} and Automatic; "
         "viewers' clips off, nothing learned",
         f"- Laughter detector: {'on' if side.get('events_model') else '**off** (src/models/audio_events.onnx is missing)'}"
         f" ({sum(1 for v in per if v['events'])} of {len(per)} VODs analysed with it)", ""]
    if not with_truth:
        L += [f"No VOD has {ev['min_truth']} truth moments, so there is nothing to score.", ""]
    L += ["## Across the corpus", "", "Each VOD counts once. Mean, spread (sd), lowest and highest, and how many VODs.", "",
          "| Metric | Mean | sd | Min | Max | VODs |", "|---|---|---|---|---|---|"]
    for k, label, pct in HEAD:
        s = ev["summary"].get(k) or {}
        L.append(f"| {label} | {_f(s.get('mean'), pct)} | {_sd(s.get('sd'), pct)} | {_f(s.get('min'), pct)} | "
                 f"{_f(s.get('max'), pct)} | {s.get('n', 0)} |")
    never = sum(1 for v in with_truth if v["first_hit"] is None)
    L += ["", f"First hit rank is over the VODs where the biggest moment was found at all; it wasn't in {never} of "
          f"{len(with_truth)}.", ""]
    if len(ev["by_category"]) > 1:
        L += ["### By category", "", "| Category | VODs | Recall@10 | Weighted recall@10 | Payoff kept |", "|---|---|---|---|---|"]
        for c, sm in ev["by_category"].items():
            L.append(f"| {c or '–'} | {sm['recall@10']['n']} | {_f(sm['recall@10']['mean'])} | {_f(sm['wrecall@10']['mean'])} | "
                     f"{_f(sm['payoff_kept']['mean'])} |")
        L.append("")
    if base_ev is not None:
        d = rm.diff(ev["summary"], base_ev["summary"])
        rule = rm.rule_check(ev, base_ev)
        L += [f"## Against `{args.baseline}`", "",
              f"**The rule for ranking changes (docs/plans/README.md): {'passes' if rule['passes'] else 'FAILS'}.**", "",
              "| Checked | Baseline | This checkout | Change | Allowed drop | |", "|---|---|---|---|---|---|"]
        for what, b, n, c, lim, ok in rule["checks"]:
            L.append(f"| {what} | {b:.3f} | {n:.3f} | {c:+.3f} | {lim:.2f} | {'ok' if ok else '**fails**'} |")
        small = [c for c in ev["by_category"] if ((ev["by_category"][c] or {}).get("wrecall@10") or {}).get("n", 0) < rm.RULE_MIN_VODS]
        if small:
            L.append(f"\nNot judged (fewer than {rm.RULE_MIN_VODS} VODs): {', '.join(c or 'no category' for c in sorted(small))}.")
        L += ["", "| Metric | Baseline | This checkout | Change |", "|---|---|---|---|"]
        for k, label, pct in HEAD:
            b, n = (base_ev["summary"].get(k) or {}).get("mean"), (ev["summary"].get(k) or {}).get("mean")
            ch = d.get(k)
            L.append(f"| {label} | {_f(b, pct)} | {_f(n, pct)} | {'–' if ch is None else f'{ch:+.3f}'} |")
        bper = {v["name"]: v for v in base_ev["per_vod"]}
        L += ["", "| VOD | Recall@10 | | Weighted recall@10 | | Payoff kept | |", "|---|---|---|---|---|---|---|",
              "| | baseline → new | change | baseline → new | change | baseline → new | change |"]
        for v in with_truth:
            b = bper.get(v["name"]) or {}
            cells = []
            for k in ("recall@10", "wrecall@10", "payoff_kept"):
                x, y = b.get(k), v.get(k)
                cells += [f"{_f(x)} → {_f(y)}", "–" if x is None or y is None else f"{y - x:+.2f}"]
            L.append(f"| {v['name']} | " + " | ".join(cells) + " |")
        L.append("")
    L += ["## Per VOD", "",
          "| VOD | Category | Truth | R@5 | R@10 (best possible) | R@20 | wR@10 | P@10 | Payoff kept | First hit | Automatic / truth |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in per:
        name = v["name"] + (" (held out)" if v.get("holdout") else "") + ("" if v["scored"] else " (not scored: too few moments)")
        L.append(f"| {name} | {v['category'] or '–'} | {v['truth']}" + (f" (+{v['at_edge']} at edges)" if v["at_edge"] else "") +
                 f" | {_f(v['recall@5'])} | {_f(v['recall@10'])} ({_f(v.get('ceiling@10'))}) | {_f(v['recall@20'])} | {_f(v['wrecall@10'])} | "
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
    found = projects(args.corpus, args.only)
    if not found:
        print(f"No projects in {args.corpus} (each needs video.mp4 and twitch_clips.json)")
        return 2
    held = [os.path.basename(wd) for wd in found if (_read(os.path.join(wd, "meta.json"), {}) or {}).get("holdout")]
    if held and not args.holdout:
        args.only = [os.path.basename(wd) for wd in found if os.path.basename(wd) not in held]
        print(f"{len(held)} held-out VOD{'s' if len(held) != 1 else ''} left out (--holdout to include them)")
        if not args.only:
            print("Every project is held out.")
            return 2
    os.makedirs(args.out, exist_ok=True)
    base_side = base_ev = None
    if args.baseline:
        sha = _git("rev-parse", "--short=10", args.baseline)
        print(f"baseline: {args.baseline} ({sha})", flush=True)
        base_side = _side(_ref_src(args.baseline), f"ref-{sha}", args, os.path.join(args.out, "baseline.json"))
        base_ev = evaluate(args.corpus, base_side, args.min_truth)
    print("this checkout", flush=True)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    side = run_side(args.corpus, "this", ablate=args.ablate, count=args.count, fresh=args.fresh, only=args.only)
    ev = evaluate(args.corpus, side, args.min_truth)
    md = report(args, ev, side, base_ev)
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"date": datetime.date.today().isoformat(), "checkout": _describe(), "baseline": args.baseline or None,
                   "caveat": CAVEAT, "side": side, "eval": ev, "ablation": ablation(ev),
                   "baseline_side": base_side, "baseline_eval": base_ev,
                   "rule": rm.rule_check(ev, base_ev) if base_ev is not None else None}, f, indent=1)
    s = ev["summary"]
    line = "  ".join(f"{k} {_f((s.get(k) or {}).get('mean'), k not in ('first_hit', 'auto_count'))}"
                     for k in ("recall@10", "wrecall@10", "precision@10", "payoff_kept", "first_hit", "auto_count"))
    print(line)
    if base_ev is not None:
        d = rm.diff(s, base_ev["summary"])
        print("change: " + "  ".join(f"{k} {d[k]:+.3f}" for k in ("recall@10", "wrecall@10", "payoff_kept") if d.get(k) is not None))
        rule = rm.rule_check(ev, base_ev)
        print("the rule for ranking changes: " + ("passes" if rule["passes"] else "FAILS: " + "; ".join(rule["fails"])))
    print(f"Wrote {os.path.join(args.out, 'report.md')} and report.json")
    return 0


# ---------------------------------------------------------------------------------------------- building the corpus
MANIFEST_LINE = re.compile(r"^\s*(\d+)(?:@(\d+)-(\d+))?((?:\s+[^#\s]+)*)\s*(?:#\s?(.*))?$")
BUILT = re.compile(r"\s*\[built [^\]]*\]")


def parse_manifest(text: str) -> list:
    """One entry per VOD line: {vod, a, b (None for a stretch picked at build time), tags {key: value}, holdout,
    note, line (0-based)}. Blank lines and lines starting with # aren't VODs. A line that isn't a VOD id is an error."""
    out = []
    for i, line in enumerate(text.splitlines()):
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        m = MANIFEST_LINE.match(line)
        if not m:
            raise ValueError(f"line {i + 1}: expected VOD_ID[@START-END] [key=value ...] [holdout] [# note]: {t}")
        a, b = (int(m.group(2)), int(m.group(3))) if m.group(2) else (None, None)
        if a is not None and b <= a:
            raise ValueError(f"line {i + 1}: the stretch ends before it starts: {t}")
        tags, holdout = {}, False
        for tok in (m.group(4) or "").split():
            if tok == "holdout":
                holdout = True
            elif "=" in tok:
                k, _, v = tok.partition("=")
                tags[k] = v
            else:
                raise ValueError(f"line {i + 1}: {tok!r} isn't key=value or holdout")
        out.append({"vod": m.group(1), "a": a, "b": b, "tags": tags, "holdout": holdout,
                    "note": BUILT.sub("", m.group(5) or "").strip(), "line": i})
    return out


def manifest_line(e: dict, built: dict = None) -> str:
    rng = f"@{e['a']}-{e['b']}" if e.get("a") is not None else ""
    toks = [f"{k}={v}" for k, v in e["tags"].items()] + (["holdout"] if e["holdout"] else [])
    note = e.get("note") or ""
    if built:
        note = (note + " " if note else "") + (f"[built {built['built']}: {built['clips']} clips, {built['moments']} moments, "
                                               f"{built['truth']} truth]")
    return f"{e['vod']}{rng}" + "".join(f" {t}" for t in toks) + (f"  # {note}" if note else "")


def write_manifest(path: str, entries: list, built: dict):
    """The manifest with each built VOD's stretch, build date and counts filled in; other lines as they were."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for e in entries:
        b = built.get(e["vod"])
        if b:
            lines[e["line"]] = manifest_line(dict(e, a=b["window"][0], b=b["window"][1]), b)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def coverage(asc: list, desc: list, length: float, page: int = PAGE) -> list:
    """Where in the VOD every clip is known, as [[from, to], ...]: every clip that ends in there (videoOffsetSeconds is
    a clip's end), so every clip wholly inside a stretch within one of them. Twitch gives an anonymous client
    one page of a VOD's clips, in the order they come in the VOD, from the start (ASC) or from the end (DESC): all of
    them when there are fewer than a page, or when the two pages meet; otherwise the start and the end, not the middle."""
    length = float(length)
    asc = [float(x) for x in asc if isinstance(x, (int, float))]
    desc = [float(x) for x in desc if isinstance(x, (int, float))]
    if len(asc) < page or not asc or not desc:
        return [[0.0, length]]
    hi, lo = max(asc), min(desc)
    if lo <= hi:
        return [[0.0, length]]
    # the clip at a page's last place may have others at the same second on the next page: stop a second short
    return [[0.0, hi - 1], [lo + 1, length]]


def vod_clips(vod: str):
    """Every clip of the VOD that Twitch tells an anonymous client about, with who made it, and where that list is
    complete: {"vod", "fetched", "length", "complete", "clips": [{id, start, end, views, title, creator}]}. None when
    Twitch couldn't be asked. twitchclips.fetch (what the app uses) reads the channel's most viewed clips instead,
    which on most VODs is a small part of them."""
    import title_corpus as tc
    pages, length = {}, 0
    for order in ("ASC", "DESC"):
        d = tc.gql('query{video(id:"%s"){lengthSeconds clips(first:%d,sort:%s){edges{node{id title viewCount '
                   'durationSeconds videoOffsetSeconds curator{login} video{id}}}}}}' % (vod, PAGE, order))
        v = (d.get("data") or {}).get("video")
        if not v or not isinstance((v.get("clips") or {}).get("edges"), list):
            return None
        length = int(v.get("lengthSeconds") or length)
        pages[order] = [(e or {}).get("node") or {} for e in v["clips"]["edges"]]
    # where a clip is in the VOD: Twitch's videoOffsetSeconds is where it ENDS (the press of the clip button), not
    # where it starts. Measured 2026-11-17 by matching the sound of 22 clips from 5 channels against their VODs: every
    # one started durationSeconds before videoOffsetSeconds (within 2 s, one 6 s). twitchclips.parse takes it as the start
    clips, seen = [], set()
    for n in pages["ASC"] + pages["DESC"]:
        off, dur = n.get("videoOffsetSeconds"), n.get("durationSeconds")
        if not n.get("id") or n["id"] in seen or str(((n.get("video") or {}).get("id")) or "") != str(vod) \
                or not isinstance(off, (int, float)) or off < 0 or not isinstance(dur, (int, float)) or dur <= 0:
            continue
        seen.add(n["id"])
        clips.append({"id": n["id"], "start": max(0.0, float(off) - float(dur)), "end": float(off), "views": int(n.get("viewCount") or 0),
                      "title": str(n.get("title") or "")[:140], "creator": ((n.get("curator") or {}).get("login")) or ""})
    clips.sort(key=lambda c: (c["start"], c["id"]))
    return {"vod": str(vod), "fetched": time.time(), "length": length, "source": "video.clips", "offset_is": "end",
            "complete": coverage([n.get("videoOffsetSeconds") for n in pages["ASC"]],
                                 [n.get("videoOffsetSeconds") for n in pages["DESC"]], length),
            "clips": clips}


def stretch(vod: str, length: int, seed: int = SEED, complete=None):
    """The stretch of a VOD to use, at random but the same every time for this seed and VOD: the whole VOD when it's
    under 3 hours and every clip of it is known, otherwise 60-120 minutes where every clip is known (shorter when
    only a shorter part is, down to 30 minutes). None when no part of 30 minutes is."""
    length = int(length)
    parts = [(int(math.ceil(a)), int(b)) for a, b in (complete or [[0, length]])]
    if parts == [(0, length)] and length < WHOLE_UNDER:
        return 0, length
    r = random.Random(f"{seed}:{vod}")
    w = r.randint(STRETCH_MIN, STRETCH_MAX)
    fits = [(a, b) for a, b in parts if b - a >= w] or [max(parts, key=lambda p: p[1] - p[0])]
    a0, b0 = fits[0] if len(fits) == 1 else r.choice(fits)
    w = min(w, b0 - a0)
    if w < STRETCH_SHORTEST:
        return None
    a = r.randint(a0, b0 - w)
    return a, a + w


def inside(a: float, b: float, complete: list) -> bool:
    return any(lo <= a and b <= hi for lo, hi in complete or [])


def video_ok(path: str, want: float) -> bool:
    """A downloaded stretch with sound, nearly as long as asked for (a section of a VOD Twitch muted parts of comes
    down as an empty 262-byte file)."""
    import engine
    try:
        info = engine.probe(path)
    except Exception:
        return False
    return bool(info.get("has_audio")) and float(info.get("duration") or 0) >= 0.95 * want - 5


def freeze_clips(vc: dict, a: int, b: int) -> dict:
    """The VOD's clips, kept to the stretch with times from its start."""
    import title_corpus as tc
    return {**vc, "clips": tc.clips_in(vc.get("clips") or [], a, b), "window": [a, b], "frozen": time.time()}


def _write(path: str, d, indent=None):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=indent)
    os.replace(tmp, path)


def build_one(e: dict, corpus: str, ytdlp: str, seed: int = SEED, log=print) -> dict:
    """Download and freeze one VOD's project (resuming one started before). {"vod", "error"} when it can't."""
    import emotes
    import engine
    import title_corpus as tc
    import twitchclips
    vod = e["vod"]
    v = tc.video_info(vod)
    if not v:
        return {"vod": vod, "error": "not found (deleted, or Twitch didn't answer)"}
    login = (v.get("owner") or {}).get("login", "")
    game = (v.get("game") or {}).get("name", "")
    length = int(v.get("lengthSeconds") or 0)
    # every clip Twitch knows of, frozen once for the VOD (viewers keep clipping, and the answer key mustn't move;
    # the stretch is picked from where it's complete, so it must be the same list on a run started again)
    rawp = os.path.join(corpus, "_clips", f"{vod}.json")
    vc = _read(rawp)
    if not isinstance(vc, dict):
        vc = vod_clips(vod)
        if vc is None:
            return {"vod": vod, "error": "couldn't read the viewers' clips"}
        os.makedirs(os.path.dirname(rawp), exist_ok=True)
        _write(rawp, vc, indent=1)
    if e.get("a") is not None:
        a, b = e["a"], e["b"]
        if length and b > length:
            return {"vod": vod, "error": f"the stretch {a}-{b} goes past the VOD's end ({length} s)"}
        if not inside(a, b, vc["complete"]):
            return {"vod": vod, "error": f"not every clip of {a}-{b} s is known (known: {vc['complete']}); leave the "
                                         "stretch out of the manifest to have one picked"}
    else:
        ab = stretch(vod, length, seed, vc["complete"])
        if ab is None:
            return {"vod": vod, "error": f"too many clips to know them all for 30 minutes anywhere (known: {vc['complete']})"}
        a, b = ab
    final = os.path.join(corpus, f"{login}_{vod}_{a}")
    if os.path.isfile(os.path.join(final, "built.json")):
        log(f"{vod}: already built ({os.path.basename(final)})")
        return dict(_read(os.path.join(final, "built.json"), {}), skipped=True)
    wd = final + ".part"
    os.makedirs(wd, exist_ok=True)
    log(f"{vod} {login} · {game} · {v.get('title', '')[:60]} · {a}-{b} s ({(b - a) / 60:.0f} min)")
    cpath = os.path.join(wd, twitchclips.FILE)
    if not os.path.exists(cpath):
        _write(cpath, freeze_clips(vc, a, b), indent=1)
    frozen = _read(cpath, {}) or {}
    epath = os.path.join(wd, "emotes.json")
    if not os.path.exists(epath):
        es = emotes.for_channel("", login)
        names: dict = {}
        for n, (svc, _kind) in es.by_name.items():
            names.setdefault(svc, []).append(n)
        _write(epath, names)
    # the video before the chat: a VOD that can't be downloaded says so in seconds, before minutes of chat
    video = os.path.join(wd, "video.mp4")
    try:
        tc.download_audio(vod, a, b, video, ytdlp)
    except (subprocess.SubprocessError, OSError, IndexError) as ex:
        return {"vod": vod, "error": f"couldn't download the video ({str(ex)[:120]}); run build again to retry"}
    if not video_ok(video, b - a):
        try:
            os.remove(video)
        except OSError:
            pass
        return {"vod": vod, "error": "the video came down empty or short. A VOD with parts Twitch muted for music can't be cut "
                                     "this way (yt-dlp's sections read nothing from its playlist), nor a subscriber-only one"}
    tc.save_chat(vod, a, b, os.path.join(wd, "chat.json"), login, game)
    cat = e["tags"].get("cat") or game
    engine._save_meta(wd, title=v.get("title", ""), streamer=login, streamer_display=(v.get("owner") or {}).get("displayName", ""),
                      game=game, platform="twitch", source=f"https://www.twitch.tv/videos/{vod}", start=a, end=b, vod=vod,
                      created=v.get("createdAt", ""), category=cat, holdout=e["holdout"], tags=e["tags"])
    ms = rm.moments(frozen.get("clips") or [])
    _, kept, _ = truth_of(wd, b - a)
    out = {"vod": vod, "name": os.path.basename(final), "window": [a, b], "built": datetime.date.today().isoformat(),
           "clips": len(frozen.get("clips") or []), "with_creator": sum(1 for c in frozen.get("clips") or [] if c.get("creator")),
           "moments": len(ms), "truth": len(rm.truth(kept)), "category": cat, "holdout": e["holdout"]}
    _write(os.path.join(wd, "built.json"), out, indent=1)
    os.replace(wd, final)
    log(f"  {out['clips']} clips ({out['with_creator']} with their creator), {out['moments']} moments, {out['truth']} truth"
        + ("" if out["truth"] >= MIN_TRUTH else f": fewer than {MIN_TRUTH}, so it won't be scored"))
    return out


def build(args) -> int:
    if os.path.abspath(args.corpus).startswith(ROOT + os.sep):
        print("Keep CORPUS outside the repository: chat and video are other people's.")
        return 2
    with open(args.manifest, encoding="utf-8") as f:
        entries = parse_manifest(f.read())
    if args.only:
        entries = [e for e in entries if e["vod"] in args.only]
    os.makedirs(args.corpus, exist_ok=True)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    done, failed = {}, []
    for e in entries:
        try:
            r = build_one(e, args.corpus, args.ytdlp, args.seed)
        except Exception as ex:          # one VOD going wrong doesn't stop the rest; run again to retry it
            r = {"vod": e["vod"], "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
        if r.get("error"):
            failed.append(r)
            print(f"{e['vod']}: {r['error']}", flush=True)
        else:
            done[e["vod"]] = r
    if args.write_manifest:
        write_manifest(args.manifest, entries, done)
    ok = [r for r in done.values() if r["truth"] >= MIN_TRUTH]
    print(f"{len(done)} of {len(entries)} built, {len(ok)} with at least {MIN_TRUTH} truth moments"
          + (f"; {len(failed)} failed (run again to retry): {', '.join(r['vod'] for r in failed)}" if failed else ""))
    return 1 if failed else 0


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
    sc.add_argument("--min-truth", type=int, default=MIN_TRUTH, help="truth moments a VOD needs to be scored")
    sc.add_argument("--holdout", action="store_true", help="include the VODs held out in the manifest (only once a change "
                                                           "is otherwise ready, docs/plans/ranking-eval.md section 8)")
    bp = sub.add_parser("build", help="download and freeze the corpus a manifest lists")
    bp.add_argument("corpus")
    bp.add_argument("--manifest", default=os.path.join(HERE, "ranking_corpus.txt"))
    bp.add_argument("--only", action="append", help="only this VOD id (repeatable)")
    bp.add_argument("--seed", type=int, default=SEED, help="picks the stretches the manifest doesn't give")
    bp.add_argument("--ytdlp", default=shutil.which("yt-dlp") or "yt-dlp")
    bp.add_argument("--write-manifest", action="store_true",
                    help="write each stretch, the build date and the counts back into the manifest")
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
    if a.cmd == "build":
        return build(a)
    if a.ablate and a.baseline:
        ap.error("--ablate runs on this checkout only: run it without --baseline")
    return score(a)


if __name__ == "__main__":
    sys.exit(main())
