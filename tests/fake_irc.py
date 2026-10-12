"""
A stand-in for Twitch chat (IRC) used by the tests.

Accepts the same anonymous login Twitch uses, then sends ordinary chatter
plus bursts of reactions at chosen times (seconds after JOIN).

  python fake_irc.py PORT BURST_AT[,BURST_AT...]
"""

from __future__ import annotations

import random
import socket
import sys
import threading
import time

CHATTER = ["hi", "lol nice", "what game is this", "hello chat", "ok", "gg", "W stream"]
BURST = ["KEKW", "KEKW KEKW", "LMAOOO", "clip it", "OMEGALUL", "no way", "CLIP IT"]


def serve_client(conn: socket.socket, bursts: list):
    conn.settimeout(120)
    buf = b""
    channel = None
    while channel is None:
        data = conn.recv(4096)
        if not data:
            return
        buf += data
        for line in buf.split(b"\r\n"):
            if line.startswith(b"JOIN #"):
                channel = line[6:].decode()
    conn.sendall(f":tmi.twitch.tv 001 justinfan :Welcome\r\n:justinfan!j@j.tmi.twitch.tv JOIN #{channel}\r\n".encode())
    t0 = time.time()
    sent = set()
    try:
        while True:
            el = time.time() - t0
            msgs = []
            if random.random() < 0.6:
                msgs.append(random.choice(CHATTER))
            for b in bursts:
                if b <= el < b + 10:
                    msgs += random.choices(BURST, k=12)
            if int(el) % 30 == 0 and int(el) not in sent:
                sent.add(int(el))
                msgs.append("\x01PING")  # exercise odd lines
                conn.sendall(b"PING :tmi.twitch.tv\r\n")
            for m in msgs:
                u = f"viewer{random.randint(1, 500)}"
                conn.sendall(f"@badge-info=;color= :{u}!{u}@{u}.tmi.twitch.tv PRIVMSG #{channel} :{m}\r\n".encode())
            time.sleep(1)
    except OSError:
        pass


def main(port: int, bursts: list):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)
    print(f"fake IRC on {port}, bursts at {bursts}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_client, args=(conn, bursts), daemon=True).start()


def start_in_thread(port: int, bursts: list):
    threading.Thread(target=main, args=(port, bursts), daemon=True).start()


if __name__ == "__main__":
    main(int(sys.argv[1]), [float(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [60])
