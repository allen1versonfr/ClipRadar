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
    # 5.1: the rest of the dialog, for a clip uploaded with Settings › YouTube's choices
    "kids_no": "document.querySelector('tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]')",
    "kids_yes": "document.querySelector('tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_MFK]')",
    "more": "document.querySelector('#toggle-button')",                       # "Show more", where the tags are
    "tags": ("document.querySelector('#tags-container input') || document.querySelector('ytcp-free-text-chip-bar input')"
             " || document.querySelector('input[aria-label*=tags i]')"),
    "next": "document.querySelector('#next-button')",
    "visibility": "document.querySelector('tp-yt-paper-radio-button[name=PUBLIC]')",
    "done": "document.querySelector('#done-button')",
    # after Publish: the dialog saying it's out, or the upload dialog gone
    "published": ("document.querySelector('ytcp-video-share-dialog') || document.querySelector('ytcp-uploads-still-processing-dialog')"
                  " || !document.querySelector('ytcp-uploads-dialog #done-button')"),
}
VISIBILITY = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}


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


def _click(page: cdp.Page, what: str) -> bool:
    return bool(page.eval(f"(() => {{ const e = {what}; if (!e) return false; e.scrollIntoView({{block: 'center'}}); e.click(); return true }})()",
                          timeout=10))


def _finish(page: cdp.Page, opts: dict, step: Callable[[str], None], check: Callable[[], None]) -> dict:
    """5.1, for a clip: the rest of the dialog as Settings › YouTube has it. Who it's for (made for kids or not),
    the tags, then Next to the visibility step, where public, unlisted or private is picked, and Publish pressed
    only when "Publish for me" is on. Any part YouTube's page doesn't show is left for you, never guessed at:
    {"audience", "tags", "visibility", "published": done?, "left": what's left to do in the window}."""
    out = {"audience": False, "tags": False, "visibility": False, "published": False, "left": ""}
    step("Setting who it's for")
    if studio_auto._wait(page, YT["kids_no"], 10, check):
        out["audience"] = _click(page, YT["kids_yes" if opts.get("kids") else "kids_no"])
    tags = [x.strip() for x in str(opts.get("tags") or "").split(",") if x.strip()]
    if tags:
        step("Adding the tags")
        shown = f"(e => !!e && e.offsetParent !== null)({YT['tags']})"      # behind "Show more" until it's pressed
        if not page.eval(shown, timeout=10):
            _click(page, YT["more"])
            studio_auto._wait(page, shown, 5, check)
        if page.eval(f"(() => {{ const e = {YT['tags']}; if (!e) return false; e.focus(); return true }})()", timeout=10):
            page.call("Input.insertText", text=", ".join(tags)[:500] + ",")
            out["tags"] = True
    step("Going to the visibility step")
    for _ in range(4):                              # Details › Video elements › Checks › Visibility
        if page.eval(f"!!({YT['visibility']})", timeout=10):
            break
        if not _click(page, YT["next"]):
            break
        studio_auto._wait(page, YT["visibility"], 6, check)
    vis = VISIBILITY.get(str(opts.get("visibility") or "public"), "PUBLIC")
    if page.eval(f"!!({YT['visibility']})", timeout=10):
        out["visibility"] = _click(page, f"document.querySelector('tp-yt-paper-radio-button[name={vis}]')")
    if not out["visibility"]:
        out["left"] = "Choose who can see it and publish it in the YouTube window."
        return out
    if not opts.get("publish"):
        out["left"] = "Press Publish (or Save) in the YouTube window."
        return out
    step("Publishing")
    if _click(page, YT["done"]) and studio_auto._wait(page, YT["published"], 30, check, every=0.5):
        out["published"] = True
    else:
        out["left"] = "Clip Radar pressed Publish, but YouTube didn't say it's out. Check the YouTube window."
    return out


def upload_youtube(path: str, title: str, description: str, url: str, step: Callable[[str], None],
                   check: Callable[[], None] = lambda: None, opts: dict | None = None) -> dict:
    """Drive YouTube Studio's upload dialog in Clip Radar's own browser window: the video put in, the title and
    description typed. Without opts (Export VOD) it stops there: audience, visibility and Publish are left to you.
    With opts (a clip, 5.1: Settings › YouTube) it goes on: who it's for, the tags, the visibility, and Publish only
    when opts["publish"]. Returns {"ready": True, "title": typed?, "description": typed?} (and what _finish did);
    raises StudioError."""
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
        rest = _finish(page, opts, step, check) if opts is not None else {}
        page.call("Page.bringToFront")
        return dict({"ready": True, "title": typed, "description": wrote}, **rest)
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
