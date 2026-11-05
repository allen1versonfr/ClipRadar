"""
When Upload to TikTok starts again, and when it doesn't (4.45, studio_auto.upload).

It starts again, once, only when Clip Radar's TikTok window or tab went away. Not when:
  - a script on TikTok's page failed (the page changed): that uploaded the video a second time, then said the
    window was lost and opened TikTok Studio in your own browser too. It's a failure of the step it was on.
  - Post had been pressed: starting again could post the clip twice. It says to check TikTok first.

  python tests/test_studio_retry.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import cdp  # noqa: E402
import studio_auto  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


clip = os.path.join(tempfile.mkdtemp(prefix="vc_clip_"), "clip.mp4")
open(clip, "wb").write(b"x" * 1000)
studio_auto.cdp.alive = lambda port: True
real = studio_auto._upload_once


def run(script):
    """upload() with _upload_once replaced by script(n, step, at): n counts the tries."""
    tries, steps = [], []

    def fake(path, caption, press_post, url, step, check, at=None):
        tries.append(1)
        return script(len(tries), step, at)
    studio_auto._upload_once = fake
    try:
        return studio_auto.upload(clip, "Big play #fyp", True, "http://x/tiktokstudio/upload", steps.append), tries, steps, None
    except studio_auto.StudioError as e:
        return None, tries, steps, e
    finally:
        studio_auto._upload_once = real


# the window went away: opened again, once, and it goes through
def lost_once(n, step, at):
    step("Opening TikTok Studio")
    if n == 1:
        raise cdp.CDPError("The browser window was closed.")
    return {"posted": True, "pressed": True}


r, tries, steps, e = run(lost_once)
check("the window went away: it opens it again and carries on", r == {"posted": True, "pressed": True} and len(tries) == 2, (r, e))
check("and says so", "The TikTok window went away; opening it again" in steps, steps)


# a script on TikTok's page failed while typing the caption: not tried again, and named
def page_changed(n, step, at):
    step("Opening TikTok Studio")
    step("Putting the video in")
    step("Typing the caption")
    raise cdp.CDPError("Page script failed: TypeError: Cannot read properties of null (reading 'focus')")


r, tries, steps, e = run(page_changed)
check("TikTok's page changed: the video isn't uploaded a second time", len(tries) == 1, len(tries))
check("it's the caption step that failed, not a lost window",
      e is not None and e.step == "caption box" and "TikTok may have changed the page" in str(e), (e and e.step, str(e)))
check("so Clip Radar doesn't open TikTok in your own browser as well", e is not None and e.step != "browser")


# Post was pressed, then the window went away: never tried again, and told to check TikTok
def lost_after_post(n, step, at):
    step("Pressing Post")
    at["pressed"] = True
    raise cdp.CDPError("The browser window was closed.")


r, tries, steps, e = run(lost_after_post)
check("lost after pressing Post: not tried again (it could post twice)", len(tries) == 1, len(tries))
check("it says to check TikTok first", e is not None and e.step == "post" and "may have been posted" in str(e), (e and e.step, str(e)))


# lost twice before Post: a lost window, said as before
def always_lost(n, step, at):
    raise OSError("connection reset")


r, tries, steps, e = run(always_lost)
check("lost twice: a lost window, after one more try", len(tries) == 2 and e is not None and e.step == "browser", (len(tries), e))

# the lock is given back every time: a second upload isn't left waiting
check("one upload at a time is released after each", studio_auto._one_at_a_time.acquire(blocking=False))
studio_auto._one_at_a_time.release()

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
