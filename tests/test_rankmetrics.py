"""
The ranking metrics (scripts/rankmetrics.py, docs/plans/ranking-eval.md section 4) on hand-made clip lists: viewers'
clips grouped into moments, which of Clip Radar's clips find them, and every number the report gives. No network,
no video.

  python tests/test_rankmetrics.py
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
ROOT = os.path.normpath(os.path.join(HERE, ".."))
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rankmetrics as rm  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def vc(start, end, views=0, creator="", title=""):
    return {"start": start, "end": end, "views": views, "creator": creator, "title": title}


def clip(start, end):
    return {"start": start, "end": end}


near = lambda a, b: a is not None and abs(a - b) < 1e-9  # noqa: E731

# ---------------------------------------------------------------- grouping clips into moments
ms = rm.moments([vc(100, 130, 50, "a"), vc(120, 150, 900, "b"), vc(140, 170, 10, "c")])
check("overlapping clips merge into one moment (a chain of three too)", len(ms) == 1, ms)
m = ms[0]
check("the span is the union of the clips", (m["start"], m["end"]) == (100, 170), m)
check("the payoff is the last 10 s of the most viewed clip", m["payoff"] == [140, 150], m)
check("clippers and views add up", (m["clippers"], m["views"], m["clips"]) == (3, 960, 3), m)

ms = rm.moments([vc(100, 130, 5, "a"), vc(135, 148, 5, "b")])
check("clips that don't overlap but end within 20 s are one moment", len(ms) == 1 and ms[0]["end"] == 148, ms)
ms = rm.moments([vc(100, 130, 5, "a"), vc(140, 151, 5, "b")])
check("ends 21 s apart, no overlap: two moments", len(ms) == 2, ms)
ms = rm.moments([vc(0, 30, 5, "a"), vc(30, 60, 5, "b"), vc(200, 230, 5, "c")])
check("clips that only touch, ends 30 s apart: separate", len(ms) == 3, ms)

ms = rm.moments([vc(100, 130, 5, "a"), vc(105, 131, 5, "a"), vc(110, 132, 5, "")])
check("the same person twice counts once; a clip without a creator counts as one more", ms[0]["clippers"] == 2, ms)

ms = rm.moments([vc(100, 106, 9000, "a"), vc(95, 120, 10, "b")])
check("a clip shorter than 10 s: the payoff is the whole clip", ms[0]["payoff"] == [100, 106], ms)

ms = rm.moments([vc(100, 130, 500, "a"), vc(110, 140, 500, "b")])
check("tie for most viewed: the clip that ends later sets the payoff", ms[0]["payoff"] == [130, 140], ms)

ms = rm.moments([vc(50, 40, 5), {"start": "x", "end": None}, vc(10, 40, 3)])
check("clips without a length (or with junk times) are left out", len(ms) == 1 and ms[0]["clips"] == 1, ms)
check("no clips: no moments", rm.moments([]) == [] and rm.moments(None) == [])

# ---------------------------------------------------------------- which moments count
ms = rm.moments([vc(0, 30, 10, "a"),                                       # one person, few views
                 vc(100, 130, 600, "b"),                                   # one person, 600 views
                 vc(200, 230, 1, "c"), vc(205, 232, 1, "d"),               # two people
                 vc(300, 330, 1, "e"), vc(301, 331, 1, "f"), vc(302, 332, 1, "g")])   # three
check("main: two clippers or 500 views", [m["start"] for m in rm.truth(ms)] == [100, 200, 300])
check("loose: every moment", len(rm.truth(ms, "loose")) == 4)
check("strict: three clippers or 2000 views", [m["start"] for m in rm.truth(ms, "strict")] == [300])

# ---------------------------------------------------------------- matching
m = rm.moments([vc(100, 130, 600)])[0]                                     # payoff 120-130
check("a clip ending exactly where the payoff starts misses it", not rm.finds(clip(60, 120), m))
check("a payoff just after a clip is a miss", not rm.finds(clip(60, 119.9), m))
check("a clip starting where the payoff ends misses it", not rm.finds(clip(130, 160), m))
check("one second into the payoff finds it, but doesn't keep it", rm.finds(clip(60, 121), m) and not rm.keeps(clip(60, 121), m))
check("a clip around the whole payoff keeps it", rm.keeps(clip(118, 131), m) and rm.keeps(clip(120, 130), m))
check("a clip inside the span but before the payoff doesn't find it", not rm.finds(clip(100, 115), m))

# ---------------------------------------------------------------- one VOD
viewers = [vc(100, 130, 1000, "a"), vc(110, 131, 50, "b"),               # A: payoff 120-130, 1050 views
           vc(400, 430, 3000, "c"), vc(402, 432, 10, "d"),               # B: payoff 420-430, the biggest
           vc(700, 730, 600, "e"),                                       # C: one clipper, 600 views
           vc(900, 930, 3, "f")]                                         # D: one clipper, 3 views (not truth)
ranked = [clip(115, 135),        # finds A, keeps it
          clip(118, 125),        # finds A again (counted once)
          clip(1000, 1040),      # nothing near
          clip(890, 925),        # finds D, which isn't in the main set
          clip(410, 425),        # finds B, cuts the payoff
          clip(0, 20)]
s = rm.score_vod(ranked, viewers)
check("three truth moments of four", (s["truth"], s["moments"]) == (3, 4), s)
check("recall@5: A and B of three", near(s["recall@5"], 2 / 3), s["recall@5"])
check("one moment found by two clips counts once", near(s["recall@10"], 2 / 3), s["recall@10"])
check("recall@n when fewer clips than n: all of them count", near(s["recall@20"], 2 / 3))
w = [math.log2(1 + 1050), math.log2(1 + 3010), math.log2(1 + 600)]
check("weighted recall weights each moment by log2(1 + views)", near(s["wrecall@10"], (w[0] + w[1]) / sum(w)), s["wrecall@10"])
check("precision@10: clips overlapping any viewer clip, one-clipper ones too", near(s["precision@10"], 4 / 6), s["precision@10"])
check("payoff kept: A whole, B cut", near(s["payoff_kept"], 0.5), s["payoff_kept"])
check("first hit: the biggest moment (B) comes 5th", s["first_hit"] == 5, s["first_hit"])
check("the stricter set is reported too", "recall@10_strict" in s and "recall@10_loose" in s, s)
check("the loose set includes D: 3 of 4", near(s["recall@10_loose"], 0.75), s["recall@10_loose"])

ranked1 = [clip(0, 20)] * 5 + [clip(415, 431)]
check("recall@5 misses what only the 6th clip finds", rm.recall(ranked1, rm.truth(rm.moments(viewers)), 5) == 0)
check("first hit beyond the top 10 is still its rank", rm.score_vod([clip(0, 20)] * 12 + [clip(415, 431)], viewers)["first_hit"] == 13)
check("never found: first hit is None", rm.score_vod([clip(0, 20)], viewers)["first_hit"] is None)

s = rm.score_vod(ranked, viewers, auto=[clip(115, 135), clip(0, 20)])
check("Automatic: how many it suggested and what it found", s["auto_count"] == 2 and near(s["auto_recall"], 1 / 3), s)

miss = rm.misses(ranked1, rm.truth(rm.moments(viewers)), 5)
check("misses, most viewed first, with where they came", [(m["views"], m["rank"]) for m in miss] == [(3010, 6), (1050, None), (600, None)],
      [(m["views"], m["rank"]) for m in miss])

# ---------------------------------------------------------------- nothing to find
s = rm.score_vod(ranked, [vc(900, 930, 3, "f")])
check("an empty truth set: recall, payoff and first hit are None, not 0",
      s["truth"] == 0 and s["recall@10"] is None and s["wrecall@10"] is None and s["payoff_kept"] is None and s["first_hit"] is None, s)
check("precision is still measured", near(s["precision@10"], 1 / 6), s["precision@10"])
s = rm.score_vod([], viewers)
check("no clips at all: recall 0, precision None", s["recall@10"] == 0 and s["precision@10"] is None, s)
check("moments with no views: weighted recall is None",
      rm.weighted_recall([clip(0, 100)], rm.moments([vc(0, 30, 0, "a"), vc(5, 31, 0, "b")]), 10) is None)
check("payoff kept with nothing found is None", rm.payoff_kept([clip(0, 5)], rm.truth(rm.moments(viewers)), 10) is None)

# ---------------------------------------------------------------- across VODs
per = [{"recall@10": 0.5, "first_hit": 2}, {"recall@10": 1.0, "first_hit": None}, {"recall@10": None}]
sm = rm.summarize(per, ["recall@10", "first_hit"])
check("each VOD counts once, None left out", near(sm["recall@10"]["mean"], 0.75) and sm["recall@10"]["n"] == 2, sm)
check("spread: sd, min, max", near(sm["recall@10"]["sd"], 0.25) and sm["recall@10"]["min"] == 0.5 and sm["recall@10"]["max"] == 1.0, sm)
check("a metric with one value has sd 0", sm["first_hit"]["sd"] == 0.0 and sm["first_hit"]["n"] == 1, sm)
check("a metric no VOD has: None", rm.summarize([{}], ["recall@10"])["recall@10"]["mean"] is None)
d = rm.diff({"recall@10": {"mean": 0.6}, "x": {"mean": 1}}, {"recall@10": {"mean": 0.5}})
check("diff: new minus base, None where one side lacks it", near(d["recall@10"], 0.1) and d["x"] is None, d)

# ---------------------------------------------------------------- eval_ranking.py score on a synthetic VOD
# Ten minutes of quiet noise with four loud stretches that chat laughs at, which viewers clipped (two people each),
# and one quiet stretch three people clipped with 5,000 views each, where nothing happens. If the viewers' clips
# were mixed into the ranking, the quiet one would be near the top.
LOUD, QUIET, DUR = (120, 250, 400, 500), 320, 600


def synthetic(wd: str, ff: str):
    os.makedirs(wd, exist_ok=True)
    vol = "+".join(f"between(t,{t},{t + 4})*0.9" for t in LOUD)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:size=160x90:rate=10:duration={DUR}",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:duration={DUR}:seed=7",
                    "-filter_complex", f"[1:a]volume='0.03+{vol}':eval=frame[a]", "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", os.path.join(wd, "video.mp4")],
                   check=True)
    rnd = random.Random(3)
    com = []
    for t in range(DUR):
        k = 12 if any(a + 2 <= t <= a + 12 for a in LOUD) else (1 if rnd.random() < 0.5 else 0)
        for j in range(k):
            body = "KEKW LUL" if k > 1 else rnd.choice(["hi", "what game is this", "ok", "lol"])
            com.append({"content_offset_seconds": t + j / k, "commenter": {"name": f"u{rnd.randint(0, 300)}"},
                        "message": {"body": body}})
    with open(os.path.join(wd, "chat.json"), "w", encoding="utf-8") as f:
        json.dump({"streamer": {"name": "synthetic"}, "comments": com}, f)
    clips = []
    for i, a in enumerate(LOUD):
        clips += [{"start": a - 22, "end": a + 8, "views": 800, "title": f"loud {i}", "creator": f"c{i}a"},
                  {"start": a - 20, "end": a + 9, "views": 40, "title": f"loud {i} again", "creator": f"c{i}b"}]
    clips += [{"start": QUIET - 25 + k, "end": QUIET + 5 + k, "views": 5000, "title": "quiet | but clipped", "creator": f"q{k}"}
              for k in range(3)]
    with open(os.path.join(wd, "twitch_clips.json"), "w", encoding="utf-8") as f:
        json.dump({"vod": "1234567890", "fetched": 0, "clips": clips}, f)
    with open(os.path.join(wd, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"vod": "1234567890", "start": 3600, "game": "Synthetic"}, f)


ff = shutil.which("ffmpeg") or next((p for p in (os.path.join(ROOT, "tools", "ffmpeg.exe"),) if os.path.isfile(p)), None)
if not ff or not shutil.which("ffprobe") and not os.path.isfile(os.path.join(ROOT, "tools", "ffprobe.exe")):
    print("[skip] eval_ranking.py score on a synthetic VOD: no FFmpeg")
else:
    tmp = tempfile.mkdtemp(prefix="rank_test_")
    corpus, out = os.path.join(tmp, "corpus"), os.path.join(tmp, "out")
    synthetic(os.path.join(corpus, "synthetic_1"), ff)
    run = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "eval_ranking.py"), "score", corpus, "--out", out,
                          "--ablate"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("score runs", run.returncode == 0, run.stdout[-500:] + run.stderr[-1500:])
    rep = json.load(open(os.path.join(out, "report.json"), encoding="utf-8")) if run.returncode == 0 else {}
    side = (rep.get("side") or {}).get("projects") or [{}]
    per = ((rep.get("eval") or {}).get("per_vod") or [{}])[0]
    check("the viewers' clips aren't part of the score", "viewers" not in side[0].get("parts", ["viewers"]), side[0].get("parts"))
    check("and no clip's reason mentions them", not any("clipped by" in c["reason"] for c in side[0].get("fixed", [])))
    check("five truth moments: four loud, one quiet", per.get("truth") == 5, per)
    top4 = side[0].get("fixed", [])[:4]
    found = [m for m in rm.truth(rm.moments(json.load(open(os.path.join(corpus, "synthetic_1", "twitch_clips.json")))["clips"]))
             if any(rm.finds(c, m) for c in top4)]
    check("the top four clips are the four loud moments", sorted(m["title"] for m in found) == [f"loud {i}" for i in range(4)],
          [m["title"] for m in found])
    check("the quiet moment, the most viewed, isn't among them", (per.get("first_hit") or 99) > 4, per.get("first_hit"))
    check("recall@5 is 4 of 5", near(per.get("recall@5"), 0.8), per.get("recall@5"))
    check("Automatic suggests the four", per.get("auto_count") == 4, per.get("auto_count"))
    md = open(os.path.join(out, "report.md"), encoding="utf-8").read() if run.returncode == 0 else ""
    check("the report says what it measures", "isn't a measure of what will do well on TikTok" in md)
    check("the report has the headline, per-VOD and ablation tables",
          all(h in md for h in ("Recall@10 (headline)", "## Per VOD", "## Each part switched off", "| chat |")), md[:400])
    chat_off = next((r["diff"] for r in rep.get("ablation", []) if r["part"] == "chat"), {})
    check("ablation: with chat off it finds fewer moments", (chat_off.get("recall@10") or 0) < 0, chat_off)

    # the control: the app's own path, with the viewers' clips loaded, ranks the quiet moment higher. So the checks
    # above would catch them leaking into the score
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import detect  # noqa: E402
    import engine  # noqa: E402
    wd = os.path.join(corpus, "synthetic_1")
    chat = engine.load_chat(os.path.join(wd, "chat.json"), {})
    an = engine.analyze_cached(wd, os.path.join(wd, "video.mp4"), chat, chat_delay="auto", events=True,
                               weights=detect.preset("any"))
    with_viewers = engine.pick_clips(an, count=20, chat=chat, chat_delay=an.chat_delay)
    quiet = [m for m in rm.moments(json.load(open(os.path.join(wd, "twitch_clips.json")))["clips"]) if m["title"].startswith("quiet")][0]
    hit = rm.first_rank([{"start": c.start, "end": c.end} for c in with_viewers], quiet)
    check("control: with the viewers' clips mixed in, the quiet moment comes higher",
          "viewers" in an.parts and hit is not None and hit < (per.get("first_hit") or 99), (sorted(an.parts), hit, per.get("first_hit")))

    run = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "eval_ranking.py"), "score", corpus, "--out", out,
                          "--baseline", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("--baseline HEAD runs", run.returncode == 0, run.stdout[-500:] + run.stderr[-1500:])
    rep = json.load(open(os.path.join(out, "report.json"), encoding="utf-8")) if run.returncode == 0 else {}
    d = rm.diff((rep.get("eval") or {}).get("summary") or {}, (rep.get("baseline_eval") or {}).get("summary") or {})
    check("the same src/ on both sides: no change", d and all(v is None or abs(v) < 1e-9 for v in d.values()), d)
    md = open(os.path.join(out, "report.md"), encoding="utf-8").read() if run.returncode == 0 else ""
    check("the report has the A/B table", "## Against `HEAD`" in md and "baseline → new" in md)
    shutil.rmtree(tmp, ignore_errors=True)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
