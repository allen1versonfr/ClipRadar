"""
Retakes the README screenshots (docs/images/*.png, not the banner) from the real app: the real server and
UI, in a real browser, on a real VOD you have permission to show (--video, with its chat replay as --chat),
or else on a generated sample video that says it's a sample. Nothing is staged in the page: the clips,
titles, flags and renders are what Clip Radar made of that video.

Whisper isn't run (no model download): the clips are marked as listened to, as they are once Whisper has
found no speech, and the captions are typed in on the Captions tab, as a user would. Live clipping isn't
pictured: without a real stream it could only show a session still connecting.

Needs FFmpeg on PATH, the React build (cd web && npm run build) and Playwright.

  python scripts/readme_shots.py            # writes docs/images
  python scripts/readme_shots.py <folder>   # or somewhere else, to compare first
  python scripts/readme_shots.py --video "NMS BOXING 5.mp4" --chat chat.json   # a real VOD and its chat

The video's file name is the project's title in the pictures. The chat is a TwitchDownloader-style JSON
(its "streamer" names the channel).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import argparse  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_ap = argparse.ArgumentParser()
_ap.add_argument("out", nargs="?", default=os.path.join(ROOT, "docs", "images"))
_ap.add_argument("--video", help="a real video to show instead of the generated sample")
_ap.add_argument("--chat", help="its chat replay (TwitchDownloader JSON)")
ARGS = _ap.parse_args()
OUT = os.path.abspath(ARGS.out)
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_shots_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_shots_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"     # no update check in the pictures
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

import safety  # noqa: E402
import server  # noqa: E402
from browser import launch  # noqa: E402

VIEW = {"width": 1440, "height": 900}
safety.save_config({"custom": ["watch"]})          # the title "Watch till the end" gets a flag to show


def _no_whisper(pid, order, model, lang):
    job = server.new_job("listen")
    server.projects[pid]["listen_job"] = job["id"]
    return job


server.start_listening = _no_whisper


def sample_video(path: str, seconds: int) -> None:
    """A slow colour gradient labelled SAMPLE STREAM, with crowd-like noise that gets loud a few times."""
    work = os.path.dirname(path)
    shutil.copy(os.path.join(ROOT, "src", "ui", "fonts", "captions", "Anton-Regular.ttf"), os.path.join(work, "font.ttf"))
    loud = "+".join(f"between(t,{t},{t + 3})" for t in range(25, seconds - 10, 37))
    vf = (f"gradients=s=1280x720:r=30:d={seconds}:c0=0x1d1147:c1=0x3a2a8c:c2=0x0f3a4a:c3=0x12161d:n=4:speed=0.012,"
          # stacked and narrow, so it reads inside the 9:16 crop too
          "drawtext=fontfile=font.ttf:text='SAMPLE':fontsize=120:fontcolor=white@0.92:x=(w-tw)/2:y=(h/2)-130,"
          "drawtext=fontfile=font.ttf:text='STREAM':fontsize=120:fontcolor=white@0.92:x=(w-tw)/2:y=(h/2)-5,"
          "drawtext=fontfile=font.ttf:text='for screenshots':fontsize=30:fontcolor=white@0.6:x=(w-tw)/2:y=(h/2)+130")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", vf,
                    "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:a=0.03:seed=3",
                    "-af", f"volume='if({loud},30,1)':eval=frame", "-shortest",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
                   check=True, cwd=work)


def call(url, path, body=None):
    rq = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq) as r:
        return json.load(r)


def wait(fn, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    raise TimeoutError(getattr(fn, "__name__", "condition"))


def main() -> None:
    if not shutil.which("ffmpeg") or not (server.WEB_DIR / "index.html").is_file():
        sys.exit("Needs FFmpeg on PATH and the React build (cd web && npm run build).")
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="vc_shots_media_")
    if ARGS.video:
        vod = os.path.abspath(ARGS.video)
    else:
        vod = os.path.join(tmp, "Sample stream.mp4")
        sample_video(vod, 300)
    srv, url = server.start(7989)
    try:
        body = {"source": vod, "count": 6}
        if ARGS.chat:
            body["chat_file"] = os.path.abspath(ARGS.chat)
        job = call(url, "/api/analyze", body)["job"]
        wait(lambda: call(url, "/api/job/" + job)["status"] != "running", 1800)
        pid = call(url, "/api/job/" + job)["result"]["pid"]
        for d in server.projects[pid]["clips"]:          # as after Whisper found no speech: titles are final
            d["listened"] = True

        with sync_playwright() as pw:
            browser = launch(pw)
            page = browser.new_page(viewport=VIEW)

            def shot(name: str, top: bool = True) -> None:
                # passing notices ("Auto-frame: no face on screen") would cover the picture: they're cleared,
                # and the screen is shown from its top unless the shot is about something further down
                # hidden, not removed: React owns those elements
                page.evaluate("document.querySelector('[data-testid=toasts]').style.visibility = 'hidden'")
                if top:
                    page.evaluate("document.querySelector('.main').scrollTo(0, 0)")
                page.wait_for_timeout(300)
                page.screenshot(path=os.path.join(OUT, name))
                page.evaluate("document.querySelector('[data-testid=toasts]').style.visibility = ''")

            page.goto(url + "/#home")
            page.wait_for_selector("[data-testid=running-now]")
            page.wait_for_timeout(800)
            shot("home.png")

            page.goto(url + f"/#review/{pid}")
            page.wait_for_selector("[data-testid=rv-row]")
            page.locator("[data-testid=rv-row]").first.click()
            page.wait_for_function("document.querySelector('[data-testid=rv-video]')?.readyState >= 2", timeout=60000)
            page.wait_for_timeout(1200)
            shot("review.png")

            page.get_by_role("tab", name="Text", exact=True).click()
            page.get_by_label("Captions on the video").check()
            page.wait_for_timeout(600)
            shot("text.png")

            page.get_by_role("tab", name="Captions", exact=True).click()
            for words in ("no way he actually hit that", "chat is losing it"):
                page.get_by_role("button", name="+ Add caption at playhead").click()
                line = page.get_by_test_id("rv-captions").locator("input[value='New caption']").element_handle()
                line.fill(words)
                line.press("Enter")
                page.evaluate("(() => { const v = document.querySelector('[data-testid=rv-video]'); v.currentTime += 2 })()")
                page.wait_for_timeout(500)
            page.wait_for_timeout(800)
            shot("captions.png")

            page.get_by_role("tab", name="Clip", exact=True).click()
            page.get_by_role("button", name="Keep and render").last.click()
            wait(lambda: any(q.get("status") == "done" for q in server.queue), 240)
            page.goto(url + "/#exports")
            page.wait_for_selector("[data-testid=export-row]")
            page.locator("[data-testid=export-row]").first.click()
            page.wait_for_timeout(4000)          # the status bar catches up with the finished render
            shot("exports.png")

            page.goto(url + "/#settings/safety")
            page.wait_for_selector("[data-testid=sf-table]")
            page.wait_for_timeout(800)
            page.get_by_test_id("set-safety").scroll_into_view_if_needed()
            page.evaluate("document.querySelector('[data-testid=set-safety]').scrollIntoView({block: 'start'})")
            shot("content-check.png", top=False)

            page.goto(url + f"/#review/{pid}")
            page.wait_for_selector("[data-testid=rv-row]")
            page.locator("[data-testid=rv-row]").nth(1).click()
            page.get_by_role("tab", name="Layout", exact=True).click()
            page.wait_for_timeout(1200)
            shot("layout.png")
            browser.close()
    finally:
        server.SHUTTING_DOWN[0] = True
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("Screenshots written to", OUT)


if __name__ == "__main__":
    main()
