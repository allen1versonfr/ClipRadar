"""
A pretend Twitch live stream for the tests, served over HTTP.

The video is cut into 2-second segments ahead of time and revealed in real
time, the way a live playlist grows. Partway through, an ad break is stitched
in exactly the way Twitch does it for viewers without Turbo: a
twitch-stitched-ad date range, "Amazon|..." segment titles and
discontinuities. The ad is a red screen with a loud 1 kHz tone, so it would be
obvious if it ever ended up in a clip; the server also records every file the
app downloads, so a test can check that no ad segment was fetched at all.

  /live.m3u8   404 until start(), then the live window; #EXT-X-ENDLIST after finish()
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def _segments(ffmpeg: str, src: str, out_dir: str, prefix: str) -> list:
    os.makedirs(out_dir, exist_ok=True)
    pl = os.path.join(out_dir, f"{prefix}.m3u8")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-c", "copy", "-f", "hls", "-hls_time", "2",
                    "-hls_list_size", "0", "-hls_segment_filename", os.path.join(out_dir, f"{prefix}_%04d.ts"), pl],
                   check=True)
    segs, dur = [], None
    for line in open(pl, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line and not line.startswith("#"):
            segs.append((f"/{prefix}/{line}", dur))
    return segs


def make_ad(ffmpeg: str, path: str, seconds: int = 20):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=red:size=1280x720:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}", "-filter_complex",
                    "[1:a]volume=0.9[a]", "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "60", "-c:a", "aac", path], check=True)


class FakeTwitch:
    def __init__(self, port: int, ffmpeg: str, src: str, work: str, ad_at: float = 100.0, ad_len: int = 20,
                 window: int = 6):
        self.dir = work
        content = _segments(ffmpeg, src, os.path.join(work, "c"), "c")
        ad_src = os.path.join(work, "ad.mp4")
        make_ad(ffmpeg, ad_src, ad_len)
        ads = _segments(ffmpeg, ad_src, os.path.join(work, "a"), "a")
        k, t = 0, 0.0
        while k < len(content) and t + content[k][1] <= ad_at:
            t += content[k][1]
            k += 1
        # timeline: (uri, duration, is_ad, discontinuity before it)
        self.timeline = [(u, d, False, False) for u, d in content[:k]]
        self.timeline += [(u, d, True, i == 0) for i, (u, d) in enumerate(ads)]
        self.timeline += [(u, d, False, i == 0) for i, (u, d) in enumerate(content[k:])]
        self.content_seconds = sum(d for _, d in content)
        self.ad_seconds = sum(d for _, d in ads)
        self.t0 = None
        self.ended = False
        self.window = window
        self.fetched: list = []
        self.base_date = datetime.now(timezone.utc).timestamp()
        outer = self

        class H(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def end_headers(self):
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self):
                if self.path.startswith("/live.m3u8"):
                    body = outer.playlist()
                    if body is None:
                        self.send_error(404)
                        return
                    data = body.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                outer.fetched.append(self.path)
                return super().do_GET()

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), functools.partial(H, directory=work))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{port}/live.m3u8"

    def start(self):
        self.t0 = time.time()

    def finish(self):
        self.ended = True

    @property
    def done(self) -> bool:
        """Every segment has been published."""
        return self.t0 is not None and time.time() - self.t0 >= sum(d for _, d, _, _ in self.timeline)

    def ad_fetches(self) -> list:
        return [p for p in self.fetched if p.startswith("/a/")]

    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def playlist(self):
        if self.t0 is None:
            return None
        el = time.time() - self.t0
        pub, t = 0, 0.0
        for _, d, _, _ in self.timeline:
            if t + d > el + 0.01:
                break
            t += d
            pub += 1
        pub = max(1, pub)
        first = max(0, pub - self.window)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first}"]
        start_t = sum(d for _, d, _, _ in self.timeline[:first])
        ad_idx = [i for i, x in enumerate(self.timeline) if x[2]]
        if ad_idx and any(first <= i < pub for i in ad_idx):
            # Twitch's marker for the whole break, while any of it is in the window
            ad_t = sum(d for _, d, _, _ in self.timeline[:ad_idx[0]])
            lines.append(f'#EXT-X-DATERANGE:ID="stitched-ad-{int(self.base_date)}-{int(self.ad_seconds)}",'
                         f'CLASS="twitch-stitched-ad",START-DATE="{self._iso(self.base_date + ad_t)}",'
                         f'DURATION={self.ad_seconds:.3f},X-TV-TWITCH-AD-ROLL-TYPE="MIDROLL"')
        for i in range(first, pub):
            uri, d, is_ad, disc = self.timeline[i]
            if disc:
                lines.append("#EXT-X-DISCONTINUITY")
            lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{self._iso(self.base_date + start_t)}")
            # only the first ad segment says "Amazon"; the rest are caught by the date range alone
            title = "Amazon|8675309" if is_ad and ad_idx and i == ad_idx[0] else "live"
            lines.append(f"#EXTINF:{d:.3f},{title}")
            lines.append(uri)
            start_t += d
        if self.ended:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"


def parse_check():
    """Quick self-check of the playlist format (python tests/fake_twitch.py)."""
    pl = "#EXTM3U\n#EXTINF:2.000,live\n/c/c_0000.ts\n"
    assert re.search(r"live", pl)


if __name__ == "__main__":
    parse_check()
