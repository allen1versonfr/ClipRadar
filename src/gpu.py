"""
What Clip Radar can do on the graphics card (4.36), and the GPU support download for Whisper.

Rendering uses the GPU by itself: FFmpeg's NVIDIA, Intel and AMD encoders and the GPU decoders (engine.py).
Whisper (titles, captions, Deep scan) runs on an NVIDIA GPU only with NVIDIA's CUDA 12 libraries, cuBLAS and
cuDNN 9, which Windows doesn't have and Clip Radar doesn't ship (about 1.3 GB). Without them Whisper ran on the
CPU without saying why, several times slower. Settings › Performance › GPU support downloads them, once, from
NVIDIA's packages on PyPI (each checked against PyPI's SHA-256) into Clip Radar's data folder; only their
library files are kept.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import paths

PACKAGES = (("nvidia-cublas-cu12", 12), ("nvidia-cudnn-cu12", 9))     # (PyPI name, major version Whisper needs)
PYPI = os.environ.get("CLIPRADAR_PYPI", "https://pypi.org/pypi")
NEED_FREE = 3.5e9          # the two packages (1.3 GB) while their files are taken out (1.7 GB)
# what Whisper's CUDA build loads (ctranslate2: CUDA 12, cuDNN 9)
LIBS = {"win32": ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"),
        "linux": ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")}
_lock = threading.Lock()
_cache: dict = {}


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


def forget() -> None:
    """Look again (after the download, or when the graphics driver changed)."""
    for k in ("libs", "nvidia"):
        _cache.pop(k, None)


def status() -> dict:
    """For Settings › Performance: the card, and what runs on it."""
    import engine
    enc = engine._encoder_cache.get("hw")
    return {
        "nvidia": nvidia_gpu(),
        "whisper_gpu": whisper_gpu_ok(),
        "cuda_libs": cuda_libs(),
        "pack": installed(),
        "whisper_device": engine.whisper_device(),
        "encoder": (enc[0] if enc else "CPU") if "hw" in engine._encoder_cache else "",
        "hw_decode": bool(engine._encoder_cache.get("hwdec", True)),
        "download_mb": 1300,
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
    """Download Whisper's CUDA libraries into the data folder. Safe to run again: it only replaces the folder
    once everything is in and checked."""
    with _lock:
        if not nvidia_gpu():
            raise RuntimeError("GPU support for Whisper needs an NVIDIA graphics card with its driver installed.")
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
    forget()
    return status()
