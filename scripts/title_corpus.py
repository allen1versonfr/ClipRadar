"""
Build saved Clip Radar projects from public Twitch VODs, for scripts/eval_titles.py. A developer tool, not shipped.

For each VOD it takes one stretch (20-40 minutes), and:

  1. reads the VOD's chat from Twitch's GQL by stepping video.comments(contentOffsetSeconds: N) through the
     stretch (paging by cursor is refused to anonymous clients),
  2. downloads only that stretch with yt-dlp (--download-sections, the 160p rendition: the engine wants a video),
  3. reads the clips viewers made of the VOD (twitchclips.fetch),
  4. finds the clips the way the app does (engine.analyze_cached with chat, viewers' clips mixed in, then
     engine.pick_clips),
  5. has Whisper listen to each clip (engine._hear, faster-whisper on the CPU), so each clip has its words,

and writes a project folder (project.json, meta.json, chat.json, twitch_clips.json, video.mp4) under OUT.
Times are from the start of the stretch, as if the stretch were the whole video.

  python scripts/title_corpus.py OUT --vod 2910903624@7200-9600 --vod 2921768812
  python scripts/title_corpus.py OUT --vod 2921768812 --minutes 30 --clips 12 --whisper small

Without @start-end it picks the stretch with the most viewer clips in it (or the middle of the VOD). Keep OUT
outside the repository: chat and sound are other people's, never committed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))


def gql(query: str, tries: int = 4) -> dict:
    import twitchclips
    for k in range(tries):
        try:
            d = twitchclips._gql(query, 20)
            if d.get("data") is not None:
                return d
        except Exception:
            pass
        time.sleep(1.5 * (k + 1))
    return {}


def video_info(vod: str) -> dict:
    d = gql('query{video(id:"%s"){title lengthSeconds createdAt game{name} owner{login displayName}}}' % vod)
    return ((d.get("data") or {}).get("video")) or {}


def _page(vod: str, at: int) -> list:
    q = ('query{video(id:"%s"){comments(contentOffsetSeconds:%d){edges{node{id contentOffsetSeconds '
         'commenter{login displayName} message{fragments{text}}}}}}}' % (vod, at))
    d = gql(q)
    return ((((d.get("data") or {}).get("video") or {}).get("comments") or {}).get("edges")) or []


def chat(vod: str, a: int, b: int, workers: int = 8) -> list:
    """Every comment from a to b seconds, stepping by offset: each page holds a few seconds of chat."""
    def span(lo: int, hi: int) -> list:
        got, at = {}, lo
        while at < hi:
            edges = _page(vod, at)
            if not edges:
                at += 5
                continue
            last = at
            for e in edges:
                n = e.get("node") or {}
                off = int(n.get("contentOffsetSeconds") or 0)
                if lo <= off < hi and n.get("id"):
                    got[n["id"]] = n
                last = max(last, off)
            at = last + 1 if last > at else at + 1
        return list(got.values())
    step = max(30, (b - a) // (workers * 4))
    parts = [(x, min(b, x + step)) for x in range(a, b, step)]
    with ThreadPoolExecutor(workers) as ex:
        found = [n for p in ex.map(lambda r: span(*r), parts) for n in p]
    uniq = {n["id"]: n for n in found}
    return sorted(uniq.values(), key=lambda n: n["contentOffsetSeconds"])


def pick_window(clips: list, length: int, minutes: int) -> tuple:
    w = minutes * 60
    if length <= w:
        return 0, length
    best, at = -1.0, max(0, length // 2 - w // 2)
    for c in clips:
        for a in (int(c["start"]) - 120, int(c["start"]) - w + 180):
            a = max(0, min(length - w, a))
            s = sum(1 + (x["views"] >= 500) for x in clips if a + 30 <= x["start"] and x["end"] <= a + w - 30)
            if s > best:
                best, at = s, a
    return at, at + w


def download_audio(vod: str, a: int, b: int, out: str, ytdlp: str):
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return
    tmp = out + ".part.%(ext)s"
    cmd = [ytdlp, "-q", "--no-warnings", "-f", "160p30/160p/worst", "--download-sections", f"*{a}-{b}",
           "--remux-video", "mp4", "-o", tmp,
           f"https://www.twitch.tv/videos/{vod}"]
    subprocess.run(cmd, check=True, timeout=1800)
    made = [f for f in os.listdir(os.path.dirname(out)) if f.startswith(os.path.basename(out) + ".part")]
    shutil.move(os.path.join(os.path.dirname(out), made[0]), out)


def build(vod: str, window: tuple, args, model) -> str:
    import engine
    import twitchclips
    v = video_info(vod)
    if not v:
        print(f"{vod}: not found")
        return ""
    login = (v.get("owner") or {}).get("login", "")
    disp = (v.get("owner") or {}).get("displayName", "") or login
    game = (v.get("game") or {}).get("name", "")
    length = int(v.get("lengthSeconds") or 0)
    vc = twitchclips.fetch(vod) or {"clips": []}
    a, b = window if window else pick_window(vc["clips"], length, args.minutes)
    wd = os.path.join(args.out, f"{login}_{vod}_{a}")
    os.makedirs(wd, exist_ok=True)
    print(f"{vod} {login} · {game} · {v.get('title', '')[:60]} · {a}-{b} s", flush=True)
    cpath = os.path.join(wd, "chat.json")
    if not os.path.exists(cpath):
        t0 = time.time()
        nodes = chat(vod, max(0, a - 5), b + 40)
        comments = [{"content_offset_seconds": n["contentOffsetSeconds"] - a,
                     "commenter": {"name": ((n.get("commenter") or {}).get("login") or ""),
                                   "display_name": ((n.get("commenter") or {}).get("displayName") or "")},
                     "message": {"body": "".join(f.get("text") or "" for f in (n.get("message") or {}).get("fragments") or [])}}
                    for n in nodes]
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"streamer": {"name": login}, "video": {"game": game}, "comments": comments}, f)
        print(f"  chat: {len(comments):,} messages in {time.time() - t0:.0f} s", flush=True)
    mine = [dict(c, start=c["start"] - a, end=c["end"] - a) for c in vc["clips"] if a <= c["start"] and c["end"] <= b]
    with open(os.path.join(wd, twitchclips.FILE), "w", encoding="utf-8") as f:
        json.dump({**vc, "clips": mine}, f, indent=1)
    audio = os.path.join(wd, "video.mp4")
    download_audio(vod, a, b, audio, args.ytdlp)
    engine._save_meta(wd, title=v.get("title", ""), streamer=login, streamer_display=disp, game=game, platform="twitch",
                      source=f"https://www.twitch.tv/videos/{vod}", start=a, vod=vod)
    info: dict = {}
    msgs = engine.load_chat(cpath, info)
    an = engine.analyze_cached(wd, audio, msgs, chat_delay="auto", events=False)
    engine._save_meta(wd, chat_delay=float(an.chat_delay))
    clips = engine.pick_clips(an, count=args.clips, chat=msgs, chat_delay=an.chat_delay)
    clips.sort(key=lambda c: c.start)
    out = []
    for c in clips:
        samples = engine.extract_audio(audio, c.start, c.end - c.start)
        words = []
        if samples is not None and len(samples):
            words = [{"start": round(w["start"] + c.start, 2), "end": round(w["end"] + c.start, 2), "word": w["word"]}
                     for w in engine._hear(model, samples, "en", disp, lambda f: None)]
        out.append({"start": c.start, "end": c.end, "peak": c.peak, "score": c.score, "reason": c.reason,
                    "words": words, "listened": True})
        print(f"  clip {len(out):>2} {c.start:7.1f}-{c.end:7.1f}  {len(words):>3} words  {c.reason[:60]}", flush=True)
    with open(os.path.join(wd, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"title": v.get("title", ""), "streamer": login, "source": f"https://www.twitch.tv/videos/{vod}",
                   "clips": out}, f, indent=1)
    return wd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--vod", action="append", required=True, help="VOD id, optionally @start-end in seconds")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--clips", type=int, default=12)
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--ytdlp", default=shutil.which("yt-dlp") or "yt-dlp")
    a = ap.parse_args()
    if os.path.abspath(a.out).startswith(ROOT + os.sep):
        ap.error("keep OUT outside the repository")
    os.environ.setdefault("CLIPRADAR_DATA", os.path.join(a.out, "_data"))
    os.makedirs(a.out, exist_ok=True)
    from faster_whisper import WhisperModel
    model = WhisperModel(a.whisper, device="cpu", compute_type="int8")
    for spec in a.vod:
        vod, _, rng = spec.partition("@")
        win = tuple(int(x) for x in rng.split("-")) if rng else None
        build(vod, win, a, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
