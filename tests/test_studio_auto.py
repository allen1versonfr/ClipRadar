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
import subprocess
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


# 4.38: Edge signed in to an account lists its own sync dialog as a page, first. Driving it failed every upload
# with "Lost Clip Radar's TikTok window"; only a browser with an account shows it, so this one is by hand
class _Tab:
    def __init__(self, ws, tid):
        self.ws = ws


def _pick(listed, prefix=""):
    real = cdp.targets
    cdp.targets = lambda port: [dict(type="page", webSocketDebuggerUrl=u, url=u) for u in listed]
    try:
        return cdp.Page.connect.__func__(_Tab, 1, prefix).ws
    except cdp.CDPError:
        return ""
    finally:
        cdp.targets = real


SYNC = "edge://sync-confirmation-dialog/"
check("the browser's own dialog is never the tab", _pick([SYNC, "about:blank"]) == "about:blank")
check("nor when the tab wanted isn't there", _pick([SYNC, "http://a/"], "http://b") == "http://a/")
check("only browser pages: no tab to use, so one is opened", _pick([SYNC, "edge://newtab/"]) == "")

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
        orig, orig_first = studio_auto._wait, studio_auto._first
        studio_auto._wait = lambda page, what, seconds, check, every=0.5: orig(page, what, min(seconds, 3), check, every)
        studio_auto._first = lambda page, whats, seconds, check, every=0.25: orig_first(page, whats, min(seconds, 3), check, every)
        studio_auto.upload(clip, CAPTION, False, srv.url, steps.append)
        check("a page without the upload box is a named failure", False, "no error")
    except studio_auto.StudioError as e:
        check("a page without the upload box is a named failure", e.step == "file box" and "TikTok may have changed" in str(e),
              (e.step, str(e)))
    finally:
        studio_auto._wait, studio_auto._first = orig, orig_first
    srv.shutdown()

    # 6. a page that ignores the first file and drops the first typing: both done again, and it still works
    srv = fake_studio.start(mode="flaky")
    steps.clear()
    r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
    check("a page that ignores the first file and typing: tried again, posted", r == {"posted": True, "pressed": True}
          and srv.posted and srv.posted[0]["caption"].split() == CAPTION.split(), (r, srv.posted))

    # 7. you closed the tab (the window stays): a new tab, not a failure
    for t in cdp.targets(studio_auto._state["port"]):
        if t.get("type") == "page":
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{studio_auto._state['port']}/json/close/{t['id']}", timeout=5).read()
    time.sleep(0.5)
    srv.posted.clear()
    r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
    check("the tab was closed: it opens one and posts", r == {"posted": True, "pressed": True} and len(srv.posted) == 1, r)

    # 8. two uploads at once: one after the other, both posted
    srv.posted.clear()
    out, errs, said = [], [], []
    ths = [threading.Thread(target=lambda: (out.append(studio_auto.upload(clip, CAPTION, True, srv.url, said.append))))
           for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(120)
    check("two uploads at once go one after the other", len(out) == 2 and all(x["posted"] for x in out) and len(srv.posted) == 2
          and any("before it" in x for x in said), (out, len(srv.posted), said[:3]))
    srv.shutdown()

    # 9 (4.36). Clip Radar restarted while its TikTok window stayed open: it finds that window again (the
    # browser hands a second start with the same profile to the open window and exits, so waiting for a new
    # one failed with "The browser window didn't open")
    proc = studio_auto._state["proc"]
    studio_auto._state.update(proc=None, port=0)
    srv = fake_studio.start()
    try:
        r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
        check("after a restart, the open TikTok window is used again", r["posted"] and len(srv.posted) == 1, r)
    except studio_auto.StudioError as e:
        check("after a restart, the open TikTok window is used again", False, str(e))
    if studio_auto._state["proc"] is None:
        studio_auto._state["proc"] = proc
    srv.shutdown()

    # 9b (4.38). A browser holds the profile with no DevTools port: every start was handed to it and lost
    # ("The browser window didn't open"), and closing Edge didn't help when it had no window. It's closed, the
    # window opens, and another browser on a profile with a similar name is left alone
    for pid, _ in studio_auto._profile_processes():
        studio_auto._end_process(pid, force=True)
    end = time.time() + 10
    while studio_auto._profile_processes() and time.time() < end:
        time.sleep(0.3)
    exe = studio_auto.find_browser()
    other_dir = studio_auto._profile_dir() + "-other"
    flags = ["--headless=new", "--no-sandbox", "--no-first-run"]
    holder = subprocess.Popen([exe, *flags, f"--user-data-dir={studio_auto._profile_dir()}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    other = subprocess.Popen([exe, *flags, f"--user-data-dir={other_dir}", "about:blank"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 15
    while not studio_auto._profile_processes() and time.time() < end:
        time.sleep(0.3)
    time.sleep(2)
    studio_auto._state.update(proc=None, port=0)
    check("a browser holding the profile is found", len(studio_auto._profile_processes()) > 0)
    srv = fake_studio.start()
    try:
        r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
        check("a browser holding the profile with no port is closed, and the upload goes on",
              r["posted"] and len(srv.posted) == 1, r)
    except studio_auto.StudioError as e:
        check("a browser holding the profile with no port is closed, and the upload goes on", False, str(e))
    check("the browser on another profile is left alone", other.poll() is None, other.poll())
    for p in (other, holder):
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True) if os.name == "nt" else p.kill()
    srv.shutdown()

    # 10. a clip left in TikTok's editor asks "Leave site?" when the next upload opens a fresh page
    srv = fake_studio.start(mode="leave")
    try:
        studio_auto.upload(clip, CAPTION, False, srv.url, steps.append)       # stays in the editor, typed in
        t0 = time.time()
        r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
        check("a 'Leave site?' question doesn't stop the next upload", r["posted"] and len(srv.posted) == 1, r)
        check("and doesn't hold it up", time.time() - t0 < 30, round(time.time() - t0, 1))
    except studio_auto.StudioError as e:
        check("a 'Leave site?' question doesn't stop the next upload", False, (e.step, str(e)))
    srv.shutdown()

    # 11. TikTok says the upload failed: said at once, with TikTok's words (it waited 15 minutes)
    srv = fake_studio.start(mode="fails")
    t0 = time.time()
    try:
        studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
        check("TikTok refusing the video is a named failure", False, "no error")
    except studio_auto.StudioError as e:
        check("TikTok refusing the video is a named failure, with what TikTok said",
              e.step == "upload" and "format" in str(e), (e.step, str(e)))
    check("and is said within seconds", time.time() - t0 < 30, round(time.time() - t0, 1))
    srv.shutdown()

    # 12. a caption longer than TikTok takes: shortened to fit, keeping the hashtags, instead of failing
    srv = fake_studio.start(mode="short")
    long_cap = "This is a very long caption " * 8 + "\n\n#twitch #kaicenat"
    old_max = studio_auto.CAPTION_MAX
    studio_auto.CAPTION_MAX = 120
    try:
        r = studio_auto.upload(clip, long_cap, True, srv.url, steps.append)
        got_cap = srv.posted[0]["caption"] if srv.posted else ""
        check("a caption past TikTok's limit is shortened, and posted", r["posted"] and len(got_cap) <= 120, (r, len(got_cap)))
        check("keeping its hashtags", got_cap.split()[-2:] == ["#twitch", "#kaicenat"], got_cap)
    except studio_auto.StudioError as e:
        check("a caption past TikTok's limit is shortened, and posted", False, (e.step, str(e)))
    finally:
        studio_auto.CAPTION_MAX = old_max
    srv.shutdown()

    # 13. TikTok in another language: Post and Post now found without English words
    srv = fake_studio.start(mode="es")
    try:
        r = studio_auto.upload(clip, CAPTION, True, srv.url, steps.append)
        check("TikTok in Spanish: posts", r == {"posted": True, "pressed": True} and len(srv.posted) == 1, r)
    except studio_auto.StudioError as e:
        check("TikTok in Spanish: posts", False, (e.step, str(e)))
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
