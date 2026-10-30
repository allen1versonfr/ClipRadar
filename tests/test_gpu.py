"""
Using the graphics card (4.36): GPU encoders (H.264 and H.265), decoding on the GPU, how a GPU failure is
handled (one render on the CPU, not the whole session), how many renders encode on the GPU at once, Whisper
on the GPU (and the size Deep scan picks), and the GPU support download for Whisper.

There's no GPU in CI: a "GPU encoder" here is FFmpeg's x264 under an NVENC name (or a name FFmpeg doesn't
have, to fail), Whisper is a stand-in, and the download comes from a stand-in PyPI.

  python tests/test_gpu.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import engine  # noqa: E402
import gpu  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- failures don't turn the GPU off for good
engine._encoder_cache.clear()
engine._hw_fails["n"] = 0
engine._encoder_cache["hw"] = ("h264_nvenc", ["-c:v", "h264_nvenc"])
check("the GPU being busy (too many encodes, memory) never turns it off",
      not engine.hw_failed("h264_nvenc", "OpenEncodeSessionEx failed: incompatible client key (21)")
      and not engine.hw_failed("h264_nvenc", "CUDA_ERROR_OUT_OF_MEMORY: out of memory")
      and engine._encoder_cache["hw"] is not None and engine._hw_fails["n"] == 0)
check("one other failure doesn't either", not engine.hw_failed("h264_nvenc", "Error while opening encoder")
      and engine._encoder_cache["hw"] is not None)
engine.hw_worked()
check("a render that worked starts the count again", not engine.hw_failed("h264_nvenc", "Error while opening encoder"))
check("two in a row do", engine.hw_failed("h264_nvenc", "Error while opening encoder") and engine._encoder_cache["hw"] is None)

# ---------------------------------------------------------------- H.265 on the GPU
real_run = engine.run
engine._encoder_cache.clear()
engine._encoder_cache["listing"] = " V....D hevc_nvenc  NVIDIA NVENC hevc encoder\n V....D h264_nvenc ..."
engine.run = lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", "")
try:
    name, args = engine.video_encoder(engine.RenderOpts(vcodec="hevc", hw_encode=True))
    check("H.265 encodes on the GPU when it can", name == "hevc_nvenc" and "hvc1" in args, (name, args))
    name, args = engine.video_encoder(engine.RenderOpts(vcodec="hevc", hw_encode=True, vbitrate=8000))
    check("with a bitrate, the GPU's quality setting is swapped for it", "-cq" not in args and "8000k" in args, args)
    check("and on the CPU when told to", engine.video_encoder(engine.RenderOpts(vcodec="hevc", hw_encode=False))[0] == "libx265")
finally:
    engine.run = real_run
engine._encoder_cache.clear()
engine._encoder_cache.update(listing="", hw=None, hevc=None)
check("no GPU encoder: x265 and x264, as before", engine.video_encoder(engine.RenderOpts(vcodec="hevc"))[0] == "libx265"
      and engine.video_encoder(engine.RenderOpts())[0] == "libx264")

# ---------------------------------------------------------------- renders (FFmpeg)
if engine.which("ffmpeg"):
    TMP = tempfile.mkdtemp(prefix="vc_gpu_")
    SRC = os.path.join(TMP, "src.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", SRC], check=True)
    STANDIN = ("h264_nvenc", ["-c:v", "libx264", "-preset", "ultrafast"])          # "the GPU", FFmpeg's x264 under it
    BROKEN = ("h264_nvenc", ["-c:v", "no_such_gpu_encoder"])

    def render(name, cache, **kw):
        engine._encoder_cache.clear()
        engine._encoder_cache.update(cache)
        out = os.path.join(TMP, name)
        diag, logs = {}, []
        engine.render_clip(SRC, 1.0, 4.0, out, engine.RenderOpts(layout="blur", captions=False, hw_encode=True, speed="fast",
                                                                 **kw), title="", log=logs.append, diag=diag)
        return out, diag, logs

    engine._hw_fails["n"] = 0
    out, diag, logs = render("one_fail.mp4", {"hw": BROKEN})
    check("a GPU encode that fails: the clip renders on the CPU", os.path.exists(out) and diag["encoder"] == "libx264"
          and "failed, used the CPU" in diag["fallback"], diag.get("fallback"))
    check("and the GPU is still used for the next clip (it was off until a restart before)",
          engine._encoder_cache.get("hw") == BROKEN, engine._encoder_cache.get("hw"))
    engine._encoder_cache["hw"] = BROKEN
    out, diag, logs = render("two_fail.mp4", {"hw": BROKEN})
    check("a second failure in a row turns it off for the session", engine._encoder_cache.get("hw") is None
          and any("rest of this session" in m for m in logs), logs)
    out, diag, logs = render("hevc_fallback.mp4", {"hevc": ("hevc_nvenc", ["-c:v", "no_such_gpu_encoder"])}, vcodec="hevc")
    check("an H.265 clip whose GPU encode fails is still H.265 (it fell back to H.264 before)", diag["encoder"] == "libx265",
          diag.get("encoder"))

    engine._hw_fails["n"] = 0
    out, diag, logs = render("gpu.mp4", {"hw": STANDIN})
    check("the GPU decodes the source too", "-hwaccel" in diag["cmd"] and os.path.exists(out), diag.get("cmd", "")[:200])
    taken = [engine._hw_slots.acquire(blocking=False) for _ in range(engine.HW_SLOTS)]
    try:
        out, diag, logs = render("busy.mp4", {"hw": STANDIN})
        check(f"with {engine.HW_SLOTS} clips already encoding on the GPU, the next one uses the CPU instead of failing",
              diag["encoder"] == "libx264" and "already on the GPU" in diag["fallback"] and os.path.exists(out), diag.get("fallback"))
    finally:
        for t in taken:
            if t:
                engine._hw_slots.release()
    free = [engine._hw_slots.acquire(blocking=False) for _ in range(engine.HW_SLOTS)]
    check("every render gives its GPU slot back", all(free), free)
    for t in free:
        if t:
            engine._hw_slots.release()

    old = engine.HW_DECODE
    engine.HW_DECODE = ["-hwaccel", "no_such_hwaccel"]
    try:
        out, diag, logs = render("dec_fail.mp4", {"hw": STANDIN})
        check("decoding on the GPU fails: decoded on the CPU, same encoder", os.path.exists(out) and diag["hw_decode"] is False
              and diag["encoder"] == "h264_nvenc" and "GPU decoding failed" in diag["fallback"], diag.get("fallback"))
        render("dec_fail2.mp4", {"hw": STANDIN, "hwdec_fails": 1})
        check("twice: GPU decoding isn't tried again this session", engine._encoder_cache.get("hwdec") is False)
    finally:
        engine.HW_DECODE = old
    engine._encoder_cache.clear()
    o_cpu = engine.RenderOpts(layout="blur", captions=False, hw_encode=False, speed="fast")
    d = {}
    engine.render_clip(SRC, 1.0, 3.0, os.path.join(TMP, "cpu.mp4"), o_cpu, title="", log=lambda m: None, diag=d)
    check("GPU turned off in Settings: nothing on the GPU", "-hwaccel" not in d["cmd"] and d["encoder"] == "libx264")
else:
    print("[skip] no FFmpeg: renders not checked")

# ---------------------------------------------------------------- Whisper on the GPU
loaded = []


class FakeModel:
    def __init__(self, size, device, compute_type):
        loaded.append((device, compute_type))
        if (device, compute_type) in FakeModel.fails:
            raise RuntimeError(FakeModel.fails[(device, compute_type)])

    def transcribe(self, *a, **k):
        return iter([]), None


sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeModel)
real = (gpu.nvidia_gpu, gpu.cuda_libs)
try:
    gpu.nvidia_gpu, gpu.cuda_libs = (lambda: "NVIDIA GeForce RTX 3060"), (lambda: True)
    FakeModel.fails = {("cuda", "float16"): "CUDA failed with error out of memory"}
    engine._whisper_cache.clear()
    logs = []
    engine._load_whisper("small", logs.append)
    check("a card float16 doesn't fit on runs Whisper in int8_float16, on the GPU (it went to the CPU before)",
          loaded == [("cuda", "float16"), ("cuda", "int8_float16")] and engine.whisper_device() == "cuda", loaded)
    loaded.clear()
    engine._whisper_cache.clear()
    gpu.cuda_libs = lambda: False
    logs = []
    engine._whisper_said.clear()
    engine._load_whisper("base", logs.append)
    check("no CUDA libraries: straight to the CPU, without a failed GPU try", loaded == [("cpu", "int8")], loaded)
    check("and it says why, and what to do", any("GPU support" in m and "RTX 3060" in m for m in logs), logs)
    import server  # noqa: E402
    check("Deep scan picks 'base' when Whisper can't use the GPU (a CUDA device alone picked 'small')",
          server._deep_model("auto") == "base")
    gpu.cuda_libs = lambda: True
    check("and 'small' when it can", server._deep_model("auto") == "small")
finally:
    gpu.nvidia_gpu, gpu.cuda_libs = real
    engine._whisper_cache.clear()

# ---------------------------------------------------------------- the GPU support download
WIN = sys.platform == "win32"
TAG = "win_amd64" if WIN else "manylinux_2_27_x86_64"
LIBDIR = "bin" if WIN else "lib"
NAMES = gpu.LIBS["win32"] if WIN else gpu.LIBS["linux"]


def wheel(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


WHEELS = {
    "cublas.whl": wheel({f"nvidia/cublas/{LIBDIR}/{NAMES[0]}": b"x" * 1000, f"nvidia/cublas/{LIBDIR}/{NAMES[1]}": b"y" * 500,
                         "nvidia/cublas/include/cublas.h": b"header", "nvidia_cublas_cu12-12.9.dist-info/METADATA": b"m"}),
    "cudnn.whl": wheel({f"nvidia/cudnn/{LIBDIR}/{NAMES[2]}": b"z" * 800}),
}
BAD = {"on": False}


class PyPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path.endswith("/json"):
            pkg = self.path.split("/")[1]
            key = "cublas.whl" if "cublas" in pkg else "cudnn.whl"
            good = {"filename": f"{pkg.replace('-', '_')}-1-py3-none-{TAG}.whl", "url": f"{base}/f/{key}",
                    "digests": {"sha256": hashlib.sha256(WHEELS[key]).hexdigest()}, "size": len(WHEELS[key])}
            major = 12 if "cublas" in pkg else 9
            body = {"releases": {f"{major}.1.0": [good], f"{major + 1}.0.0": [dict(good, url=f"{base}/f/nope")],
                                 f"{major}.0.0": [dict(good, filename="old-" + good["filename"])]}}
            data = json.dumps(body).encode()
        elif self.path.startswith("/f/") and self.path[3:] in WHEELS:
            data = WHEELS[self.path[3:]]
            if BAD["on"]:
                data = data[:-10] + b"0123456789"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), PyPI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
gpu.PYPI = f"http://127.0.0.1:{srv.server_address[1]}"
real_nv = gpu.nvidia_gpu
try:
    gpu.nvidia_gpu = lambda: ""
    try:
        gpu.install()
        check("no NVIDIA card: nothing to download", False)
    except RuntimeError as e:
        check("no NVIDIA card: nothing to download", "NVIDIA" in str(e), str(e))
    gpu.nvidia_gpu = lambda: "NVIDIA GeForce RTX 3060"
    w = gpu._wheel("nvidia-cublas-cu12", 12)
    check("the newest release of the major version Whisper needs (not 13.x)", w["version"] == "12.1.0", w)
    BAD["on"] = True
    try:
        gpu.install()
        check("a damaged download is refused", False)
    except RuntimeError as e:
        check("a damaged download is refused", "checksum" in str(e) and not gpu.installed(), str(e))
    check("and leaves nothing behind", not any(n.startswith("cuda_") for n in os.listdir(gpu.cuda_dir().parent)))
    BAD["on"] = False
    steps = []
    gpu.install(progress=steps.append, log=lambda m: None)
    got = sorted(os.listdir(gpu.cuda_dir()))
    check("GPU support installs Whisper's CUDA libraries, and only those", gpu.installed() and got == sorted(NAMES), got)
    check("with progress to the end", steps and steps[-1] == 1.0 and all(a <= b for a, b in zip(steps, steps[1:])), steps[-3:])
    gpu.install()
    check("installing again replaces it cleanly", sorted(os.listdir(gpu.cuda_dir())) == sorted(NAMES))
    gpu.remove()
    check("and it can be removed", not gpu.installed() and not gpu.cuda_dir().exists())
finally:
    gpu.nvidia_gpu = real_nv
    srv.shutdown()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
