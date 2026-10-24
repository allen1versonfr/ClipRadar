"""
Uploading to TikTok Studio for you, against a stand-in for TikTok Studio's page (tests/fake_studio.py)
in a real browser: Edge or Chrome when installed (Edge on Windows CI), else Playwright's Chromium.

The first time, it waits for you to sign in, in its window, then carries on. The video that goes in is
the clip's file; the caption replaces what TikTok starts with (the file name); Post is left for you
unless "Press Post for me" is on, and then it confirms TikTok's "Post now" and only says posted once
TikTok shows it. A page without the parts it needs (TikTok redesigned it) is a named step that failed.

  python tests/test_studio_auto.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_STUDIO_HEADLESS"] = "1"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import cdp  # noqa: E402
import fake_studio  # noqa: E402
import studio_auto  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


if not studio_auto.find_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        if os.path.isfile(exe):
            os.environ["CLIPRADAR_STUDIO_BROWSER"] = exe
    except Exception:
        pass
if not studio_auto.find_browser():
    print("SKIPPED: no Edge, Chrome or Playwright Chromium here")
    sys.exit(0)
print("browser:", studio_auto.find_browser())

tmp = tempfile.mkdtemp(prefix="vc_studio_")
clip = os.path.join(tmp, "Chat lost it #KaiCenat.mp4")
with open(clip, "wb") as f:
    f.write(os.urandom(200_000))
CAPTION = "Chat could not stop laughing at this one 😂\n\n#twitch #kaicenat #twitchclips"
steps = []

try:
    # 1. the first time: TikTok asks to sign in; it waits, then carries on once you're in
    srv = fake_studio.start(mode="login")
    result, error = {}, []

    def run():
        try:
            result.update(studio_auto.upload(clip, CAPTION, False, srv.url, steps.append))
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=run)
    t.start()
    end = time.time() + 30
    while time.time() < end and not any("Sign in" in s for s in steps):
        time.sleep(0.2)
    check("it asks you to sign in, in its window, the first time", any("Sign in" in s for s in steps), steps)
    page = cdp.Page.connect(studio_auto._state["port"])
    page.eval("document.getElementById('signin').click(), true")        # you sign in
    page.close()
    t.join(60)
    check("then it carries on by itself", not t.is_alive() and not error, error)
    srv.shutdown()

    # 2. upload and caption; Post is left for you
    srv = fake_studio.start()
    steps.clear()
    r = studio_auto.upload(clip, CAPTION, False, srv.url, steps.append)
    page = cdp.Page.connect(studio_auto._state["port"])
    got = page.eval("({cap: document.getElementById('cap').innerText, name: document.getElementById('f').files[0]?.name,"
                    " size: document.getElementById('f').files[0]?.size, post: !document.getElementById('post').disabled,"
                    " confirm: !document.getElementById('confirm').hidden})")
    page.close()
    check("the clip's own file goes in", got["name"] == os.path.basename(clip) and got["size"] == 200_000, got)
    check("the caption replaces TikTok's (the file name)", got["cap"].split() == CAPTION.split(), repr(got["cap"]))
    check("Post is left for you", r == {"posted": False, "pressed": False} and not got["confirm"] and not srv.posted, (r, got))
    check("it says what it's doing", steps[:4] == ["Opening TikTok Studio", "Putting the video in", "Typing the caption",
                                                   "Uploading the video to TikTok"], steps)
    check("the window is reused, not opened again", studio_auto.describe()["open"])

    # 3. Press Post for me: confirms TikTok's "Post now", and says posted only once TikTok shows it
    steps.clear()
    r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
    check("with Press Post for me, it posts", r == {"posted": True, "pressed": True} and len(srv.posted) == 1, (r, srv.posted))
    check("what TikTok got is the clip and the caption", srv.posted and srv.posted[0]["name"] == os.path.basename(clip)
          and srv.posted[0]["caption"].split() == CAPTION.split(), srv.posted)
    srv.shutdown()

    # 4. TikTok changed the page: the step that failed is named
    srv = fake_studio.start(mode="changed")
    try:
        orig = studio_auto._wait
        studio_auto._wait = lambda page, what, seconds, check, every=0.5: orig(page, what, min(seconds, 3), check, every)
        studio_auto.upload(clip, CAPTION, False, srv.url, steps.append)
        check("a page without the upload box is a named failure", False, "no error")
    except studio_auto.StudioError as e:
        check("a page without the upload box is a named failure", e.step == "file box" and "TikTok may have changed" in str(e),
              (e.step, str(e)))
    finally:
        studio_auto._wait = orig
    srv.shutdown()

    # 5. a clip whose file is gone
    try:
        studio_auto.upload(os.path.join(tmp, "gone.mp4"), CAPTION, False, "http://127.0.0.1:9/", steps.append)
        check("a missing file is refused before anything opens", False)
    except studio_auto.StudioError as e:
        check("a missing file is refused before anything opens", e.step == "file", e.step)

    # settings: Press Post for me is off until you turn it on, and it's remembered
    check("Press Post for me is off by default", studio_auto.load_settings() == {"press_post": False})
    studio_auto.save_settings(True)
    check("and remembered when turned on", studio_auto.load_settings() == {"press_post": True})
finally:
    p = studio_auto._state.get("proc")
    if p:
        p.kill()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
