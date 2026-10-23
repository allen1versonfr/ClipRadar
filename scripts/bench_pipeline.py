"""
Scan and render cost on a made-up stream, measured through the app's own server, so two versions
of Clip Radar can be compared on the same PC:

  python scripts/bench_pipeline.py                         # this checkout, 30-minute test stream
  python scripts/bench_pipeline.py --src path/to/old/src   # another version's src folder
  python scripts/bench_pipeline.py --presets               # also each export quality preset

What it does, in a fresh Python process with throwaway data and work folders:
  1. makes a 1280x720 30 FPS stream with pink noise, loud moments and a chat replay (cached, so
     every version gets the same file)
  2. "Find clips" on it: wall time and the process's peak memory
  3. renders the top clip (30 s, 9:16, no captions, software encoder) through the queue: wall time
     and the speed as a multiple of real time
  4. --presets: renders the same 30 s with each quality preset straight through the engine and
     reports time, file size and SSIM against the High quality render

Numbers are from this machine only. CPU-only; GPU encoders aren't measured.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")

CHILD = r"""
import json, os, sys, threading, time, urllib.request
sys.path.insert(0, sys.argv[1])
vod, chat, port = sys.argv[2], sys.argv[3], int(sys.argv[4])

def peak_mb():
    if os.name == "nt":                     # peak working set: the Windows counterpart of VmHWM
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("rest", ctypes.c_size_t * 7)]
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        c = PMC(cb=ctypes.sizeof(PMC))
        return c.PeakWorkingSetSize / 1048576 if k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), c, c.cb) else 0.0
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return 0.0

def call(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

import server
srv, url = server.start(port)
base_mb = peak_mb()
t0 = time.perf_counter()
j = call("/api/analyze", {"source": vod, "chat_file": chat, "count": 0, "length": 0, "events": True})
while True:
    r = call(f"/api/job/{j['job']}")
    if r["status"] != "running":
        break
    time.sleep(0.2)
scan_s = time.perf_counter() - t0
scan_peak = peak_mb()
res = r.get("result") or {}
clips = res.get("clips") or []
out = {"version": server.VERSION if hasattr(server, "VERSION") else "", "scan_s": round(scan_s, 2),
       "scan_status": r["status"], "clips": len(clips), "peaks": sorted(round(c["peak"]) for c in clips), "peak_mb_after_scan": round(scan_peak, 1),
       "peak_mb_before_scan": round(base_mb, 1)}
if clips:
    c = clips[0]
    mid = (c["start"] + c["end"]) / 2
    clip = {"id": c["id"], "rank": 1, "start": mid - 15, "end": mid + 15, "title": "Bench clip"}
    opts = {"captions": False, "hw_encode": False}
    t1 = time.perf_counter()
    it = call("/api/queue/add", {"pid": res["pid"], "clip": clip, "opts": opts})
    while True:
        q = next((i for i in call("/api/queue")["items"] if i["id"] == it["id"]), None)
        if q and q["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    rs = time.perf_counter() - t1
    out.update(render_s=round(rs, 2), render_x_realtime=round(30 / rs, 2), render_status=q["status"],
               render_mb=round(sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(os.environ["CLIPRADAR_WORK"])
                                   for f in fs if f.startswith("Bench clip") and f.endswith(".mp4")) / 1e6, 2),
               peak_mb_after_render=round(peak_mb(), 1))
print("RESULT " + json.dumps(out), flush=True)          # os._exit skips the flush
server.SHUTTING_DOWN[0] = True
os._exit(0)
"""


def make_stream(folder: str, minutes: int) -> tuple:
    vod = os.path.join(folder, f"bench_{minutes}m.mp4")
    chat = os.path.join(folder, f"bench_{minutes}m.chat.json")
    if os.path.isfile(vod) and os.path.isfile(chat):
        return vod, chat
    dur = minutes * 60
    rnd = random.Random(11)
    moments = sorted(rnd.sample(range(60, dur - 60, 30), max(4, minutes // 3)))
    loud = "+".join(f"between(t,{t},{t + 5})" for t in moments)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:duration={dur}:seed=3",
                    "-f", "lavfi", "-i", f"sine=frequency=500:duration={dur}",
                    "-filter_complex", f"[2:a]volume='if({loud},0.8,0)':eval=frame[s];[1:a][s]amix=inputs=2:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
                    "-c:a", "aac", vod], check=True)
    users = [f"viewer{i}" for i in range(800)]
    com = []

    def add(t, user, body):
        com.append({"content_offset_seconds": round(t, 2), "commenter": {"name": user, "display_name": user},
                    "message": {"body": body, "bits_spent": 0}})
    for t in range(dur):
        for _ in range(rnd.choice([0, 1, 1, 2, 3])):
            add(t + rnd.random(), rnd.choice(users), rnd.choice(["hi", "lol", "nice", "gg", "what game", "ok", "LUL"]))
    for m in moments:
        for k in range(rnd.randint(40, 120)):
            add(m + 6 + rnd.random() * 8, rnd.choice(users), rnd.choice(["KEKW", "OMEGALUL", "clip it", "no way", "LETSGO"]))
    for t in rnd.sample(range(60, dur - 60), max(2, minutes // 10)):          # F waves: shouldn't rank
        print(f"F wave at {t}s", file=sys.stderr)
        for k in range(80):
            add(t + rnd.random() * 6, rnd.choice(users), "F")
    com.sort(key=lambda c: c["content_offset_seconds"])
    with open(chat, "w", encoding="utf-8") as f:
        json.dump({"streamer": {"name": "benchstreamer", "id": 1}, "comments": com}, f)
    return vod, chat


def run(src: str, vod: str, chat: str, port: int) -> dict:
    env = dict(os.environ, CLIPRADAR_DATA=tempfile.mkdtemp(prefix="vc_bench_"),
               CLIPRADAR_WORK=tempfile.mkdtemp(prefix="vc_bench_"), CLIPRADAR_UPDATE_API="http://127.0.0.1:9/none",
               CLIPRADAR_EMOTE_API="http://127.0.0.1:9", CLIPRADAR_NO_BROWSER="1")
    out = subprocess.run([sys.executable, "-c", CHILD, os.path.abspath(src), vod, chat, str(port)], env=env,
                         capture_output=True, text=True, timeout=3600)
    line = [x for x in out.stdout.splitlines() if x.startswith("RESULT ")]
    if not line:
        raise SystemExit(out.stdout[-3000:] + out.stderr[-3000:])
    return json.loads(line[-1][7:])


def presets(src: str, vod: str) -> dict:
    sys.path.insert(0, os.path.abspath(src))
    import time
    import engine
    import exports
    tmp = tempfile.mkdtemp(prefix="vc_bench_")
    res, ref = {}, None
    for sp in ("high", "balanced", "fast"):                             # Settings › Export format › Quality
        q = exports.QUALITY[sp]
        out = os.path.join(tmp, f"{sp}.mp4")
        t = time.perf_counter()
        engine.render_clip(vod, 300.0, 330.0, out, engine.RenderOpts(layout="blur", hw_encode=False, speed=q["speed"],
                                                                     fps=q["fps"], captions=False),
                           log=lambda m: None)
        took = time.perf_counter() - t
        fps = engine.probe(out).get("fps")
        res[sp] = {"seconds": round(took, 2), "x_realtime": round(30 / took, 2), "mb": round(os.path.getsize(out) / 1e6, 2),
                   "fps": round(fps or 0, 2)}
        if sp == "high":
            ref = out
        else:
            p = subprocess.run(["ffmpeg", "-v", "info", "-i", out, "-i", ref, "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"],
                               capture_output=True, text=True)
            ssim = [ln.split("All:")[1].split()[0] for ln in p.stderr.splitlines() if "SSIM" in ln and "All:" in ln]
            res[sp]["ssim_vs_high"] = float(ssim[-1]) if ssim else None
    return res


def main():
    a = sys.argv
    src = a[a.index("--src") + 1] if "--src" in a else SRC
    minutes = int(a[a.index("--minutes") + 1]) if "--minutes" in a else 30
    cache = a[a.index("--cache") + 1] if "--cache" in a else os.path.join(tempfile.gettempdir(), "clipradar_bench")
    os.makedirs(cache, exist_ok=True)
    vod, chat = make_stream(cache, minutes)
    out = {"minutes": minutes, "pipeline": run(src, vod, chat, 7975)}
    if "--presets" in a:
        out["presets"] = presets(src, vod)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
