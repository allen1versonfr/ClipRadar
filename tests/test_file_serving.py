"""
A video being streamed to the page doesn't keep the file open. A <video> asks for the whole file,
then stops reading once it has buffered enough; the server used to hold the file open for as long
as that request was pending, so on Windows it couldn't be deleted (Delete VODs left live moments
behind: "couldn't delete: moment_0001.mp4 ... open in ClipRadar"). Here a client asks for a large
file, reads a little and stalls like a browser: the file must not be open in Clip Radar and must be
deletable, and a normal and a ranged request still get exactly the right bytes.

  python tests/test_file_serving.py
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import server  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def held_open(path):
    """Is the file open in this process? Linux reads /proc; Windows tries to delete a copy's name."""
    if os.path.isdir("/proc/self/fd"):
        real = os.path.realpath(path)
        for fd in os.listdir("/proc/self/fd"):
            try:
                if os.readlink(f"/proc/self/fd/{fd}") == real:
                    return True
            except OSError:
                pass
        return False
    try:                      # Windows: a file open without FILE_SHARE_DELETE can't be renamed
        os.rename(path, path + ".probe")
        os.rename(path + ".probe", path)
        return False
    except OSError:
        return True


tmp = tempfile.mkdtemp(prefix="vc_serve_")
video = os.path.join(tmp, "moment_0001.mp4")
data = os.urandom(1 << 20) * 48                    # 48 MB: far more than the socket buffers hold
with open(video, "wb") as f:
    f.write(data)
server.projects["servetest"] = {"video": video, "clips": [], "info": {}}
srv, url = server.start(7995)
port = int(url.rsplit(":", 1)[1])


def request(headers=""):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    s.sendall(f"GET /vod/servetest HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{headers}\r\n".encode())
    return s


def read_all(s):
    buf = b""
    while True:
        part = s.recv(1 << 16)
        if not part:
            break
        buf += part
    head, _, body = buf.partition(b"\r\n\r\n")
    return head.decode("latin-1"), body


try:
    # 1. a stalled request, like a paused <video>
    stalled = request()
    first = stalled.recv(64 * 1024)                # the headers and a little of the video, then nothing
    check("the video starts streaming", b" 200 " in first.split(b"\r\n", 1)[0], first[:60])
    time.sleep(1.0)                                # the server fills the socket and blocks
    check("a stalled video request doesn't keep the file open", not held_open(video))
    try:
        os.remove(video)
        removed = True
    except OSError as e:
        removed = str(e)
    check("and the file can be deleted while the request is pending", removed is True, removed)
    stalled.close()

    # 2. the bytes are still right
    with open(video, "wb") as f:
        f.write(data)
    s = request("Connection: close\r\n")
    head, body = read_all(s)
    check("a whole-file request gets every byte", head.startswith("HTTP/1.") and " 200 " in head.split("\r\n")[0]
          and body == data, (head.split("\r\n")[0], len(body)))
    a, b = (1 << 20) - 7, 5 * (1 << 20) + 11       # across chunk boundaries
    s = request(f"Range: bytes={a}-{b}\r\nConnection: close\r\n")
    head, body = read_all(s)
    check("a ranged request gets exactly its bytes", " 206 " in head.split("\r\n")[0] and body == data[a:b + 1],
          (head.split("\r\n")[0], len(body)))
    s = request("Range: bytes=-100\r\nConnection: close\r\n")
    head, body = read_all(s)
    check("a request for the last bytes gets them", body == data[-100:], len(body))
finally:
    server.SHUTTING_DOWN[0] = True
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
