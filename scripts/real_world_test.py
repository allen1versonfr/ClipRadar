"""
A real-world test of Clip Radar on real links: download, find clips, title them, render the best
few, and write down everything that happened, with times and what came out.

  python scripts/real_world_test.py https://www.twitch.tv/videos/2881416511
  python scripts/real_world_test.py <link> <link> --clips 5 --model small --out real-test
  python scripts/real_world_test.py <link> --app "C:/Program Files/Clip Radar/ClipRadar.exe"

By default it runs this checkout's own server (src/), with throwaway data and work folders, so the
installed app and its settings aren't touched. With --app it starts that copy instead (also with
throwaway folders). Needs FFmpeg and yt-dlp, the same as the app (tools/ or PATH).

Writes <out>/report.md (for reading) and <out>/report.json (everything), and leaves the rendered
clips in <out>/work so they can be watched. Nothing is uploaded anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))


class App:
    def __init__(self, args, out: str):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.data, self.work = os.path.join(out, "data"), os.path.join(out, "work")
        os.makedirs(self.data, exist_ok=True)
        os.makedirs(self.work, exist_ok=True)
        env = dict(os.environ, CLIPRADAR_DATA=self.data, CLIPRADAR_WORK=self.work, CLIPRADAR_PORT=str(self.port),
                   CLIPRADAR_NO_BROWSER="1")
        if args.app:
            cmd = shlex.split(args.app, posix=(os.name != "nt")) + ["--browser"]
        else:
            cmd = [sys.executable, os.path.join(SRC, "app.py"), "--browser"]
        self.log = open(os.path.join(out, "app.log"), "w", encoding="utf-8")
        self.p = subprocess.Popen(cmd, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.peak_mb = 0.0
        threading.Thread(target=self._watch_memory, daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                self.get("/api/status")
                return
            except Exception:
                time.sleep(0.5)
        raise SystemExit("The app didn't start; see app.log")

    def _watch_memory(self):
        try:
            import psutil
            proc = psutil.Process(self.p.pid)
        except Exception:
            return
        while self.p.poll() is None:
            try:
                mb = sum(p.memory_info().rss for p in [proc] + proc.children(recursive=True)) / 2 ** 20
                self.peak_mb = max(self.peak_mb, mb)
            except Exception:
                pass
            time.sleep(1)

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=60) as r:
            return json.load(r)

    def post(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return dict(json.load(e), _status=e.code)
            except Exception:
                return {"_status": e.code, "error": str(e)}

    def stop(self):
        try:
            self.post("/api/quit")
        except Exception:
            pass
        try:
            self.p.wait(timeout=15)
        except Exception:
            self.p.kill()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(fn, timeout, every=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = fn()
        if r:
            return r
        time.sleep(every)
    return None


def _probe(path: str) -> dict:
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        d = json.loads(p.stdout)
        v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
        a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        return {"size": f"{v.get('width')}x{v.get('height')}", "fps": round(float(num) / float(den or 1), 2),
                "seconds": round(float(d["format"].get("duration") or 0), 2), "audio": a is not None,
                "mb": round(int(d["format"].get("size") or 0) / 1e6, 2)}
    except Exception as e:
        return {"error": str(e)[:200]}


def test_link(app: App, link: str, args) -> dict:
    r = {"link": link, "steps": {}, "problems": []}
    t0 = time.time()
    j = app.post("/api/analyze", {"source": link, "count": args.count, "length": 0, "events": True})
    if not j.get("job"):
        r["problems"].append(f"couldn't start: {j.get('error')}")
        return r
    job = _wait(lambda: (lambda x: x if x["status"] != "running" else None)(app.get(f"/api/job/{j['job']}")), args.timeout, 3)
    r["steps"]["download_and_analysis_s"] = round(time.time() - t0, 1)
    if not job or job["status"] != "done":
        r["problems"].append(f"opening failed: {(job or {}).get('error') or 'timed out'}")
        r["log"] = (job or {}).get("log", [])[-40:]
        return r
    pv = job["result"]
    pid = pv["pid"]
    r["log"] = job.get("log", [])[-40:]
    r["video"] = {k: pv.get(k) for k in ("title", "duration", "width", "height", "has_chat", "chat_count", "chat_stats",
                                         "estimate", "streamer_name", "detect_text", "pick_note")}
    r["origin"] = pv.get("origin")
    # titles: the listening job transcribes each clip (or uses YouTube's captions)
    t1 = time.time()
    lj = app.post("/api/listen", {"pid": pid, "order": [c["id"] for c in pv["clips"]], "model": args.model})
    if lj.get("job"):
        _wait(lambda: app.get(f"/api/job/{lj['job']}")["status"] != "running", args.timeout, 3)
    r["steps"]["titles_s"] = round(time.time() - t1, 1)
    clips = app.get(f"/api/clips/{pid}")["clips"]
    r["clips"] = [{"id": c["id"], "start": c["start"], "end": c["end"], "peak": c.get("peak"), "score": c.get("quality"),
                   "reason": c.get("reason"), "title": c.get("title"), "title_b": c.get("title_b"),
                   "chat_title": c.get("chat_title", ""), "listened": c.get("listened")} for c in clips]
    for wd in os.listdir(app.work):
        p = os.path.join(app.work, wd, "youtube.json")
        if os.path.isfile(p):
            d = json.load(open(p, encoding="utf-8"))
            r["youtube"] = {"most_replayed": bool(d.get("heatmap")), "chapters": len(d.get("chapters") or []),
                            "comment_timestamps": len(d.get("comments") or []), "caption_words": len(d.get("words") or [])}
    # render the best few with the default export settings
    t2 = time.time()
    items = []
    for c in clips[:args.clips]:
        it = app.post("/api/queue/add", {"pid": pid, "clip": {"id": c["id"], "rank": c["id"], "start": c["start"], "end": c["end"],
                                                               "title": c.get("title") or f"clip {c['id']}"}, "opts": {}})
        if it.get("id"):
            items.append(it["id"])
        else:
            r["problems"].append(f"clip {c['id']} not queued: {it.get('error')}")
    done = _wait(lambda: (lambda q: q if all(i["status"] in ("done", "error") for i in q if i["id"] in items) else None)(
        app.get("/api/queue")["items"]), args.timeout, 3) or app.get("/api/queue")["items"]
    r["steps"]["renders_s"] = round(time.time() - t2, 1)
    r["renders"] = []
    for i in done:
        if i["id"] not in items:
            continue
        f = os.path.join(i.get("folder") or "", i.get("name") or "")
        row = {"name": i.get("name"), "status": i["status"], "error": i.get("error", ""), "step": i.get("step", ""),
               "warning": i.get("warning", "")}
        if i["status"] == "done" and os.path.isfile(f):
            row.update(_probe(f))
        else:
            r["problems"].append(f"render {i.get('name')}: {i.get('error') or i['status']}")
        r["renders"].append(row)
    r["steps"]["total_s"] = round(time.time() - t0, 1)
    return r


def report_md(results: list, app: App, args) -> str:
    out = [f"# Clip Radar real-world test ({time.strftime('%Y-%m-%d %H:%M')})", ""]
    st = app.get("/api/status")
    out += [f"Version {st.get('version')}; app peak memory {app.peak_mb:.0f} MB" if app.peak_mb else f"Version {st.get('version')}", ""]
    for r in results:
        out += [f"## {r['link']}", ""]
        v = r.get("video") or {}
        if v:
            est = v.get("estimate") or {}
            out += [f"- **{v.get('title')}** ({(v.get('duration') or 0) / 60:.0f} min, {v.get('width')}x{v.get('height')})"
                    + (f", by {v.get('streamer_name')}" if v.get("streamer_name") else ""),
                    f"- Chat: {v.get('chat_count', 0):,} messages" + (f", {v['chat_stats'].get('chatters', 0):,} chatters"
                                                                     if v.get("chat_stats") else ""),
                    f"- Suggested {len(r.get('clips', []))} clips (estimate: {est.get('strong')} strong moments)",
                    f"- Detection: {v.get('detect_text')}"]
        if r.get("youtube"):
            y = r["youtube"]
            out.append(f"- YouTube: Most replayed {'yes' if y['most_replayed'] else 'no'}, {y['chapters']} chapters, "
                       f"{y['comment_timestamps']} comment timestamps, {y['caption_words']:,} caption words")
        s = r.get("steps", {})
        out.append("- Times: " + ", ".join(f"{k.replace('_s', '').replace('_', ' ')} {v:.0f} s" for k, v in s.items()))
        if r.get("clips"):
            out += ["", "| # | at | length | score | title | why |", "|---|---|---|---|---|---|"]
            for c in r["clips"]:
                at = int(c["peak"] or c["start"])
                out.append(f"| {c['id']} | {at // 3600}:{at % 3600 // 60:02d}:{at % 60:02d} | {c['end'] - c['start']:.0f} s | "
                           f"{c.get('score') or ''} | {(c.get('title') or '').replace('|', '/')} | {(c.get('reason') or '').replace('|', '/')} |")
        if r.get("renders"):
            out += ["", "| file | result | size | fps | length | MB |", "|---|---|---|---|---|---|"]
            for x in r["renders"]:
                note = (x.get("error") or x.get("warning") or "")[:90].replace("|", "/")
                out.append(f"| {x.get('name')} | {x['status']} {note} | {x.get('size', '')} | {x.get('fps', '')} | "
                           f"{x.get('seconds', '')} | {x.get('mb', '')} |")
        if r.get("problems"):
            out += ["", "**Problems:**"] + [f"- {p}" for p in r["problems"]]
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="+")
    ap.add_argument("--clips", type=int, default=5, help="how many of the top clips to render")
    ap.add_argument("--count", type=int, default=0, help="clips to suggest (0 = automatic)")
    ap.add_argument("--model", default="small", help="Whisper model for titles")
    ap.add_argument("--app", default="", help="an installed ClipRadar.exe to test instead of src/")
    ap.add_argument("--out", default="", help="folder for the report, the clips and the logs")
    ap.add_argument("--timeout", type=float, default=4 * 3600)
    args = ap.parse_args()
    out = os.path.abspath(args.out or tempfile.mkdtemp(prefix="clipradar_real_"))
    os.makedirs(out, exist_ok=True)
    app = App(args, out)
    results = []
    try:
        for link in args.links:
            print(f"Testing {link} ...", flush=True)
            results.append(test_link(app, link, args))
            with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
                json.dump(results, f, indent=1, ensure_ascii=False)
        md = report_md(results, app, args)
    finally:
        app.stop()
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\nReport: {os.path.join(out, 'report.md')}")
    sys.exit(1 if any(r.get("problems") for r in results) else 0)


if __name__ == "__main__":
    main()
