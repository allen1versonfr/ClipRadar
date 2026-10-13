"""
How good is the clip finder on a real stream?

Takes a Twitch VOD, uses the clips viewers made of it as the "right answers"
(Twitch records where in the VOD each clip came from, so the moments people
clipped most are known highlights), runs VodClipper's clip finder on the same
stream, and scores it:

  precision   how many of its picks overlap a moment viewers clipped
  recall      how many of the most-clipped moments it found
  boundaries  whether its clips contain the whole clipped moment, and how long they are

It also runs the old 3.0 method (raw chat counts + loudness, fixed 30 s clips)
and a few variants on the same data, so changes can be compared on equal terms.

  python tests/eval_vod.py --vod 2848966623 --media media/ --out eval/

Needs network access to Twitch (GitHub Actions has it), ffmpeg, yt-dlp,
TwitchDownloaderCLI and the laughter detector model in src/models.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402

CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def note(title, msg, level="notice"):
    msg = str(msg).replace("%", "%25").replace("\r", "").replace("\n", " | ")
    print(f"::{level} title={title}::{msg}", flush=True)


# ------------------------------------------------------------------ Twitch data

def gql(query: str, variables: dict = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request("https://gql.twitch.tv/gql", data=body,
                                 headers={"Client-ID": CID, "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if isinstance(out, dict) and out.get("errors"):
        raise RuntimeError(f"Twitch GQL: {out['errors'][:2]}")
    return out["data"]


def video_info(vod: str) -> dict:
    d = gql("query($id: ID!) { video(id: $id) { id title lengthSeconds createdAt owner { login } } }", {"id": vod})
    return d["video"]


CLIP_FIELDS = "id slug title viewCount durationSeconds videoOffsetSeconds createdAt video { id }"


def clips_of(login: str, vod: str, since: str, max_pages: int = 100) -> list:
    """Every clip of this VOD that Twitch will list. Tries a few ways of asking, since Twitch's
    unofficial API changes; the first that works is used."""
    variants = [
        ("video", "{sort: VIEWS_DESC}"),
        ("video", "{sort: VIEWS_DESC, period: ALL_TIME}"),
        ("video", "{filter: ALL_TIME}"),
        ("video", None),
        ("criteria", "{filter: ALL_TIME, sort: CREATED_AT_DESC}"),
        ("criteria", "{period: ALL_TIME, sort: CREATED_AT_DESC}"),
        ("criteria", "{filter: LAST_MONTH}"),
        ("criteria", "{filter: ALL_TIME}"),
        ("criteria", "{period: ALL_TIME, sort: VIEWS_DESC}"),
    ]
    found: dict = {}
    for kind, crit in variants:
        got, pages, err = 0, 0, ""
        try:
            after = None
            for _ in range(max_pages):
                if kind == "video":
                    args = "first: 100, after: $after" + (f", criteria: {crit}" if crit else "")
                    q = ("query($id: ID!, $after: Cursor) { video(id: $id) { clips(" + args + ") { "
                         "pageInfo { hasNextPage } edges { cursor node { " + CLIP_FIELDS + " } } } } }")
                    d = gql(q, {"id": vod, "after": after})
                    cl = (d.get("video") or {}).get("clips") or {}
                else:
                    q = ("query($login: String!, $after: Cursor) { user(login: $login) { clips(first: 100, after: $after, "
                         f"criteria: {crit}) {{ pageInfo {{ hasNextPage }} edges {{ cursor node {{ " + CLIP_FIELDS + " } } } } }")
                    d = gql(q, {"login": login, "after": after})
                    cl = (d.get("user") or {}).get("clips") or {}
                edges = cl.get("edges") or []
                pages += 1
                for e in edges:
                    n = e["node"]
                    if (n.get("video") or {}).get("id") == vod and n.get("videoOffsetSeconds") is not None:
                        if n["id"] not in found:
                            got += 1
                        found[n["id"]] = n
                if not edges or not cl.get("pageInfo", {}).get("hasNextPage"):
                    break
                after = edges[-1]["cursor"]
                if "CREATED_AT_DESC" in (crit or "") and edges[-1]["node"]["createdAt"] < since:
                    break
                time.sleep(0.15)
        except Exception as e:
            err = str(e)[:200]
        print(f"clips via {kind} {crit or ''}: {got} new of this VOD in {pages} pages {('ERROR ' + err) if err else ''}",
              flush=True)
        if len(found) >= 1000:
            break
    return list(found.values())


def highlights(clips: list, gap: float = 8.0) -> list:
    """Clips of the same moment overlap: merge them into highlights, ranked by total views."""
    iv = sorted((float(c["videoOffsetSeconds"]), float(c["videoOffsetSeconds"]) + float(c["durationSeconds"] or 30),
                 int(c["viewCount"] or 0), c.get("title", "")) for c in clips)
    out = []
    for a, b, v, t in iv:
        if out and a <= out[-1]["end"] + gap:
            h = out[-1]
            h["end"] = max(h["end"], b)
            h["views"] += v
            h["clips"] += 1
            if v > h["top_views"]:
                h["top_views"], h["title"] = v, t
        else:
            out.append({"start": a, "end": b, "views": v, "clips": 1, "top_views": v, "title": t})
    out.sort(key=lambda h: -h["views"])
    for i, h in enumerate(out, 1):
        h["rank"] = i
    return out


# ------------------------------------------------------------------ media

def download(vod: str, media: str) -> tuple:
    os.makedirs(media, exist_ok=True)
    audio = next((os.path.join(media, f) for f in os.listdir(media) if f.startswith("audio.") and not f.endswith(".part")), "")
    if not audio:
        t = time.time()
        subprocess.run(["yt-dlp", "-f", "Audio_Only/audio_only/bestaudio/worst", "-N", "16", "--no-part",
                        "-o", os.path.join(media, "audio.%(ext)s"), f"https://www.twitch.tv/videos/{vod}"], check=True)
        audio = next(os.path.join(media, f) for f in os.listdir(media) if f.startswith("audio."))
        note("download audio", f"{os.path.getsize(audio) / 1e6:.0f} MB in {time.time() - t:.0f}s")
    chat = os.path.join(media, "chat.json")
    if not os.path.exists(chat):
        t = time.time()
        tdl = shutil.which("TwitchDownloaderCLI") or os.path.join(HERE, "..", "tools", "TwitchDownloaderCLI")
        subprocess.run([tdl, "chatdownload", "--id", vod, "-o", chat, "-t", "16", "--collision", "Overwrite"], check=True)
        note("download chat", f"{os.path.getsize(chat) / 1e6:.0f} MB in {time.time() - t:.0f}s")
    return audio, chat


def duration(path: str) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    return float(p.stdout.strip())


# ------------------------------------------------------------------ methods

def old_rate(chat: list, n: int, delay: float = 6.0) -> np.ndarray:
    """3.0: every message counts by its hype words, nothing filtered."""
    rate = np.zeros(n, dtype=np.float32)
    for m in chat:
        s = int(m[0] - delay)
        if 0 <= s < n:
            rate[s] += engine.chat_weight(m[1])
    return rate


def score(picks: list, hl: list, top: int = 20) -> dict:
    """picks: [(start, end)] in rank order."""
    def overlaps(p, h):
        return p[0] < h["end"] and p[1] > h["start"]
    out = {"picks": len(picks)}
    hit = [any(overlaps(p, h) for h in hl) for p in picks]
    for k in (5, 10, 20, 30):
        if len(picks) >= k:
            out[f"precision@{k}"] = round(sum(hit[:k]) / k, 3)
    out["precision_all"] = round(sum(hit) / max(1, len(picks)), 3)
    best = hl[:top]
    found = [h for h in best if any(overlaps(p, h) for p in picks)]
    out[f"recall_top{top}"] = round(len(found) / max(1, len(best)), 3)
    out[f"recall_top{top}_by_views"] = round(sum(h["views"] for h in found) / max(1, sum(h["views"] for h in best)), 3)
    # boundaries: of the matched pairs, how much of the clipped moment is inside the pick
    cover, lens = [], []
    for p in picks:
        hs = [h for h in hl if overlaps(p, h)]
        if hs:
            h = max(hs, key=lambda h: h["views"])
            a, b = max(p[0], h["start"]), min(p[1], h["end"])
            cover.append((b - a) / max(1.0, h["end"] - h["start"]))
        lens.append(p[1] - p[0])
    out["moment_inside_pick"] = round(float(np.mean(cover)), 3) if cover else None
    out["median_length"] = round(float(np.median(lens)), 1) if lens else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vod", required=True)
    ap.add_argument("--media", default="media")
    ap.add_argument("--out", default="eval")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    media = os.path.join(a.media, a.vod)
    os.makedirs(media, exist_ok=True)

    info = video_info(a.vod)
    login = info["owner"]["login"]
    note("VOD", f"{info['title']} | {login} | {engine.fmt_ts(info['lengthSeconds'])} | {info['createdAt']}")
    clips_file = os.path.join(a.out, f"clips_{a.vod}.json")
    if os.path.exists(clips_file):
        clips = json.load(open(clips_file, encoding="utf-8"))
    else:
        clips = clips_of(login, a.vod, info["createdAt"])
        json.dump(clips, open(clips_file, "w", encoding="utf-8"))
    hl = highlights(clips)
    note("labels", f"{len(clips)} viewer clips of this VOD -> {len(hl)} distinct moments; top: "
         + "; ".join(f"{engine.fmt_ts(h['start'])} {h['views']} views '{h['title'][:40]}'" for h in hl[:8]))
    if len(hl) < 5:
        note("labels", "too few viewer clips to score against", "warning")

    audio, chat_path = download(a.vod, media)
    dur = duration(audio)
    n = int(math.ceil(dur)) + 1
    import audio_events
    mp = audio_events.model_path()
    note("detector", f"available={audio_events.available()} model={mp} size={mp.stat().st_size if mp else 0}")
    t = time.time()
    loud, fine, ev = engine.measure_audio(audio, dur, events=True, log=lambda m: note("measure", m, "warning"))
    t_audio = time.time() - t
    chat = engine.load_chat(chat_path)
    note("analysis", f"{engine.fmt_ts(dur)} of audio measured in {t_audio:.0f}s "
         f"({dur / max(t_audio, 1):.0f}x real time, laughter detector {'on' if ev else 'MISSING'}); {len(chat):,} chat messages")

    K = max(10, min(30, len(hl)))
    results = {}
    # Twitch only lets us list part of a VOD's clips, so score only the stretch the labels cover
    span = max(h["end"] for h in hl) + 180 if hl else dur
    limit = int(min(dur, span))
    if limit < dur - 60:
        note("window", f"viewer clips cover only the first {engine.fmt_ts(limit)}; scoring that part of the stream")
    K = min(K, max(5, len(hl)))

    def run(name, an, length, count):
        if limit < dur - 60:
            an = an.part(0, limit)
        cl = engine.pick_clips(an, count=count, length=length, chat=chat)
        results[name] = score([(c.start, c.end) for c in cl], hl)
        results[name]["clips"] = [{"start": c.start, "end": c.end, "score": c.score, "reason": c.reason} for c in cl]
        return cl

    # 3.0: raw chat counts + loudness, fixed 30 s
    an_old = engine.score_signals(loud, [], n, dur, rate=old_rate(chat, n), stats={})
    run("3.0 (raw chat + loudness, 30 s)", an_old, 30, K)
    # 3.1 pieces, one at a time
    an_chat = engine.score_signals(loud, chat, n, dur, fine=fine)
    run("3.1 chat cleaning only", an_chat, 30, K)
    an_new = engine.score_signals(loud, chat, n, dur, events=ev, fine=fine)
    run("3.1 + laughter/screams, 30 s", an_new, 30, K)
    run("3.1 full (smart length)", an_new, 0, K)
    est = engine.estimate(an_new)
    run("3.1 automatic count", an_new, 0, 0)
    an_audio = engine.score_signals(loud, [], n, dur, events=ev, fine=fine)
    run("3.1 without chat", an_audio, 0, K)

    rows = []
    for name, r in results.items():
        rows.append(f"| {name} | {r['picks']} | {r.get('precision@10', '-')} | {r.get('precision_all')} | "
                    f"{r['recall_top20']} | {r['recall_top20_by_views']} | {r['moment_inside_pick']} | {r['median_length']} |")
        note(f"score: {name}", {k: v for k, v in r.items() if k != "clips"})
    md = [f"# Clip finder on {login}, VOD {a.vod}", "",
          f"{info['title']} ({engine.fmt_ts(dur)}). {len(clips)} viewer clips, {len(hl)} distinct clipped moments. "
          f"Estimate: {est}.", "",
          "| method | picks | precision@10 | precision (all) | recall top 20 | recall top 20 (by views) | moment inside pick | median length s |",
          "|---|---|---|---|---|---|---|---|", *rows, "", "## Most-clipped moments", ""]
    for h in hl[:30]:
        found = [k for k, r in results.items() if any(c["start"] < h["end"] and c["end"] > h["start"] for c in r["clips"])]
        md.append(f"- {engine.fmt_ts(h['start'])}-{engine.fmt_ts(h['end'])} · {h['views']} views, {h['clips']} clips · "
                  f"{h['title'][:60]} · found by: {', '.join(found) if found else 'none'}")
    full = results["3.1 full (smart length)"]
    md += ["", "## 3.1 picks that no viewer clipped", ""]
    for c in full["clips"]:
        if not any(c["start"] < h["end"] and c["end"] > h["start"] for h in hl):
            md.append(f"- {engine.fmt_ts(c['start'])}-{engine.fmt_ts(c['end'])} score {c['score']} · {c['reason']}")
    open(os.path.join(a.out, f"report_{a.vod}.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    json.dump({"vod": a.vod, "info": info, "highlights": hl, "results": results, "estimate": est},
              open(os.path.join(a.out, f"report_{a.vod}.json"), "w", encoding="utf-8"), indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
