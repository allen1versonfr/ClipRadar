"""
Unit tests for the FFmpeg download (Settings > Downloads and tools, and the build's tools folder):

  - the download is pinned: a fixed release URL and a SHA-256, never a floating "latest"
  - a file that doesn't match its checksum is refused, and the mirror (also checked) is used
  - a shared build installs ffmpeg.exe, ffprobe.exe and its DLLs together, and nothing else (no ffplay)
  - updating a static install to the shared build, or a shared one to another version or to the static
    mirror, leaves one working set: no DLLs the new programs don't use
  - other tools in the folder (yt-dlp, TwitchDownloaderCLI) are left alone

  python tests/test_tools_setup.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import tools_setup  # noqa: E402

SHARED = {"ffmpeg.exe": b"shared ffmpeg", "ffprobe.exe": b"shared ffprobe", "ffplay.exe": b"ffplay",
          "avcodec-63.dll": b"c", "avformat-63.dll": b"f", "avutil-61.dll": b"u", "avfilter-12.dll": b"fl",
          "avdevice-63.dll": b"d", "swscale-10.dll": b"s", "swresample-7.dll": b"r"}
STATIC = {"ffmpeg.exe": b"static ffmpeg", "ffprobe.exe": b"static ffprobe", "ffplay.exe": b"ffplay"}


def make_zip(path: str, root: str, files: dict) -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{root}/LICENSE.txt", "license")
        z.writestr(f"{root}/doc/ffmpeg.html", "doc")
        for name, data in files.items():
            z.writestr(f"{root}/bin/{name}", data)
        z.writestr(f"{root}/lib/avcodec.lib", "import library")
    return path


def listing(d: str) -> set:
    return {f for f in os.listdir(d)}


# pinned: a versioned release and a checksum, for the main download and the mirror
for url in (tools_setup.FFMPEG_ZIP, tools_setup.FFMPEG_ZIP_MIRROR):
    assert "latest" not in url and "master" not in url, url
for sha in (tools_setup.FFMPEG_SHA256, tools_setup.FFMPEG_MIRROR_SHA256):
    assert len(sha) == 64 and int(sha, 16) >= 0, sha

with tempfile.TemporaryDirectory() as td:
    out = os.path.join(td, "tools")
    os.makedirs(out)
    shared = make_zip(os.path.join(td, "shared.zip"), "ffmpeg-n9.0.2-win64-gpl-shared-9.0", SHARED)
    static = make_zip(os.path.join(td, "static.zip"), "ffmpeg-9.0.2-essentials_build", STATIC)

    # an existing static install, plus the other tools
    for name, data in {**STATIC, "yt-dlp.exe": b"y", "TwitchDownloaderCLI.exe": b"t"}.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    os.remove(os.path.join(out, "ffplay.exe"))

    tools_setup._install_ffmpeg(shared, out)
    want = {n for n in SHARED if n != "ffplay.exe"} | {"yt-dlp.exe", "TwitchDownloaderCLI.exe"}
    assert listing(out) == want, listing(out)
    assert open(os.path.join(out, "ffmpeg.exe"), "rb").read() == b"shared ffmpeg"
    assert open(os.path.join(out, "avcodec-63.dll"), "rb").read() == b"c"

    # a later shared build with new library versions: the old ones go
    newer = {k.replace("-63", "-64"): v for k, v in SHARED.items()}
    tools_setup._install_ffmpeg(make_zip(os.path.join(td, "newer.zip"), "n", newer), out)
    assert "avcodec-63.dll" not in listing(out) and "avcodec-64.dll" in listing(out), listing(out)
    assert "avutil-61.dll" in listing(out)          # unchanged name, kept

    # the static mirror: no FFmpeg DLLs left behind
    tools_setup._install_ffmpeg(static, out)
    assert listing(out) == {"ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe", "TwitchDownloaderCLI.exe"}, listing(out)
    assert open(os.path.join(out, "ffprobe.exe"), "rb").read() == b"static ffprobe"
    assert not [f for f in listing(out) if f.startswith(".ffmpeg-")], "staging folder left behind"

    # an archive without ffprobe is refused and changes nothing
    broken = make_zip(os.path.join(td, "broken.zip"), "x", {"ffmpeg.exe": b"lonely", "avcodec-65.dll": b"c"})
    try:
        tools_setup._install_ffmpeg(broken, out)
    except RuntimeError as e:
        assert "ffprobe.exe" in str(e)
    else:
        raise AssertionError("an archive without ffprobe.exe was accepted")
    assert open(os.path.join(out, "ffmpeg.exe"), "rb").read() == b"static ffmpeg"
    assert "avcodec-65.dll" not in listing(out)

    # checksums: the main download doesn't match, so the mirror is used (and checked too)
    tools_setup.paths.TOOLS_WRITE = out
    calls, logs = [], []
    real_download, real_main, real_mirror = (tools_setup.download, tools_setup.FFMPEG_SHA256,
                                             tools_setup.FFMPEG_MIRROR_SHA256)

    def fake_download(url, dest, progress=None, check=None):
        calls.append(url)
        src = shared if url == tools_setup.FFMPEG_ZIP else static
        with open(src, "rb") as s, open(dest, "wb") as d:
            d.write(s.read())
        return dest

    try:
        tools_setup.download = fake_download
        tools_setup.FFMPEG_SHA256 = "0" * 64
        tools_setup.FFMPEG_MIRROR_SHA256 = tools_setup.sha256_of(static)
        tools_setup.get_ffmpeg(log=logs.append)
        assert calls == [tools_setup.FFMPEG_ZIP, tools_setup.FFMPEG_ZIP_MIRROR], calls
        assert any("checksum" in m for m in logs), logs
        assert open(os.path.join(out, "ffmpeg.exe"), "rb").read() == b"static ffmpeg"

        # both right: the shared build, from the main download only
        calls.clear()
        tools_setup.FFMPEG_SHA256 = tools_setup.sha256_of(shared)
        tools_setup.get_ffmpeg(log=logs.append)
        assert calls == [tools_setup.FFMPEG_ZIP], calls
        assert "avcodec-63.dll" in listing(out)

        # both wrong: nothing is installed
        calls.clear()
        tools_setup.FFMPEG_SHA256 = tools_setup.FFMPEG_MIRROR_SHA256 = "f" * 64
        before = {f: open(os.path.join(out, f), "rb").read() for f in listing(out)}
        try:
            tools_setup.get_ffmpeg(log=logs.append)
        except RuntimeError as e:
            assert "checksum" in str(e)
        else:
            raise AssertionError("a download that matched no checksum was installed")
        assert {f: open(os.path.join(out, f), "rb").read() for f in listing(out)} == before
    finally:
        tools_setup.download = real_download
        tools_setup.FFMPEG_SHA256, tools_setup.FFMPEG_MIRROR_SHA256 = real_main, real_mirror

print("PASSED: pinned FFmpeg download, checksums, and shared/static installs that never mix")
