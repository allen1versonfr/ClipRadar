"""
Speed audit on a real PC (docs/plans/efficiency-audit.md, phase 1). Runs the app's own server on real videos, the way
you use it, and records where the time goes.

The easy way, on Windows: double-click scripts\\speed_audit.bat. It sets up Python and the packages, then runs
  python scripts/audit_speed.py --auto
which sets up FFmpeg and the laughter detector, picks videos from your Clip Radar projects (a long VOD with chat, about
20 minutes, a long video), measures each three times with Whisper small and once with large-v3, and summarises the
Timing lines the app already wrote in real use. Results: audit_runs/<date>/SUMMARY.md.

By hand:
  python scripts/audit_speed.py --input D:\\vods\\stream.mp4 --chat D:\\vods\\stream.chat.json --input D:\\vods\\yt.mp4
  python scripts/audit_speed.py --input stream.mp4 --chat chat.json --model large-v3 --clips 5 --runs 3

For each input, in a fresh Python process (cold, like opening the app):
  1. open: Find clips (POST /api/analyze), with its chat file if given
  2. titles: listen to the clips with Whisper (POST /api/listen), the --model asked for
  3. render: keep the top --clips clips one at a time (POST /api/queue/add), captions on, GPU encoder allowed
  4. idle: the app left alone for --idle seconds afterwards

What it records:
  - every FFmpeg, ffprobe and whisper.cpp launch (subprocess.Popen is wrapped inside the child): its step, how long it
    ran, which file and which seconds of it it read
  - the app's own Timing lines (GET /api/timings)
  - CPU and memory of the app and everything it started, once a second (needs psutil: pip install psutil)
  - on Windows, the graphics card's 3D, video decode and video encode load (typeperf)
  - --pyspy: a flame graph of the open step (needs py-spy: pip install py-spy)

Your settings are used (Settings › Export format, content check, campaigns: the JSON files in Clip Radar's data folder,
copied), and your Whisper models and GPU support files (linked, not copied, so nothing downloads again). Your projects and
library aren't touched: the run's projects and clips go to a throwaway folder that's deleted at the end (--keep keeps it).

Output: --out (default audit_speed_<date>/): report.md (the tables), results.json (everything), launches.csv,
samples.csv. report.md is what to read, or to hand to Claude.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
IS_WIN = os.name == "nt"

# --------------------------------------------------------------------------
# the child: the app's server, with every launch logged
# --------------------------------------------------------------------------

CHILD = r"""
import json, os, sys, threading, time, urllib.request, subprocess
sys.path.insert(0, sys.argv[1])
cfg = json.loads(sys.argv[2])
LOG = open(cfg["launch_log"], "a", encoding="utf-8", buffering=1)
PHASE = ["start"]
_lock = threading.Lock()

def say(kind, **kw):
    print(kind + " " + json.dumps(dict(kw, t=time.time())), flush=True)

def phase(name):
    PHASE[0] = name
    say("PHASE", name=name)

REAL = subprocess.Popen
WATCH = ("ffmpeg", "ffprobe", "whisper")

class Logged(REAL):
    def __init__(self, cmd, *a, **kw):
        self._rec = None
        exe = os.path.basename(str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd).split()[0]).lower()
        if any(w in exe for w in WATCH):
            args = [str(x) for x in cmd] if isinstance(cmd, (list, tuple)) else str(cmd).split()
            def opt(name, src=args):
                return src[src.index(name) + 1] if name in src[:-1] else ""
            ins = [args[i + 1] for i, x in enumerate(args[:-1]) if x == "-i"]
            first_in = args.index("-i") if "-i" in args else len(args)
            pre = args[:first_in]
            self._rec = {"phase": PHASE[0], "tool": exe.replace(".exe", ""), "start": time.time(),
                         "thread": threading.current_thread().name,
                         "ss": opt("-ss", pre), "t": opt("-t", pre), "sseof": opt("-sseof", pre),
                         "inputs": ins, "args": " ".join(args[1:])[:600]}
        super().__init__(cmd, *a, **kw)

    def _done(self):
        r = self._rec
        if r is not None and self.returncode is not None and "end" not in r:
            r["end"], r["rc"] = time.time(), self.returncode
            with _lock:
                LOG.write(json.dumps(r) + "\n")

    def wait(self, timeout=None):
        rc = super().wait(timeout)
        self._done()
        return rc

    def poll(self):
        rc = super().poll()
        self._done()
        return rc

subprocess.Popen = Logged

def call(path, body=None, timeout=120):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def wait_job(jid, what):
    last = ""
    while True:
        r = call(f"/api/job/{jid}")
        if r.get("step") and r["step"] != last:
            last = r["step"]
            say("STEP", what=what, step=last[:120])
        if r["status"] != "running":
            return r
        time.sleep(0.25)

import server
srv, url = server.start(cfg["port"])
say("READY", pid=os.getpid(), version=getattr(server, "VERSION", ""))
out = {"version": getattr(server, "VERSION", ""), "phases": {}, "whisper": server.engine.whisper_available()}
try:
    phase("open")
    t0 = time.time()
    body = {"source": cfg["input"], "count": cfg["count"], "length": 0, "events": True}
    if cfg.get("chat"):
        body["chat_file"] = cfg["chat"]
    j = call("/api/analyze", body)
    r = wait_job(j["job"], "open")
    out["phases"]["open"] = {"s": round(time.time() - t0, 2), "status": r["status"], "error": r.get("error", "")}
    res = r.get("result") or {}
    clips = sorted(res.get("clips") or [], key=lambda c: -float(c.get("score") or 0))
    pid = res.get("pid")
    out["clips_found"] = len(clips)
    out["duration"] = res.get("duration")
    if r["status"] != "done" or not clips:
        raise SystemExit("nothing to go on")

    if cfg["titles"]:
        phase("titles")
        t0 = time.time()
        j = call("/api/listen", {"pid": pid, "order": [c["id"] for c in clips], "model": cfg["model"], "language": ""})
        r = wait_job(j["job"], "titles")
        out["phases"]["titles"] = {"s": round(time.time() - t0, 2), "status": r["status"], "clips": len(clips),
                                   "error": r.get("error", "")}
        now = {c["id"]: c for c in (call(f"/api/clips/{pid}").get("clips") or [])}
        clips = [now.get(c["id"], c) for c in clips]

    renders = []
    for n, c in enumerate(clips[:cfg["renders"]], 1):
        phase(f"render {n}")
        t0 = time.time()
        clip = {"id": c["id"], "rank": n, "start": c["start"], "end": c["end"], "title": c.get("title") or f"Audit clip {n}",
                "post": c.get("post") or ""}
        it = call("/api/queue/add", {"pid": pid, "clip": clip, "opts": {"captions": True, "hw_encode": cfg["hw"],
                                                                        "whisper_model": cfg["model"]}, "sound": False})
        stages = {}
        while True:
            q = next((i for i in call("/api/queue")["items"] if i["id"] == it["id"]), None)
            if q and q.get("stage") and q["stage"] not in stages:
                stages[q["stage"]] = round(time.time() - t0, 2)
            if q is None or q["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        renders.append({"n": n, "s": round(time.time() - t0, 2), "length": round(c["end"] - c["start"], 1),
                        "status": (q or {}).get("status", "gone"), "error": (q or {}).get("error", ""), "stages": stages})
    out["phases"]["render"] = renders

    phase("idle")
    time.sleep(cfg["idle"])
    out["timings"] = call("/api/timings").get("items", [])
except SystemExit as e:
    out["stopped"] = str(e)
    try:
        out["timings"] = call("/api/timings").get("items", [])
    except Exception:
        pass
except Exception as e:
    out["stopped"] = f"{type(e).__name__}: {e}"
phase("end")
print("RESULT " + json.dumps(out), flush=True)
server.SHUTTING_DOWN[0] = True
os._exit(0)
"""


# --------------------------------------------------------------------------
# the parent: a fresh data folder, the samplers, the report
# --------------------------------------------------------------------------

def real_data_dir() -> Path:
    if os.environ.get("CLIPRADAR_DATA"):
        return Path(os.environ["CLIPRADAR_DATA"])
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local") if IS_WIN \
        else os.path.join(Path.home(), ".local", "share")
    return Path(base) / "ClipRadar"


def link_dir(src: Path, dst: Path) -> str:
    """dst points at src without copying (a junction on Windows: no admin needed). Returns how, or '' if it couldn't."""
    if not src.is_dir():
        return ""
    try:
        if IS_WIN:
            import _winapi
            _winapi.CreateJunction(str(src), str(dst))
            return "junction"
        os.symlink(src, dst, target_is_directory=True)
        return "symlink"
    except OSError:
        return ""


def fresh_data(tmp: Path) -> dict:
    """A data folder with your settings and your models and GPU support, but none of your projects."""
    real, data = real_data_dir(), tmp / "data"
    data.mkdir(parents=True)
    copied = []
    if real.is_dir():
        for f in real.glob("*.json"):
            shutil.copy2(f, data / f.name)
            copied.append(f.name)
    linked = {name: link_dir(real / name, data / name) for name in ("models", "tools")}
    return {"real": str(real), "data": str(data), "settings": copied, "linked": linked}


class Sampler(threading.Thread):
    """CPU and memory of a process and its children, once a second."""

    def __init__(self, pid: int, phase_of):
        super().__init__(daemon=True)
        import psutil
        self.psutil, self.proc, self.phase_of = psutil, psutil.Process(pid), phase_of
        self.rows, self.stop = [], threading.Event()
        self.ncpu = psutil.cpu_count() or 1

    def run(self):
        ps, seen = self.psutil, {}
        while not self.stop.is_set():
            try:
                procs = [self.proc] + self.proc.children(recursive=True)
            except ps.Error:
                return
            cpu = rss = 0.0
            kids = 0
            for p in procs:
                try:
                    if p.pid not in seen:
                        seen[p.pid] = p
                        p.cpu_percent(None)
                    cpu += seen[p.pid].cpu_percent(None)
                    rss += p.memory_info().rss
                    kids += p is not self.proc
                except ps.Error:
                    pass
            self.rows.append({"t": time.time(), "phase": self.phase_of(), "cpu_pct_of_pc": round(cpu / self.ncpu, 1),
                              "rss_mb": round(rss / 1048576, 1), "children": kids,
                              "pc_cpu_pct": ps.cpu_percent(None)})
            self.stop.wait(1.0)


def start_gpu_log(path: Path):
    """Windows only: the card's engines' load, once a second, as CSV (typeperf ships with Windows)."""
    if not IS_WIN:
        return None
    counters = [r"\GPU Engine(*engtype_3D)\Utilization Percentage",
                r"\GPU Engine(*engtype_VideoDecode)\Utilization Percentage",
                r"\GPU Engine(*engtype_VideoEncode)\Utilization Percentage",
                r"\GPU Engine(*engtype_Compute*)\Utilization Percentage",
                r"\GPU Adapter Memory(*)\Dedicated Usage"]
    try:
        return subprocess.Popen(["typeperf", *counters, "-si", "1", "-f", "CSV", "-o", str(path), "-y"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def read_gpu_log(path: Path) -> list:
    """typeperf's CSV, one row per second: the sum of each engine type across processes, and dedicated memory."""
    if not path.is_file():
        return []
    from datetime import datetime
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        head = next(r, [])
        kinds = []
        for h in head[1:]:
            hl = h.lower()
            kinds.append("vram_mb" if "dedicated usage" in hl else "decode" if "videodecode" in hl else
                         "encode" if "videoencode" in hl else "compute" if "compute" in hl else "3d" if "3d" in hl else "")
        for line in r:
            try:
                t = datetime.strptime(line[0], "%m/%d/%Y %H:%M:%S.%f").timestamp()
            except (ValueError, IndexError):
                continue
            row = {"t": t, "3d": 0.0, "decode": 0.0, "encode": 0.0, "compute": 0.0, "vram_mb": 0.0}
            for k, v in zip(kinds, line[1:]):
                if not k:
                    continue
                try:
                    row[k] += float(v) / 1048576 if k == "vram_mb" else float(v)
                except ValueError:
                    pass
            rows.append(row)
    return rows


def phase_at(phases: list, t: float) -> str:
    name = "start"
    for at, n in phases:
        if at <= t:
            name = n
    return name


def run_one(src: Path, inp: dict, a, run_dir: Path, port: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="clipradar_audit_"))
    data = fresh_data(tmp)
    work = tmp / "work"
    work.mkdir()
    launch_log = run_dir / "launches.jsonl"
    env = dict(os.environ, CLIPRADAR_DATA=data["data"], CLIPRADAR_WORK=str(work), CLIPRADAR_NO_BROWSER="1",
               PYTHONUNBUFFERED="1")
    if (Path(data["real"]) / "models").is_dir() and not data["linked"].get("models"):
        env["HF_HOME"] = str(Path(data["real"]) / "models")          # couldn't link: point at it instead
    cfg = {"port": port, "input": inp["input"], "chat": inp.get("chat") or "", "count": a.count, "model": a.model,
           "titles": not a.no_titles, "renders": a.clips, "hw": not a.cpu, "idle": a.idle, "launch_log": str(launch_log)}
    phases: list = []
    p = subprocess.Popen([sys.executable, "-c", CHILD, str(src), json.dumps(cfg)], env=env, cwd=str(tmp),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    sampler = gpu = spy = None
    result, log_lines = {}, []
    gpu_csv = run_dir / "gpu.csv"
    try:
        for line in p.stdout:
            log_lines.append(line.rstrip())
            kind, _, rest = line.partition(" ")
            if kind == "READY":
                info = json.loads(rest)
                try:
                    sampler = Sampler(info["pid"], lambda: phases[-1][1] if phases else "start")
                    sampler.start()
                except ImportError:
                    print("  (psutil isn't installed: no CPU or memory samples. pip install psutil)", flush=True)
                gpu = start_gpu_log(gpu_csv)
            elif kind == "PHASE":
                ph = json.loads(rest)
                phases.append((ph["t"], ph["name"]))
                print(f"  {ph['name']}...", flush=True)
                if ph["name"] == "open" and a.pyspy and pyspy_exe():
                    spy = subprocess.Popen([pyspy_exe(), "record", "--pid", str(info["pid"]), "--native", "--rate", "50",
                                            "-o", str(run_dir / "open_flame.svg")],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif spy and ph["name"] != "open":
                    spy.terminate()
                    spy = None
            elif kind == "STEP":
                st = json.loads(rest)
                print(f"    {st['step']}", flush=True)
            elif kind == "RESULT":
                result = json.loads(rest)
        p.wait()
    finally:
        if sampler:
            sampler.stop.set()
        if gpu and gpu.poll() is None:
            gpu.terminate()
            gpu.wait()
        if spy and spy.poll() is None:
            spy.terminate()
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    (run_dir / "app.log").write_text("\n".join(log_lines), encoding="utf-8")
    launches = []
    if launch_log.is_file():
        with open(launch_log, encoding="utf-8") as f:
            launches = [json.loads(x) for x in f if x.strip()]
    time.sleep(1.5)                                               # typeperf flushes its file when it stops
    gpu_rows = read_gpu_log(gpu_csv)
    for g in gpu_rows:
        g["phase"] = phase_at(phases, g["t"])
    return {"input": inp, "data": data, "result": result, "phases": phases, "launches": launches,
            "samples": sampler.rows if sampler else [], "gpu": gpu_rows, "kept": str(tmp) if a.keep else ""}


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def _f(x, d=1):
    return "" if x is None else f"{x:.{d}f}"


def summarize(run: dict) -> dict:
    res, launches = run["result"], run["launches"]
    src = os.path.normcase(os.path.abspath(run["input"]["input"]))
    by_phase: dict = {}
    for r in launches:
        d = by_phase.setdefault(r["phase"], {"launches": 0, "seconds": 0.0, "source_reads": 0, "tools": {}})
        d["launches"] += 1
        d["seconds"] += r["end"] - r["start"]
        d["tools"][r["tool"]] = d["tools"].get(r["tool"], 0) + 1
        d["source_reads"] += any(os.path.normcase(os.path.abspath(i)) == src for i in r["inputs"])
    res_phases = {}
    for name, d in by_phase.items():
        rows = [s for s in run["samples"] if s["phase"] == name]
        g = [x for x in run["gpu"] if x["phase"] == name]
        res_phases[name] = dict(d, seconds=round(d["seconds"], 2),
                                cpu_avg=_mean([s["cpu_pct_of_pc"] for s in rows]),
                                cpu_max=max((s["cpu_pct_of_pc"] for s in rows), default=None),
                                rss_max=max((s["rss_mb"] for s in rows), default=None),
                                gpu_3d=_mean([x["3d"] for x in g]), gpu_enc=_mean([x["encode"] for x in g]),
                                gpu_dec=_mean([x["decode"] for x in g]), gpu_compute=_mean([x["compute"] for x in g]),
                                vram_max=max((x["vram_mb"] for x in g), default=None))
    for name in {s["phase"] for s in run["samples"]} - set(res_phases):
        rows = [s for s in run["samples"] if s["phase"] == name]
        res_phases[name] = {"launches": 0, "seconds": 0.0, "source_reads": 0, "tools": {},
                            "cpu_avg": _mean([s["cpu_pct_of_pc"] for s in rows]),
                            "cpu_max": max((s["cpu_pct_of_pc"] for s in rows), default=None),
                            "rss_max": max((s["rss_mb"] for s in rows), default=None)}
    slow = sorted(launches, key=lambda r: r["start"] - r["end"])[:12]
    srcs = sorted({i for r in launches for i in r["inputs"]}, key=len, reverse=True)

    def short(args: str) -> str:
        for i in srcs:
            args = args.replace(i, "<source>" if os.path.normcase(os.path.abspath(i)) == src else "<" + os.path.basename(i) + ">")
        return args
    return {"version": res.get("version"), "duration": res.get("duration"), "clips_found": res.get("clips_found"),
            "whisper": res.get("whisper"),
            "phases": res.get("phases", {}), "stopped": res.get("stopped", ""), "timings": res.get("timings", []),
            "by_phase": res_phases, "slowest_launches": [
                {"phase": r["phase"], "tool": r["tool"], "s": round(r["end"] - r["start"], 2), "args": short(r["args"])[:240]}
                for r in slow]}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def report(runs: list, a) -> str:
    out = ["# Speed audit results", "",
           f"{time.strftime('%Y-%m-%d %H:%M')}. Whisper `{a.model}`, {a.clips} clip(s) rendered per input, "
           f"{'CPU encoder only' if a.cpu else 'GPU encoder allowed'}, {a.runs} run(s) per input. "
           "CPU % is of the whole PC (100% = every core busy).", ""]
    if runs:
        d = runs[0]["data"]
        links = ", ".join(f"{k} {v or 'not linked'}" for k, v in d["linked"].items())
        out += [f"Settings copied from `{d['real']}`: {', '.join(d['settings']) or 'none found'}. "
                f"Models and tools: {links}.", ""]
    for inp_name in dict.fromkeys(r["input"]["input"] for r in runs):
        rs = [r for r in runs if r["input"]["input"] == inp_name]
        ss = [summarize(r) for r in rs]
        s0 = ss[0]
        dur = s0.get("duration") or 0
        out += [f"## {os.path.basename(inp_name)}", "",
                f"{dur / 60:.1f} minutes, chat: {'yes' if rs[0]['input'].get('chat') else 'no'}, "
                f"{s0.get('clips_found')} clips found, Clip Radar {s0.get('version')}."
                + (f" **Stopped early: {s0['stopped']}**" if s0.get("stopped") else "")
                + ("" if s0.get("whisper") else " **Whisper isn't installed here, so titles and captions didn't run: "
                   "install faster-whisper (requirements.txt) and run again.**"), ""]
        out += ["### Steps (wall time, each run)", "", "| Step | " + " | ".join(f"run {i + 1}" for i in range(len(ss))) + " |",
                "|---|" + "---|" * len(ss)]
        for step in ("open", "titles"):
            out.append(f"| {step} | " + " | ".join(_f((s["phases"].get(step) or {}).get("s"), 1) + " s" for s in ss) + " |")
        n_r = max((len(s["phases"].get("render") or []) for s in ss), default=0)
        for k in range(n_r):
            cells = []
            for s in ss:
                rr = (s["phases"].get("render") or [])
                if k < len(rr):
                    x = rr[k]
                    cells.append(f"{_f(x['s'])} s for {x['length']} s" + ("" if x["status"] == "done" else f" ({x['status']})"))
                else:
                    cells.append("")
            out.append(f"| render {k + 1} | " + " | ".join(cells) + " |")
        out += ["", "### The app's own timing (run 1, GET /api/timings)", ""]
        for t in s0.get("timings") or []:
            steps = ", ".join(f"{k} {v:.1f} s" for k, v in sorted(t["steps"].items(), key=lambda kv: -kv[1]))
            out.append(f"- **{t['what']}** ({t['total']:.1f} s): {steps}")
        out += ["", "### Launches and load per step (run 1)", "",
                "| Step | Launches | of them reading the source | tools | time in them | CPU avg / max | RAM max | GPU 3D / compute / decode / encode | VRAM max |",
                "|---|---|---|---|---|---|---|---|---|"]
        order = ["open", "titles"] + [f"render {i + 1}" for i in range(n_r)] + ["idle"]
        for ph in order + sorted(set(s0["by_phase"]) - set(order)):
            b = s0["by_phase"].get(ph)
            if not b:
                continue
            tools = ", ".join(f"{k} {v}" for k, v in b["tools"].items())
            gpu = " / ".join(_f(b.get(k), 0) for k in ("gpu_3d", "gpu_compute", "gpu_dec", "gpu_enc")) if b.get("gpu_3d") is not None else "n/a"
            out.append(f"| {ph} | {b['launches']} | {b['source_reads']} | {tools} | {b['seconds']:.1f} s | "
                       f"{_f(b.get('cpu_avg'))}% / {_f(b.get('cpu_max'))}% | {_f(b.get('rss_max'), 0)} MB | {gpu} | "
                       f"{_f(b.get('vram_max'), 0) + ' MB' if b.get('vram_max') is not None else 'n/a'} |")
        out += ["", "### Slowest launches (run 1)", "", "| Step | Tool | Seconds | Arguments |", "|---|---|---|---|"]
        for r in s0["slowest_launches"]:
            out.append(f"| {r['phase']} | {r['tool']} | {r['s']:.1f} | `{r['args'].replace('|', '/')[:200]}` |")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# --auto: set up, pick the inputs, run everything, collect the app's own log
# --------------------------------------------------------------------------

FFMPEG_FILES = ("ffmpeg.exe", "ffprobe.exe")


def _ffmpeg_dlls(folder: Path) -> list:
    import re
    pat = re.compile(r"^(avcodec|avdevice|avfilter|avformat|avutil|postproc|swresample|swscale)-\d+\.dll$", re.I)
    return [f for f in folder.iterdir() if pat.match(f.name)] if folder.is_dir() else []


def installed_app_dirs() -> list:
    """Where the installed Clip Radar may be (installer/clipradar.iss: {localappdata}\\Programs\\Clip Radar)."""
    bases = [Path(os.environ["LOCALAPPDATA"]) / "Programs"] if os.environ.get("LOCALAPPDATA") else []
    bases += [Path(os.environ[k]) for k in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(k)]
    return [b / n for b in bases for n in ("Clip Radar", "ClipRadar", "VodClipper") if (b / n).is_dir()]


def pyspy_exe() -> str:
    """py-spy from PATH or next to this Python (a venv's Scripts folder isn't on PATH unless it's activated)."""
    return shutil.which("py-spy") or shutil.which("py-spy", path=str(Path(sys.executable).parent)) or ""


def setup_tools(repo: Path, log) -> dict:
    """FFmpeg in the repo's tools folder (the installed app's own build, else the pinned download), and the laughter
    detector in src/models (the installed app's, else the model release). What the app uses, so nothing differs."""
    done = {}
    tools = repo / "tools"
    tools.mkdir(exist_ok=True)
    if all((tools / f).is_file() for f in FFMPEG_FILES):
        done["ffmpeg"] = f"already in {tools}"
    else:
        for d in installed_app_dirs() + [real_data_dir() / "tools"]:
            found = next((Path(r) for r, _, fs in os.walk(d) if "ffmpeg.exe" in fs and "ffprobe.exe" in fs), None)
            if found:
                for f in [found / x for x in FFMPEG_FILES] + _ffmpeg_dlls(found):
                    shutil.copy2(f, tools / f.name)
                done["ffmpeg"] = f"copied from {found}"
                break
        else:
            if IS_WIN:
                log("  Downloading the pinned FFmpeg build (about 85 MB)...")
                subprocess.run([sys.executable, str(repo / "src" / "tools_setup.py"), "--dest", str(tools), "ffmpeg"],
                               cwd=str(repo / "src"), check=False)
                done["ffmpeg"] = "downloaded" if all((tools / f).is_file() for f in FFMPEG_FILES) else "MISSING"
            else:
                done["ffmpeg"] = "from PATH" if shutil.which("ffmpeg") else "MISSING"
    model = repo / "src" / "models" / "audio_events.onnx"
    if model.is_file():
        done["laughter model"] = "already in src/models"
    else:
        model.parent.mkdir(parents=True, exist_ok=True)
        for d in installed_app_dirs() + [real_data_dir() / "models"]:
            found = next((Path(r) / "audio_events.onnx" for r, _, fs in os.walk(d) if "audio_events.onnx" in fs), None)
            if found:
                shutil.copy2(found, model)
                done["laughter model"] = f"copied from {found.parent}"
                break
        else:
            log("  Downloading the laughter detector...")
            subprocess.run([sys.executable, str(repo / "src" / "audio_events.py"), "--download", str(model.parent)],
                           cwd=str(repo / "src"), check=False)
            done["laughter model"] = "downloaded" if model.is_file() else "MISSING (sound events won't be measured)"
    try:
        import faster_whisper  # noqa: F401
        done["whisper"] = "faster-whisper installed"
    except ImportError:
        done["whisper"] = "MISSING: titles and captions won't be measured (pip install faster-whisper)"
    return done


def videos_folder() -> Path:
    """The real Videos folder (it can be moved, e.g. into OneDrive), as the app finds it (paths._videos_folder)."""
    if IS_WIN:
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
            u = uuid.UUID("18989B1D-99B5-455B-841C-AB7C74E4DDFC")
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            ptr = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0:
                out = Path(ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(ptr)
                return out
        except Exception:
            pass
    return Path.home() / "Videos"


def _locations() -> dict:
    try:
        with open(real_data_dir() / "locations.json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def probe_seconds(path: Path) -> float:
    exe = shutil.which("ffprobe", path=str(HERE.parent / "tools")) or shutil.which("ffprobe") or "ffprobe"
    try:
        p = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60)
        return float(p.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def find_projects(log) -> list:
    """Every Clip Radar project on this PC with a finished download: {video, chat, seconds, platform, title}."""
    loc = _locations()
    roots = [loc.get("projects") or "", *reversed(loc.get("_history") or []), str(videos_folder() / "Clip Radar"),
             str(Path.home() / "Videos" / "Clip Radar")]
    roots = [Path(r) for r in dict.fromkeys(r for r in roots if r) if Path(r).is_dir()]
    dl_root = Path(loc["downloads"]) if loc.get("downloads") else None
    found, seen = [], set()
    exts = (".mp4", ".mkv", ".webm", ".mov", ".ts", ".m4v", ".flv")
    for root in roots:
        for pdir in root.iterdir():
            meta_f = pdir / "meta.json"
            if not meta_f.is_file():
                continue
            try:
                meta = json.load(open(meta_f, encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if meta.get("live"):
                continue                       # a live recording is segments, not one video
            video = None
            for d in ([dl_root / pdir.name] if dl_root else []) + [pdir]:
                if d.is_dir():
                    video = next((d / f for f in sorted(os.listdir(d)) if f.startswith("vod.") and f.lower().endswith(exts)
                                  and (d / f).stat().st_size > 1_000_000), None)
                if video:
                    break
            if not video or os.path.normcase(str(video)) in seen:
                continue
            seen.add(os.path.normcase(str(video)))
            chat = pdir / "chat.json"
            secs = float(meta.get("duration") or 0) or probe_seconds(video)
            platform = str(meta.get("platform") or ("local" if meta.get("local") else "")).lower()
            found.append({"video": str(video), "chat": str(chat) if chat.is_file() and chat.stat().st_size > 100 else "",
                          "seconds": secs, "platform": platform, "title": str(meta.get("title") or pdir.name)[:80],
                          "gb": round(video.stat().st_size / 1e9, 2)})
    log(f"  Found {len(found)} project(s) with a downloaded video in: " + ", ".join(str(r) for r in roots) or "nowhere")
    return found


def pick_inputs(projects: list) -> list:
    """The plan's inputs, as close as this PC has them: A a long VOD with chat, B a ~20 minute video, C a long video."""
    left = [p for p in projects if p["seconds"] > 120]
    picked = []

    def take(cands, key, label):
        if cands:
            best = min(cands, key=key)
            left.remove(best)
            picked.append(dict(best, role=label))
    take([p for p in left if p["chat"]], lambda p: -p["seconds"], "A: long stream with chat")
    take(left, lambda p: abs(p["seconds"] - 1200) + (0 if p["platform"] == "youtube" else 600), "B: about 20 minutes")
    take([p for p in left if p["seconds"] >= 3600], lambda p: (p["platform"] != "youtube", -p["seconds"]), "C: long video")
    return picked


def machine() -> dict:
    import platform
    info = {"os": platform.platform(), "python": platform.python_version(), "cpu": platform.processor(), "gpu": "", "ram_gb": None}
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / 1024 ** 3, 1)
        info["cores"] = f"{psutil.cpu_count(logical=False)} cores, {psutil.cpu_count()} threads"
    except ImportError:
        pass
    if IS_WIN:
        for key, q in (("cpu", "Get-CimInstance Win32_Processor | Select-Object -Expand Name"),
                       ("gpu", "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' (driver ' + $_.DriverVersion + ')' }")):
            try:
                p = subprocess.run(["powershell", "-NoProfile", "-Command", q], capture_output=True, text=True, timeout=30)
                if p.stdout.strip():
                    info[key] = "; ".join(x.strip() for x in p.stdout.splitlines() if x.strip())
            except (OSError, subprocess.TimeoutExpired):
                pass
    return info


def busy_programs() -> list:
    """Other programs using a lot of CPU or memory right now (a game, OBS, the Clip Radar app): they skew the numbers."""
    try:
        import psutil
    except ImportError:
        return []
    procs = list(psutil.process_iter(["name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(2)
    out = []
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / (psutil.cpu_count() or 1)
            mb = p.info["memory_info"].rss / 1048576 if p.info.get("memory_info") else 0
            if p.pid != os.getpid() and (cpu > 5 or mb > 1500):
                out.append({"name": p.info["name"], "cpu_pct": round(cpu, 1), "mb": round(mb)})
        except psutil.Error:
            pass
    return sorted(out, key=lambda x: -x["cpu_pct"])[:10]


def real_timings(log) -> str:
    """The Timing lines the app already wrote in real use (clipradar.log, including hands-free live moments), summarised."""
    import re
    import statistics as st
    logs_dir = Path(_locations().get("logs") or real_data_dir())
    files = sorted(set(logs_dir.glob("clipradar*.log")) | set(real_data_dir().glob("clipradar*.log")))
    line_re = re.compile(r"Timing, (\w[\w ]*?)(?: \"(.*)\")?: (.*) \(all ([\d.]+) s\)")
    by: dict = {}
    raw = []
    for f in files:
        for line in open(f, encoding="utf-8", errors="replace"):
            m = line_re.search(line)
            if not m:
                continue
            what, name, steps, total = m.group(1), m.group(2) or "", m.group(3), float(m.group(4))
            d = by.setdefault(what, {"totals": [], "steps": {}})
            d["totals"].append(total)
            for part in steps.split(", "):
                mm = re.match(r"(.+) ([\d.]+) s$", part)
                if mm:
                    d["steps"].setdefault(mm.group(1), []).append(float(mm.group(2)))
            raw.append((total, what, name, steps))
    log(f"  {len(raw)} Timing lines in {len(files)} log file(s) under {logs_dir}")
    out = ["# Real-use timings from clipradar.log", "",
           f"{len(raw)} lines from {', '.join(str(f) for f in files) or 'no log files found'}.", ""]
    for what, d in sorted(by.items(), key=lambda kv: -len(kv[1]["totals"])):
        t = d["totals"]
        out += [f"## {what} ({len(t)} times)", "",
                f"Total: median {st.median(t):.1f} s, slowest {max(t):.1f} s.", "",
                "| Step | Times seen | Median | Slowest |", "|---|---|---|---|"]
        for k, v in sorted(d["steps"].items(), key=lambda kv: -st.median(kv[1])):
            out.append(f"| {k} | {len(v)} | {st.median(v):.1f} s | {max(v):.1f} s |")
        out.append("")
    if raw:
        out += ["## The 10 slowest", ""] + [f"- {tot:.1f} s, {what} \"{name}\": {steps}" for tot, what, name, steps
                                             in sorted(raw, reverse=True)[:10]]
    return "\n".join(out) + "\n"


def write_runs(runs: list, a, out: Path):
    (out / "results.json").write_text(json.dumps({"args": vars(a), "runs": runs,
                                                  "summary": [summarize(r) for r in runs]}, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report(runs, a), encoding="utf-8")


def run_matrix(inputs: list, a, out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for inp in inputs:
        for k in range(a.runs):
            run_dir = out / f"{Path(inp['input']).parent.name[:30]}_{Path(inp['input']).stem[:20]}_run{k + 1}"
            run_dir.mkdir(parents=True, exist_ok=True)
            print(f"{inp['input']}, run {k + 1} of {a.runs} (Whisper {a.model})", flush=True)
            r = run_one(Path(a.src).resolve(), inp, a, run_dir, 7990 + len(runs))
            with open(run_dir / "launches.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["phase", "tool", "seconds", "rc", "ss", "t", "sseof", "inputs", "thread", "args"])
                for x in r["launches"]:
                    w.writerow([x["phase"], x["tool"], round(x["end"] - x["start"], 3), x.get("rc"), x["ss"], x["t"],
                                x["sseof"], ";".join(x["inputs"]), x["thread"], x["args"]])
            with open(run_dir / "samples.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["t", "phase", "cpu_pct_of_pc", "rss_mb", "children", "pc_cpu_pct"])
                w.writeheader()
                w.writerows(r["samples"])
            runs.append(r)
            if r["result"].get("stopped"):
                print(f"  stopped early: {r['result']['stopped']} (see {run_dir / 'app.log'})", flush=True)
            write_runs(runs, a, out)                # so far, in case a later run fails
    return runs


def auto(a):
    """Everything, unattended. Results in audit_runs/<date>/: SUMMARY.md first."""
    repo = HERE.parent
    out = Path(a.out or repo / "audit_runs" / time.strftime("%Y%m%d_%H%M"))
    out.mkdir(parents=True, exist_ok=True)
    os.environ["PATH"] = str(repo / "tools") + os.pathsep + os.environ.get("PATH", "")   # FFmpeg for the test stream
    notes = []

    def log(m):
        print(m, flush=True)
        notes.append(m)
    log("1. Setting up")
    tools = setup_tools(repo, log)
    for k, v in tools.items():
        log(f"  {k}: {v}")
    if "MISSING" in tools.get("ffmpeg", ""):
        raise SystemExit("No FFmpeg: nothing can be measured. Install Clip Radar or run scripts\\install.bat, then try again.")
    log("2. Finding videos to measure")
    projects = find_projects(log)
    inputs = pick_inputs(projects)
    if a.input:
        inputs = [{"video": os.path.abspath(v), "chat": os.path.abspath(a.chat[i]) if i < len(a.chat) and a.chat[i] else "",
                   "seconds": probe_seconds(Path(v)), "role": "given", "platform": "", "title": Path(v).stem}
                  for i, v in enumerate(a.input)]
    if not inputs:
        log("  No downloaded videos found. Using the made-up 30-minute test stream instead (less useful: no real footage).")
        sys.path.insert(0, str(HERE))
        import bench_pipeline
        cache = Path(tempfile.gettempdir()) / "clipradar_bench"
        cache.mkdir(exist_ok=True)
        v, c = bench_pipeline.make_stream(str(cache), 30)
        inputs = [{"video": v, "chat": c, "seconds": 1800, "role": "made-up test stream", "platform": "", "title": "bench"}]
    for p in inputs:
        log(f"  {p['role']}: {p['title']} ({p['seconds'] / 60:.0f} min, {'chat' if p['chat'] else 'no chat'}) {p['video']}")
    busy = busy_programs()
    if busy:
        log("  Busy right now (may skew the numbers): " + ", ".join(f"{b['name']} {b['cpu_pct']}% CPU {b['mb']} MB" for b in busy))
    mach = machine()
    (out / "setup.json").write_text(json.dumps({"machine": mach, "tools": tools, "projects_found": projects,
                                                "inputs": inputs, "busy": busy}, indent=1), encoding="utf-8")
    hours = sum(p["seconds"] for p in inputs) / 3600
    log(f"3. Measuring. Rough guess: {20 + 25 * hours * a.runs:.0f} to {40 + 45 * hours * a.runs:.0f} minutes. Leave the PC alone meanwhile.")
    ins = [{"input": p["video"], "chat": p["chat"]} for p in inputs]
    a.model = a.model or "small"
    run_matrix(ins, a, out / f"whisper_{a.model}")
    if not a.quick and a.compare_model and a.compare_model != a.model:
        b = argparse.Namespace(**vars(a))
        b.model, b.runs = a.compare_model, 1
        run_matrix(ins[:1], b, out / f"whisper_{b.model}")
    log("4. Reading the app's own log from real use")
    (out / "real_timings.md").write_text(real_timings(log), encoding="utf-8")
    summary = ["# Speed audit: what ran", "",
               f"{time.strftime('%Y-%m-%d %H:%M')}. Read the files below; `docs/plans/efficiency-audit.md` says what to do with them.", "",
               "## Machine", ""] + [f"- {k}: {v}" for k, v in mach.items()] + ["", "## Setup and inputs", ""] + \
              [f"- {n.strip()}" for n in notes if n.startswith("  ")] + \
              ["", "## Files", "",
               f"- `whisper_{a.model}/report.md`: each input, {a.runs} run(s), Whisper {a.model}",
               *([f"- `whisper_{a.compare_model}/report.md`: the first input once with Whisper {a.compare_model}"]
                 if not a.quick and a.compare_model and a.compare_model != a.model else []),
               "- `real_timings.md`: the Timing lines from clipradar.log (real use, live moments included)",
               "- `setup.json`: machine, tools, every project found, what was busy",
               "- each run folder: `app.log`, `launches.csv` (every FFmpeg/Whisper run), `samples.csv` (CPU and memory each second),"
               " `gpu.csv` (Windows), `open_flame.svg` (py-spy)"]
    (out / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"\nDone. Start with {out / 'SUMMARY.md'}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto", action="store_true",
                    help="do everything: set up FFmpeg and the laughter model, pick videos from your projects, run, "
                         "and collect the app's log (scripts\\speed_audit.bat runs this)")
    ap.add_argument("--quick", action="store_true", help="with --auto: one run each, no second Whisper model")
    ap.add_argument("--compare-model", default="large-v3", help="with --auto: a second Whisper model, once (default large-v3)")
    ap.add_argument("--input", action="append", default=[], help="a video file (repeat for several)")
    ap.add_argument("--chat", action="append", default=[], help="chat file for the --input before it (in order; '' for none)")
    ap.add_argument("--model", default="small", help="Whisper model for titles and captions (default small)")
    ap.add_argument("--clips", type=int, default=3, help="clips to render per input (default 3)")
    ap.add_argument("--count", type=int, default=0, help="clips to find (0 = automatic, as the app does)")
    ap.add_argument("--runs", type=int, default=0, help="runs per input (default 1, or 3 with --auto)")
    ap.add_argument("--idle", type=int, default=60, help="seconds to measure the app idle at the end (default 60)")
    ap.add_argument("--cpu", action="store_true", help="CPU encoder only")
    ap.add_argument("--no-titles", action="store_true", help="skip the titles step")
    ap.add_argument("--pyspy", action="store_true", help="flame graph of the open step (py-spy on PATH; on with --auto)")
    ap.add_argument("--src", default=str(SRC), help="the src folder to measure (default this checkout's)")
    ap.add_argument("--out", default="", help="results folder (default audit_speed_<date>, or audit_runs/<date> with --auto)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway data and clips")
    a = ap.parse_args()
    try:
        import psutil  # noqa: F401
    except ImportError:
        print("psutil isn't installed, so there'll be no CPU or memory numbers: pip install psutil", flush=True)
    if a.auto:
        a.runs = a.runs or (1 if a.quick else 3)
        a.pyspy = a.pyspy or bool(pyspy_exe())
        auto(a)
        return
    if not a.input:
        ap.error("give --input (or use --auto)")
    a.runs = a.runs or 1
    inputs = []
    for i, path in enumerate(a.input):
        if not os.path.isfile(path):
            raise SystemExit(f"Not a file: {path}")
        chat = a.chat[i] if i < len(a.chat) else ""
        if chat and not os.path.isfile(chat):
            raise SystemExit(f"Not a file: {chat}")
        inputs.append({"input": os.path.abspath(path), "chat": os.path.abspath(chat) if chat else ""})
    out = Path(a.out or f"audit_speed_{time.strftime('%Y%m%d_%H%M')}")
    run_matrix(inputs, a, out)
    print(f"\nDone: {out / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
