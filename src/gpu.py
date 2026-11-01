"""
What Clip Radar can do on the graphics card (4.36), the GPU support download for Whisper, and which engine
each Whisper task runs on (4.37).

Which card this is comes from Windows, and choose() picks per task and load: on NVIDIA with its libraries,
faster-whisper uses the card by itself. On AMD and Intel (or NVIDIA without them) whisper.cpp on Vulkan
(whispercpp.py) takes titles and Deep scan, the CPU keeps final captions, whose words have to be right, and
titles move to the CPU while the card is busy (a game, the render slots full) and the CPU has room.

Rendering uses the GPU by itself: FFmpeg's NVIDIA, Intel and AMD encoders and the GPU decoders (engine.py).
Whisper (titles, captions, Deep scan) runs on an NVIDIA GPU only with NVIDIA's CUDA 12 libraries, cuBLAS and
cuDNN 9, which Windows doesn't have and Clip Radar doesn't ship (about 1.3 GB). Without them Whisper ran on the
CPU without saying why, several times slower. Settings › Performance › GPU support downloads them, once, from
NVIDIA's packages on PyPI (each checked against PyPI's SHA-256) into Clip Radar's data folder; only their
library files are kept.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import paths
import persist
import whispercpp

PACKAGES =(("nvidia-cublas-cu12", 12), ("nvidia-cudnn-cu12", 9))     # (PyPI name, major version Whisper needs)
PYPI = os.environ.get("CLIPRADAR_PYPI", "https://pypi.org/pypi")
NEED_FREE = 3.5e9          # the two packages (1.3 GB) while their files are taken out (1.7 GB)
# what Whisper's CUDA build loads (ctranslate2: CUDA 12, cuDNN 9)
LIBS = {"win32": ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"),
        "linux": ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")}
_lock = threading.Lock()
_cache: dict = {}
DISPLAY_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
VENDORS = {"nvidia": ("nvidia", "geforce", "quadro"), "amd": ("amd", "radeon"), "intel": ("intel",)}
NOT_A_CARD = re.compile(r"microsoft|basic|virtual|remote|parsec|meta|citrix|vmware", re.I)
GPU_COUNTERS = (r"\GPU Engine(*engtype_3D)\Utilization Percentage", r"\GPU Engine(*engtype_Compute*)\Utilization Percentage")
MODES = ("auto", "gpu", "cpu")
GPU_BUSY = 85              # percent of the card's busiest engine: a game, or something else with a full load on it
CPU_ROOM = 60              # percent: below this the CPU can take a title without slowing the rest of a live session
LOAD_FOR = 5               # seconds a reading of the load stays good: whole clips arrive seconds apart
_load: dict = {}
_routes: dict = {}         # task -> {"on": "gpu" or "cpu", "why": ...}, last decided


def cuda_dir() -> Path:
    return Path(paths.DATA_DIR) / "cuda"


def setup_dll_path() -> None:
    """Let Whisper find the downloaded libraries (call before faster_whisper / ctranslate2 is imported)."""
    d = cuda_dir()
    if not d.is_dir() or _cache.get("dll_path"):
        return
    _cache["dll_path"] = True
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        try:
            _cache["dll_handle"] = os.add_dll_directory(str(d))
        except OSError:
            pass
    elif sys.platform.startswith("linux"):
        os.environ["LD_LIBRARY_PATH"] = str(d) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")


def _libs_for_platform() -> tuple:
    return LIBS["win32"] if sys.platform == "win32" else LIBS["linux"]


def cuda_libs() -> bool:
    """True when Whisper's CUDA libraries can be loaded (installed system-wide, or downloaded here)."""
    if "libs" in _cache:
        return _cache["libs"]
    setup_dll_path()
    ok = True
    for name in _libs_for_platform():
        local = cuda_dir() / name
        try:
            ctypes.CDLL(str(local) if local.exists() else name)
        except OSError:
            ok = False
            break
    _cache["libs"] = ok
    return ok


def nvidia_gpu() -> str:
    """The NVIDIA graphics card's name ('' when there's none, or its driver isn't installed)."""
    if "nvidia" in _cache:
        return _cache["nvidia"]
    name = ""
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if sys.platform == "win32" else "")
    if exe and os.path.exists(exe):
        try:
            out = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            name = (out.stdout or "").strip().splitlines()[0].strip() if out.returncode == 0 and out.stdout.strip() else ""
        except (OSError, subprocess.SubprocessError, IndexError):
            name = ""
    _cache["nvidia"] = name
    return name


def whisper_gpu_ok() -> bool:
    """Whisper can run on the GPU here: an NVIDIA card and its CUDA libraries."""
    return bool(nvidia_gpu()) and cuda_libs()


def cards() -> list:
    """Windows' graphics cards, biggest memory first: [{"name", "vendor", "vram_mb"}] (real memory: WMI stops at 4 GB)."""
    if "cards" in _cache:
        return _cache["cards"]
    found = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_KEY) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                            name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                            try:
                                mem = int(winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0])
                            except (OSError, TypeError, ValueError):
                                mem = 0
                    except OSError:
                        continue
                    if not NOT_A_CARD.search(name):
                        found.append({"name": name, "vendor": _vendor(name), "vram_mb": mem // (1 << 20)})
        except OSError:
            pass
    elif nvidia_gpu():
        found.append({"name": nvidia_gpu(), "vendor": "nvidia", "vram_mb": 0})
    found.sort(key=lambda c: c["vram_mb"], reverse=True)
    _cache["cards"] = found
    return found


def _vendor(name: str) -> str:
    n = name.lower()
    return next((v for v, words in VENDORS.items() if any(w in n for w in words)), "")


def card() -> dict:
    """The card to use: the one with the most memory, so a Radeon beats the Intel graphics beside it."""
    known = [c for c in cards() if c["vendor"]]
    return known[0] if known else {"name": "", "vendor": "", "vram_mb": 0}


def cpu_load() -> float:
    """How busy the CPU is, 0-100, over the moments since the last look."""
    now = time.time()
    if now - _load.get("cpu_at", 0) < LOAD_FOR:
        return _load["cpu"]
    if sys.platform != "win32":
        _load.update(cpu=min(100.0, 100.0 * os.getloadavg()[0] / (os.cpu_count() or 1)), cpu_at=now)
        return _load["cpu"]
    a = _cpu_times()
    if "cpu_times" not in _load:
        time.sleep(0.25)
        _load["cpu_times"], a = a, _cpu_times()
    idle, total = a[0] - _load["cpu_times"][0], a[1] - _load["cpu_times"][1]
    _load.update(cpu=round(100.0 * (1 - idle / total), 1) if total > 0 else 0.0, cpu_at=now, cpu_times=a)
    return _load["cpu"]


def _cpu_times() -> tuple:
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))     # FILETIMEs; kernel time includes idle
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return idle.value, kernel.value + user.value


def _read_gpu_load() -> float:
    """The busiest engine across the card's 3D and compute engines, 0-100 (0 when Windows won't say)."""
    try:
        out = subprocess.run(["typeperf", *GPU_COUNTERS, "-sc", "1"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = list(csv.reader(line for line in out.splitlines() if line.startswith('"')))
        engines: dict = {}
        for head, val in zip(rows[0][1:], rows[1][1:]):
            m = re.search(r"luid_\w+?_phys_\d+_eng_\d+", head)
            if m and val.strip():
                engines[m.group(0)] = engines.get(m.group(0), 0.0) + float(val)
        return round(min(100.0, max(engines.values(), default=0.0)), 1)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


def gpu_load(wait: bool = True) -> float:
    """How busy the card is, 0-100. Reading it takes a second or two, so Settings takes the last value and refreshes it
    behind (wait=False); a Whisper run waits for a fresh one."""
    if time.time() - _load.get("gpu_at", 0) < LOAD_FOR:
        return _load["gpu"]
    if wait:
        _load.update(gpu=_read_gpu_load(), gpu_at=time.time())
    elif not _load.get("gpu_reading"):
        _load["gpu_reading"] = True

        def go():
            _load.update(gpu=_read_gpu_load(), gpu_at=time.time(), gpu_reading=False)
        threading.Thread(target=go, daemon=True, name="gpu-load").start()
    return _load.get("gpu", 0.0)


def mode() -> str:
    """Settings › Performance: "auto", or the user's "gpu" / "cpu" for Whisper."""
    try:
        with open(_mode_file(), encoding="utf-8") as f:
            m = json.load(f).get("whisper")
        return m if m in MODES else "auto"
    except (OSError, ValueError, AttributeError):
        return "auto"


def set_mode(m: str) -> dict:
    if m not in MODES:
        raise ValueError("unknown Whisper mode")
    persist.write_json(str(_mode_file()), {"whisper": m})
    return status()


def _mode_file() -> Path:
    return Path(paths.DATA_DIR) / "gpu.json"


def vulkan_usable() -> bool:
    """The Vulkan engine is here, this card should use it, and it hasn't been failing."""
    return (not whisper_gpu_ok() and whispercpp.installed() and not whispercpp.turned_off()
            and bool(card()["vendor"]))


def _fits(size: str) -> bool:
    """A card's memory holds the model, with a little over for the rest of the system."""
    vram, m = card()["vram_mb"], whispercpp.MODELS.get(size)
    return bool(m) and (not vram or vram >= m[1] * 1.3)


def decide(task: str, want: str, can: bool, cpu: float, busy: bool) -> tuple:
    """Pure: (engine, why). engine is "vulkan" or "whisper" (faster-whisper, on the CPU or NVIDIA's GPU).
    task: "titles", "scan" or "captions". can: Vulkan can run this. busy: the card is taken (a game, renders)."""
    if not can:
        return "whisper", "the graphics card can't run this"
    if want == "cpu":
        return "whisper", "you chose the CPU"
    if want == "gpu":
        return "vulkan", "you chose the graphics card"
    if task == "captions":
        return "whisper", "captions need exact words and timing"
    if busy and cpu < CPU_ROOM:
        return "whisper", "the graphics card is busy and the CPU has room"
    return "vulkan", "the graphics card is free" if not busy else "both are busy, this takes the least CPU"


def choose(task: str, size: str, log: Callable[[str], None] = lambda m: None) -> dict:
    """Where a Whisper task runs now: {"engine", "why"}, remembered for Settings."""
    want = mode()
    if whisper_gpu_ok():
        pick = ("whisper", "faster-whisper uses your NVIDIA card")
    elif want == "cpu" or not vulkan_usable():
        pick = ("whisper", "you chose the CPU" if want == "cpu" else "the graphics card stopped, it gets another try soon"
                if whispercpp.installed() and whispercpp.turned_off() else "no GPU support installed")
    elif task == "captions" and want == "auto":
        pick = decide(task, want, True, 0.0, False)
    elif not _fits(size):
        pick = ("whisper", f"Whisper '{size}' doesn't fit on this card")
    elif not whispercpp.has_model(size):
        whispercpp.fetch_later(size, log)
        pick = ("whisper", f"Whisper '{size}' is still downloading")
    else:
        import engine
        renders_full = engine._hw_slots._value == 0
        pick = decide(task, want, True, cpu_load(), renders_full or gpu_load() >= GPU_BUSY)
    _routes[task] = {"on": "gpu" if pick[0] == "vulkan" or whisper_gpu_ok() else "cpu", "why": pick[1]}
    return {"engine": pick[0], "why": pick[1]}


def forget() -> None:
    """Look again (after the download, or when the graphics driver changed)."""
    for k in ("libs", "nvidia", "cards"):
        _cache.pop(k, None)
    whispercpp.forget()


def status() -> dict:
    """For Settings › Performance: the card, and what runs on it."""
    import engine
    enc = engine._encoder_cache.get("hw")
    c = card()
    nv = nvidia_gpu()
    vulkan = vulkan_usable()
    return {
        "nvidia": nv,
        "whisper_gpu": whisper_gpu_ok(),
        "cuda_libs": cuda_libs(),
        "pack": installed() or whispercpp.installed(),
        "whisper_device": engine.whisper_device(),
        "encoder": (enc[0] if enc else "CPU") if "hw" in engine._encoder_cache else "",
        "hw_decode": bool(engine._encoder_cache.get("hwdec", True)),
        "download_mb": 1300 if nv else whispercpp.DOWNLOAD_MB,
        "card": c["name"],
        "vendor": c["vendor"],
        "vram_mb": c["vram_mb"],
        "vulkan": vulkan,
        "vulkan_off": whispercpp.installed() and whispercpp.turned_off(),
        "can_download": sys.platform == "win32" and bool(c["vendor"]) and not whisper_gpu_ok() and not whispercpp.installed(),
        "mode": mode(),
        "routes": dict(_routes),
        "cpu_load": cpu_load(),
        "gpu_load": gpu_load(wait=False),
    }


def installed() -> bool:
    d = cuda_dir()
    return d.is_dir() and all((d / n).exists() for n in _libs_for_platform())


def _wheel(name: str, major: int) -> dict:
    """The newest release of a package with this major version, and its wheel for this PC: {url, sha256, size}."""
    with urllib.request.urlopen(f"{PYPI}/{name}/json", timeout=30) as r:
        meta = json.loads(r.read().decode())
    tag = "win_amd64" if sys.platform == "win32" else "manylinux"

    def ver(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split(".")[:4])
        except ValueError:
            return (0,)
    for v in sorted(meta.get("releases", {}), key=ver, reverse=True):
        if ver(v)[0] != major:
            continue
        for f in meta["releases"][v]:
            fn = f.get("filename", "")
            if fn.endswith(".whl") and tag in fn and ("x86_64" in fn or tag == "win_amd64") and not f.get("yanked"):
                return {"url": f["url"], "sha256": f["digests"]["sha256"], "size": int(f.get("size") or 0),
                        "version": v, "file": fn}
    raise RuntimeError(f"No {name} {major}.x for this PC on PyPI.")


def _wanted(member: str) -> bool:
    """A library file in an NVIDIA wheel (nvidia/cublas/bin/cublas64_12.dll, nvidia/cudnn/lib/libcudnn.so.9...)."""
    base = member.rsplit("/", 1)[-1]
    if sys.platform == "win32":
        return member.startswith("nvidia/") and "/bin/" in member and base.lower().endswith(".dll")
    return member.startswith("nvidia/") and "/lib/" in member and ".so" in base


def install(progress: Callable[[float], None] = lambda f: None, log: Callable[[str], None] = lambda m: None,
            check: Callable[[], None] = lambda: None) -> dict:
    """Download what this card needs for Whisper: on NVIDIA, its CUDA libraries into the data folder (safe to run
    again: it only replaces the folder once everything is in and checked); on the others, whisper.cpp for Vulkan."""
    with _lock:
        if not nvidia_gpu():
            if sys.platform != "win32" or not card()["vendor"]:
                raise RuntimeError("GPU support for Whisper needs an NVIDIA, AMD or Intel graphics card with its driver installed.")
            whispercpp.install(progress, log, check)
            forget()
            log("GPU support installed: titles and Deep scan run on the graphics card.")
            return status()
        dest = cuda_dir()
        dest.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(dest.parent)).free
        if free < NEED_FREE:
            raise RuntimeError(f"Not enough free space: GPU support needs about {NEED_FREE / 1e9:.1f} GB while it "
                               f"installs ({free / 1e9:.1f} GB free).")
        wheels = [_wheel(n, m) for n, m in PACKAGES]
        total = sum(w["size"] for w in wheels) or 1
        done = 0
        work = Path(tempfile.mkdtemp(prefix="cuda_", dir=str(dest.parent)))
        out = work / "cuda"
        out.mkdir()
        try:
            for w in wheels:
                log(f"Downloading {w['file']} ({w['size'] / 1e6:.0f} MB)...")
                part = work / w["file"]
                h = hashlib.sha256()
                req = urllib.request.Request(w["url"], headers={"User-Agent": "ClipRadar"})
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                    while True:
                        check()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        h.update(chunk)
                        f.write(chunk)
                        done += len(chunk)
                        progress(0.95 * done / total)
                if h.hexdigest() != w["sha256"]:
                    raise RuntimeError(f"{w['file']} didn't download correctly (its checksum doesn't match). Try again.")
                log("Taking out its libraries...")
                with zipfile.ZipFile(part) as z:
                    for m in z.namelist():
                        if _wanted(m):
                            name = m.rsplit("/", 1)[-1]
                            with z.open(m) as src, open(out / name, "wb") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                part.unlink()
            missing = [n for n in _libs_for_platform() if not (out / n).exists()]
            if missing:
                raise RuntimeError("The download didn't have " + ", ".join(missing) + ".")
            old = dest.with_name("cuda.old")
            shutil.rmtree(old, ignore_errors=True)
            if dest.exists():
                dest.rename(old)
            out.rename(dest)
            shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        forget()
        progress(1.0)
        ok = whisper_gpu_ok()
        log("GPU support installed: Whisper runs on the graphics card." if ok else
            "GPU support installed, but Whisper still can't use the graphics card: update the NVIDIA driver.")
        return status()


def remove() -> dict:
    shutil.rmtree(cuda_dir(), ignore_errors=True)
    whispercpp.remove()
    forget()
    return status()
