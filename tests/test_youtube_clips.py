"""
YouTube for clips (5.1): Moments' "YouTube too" makes two edits of one moment (the TikTok one as set, and a 16:9
YouTube one with no title card), Exports lists which is which, and the YouTube one goes to YouTube with Settings ›
YouTube's ready-made choices: the pass-through (Open YouTube Studio) and the title and description from templates.

  python tests/test_youtube_clips.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_ytc_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_ytc_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_NO_BROWSER"] = "1"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


import render_queue  # noqa: E402
import ytclips  # noqa: E402

# ---- the YouTube edit's settings
o = render_queue.youtube_opts({"layout": "cam", "aspect": "9:16", "track": "auto", "punch": "beat", "caption_size": 120}, {"width": 1920, "height": 1080})
check("the YouTube edit is 16:9, the whole picture, nothing followed or zoomed, captions as set",
      o == {"layout": "full", "aspect": "16:9", "track": "off", "punch": "off", "caption_size": 120}, o)
check("a stream that isn't 16:9 is shown whole on a blurred background",
      render_queue.youtube_opts({}, {"width": 1440, "height": 1080})["layout"] == "blur")

# ---- Settings › YouTube: ready-made, changeable
cfg = ytclips.load()
check("ready-made: public, not made for kids, Publish left to you", cfg["visibility"] == "public" and cfg["kids"] is False
      and cfg["publish"] is False and "{title}" in cfg["title"], cfg)
cfg = ytclips.save({"visibility": "unlisted", "tags": " stream,  funny ,, clips", "publish": True, "nonsense": 1, "kids": "yes"})
check("changed and kept, tidied", cfg["visibility"] == "unlisted" and cfg["tags"] == "stream, funny, clips" and cfg["publish"] is True
      and cfg["kids"] is False and "nonsense" not in ytclips.load(), cfg)
check("an unknown visibility goes back to public", ytclips.save({"visibility": "everyone"})["visibility"] == "public")
ytclips.save(dict(ytclips.DEFAULTS))
it = {"title": "He did NOT see that coming", "post": "No way 😂 #gaming #fail", "link": "https://twitch.tv/videos/1?t=1h2m3s",
      "name_fields": {"streamer": "Emily"}}
t = ytclips.texts(it)
check("the title and description come from the templates", t["title"] == "He did NOT see that coming"
      and t["description"] == "No way 😂 #gaming #fail\n\nFrom Emily's stream: https://twitch.tv/videos/1?t=1h2m3s", t)
check("with no streamer or link, no empty 'From' line", ytclips.texts({"title": "T", "post": "P"})["description"] == "P")
it["yt_text"] = {"title": "My own <title>", "description": "Typed for this clip"}
t = ytclips.texts(it)
check("what you typed for a clip wins, made a title YouTube takes", t == {"title": "My own title", "description": "Typed for this clip"}, t)

if not shutil.which("ffmpeg"):
    print("[skip] no ffmpeg here")
else:
    import server  # noqa: E402
    import studio  # noqa: E402
    srv, url = server.start(7988)

    def api(path, body=None):
        req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=20).read())
        except urllib.error.HTTPError as e:
            return {"status": e.code, **json.loads(e.read() or b"{}")}

    vod = os.path.join(tempfile.mkdtemp(prefix="vc_ytc_v_"), "stream.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=30", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", vod], check=True)
    job = api("/api/analyze", {"source": vod, "count": 3, "events": False})["job"]
    end = time.time() + 120
    while time.time() < end and api(f"/api/job/{job}")["status"] == "running":
        time.sleep(0.3)
    pid = api(f"/api/job/{job}")["result"]["pid"]
    clip = {"id": 1, "rank": 1, "start": 5, "end": 11, "title": "Big moment", "post": "Big moment #clip"}
    r = api("/api/queue/add", {"pid": pid, "clip": clip, "opts": {"layout": "full", "captions": False, "hw_encode": False}, "youtube": True})
    check("keeping with YouTube too queues the clip", r.get("id") and r.get("platform") == "tiktok", r)
    end = time.time() + 240
    items = []
    while time.time() < end:
        items = api("/api/queue")["items"]
        if len(items) == 2 and all(x["status"] in ("done", "error") for x in items):
            break
        time.sleep(0.5)
    by = {x.get("platform"): x for x in items}
    check("and its YouTube edit: two renders of the one moment", sorted(by) == ["tiktok", "youtube"]
          and all(x["status"] == "done" for x in items), [(x.get("platform"), x["status"], x.get("error")) for x in items])
    if len(by) == 2 and all(x["status"] == "done" for x in items):
        import engine  # noqa: E402
        a, b = (engine.probe(os.path.join(by[k]["folder"], by[k]["name"])) for k in ("tiktok", "youtube"))
        check("the TikTok edit is 9:16", (a["width"], a["height"]) == (1080, 1920), (a["width"], a["height"]))
        check("the YouTube edit is standard YouTube, 1920 x 1080", (b["width"], b["height"]) == (1920, 1080), (b["width"], b["height"]))
        check("named apart in the same folder", "(YouTube)" in by["youtube"]["name"] and by["tiktok"]["name"] != by["youtube"]["name"],
              (by["tiktok"]["name"], by["youtube"]["name"]))
        check("the list says Clip Radar can upload, from the first answer", isinstance(api("/api/queue").get("uploads"), dict))
        # Open YouTube Studio: the file shown, the upload page opened, the title and description given to copy
        del studio.opened[:]
        r = api("/api/yt_handoff", {"item": by["youtube"]["id"], "title": "Typed title", "description": "Typed description"})
        check("Open YouTube Studio shows the file and opens YouTube's upload page",
              ("url", ytclips.UPLOAD_URL) in studio.opened and any(k == "reveal" for k, _ in studio.opened), studio.opened)
        check("with the title and description to paste", r.get("title") == "Typed title" and r.get("description") == "Typed description", r)
        yt = next(x for x in api("/api/queue")["items"] if x["id"] == by["youtube"]["id"])
        check("and the clip says YouTube Studio was opened", (yt.get("yt") or {}).get("url") == ytclips.UPLOAD_URL, yt.get("yt"))
    check("a clip that isn't rendered can't go", api("/api/yt_handoff", {"item": "nope"}).get("status") == 400)
    r = api("/api/youtube", {"visibility": "private"})
    check("Settings › YouTube is saved through the app", r.get("visibility") == "private" and api("/api/youtube")["visibility"] == "private", r)

print(f"\n{len(failures)} failed" if failures else "\nall passed")
os._exit(1 if failures else 0)
