"""
Which engine each Whisper task runs on (4.37): finding the card, the choice by task and load, whisper.cpp on Vulkan and
its way back to the CPU, and captions never keeping words the card guessed for a title.

There's no GPU in CI: the card is a stand-in, and whisper-cli is a small script that answers the way the real one does.

  python tests/test_gpu_choice.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "src", "stubs"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import gpu  # noqa: E402
import server  # noqa: E402
import whispercpp  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- finding the card
check("makers from Windows' names", [gpu._vendor(n) for n in ("AMD Radeon RX 7600 XT", "Radeon RX 480", "Intel(R) Arc(TM) A380",
                                                              "NVIDIA GeForce RTX 3060", "Matrox G200")]
      == ["amd", "amd", "intel", "nvidia", ""])
check("virtual adapters aren't cards", all(gpu.NOT_A_CARD.search(n) for n in (
    "Microsoft Basic Display Adapter", "Parsec Virtual Display Adapter", "Microsoft Remote Display Adapter")))
gpu._cache["cards"] = [{"name": "AMD Radeon RX 7600 XT", "vendor": "amd", "vram_mb": 16368},
                       {"name": "Intel(R) UHD Graphics", "vendor": "intel", "vram_mb": 128}]
check("the card with the most memory is the one used", gpu.card()["vendor"] == "amd")
gpu._cache["cards"] = [{"name": "Matrox G200", "vendor": "", "vram_mb": 16}]
check("a card nobody can use counts as none", gpu.card()["vendor"] == "")
gpu.forget()
check("the real list reads without failing", isinstance(gpu.cards(), list))
check("the CPU's load reads", 0 <= gpu.cpu_load() <= 100)
line = ('"(PDH-CSV 4.0)","\\\\PC\\GPU Engine(pid_1_luid_0x0_0xC9_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
        '"\\\\PC\\GPU Engine(pid_2_luid_0x0_0xC9_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
        '"\\\\PC\\GPU Engine(pid_3_luid_0x0_0xC9_phys_0_eng_1_engtype_Compute_0)\\Utilization Percentage"\n'
        '"10/31/2026 01:00:00.000","40.5","30.0","12.0"\n')
real_run = gpu.subprocess.run
gpu.subprocess.run = lambda *a, **k: type("R", (), {"stdout": line})()
check("the card's load is its busiest engine, added up across programs", gpu._read_gpu_load() == 70.5, gpu._read_gpu_load())
gpu.subprocess.run = lambda *a, **k: type("R", (), {"stdout": "Error: no such counter"})()
check("no counters (an old Windows): the load reads 0, not an error", gpu._read_gpu_load() == 0.0)
gpu.subprocess.run = real_run

# ---------------------------------------------------------------- the choice
d = gpu.decide
check("titles go on a free card", d("titles", "auto", True, 30, False)[0] == "vulkan")
check("Deep scan goes on a free card", d("scan", "auto", True, 30, False)[0] == "vulkan")
check("captions stay on the CPU, however free the card", d("captions", "auto", True, 95, False)[0] == "whisper")
check("a busy card and a CPU with room: titles move to the CPU", d("titles", "auto", True, 30, True)[0] == "whisper")
check("both busy: the card, which takes the least CPU", d("titles", "auto", True, 90, True)[0] == "vulkan")
check("you choosing the CPU wins", d("titles", "cpu", True, 0, False)[0] == "whisper")
check("you choosing the card wins, captions too", d("captions", "gpu", True, 95, True)[0] == "vulkan")
check("no engine on the card, nothing goes there", d("titles", "gpu", False, 0, False)[0] == "whisper")
check("the mode is kept", gpu.set_mode("cpu")["mode"] == "cpu" and gpu.mode() == "cpu")
try:
    gpu.set_mode("turbo")
    check("a made-up mode is refused", False)
except ValueError:
    check("a made-up mode is refused", gpu.mode() == "cpu")
gpu.set_mode("auto")

# ---------------------------------------------------------------- whisper.cpp, through a stand-in whisper-cli
AUDIO = np.zeros(16000 * 30, dtype=np.float32)
fake = os.path.join(tempfile.mkdtemp(prefix="cli_"), "fake_cli.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write('''import json, os, sys, time
how = os.environ.get("FAKE_CLI", "ok")
print("whisper_backend_init_gpu: no GPU found", flush=True)      # its speech detector, on the CPU on purpose
if how != "nogpu":
    print("whisper_backend_init_gpu: using Vulkan0 backend", flush=True)
print("whisper_print_progress_callback: progress =  50%", flush=True)
if how == "crash":
    sys.exit(3)
if how == "slow":
    time.sleep(float(os.environ["FAKE_SLEEP"]))
words = ["hello", "there", "thanks", "for", "watching", "chat"]
json.dump({"transcription": [{"offsets": {"from": 500 * i, "to": 500 * i + 400}, "text": " " + w} for i, w in enumerate(words)]},
          open("a.json", "w"))
''')
real_command = whispercpp.command
whispercpp.command = lambda size, lang: [sys.executable, fake]
stop = engine._HALLUCINATION
steps = []
words = whispercpp.transcribe(AUDIO, "small", "en", print, stop, steps.append)
check("whisper-cli's words come out in engine.transcribe's shape, made-up phrases gone",
      [w["word"] for w in words] == ["hello", "there", "chat"] and words[0]["end"] == 0.4, words)
check("its progress comes through", steps == [0.5], steps)
cmd = " ".join(real_command("large-v3", ""))
check("the command matches engine._transcribe's choices",
      all(x in cmd for x in ("-l auto", "-bs 5", "-mc 0", "-vsd 400", "-vspd 0", "-vp 400", "ggml-large-v3.bin")), cmd)
check("no leftovers in the temp folder", not [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("vk_")])

os.environ["FAKE_CLI"] = "crash"
for n in range(whispercpp.STRIKES):
    try:
        whispercpp.transcribe(AUDIO, "small", "en", print)
        check("a crash is a failure", False)
    except whispercpp.Failed:
        pass
check("failing twice in a row hands it to the CPU for the session", whispercpp.turned_off())
gpu.forget()
os.environ["FAKE_CLI"] = "nogpu"
try:
    whispercpp.transcribe(AUDIO, "small", "en", print)
    check("no GPU in the end is a failure", False)
except whispercpp.Failed as e:
    check("no GPU in the end is a failure", "no graphics card" in str(e), e)
gpu.forget()
os.environ["FAKE_CLI"] = "ok"
whispercpp.transcribe(AUDIO, "small", "en", print)
check("one failure doesn't stick", not whispercpp.turned_off())
os.environ.update(FAKE_CLI="slow", FAKE_SLEEP="1.5")
short = np.zeros(16000 * 20, dtype=np.float32)
real_time = whispercpp.time.time
t0 = real_time()
whispercpp.time.time = lambda: t0 + (real_time() - t0) * 20    # as if this took 30 s for 20 s of sound
real_cpu_load = gpu.cpu_load
gpu.cpu_load = lambda: 20.0
for n in range(whispercpp.STRIKES):
    whispercpp.transcribe(short, "small", "en", print)
whispercpp.time.time = real_time
check("a card slower than the sound hands it to the CPU too", whispercpp.turned_off())
gpu.forget()
gpu.cpu_load = lambda: 98.0                  # a game has the CPU as well: it would do no better
t0 = real_time()
whispercpp.time.time = lambda: t0 + (real_time() - t0) * 20
for n in range(whispercpp.STRIKES):
    whispercpp.transcribe(short, "small", "en", print)
whispercpp.time.time = real_time
gpu.cpu_load = real_cpu_load
check("slow with the CPU full too: it stays on the card", not whispercpp.turned_off())
gpu.forget()
os.environ["FAKE_CLI"] = "ok"
whispercpp._strike("a test")
whispercpp._strike("a test")
check("turned off, it's off", whispercpp.turned_off())
whispercpp.time.time = lambda: real_time() + whispercpp.RETRY_AFTER + 1
check("and the card gets another go later", not whispercpp.turned_off())
whispercpp._strike("a test")
check("one more failure then turns it off again", whispercpp.turned_off())
whispercpp.time.time = real_time
gpu.forget()


class Stop(Exception):
    pass


def cancelled():
    raise Stop()


try:
    whispercpp.transcribe(AUDIO, "small", "en", print, check=cancelled)
    check("cancelling stops it", False)
except Stop:
    check("cancelling stops it, and it doesn't count against the card", not whispercpp.turned_off())

# ---------------------------------------------------------------- the choice in practice
real = (gpu.whisper_gpu_ok, whispercpp.installed, whispercpp.has_model, gpu.card, gpu.cpu_load, gpu.gpu_load)
ran = []
real_cpp, real_cpu = whispercpp.transcribe, engine._transcribe
whispercpp.transcribe = lambda *a, **k: ran.append("card") or [{"start": 2.0, "end": 2.5, "word": "card"}]
engine._transcribe = lambda *a, **k: ran.append("cpu") or [{"start": 2.0, "end": 2.5, "word": "cpu"}]
try:
    gpu.whisper_gpu_ok = lambda: False
    whispercpp.installed = lambda: True
    whispercpp.has_model = lambda s: True
    gpu.card = lambda: {"name": "AMD Radeon RX 7600 XT", "vendor": "amd", "vram_mb": 16368}
    gpu.cpu_load, gpu.gpu_load = (lambda: 30.0), (lambda wait=True: 5.0)
    check("a Radeon: titles on the card", gpu.choose("titles", "large-v3")["engine"] == "vulkan")
    check("and Settings is told why", gpu._routes["titles"]["on"] == "gpu" and "free" in gpu._routes["titles"]["why"])
    check("and a caption stays on the CPU", gpu.choose("captions", "large-v3")["engine"] == "whisper")
    gpu.gpu_load = lambda wait=True: 96.0
    check("a game on the card: titles move to the CPU", gpu.choose("titles", "large-v3")["engine"] == "whisper")
    gpu.gpu_load = lambda wait=True: 5.0
    taken = [engine._hw_slots.acquire(blocking=False) for _ in range(engine.HW_SLOTS)]
    check("every render slot on the card taken: titles move too", gpu.choose("titles", "large-v3")["engine"] == "whisper")
    for _ in taken:
        engine._hw_slots.release()
    gpu.card = lambda: {"name": "Small card", "vendor": "amd", "vram_mb": 2048}
    check("a model too big for the card stays on the CPU", gpu.choose("titles", "large-v3")["engine"] == "whisper")
    check("a smaller one fits", gpu.choose("titles", "small")["engine"] == "vulkan")
    gpu.card = lambda: {"name": "AMD Radeon RX 7600 XT", "vendor": "amd", "vram_mb": 16368}
    asked = []
    real_later = whispercpp.fetch_later
    whispercpp.fetch_later = lambda s, log: asked.append(s)
    whispercpp.has_model = lambda s: False
    check("a model not here yet: the CPU now, the download behind", gpu.choose("titles", "medium")["engine"] == "whisper"
          and asked == ["medium"])
    asked.clear()
    gpu.gpu_load = lambda wait=True: asked.append("load") or 5.0
    check("a caption stays on the CPU without fetching a model it won't use, or reading the card's load",
          gpu.choose("captions", "large-v3")["engine"] == "whisper" and asked == [], asked)
    check("and Settings is told the real reason", "exact" in gpu._routes["captions"]["why"], gpu._routes["captions"])
    gpu.gpu_load = lambda wait=True: 5.0
    gpu.set_mode("gpu")
    check("captions you put on the card still fetch their model", gpu.choose("captions", "large-v3")["engine"] == "whisper"
          and asked == ["large-v3"], asked)
    gpu.set_mode("auto")
    whispercpp.fetch_later = real_later
    whispercpp.has_model = lambda s: True
    whispercpp._strike("a test")
    whispercpp._strike("a test")
    gpu.choose("titles", "small")
    check("Settings says the card stopped, not that nothing's installed", "stopped" in gpu._routes["titles"]["why"],
          gpu._routes["titles"])
    gpu.forget()
    gpu.whisper_gpu_ok = lambda: True
    check("NVIDIA with its libraries stays with faster-whisper on its GPU", gpu.choose("titles", "small")["engine"] == "whisper"
          and gpu._routes["titles"]["on"] == "gpu")
    gpu.whisper_gpu_ok = lambda: False

    logs = []
    pick = {"engine": "vulkan", "why": ""}

    def fails(*a, **k):
        raise whispercpp.Failed("no driver")
    whispercpp.transcribe = fails
    out = engine.transcribe(AUDIO, "small", "en", logs.append, pick=pick)
    check("the card failing: the CPU does this one, and it says so", out[0]["word"] == "cpu"
          and any("runs on the CPU" in m for m in logs), logs)
    whispercpp.transcribe = lambda *a, **k: ran.append("card") or [{"start": 2.0, "end": 2.5, "word": "card"}]

    # ------------------------------------------------------------ a caption the speech detector mostly missed
    def missed(share):
        def cpu(*a, **k):
            ran.append("cpu")
            a[6]["share"] = share
            return [{"start": 2.0, "end": 2.5, "word": "cpu"}]
        return cpu
    caption = gpu.choose("captions", "large-v3")
    engine._transcribe = missed(0.26)
    ran.clear()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = engine.transcribe(AUDIO, "large-v3", "en", print, pick=caption)
    check("a caption the detector mostly missed is heard again on the card", ran == ["cpu", "card"] and out[0]["word"] == "card", ran)
    check("and the log says so", "26% heard" in said.getvalue(), said.getvalue())
    engine._transcribe = missed(0.8)
    ran.clear()
    out = engine.transcribe(AUDIO, "large-v3", "en", print, pick=caption)
    check("a caption it heard stays on the CPU", ran == ["cpu"] and out[0]["word"] == "cpu", ran)
    engine._transcribe = missed(0.26)
    ran.clear()
    engine.transcribe(np.zeros(16000 * 10, dtype=np.float32), "large-v3", "en", print, pick=caption)
    check("a short stretch isn't heard again", ran == ["cpu"], ran)
    ran.clear()
    engine.transcribe(AUDIO, "large-v3", "en", print, pick={"engine": "whisper", "why": "", "task": "titles"})
    check("a title on the CPU isn't either: the card is busy", ran == ["cpu"], ran)
    gpu.set_mode("cpu")
    ran.clear()
    engine.transcribe(AUDIO, "large-v3", "en", print, pick=caption)
    check("you choosing the CPU wins", ran == ["cpu"], ran)
    gpu.set_mode("auto")
    whispercpp.has_model = lambda s: False
    ran.clear()
    out = engine.transcribe(AUDIO, "large-v3", "en", print, pick=caption)
    check("no model on the card: the CPU's words", ran == ["cpu"] and out[0]["word"] == "cpu", ran)
    whispercpp.has_model = lambda s: True
    whispercpp.transcribe = fails
    ran.clear()
    logs = []
    out = engine.transcribe(AUDIO, "large-v3", "en", logs.append, pick=caption)
    check("the card failing keeps the CPU's words", out[0]["word"] == "cpu", out)
    whispercpp.transcribe = lambda *a, **k: ran.append("card") or [{"start": 2.0, "end": 2.5, "word": "card"}]
    engine._transcribe = lambda *a, **k: ran.append("cpu") or [{"start": 2.0, "end": 2.5, "word": "cpu"}]

    # ------------------------------------------------------------ a title's words don't become a caption's
    pr = {"tx_lock": threading.Lock(), "duration": 60.0, "video": "x.mp4"}
    server.engine.extract_audio = lambda v, a, n: np.zeros(int(16000 * n), dtype=np.float32)
    server._vocab = lambda p: ""
    ran.clear()
    words = server.ensure_words(pr, 10, 20, "small", "en", task="titles")
    check("a title is heard on the card", ran == ["card"] and [w["word"] for w in words] == ["card"], (ran, words))
    tx = pr["tx"]["small|en"]
    check("and that stretch is marked rough", tx["rough"] == [(10, 20)], tx)
    ran.clear()
    server.ensure_words(pr, 10, 20, "small", "en", task="titles")
    check("a second look reuses it", ran == [])
    words = server.ensure_words(pr, 10, 20, "small", "en", task="captions")
    check("the caption hears it again on faster-whisper", ran == ["cpu"] and [w["word"] for w in words] == ["cpu"], (ran, words))
    check("and it isn't rough any more", tx["rough"] == [], tx)
    ran.clear()
    server.ensure_words(pr, 10, 20, "small", "en", task="captions")
    check("the next render reuses those words", ran == [])
    server.ensure_words(pr, 0, 30, "small", "en", task="titles")
    ran.clear()
    server.ensure_words(pr, 0, 30, "small", "en", task="captions")
    check("only the rough parts are heard again", ran == ["cpu", "cpu"] and server._without(tx["covered"], tx["rough"]) == [(0, 30)], (ran, tx))
    check("rough stretches split around what's cut out", server._without([(0, 10), (20, 30)], [(5, 25)]) == [(0, 5), (25, 30)])
finally:
    gpu.whisper_gpu_ok, whispercpp.installed, whispercpp.has_model, gpu.card, gpu.cpu_load, gpu.gpu_load = real
    whispercpp.transcribe, engine._transcribe = real_cpp, real_cpu

# ---------------------------------------------------------------- the download
FILES = {"/whisper-cli.exe": b"program", "/vad/" + whispercpp.VAD[0]: b"vad", "/m/" + whispercpp.MODELS["small"][0]: b"model" * 40}
BAD = {"on": False}


class Files(BaseHTTPRequestHandler):
    def do_GET(self):
        body = FILES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(b"damaged" if BAD["on"] and self.path.endswith(".exe") else body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Files)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
keep = (whispercpp.RELEASE, whispercpp.VAD_AT, whispercpp.MODELS_AT, whispercpp.BIN_SHA256, whispercpp.VAD, dict(whispercpp.MODELS))
sha = lambda b: hashlib.sha256(b).hexdigest()     # noqa: E731
whispercpp.RELEASE, whispercpp.VAD_AT, whispercpp.MODELS_AT = base, base + "/vad", base + "/m"
whispercpp.BIN_SHA256 = sha(b"program")
whispercpp.VAD = (whispercpp.VAD[0], sha(b"vad"))
whispercpp.MODELS["small"] = (whispercpp.MODELS["small"][0], 1, sha(b"model" * 40))
real_tr = whispercpp.transcribe
try:
    BAD["on"] = True
    try:
        whispercpp.install()
        check("a damaged download is refused", False)
    except RuntimeError as e:
        check("a damaged download is refused", "checksum" in str(e) and not whispercpp.exe().exists(), str(e))
    check("and leaves no part file", not [n for n in os.listdir(whispercpp.folder()) if n.endswith(".part")])
    BAD["on"] = False
    dry = []
    whispercpp.transcribe = lambda a, s, lang, log: dry.append(s) or []
    steps = []
    whispercpp.install(progress=steps.append)
    if sys.platform == "win32":
        check("the program, the detector and the first model arrive", whispercpp.installed() and whispercpp.has_model("small"))
    check("each with progress to the end", steps[-1] == 1.0 and all(a <= b for a, b in zip(steps, steps[1:])), steps)
    check("and a dry run proves it works", dry == ["small"])
    whispercpp.transcribe = real_tr
    logs = []
    whispercpp.MODELS["tiny"] = (whispercpp.MODELS["tiny"][0], 1, sha(b"tiny"))
    FILES["/m/" + whispercpp.MODELS["tiny"][0]] = b"tiny"
    whispercpp.fetch_later("tiny", logs.append)
    whispercpp.fetch_later("tiny", logs.append)       # asked twice while it's on its way: one download
    for _ in range(100):
        if whispercpp.has_model("tiny"):
            break
        time.sleep(0.05)
    check("a bigger model comes in the background", whispercpp.has_model("tiny"))
    time.sleep(0.1)
    check("once, and it says so", sum("Getting Whisper" in m for m in logs) == 1, logs)
    whispercpp.remove()
    check("it can all be removed", not whispercpp.folder().exists() and not whispercpp.installed())
finally:
    whispercpp.transcribe = real_tr
    whispercpp.RELEASE, whispercpp.VAD_AT, whispercpp.MODELS_AT, whispercpp.BIN_SHA256, whispercpp.VAD = keep[:5]
    whispercpp.MODELS.clear()
    whispercpp.MODELS.update(keep[5])
    srv.shutdown()

check("every download is pinned to a checksum", all(re.fullmatch(r"[0-9a-f]{64}", m[2]) for m in whispercpp.MODELS.values())
      and re.fullmatch(r"[0-9a-f]{64}", whispercpp.VAD[1]) and re.fullmatch(r"[0-9a-f]{64}", whispercpp.BIN_SHA256), whispercpp.BIN_SHA256)
st = gpu.status()
check("Settings gets what it needs", all(k in st for k in ("card", "vendor", "vulkan", "can_download", "mode", "routes", "cpu_load", "gpu_load")), st)
json.dumps(st)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
