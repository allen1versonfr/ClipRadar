"""
Upload an exported VOD to YouTube Studio for you (moved from studio_auto.py, hardening phase 6; no changes): Clip
Radar's own browser window opens YouTube Studio's upload dialog, puts the video in and types the title and
description. It stops there: audience, visibility and Publish are choices only you can make, so they're never pressed.

  upload_youtube(path, title, description, url, step, check)   {"ready", "title", "description"}; raises StudioError
  youtube_title(text)                                           a title YouTube takes (one line, no <>, 100 characters)
  YT                                                            how YouTube Studio's dialog is recognised (selectors)

The browser plumbing it shares with TikTok's upload (the window, its tabs, waiting for the page, the fingerprint of a
failed upload, one upload at a time) stays in studio_auto.py and is read here as studio_auto.<name> at call time, so
a change to one site's page can't break the other's code, and a test that swaps a plumbing name on studio_auto
reaches this code too. A test that swaps a name defined here swaps youtube_auto.<name>.
"""

from __future__ import annotations

import sys

if "studio_auto" not in sys.modules:
    # imported before studio_auto: studio_auto imports this module itself (at its end), so studio_auto is imported first
    # and this copy steps aside (the import then gives back the copy studio_auto loaded in full)
    del sys.modules[__name__]
    import studio_auto  # noqa: E402,F401

import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from typing import Callable  # noqa: E402
import cdp  # noqa: E402
import studio_auto  # noqa: E402  shared plumbing, read as studio_auto.<name> at call time


# ---------------------------------------------------------------- YouTube Studio (Export VOD)

YT_TITLE_MAX = 100            # YouTube's title limit (characters)


# How YouTube Studio's upload dialog is recognised. Each is a JavaScript expression run in the page.
YT = {
    # the hidden file box behind "Select files"
    "file": "document.querySelector('ytcp-uploads-file-picker input[type=file]') || document.querySelector('input[type=file]')",
    # Google asking you to sign in first
    "login": "location.hostname === 'accounts.google.com' || /ServiceLogin|\\/signin/i.test(location.pathname)",
    # the details step, once YouTube has the file: the title and description boxes (rich text)
    "title": ("document.querySelector('#title-textarea #textbox') || document.querySelector('ytcp-video-title [contenteditable=true]')"
              " || document.querySelector('[contenteditable=true][aria-label*=title i]')"),
    "description": ("document.querySelector('#description-textarea #textbox')"
                    " || document.querySelector('ytcp-video-description [contenteditable=true]')"
                    " || document.querySelector('[contenteditable=true][aria-label*=viewers i]')"),
}


def youtube_title(text: str) -> str:
    """A title YouTube takes: one line, no angle brackets (YouTube refuses them), at most 100 characters."""
    t = re.sub(r"\s+", " ", (text or "").replace("<", "").replace(">", "")).strip()
    return t[:YT_TITLE_MAX].rstrip()


def _yt_tab(port: int, url: str) -> tuple:
    """(a tab for YouTube's upload page, whether it was just opened on it). The tab already on YouTube Studio
    (or on `url`) is used again; the TikTok tab is left alone."""
    for t in cdp.targets(port):
        u = t.get("url", "")
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl") and (
                u.startswith("https://studio.youtube.com") or u.startswith(re.sub(r"[?#].*$", "", url))):
            return cdp.Page(t["webSocketDebuggerUrl"], t.get("id", "")), False
    t = cdp.new_tab(port, url)
    return cdp.Page(t["webSocketDebuggerUrl"], t.get("id", "")), True


def _yt_put(page: cdp.Page, box: str, text: str) -> bool:
    """Replace what's in one of YouTube's rich text boxes with text. True when the box then says it."""
    want = studio_auto._caption_words(text)
    for _ in range(2):
        ok = page.eval(f"(() => {{ const e = {box}; if (!e) return false; e.focus(); const r = document.createRange(); "
                       "r.selectNodeContents(e); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return true })()",
                       timeout=10)
        if not ok:
            return False
        page.call("Input.insertText", text=text)
        time.sleep(0.3)
        if studio_auto._caption_words(page.eval(f"(({box}) || {{}}).innerText || ''", timeout=10) or "") == want:
            return True
    return False


def upload_youtube(path: str, title: str, description: str, url: str, step: Callable[[str], None],
                   check: Callable[[], None] = lambda: None) -> dict:
    """Drive YouTube Studio's upload dialog in Clip Radar's own browser window: the video put in, the title and
    description typed. It stops there. Audience, visibility and Publish are choices only you can make, so they
    are never pressed. Returns {"ready": True, "title": typed?, "description": typed?}; raises StudioError."""
    if not os.path.isfile(path):
        e = studio_auto.StudioError("file", "The exported file isn't there any more. Export the video again first.")
        studio_auto._record_failure("youtube", e, None)
        raise e
    title = youtube_title(title)
    if not studio_auto._one_at_a_time.acquire(blocking=False):
        step("Waiting for the upload before it to finish")
        while not studio_auto._one_at_a_time.acquire(timeout=1.0):
            check()
    page = None
    try:
        step("Opening YouTube Studio")
        port = studio_auto.open_browser(url)
        page, fresh = _yt_tab(port, url)
        studio_auto._clear_dialog(page)
        page.call("Page.enable")
        if not fresh:
            studio_auto._navigate(page, url, check)             # a new upload dialog, not the last video's
        page.call("Page.bringToFront")
        first = studio_auto._first(page, {"file": YT["file"], "login": YT["login"]}, 45, check)
        if first == "login":
            step("Sign in to YouTube in the window that opened. Clip Radar carries on when you're in.")
            if not studio_auto._wait(page, YT["file"], studio_auto.LOGIN_WAIT, check, every=1.0):
                raise studio_auto.StudioError("sign-in", "YouTube Studio didn't open after signing in. Sign in in Clip Radar's "
                                             "browser window, then try again.")
        elif first != "file":
            raise studio_auto.StudioError("file box", "YouTube Studio's upload box didn't appear. If YouTube is asking something in "
                                          "the window (to create a channel, for example), answer it and try again.")
        step("Putting the video in")
        page.call("DOM.getDocument", depth=0)          # the DOM agent needs a document before files can be set
        node = page.call("Runtime.evaluate", expression=YT["file"])["result"]
        if node.get("subtype") == "null" or "objectId" not in node:
            raise studio_auto.StudioError("file box", "Couldn't find YouTube Studio's upload box. YouTube may have changed the page.")
        page.call("DOM.setFileInputFiles", files=[os.path.abspath(path)], objectId=node["objectId"])
        if not studio_auto._wait(page, YT["title"], 120, check):
            raise studio_auto.StudioError("details", "The video went in, but YouTube's title box didn't appear. Finish it in the "
                                         "YouTube window.")
        step("Typing the title")
        time.sleep(0.5)                                # YouTube fills the title with the file's name first
        typed = bool(title) and _yt_put(page, YT["title"], title)
        wrote = False
        if description.strip():
            step("Typing the description")
            wrote = _yt_put(page, YT["description"], description.strip())
        page.call("Page.bringToFront")
        return {"ready": True, "title": typed, "description": wrote}
    except studio_auto.StudioError as e:
        studio_auto._record_failure("youtube", e, studio_auto.fingerprint(page, "youtube", private=(title, description, path)) if page else None)
        raise
    except (cdp.CDPError, OSError) as e:
        if str(e).startswith(studio_auto.PAGE_SCRIPT):
            err = studio_auto.StudioError("details", "YouTube Studio's page didn't respond as expected. YouTube may have changed "
                                         "the page: finish it in the YouTube window.")
            studio_auto._record_failure("youtube", err, studio_auto.fingerprint(page, "youtube", private=(title, description, path)) if page else None)
            raise err from None
        err = studio_auto.StudioError("browser", f"Lost Clip Radar's browser window ({e}). Try again.")
        studio_auto._record_failure("youtube", err, None)
        raise err from None
    finally:
        if page is not None:
            page.close()
        studio_auto._one_at_a_time.release()
