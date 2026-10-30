"""
Upload a clip to TikTok Studio for you (4.18): Clip Radar opens TikTok Studio in a browser window of
its own (Edge, or Chrome), puts the video in, types the caption, and, only when "Press Post for me"
is on, presses Post. You sign in to TikTok in that window once; it keeps its own profile in Clip
Radar's data folder, apart from your everyday browser.

TikTok's page isn't an API: when TikTok changes it, a step here can stop working. Every step waits for
what it needs and says which step failed, and anything that fails leaves the window open with the
video and caption ready for you to finish by hand (the caption is also copied). Clip Radar only says
a clip was posted when it saw TikTok confirm it.

The page is found by what it shows (the file box, the caption box, the button that says Post), kept in
SELECTORS below, so an update to them is one place. CLIPRADAR_STUDIO_BROWSER picks the browser (tests).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

import cdp

PROFILE = "studio-browser"
LOGIN_WAIT = 15 * 60          # time to sign in to TikTok the first time
UPLOAD_WAIT = 15 * 60         # a long clip on a slow connection
_state: dict = {"proc": None, "port": 0}

# How the upload page is recognised. Each is a JavaScript expression run in the page.
SELECTORS = {
    # the hidden file box TikTok's "Select video" button opens
    "file": "document.querySelector('input[type=file][accept*=video]') || document.querySelector('input[type=file]')",
    # the caption box: a rich text editor (contenteditable), in the caption area when it can be told apart
    "caption": ("document.querySelector('[data-e2e=caption_container] [contenteditable=true]')"
                " || document.querySelector('.public-DraftEditor-content[contenteditable=true]')"
                " || document.querySelector('[contenteditable=true]')"),
    # the Post button (not "Save draft", not "Discard"), and whether it can be pressed yet
    "post": ("document.querySelector('button[data-e2e=post_video_button]') || [...document.querySelectorAll('button')]"
             ".find(b => /^\\s*post\\s*$/i.test(b.innerText || ''))"),
    # TikTok asking you to sign in first
    "login": "/\\/login/.test(location.pathname) || !!document.querySelector('[data-e2e=login-modal], #login-modal')",
    # TikTok saying the post went through
    "posted": ("/\\/tiktokstudio\\/content/.test(location.pathname) || /(video|post) (has been|was) "
               "(uploaded|posted|published)|your video is being uploaded/i.test(document.body.innerText || '')"),
}
# the video went in: the caption box is showing (it can be in the page, hidden, before TikTok takes the file)
SHOWN_CAPTION = f"((e) => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length))({SELECTORS['caption']})"
# TikTok's "Post now", when it asks to confirm a post
POST_NOW = "[...document.querySelectorAll('button')].find(x => /^\\s*post now\\s*$/i.test(x.innerText || ''))"


class StudioError(RuntimeError):
    """A step that couldn't be done; .step names it, for the message and the fallback."""

    def __init__(self, step: str, msg: str):
        super().__init__(msg)
        self.step = step


def find_browser() -> str:
    """Edge (every Windows 10 and 11 PC has it), else Chrome; CLIPRADAR_STUDIO_BROWSER first."""
    env = os.environ.get("CLIPRADAR_STUDIO_BROWSER", "")
    if env:
        return env if os.path.isfile(env) else ""
    if sys.platform == "win32":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            for root in roots:
                p = os.path.join(root, rel) if root else ""
                if p and os.path.isfile(p):
                    return p
        return ""
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def available() -> bool:
    return bool(find_browser())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _profile_dir() -> str:
    import paths
    d = os.path.join(str(paths.DATA_DIR), PROFILE)
    os.makedirs(d, exist_ok=True)
    return d


def open_browser(url: str) -> int:
    """The DevTools port of Clip Radar's TikTok window, opening it (at url) if it isn't open."""
    port = _state["port"]
    if port and cdp.alive(port):
        return port
    exe = find_browser()
    if not exe:
        raise StudioError("browser", "Clip Radar needs Microsoft Edge or Google Chrome to upload for you.")
    port = _free_port()
    args = [exe, f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={_profile_dir()}", "--no-first-run", "--no-default-browser-check",
            "--new-window", url]
    if os.environ.get("CLIPRADAR_STUDIO_HEADLESS") == "1":          # tests only
        args[1:1] = ["--headless=new", "--no-sandbox"]
    # not tied to Clip Radar (procjob): closing Clip Radar mustn't cut off an upload in progress
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _state["proc"] = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      creationflags=flags if sys.platform == "win32" else 0)
    end = time.time() + 30
    while time.time() < end:
        if cdp.alive(port):
            _state["port"] = port
            return port
        if _state["proc"].poll() is not None:
            break
        time.sleep(0.3)
    raise StudioError("browser", "The browser window didn't open. If Edge is already open with another "
                                 "Clip Radar window, close it and try again.")


def _wait(page: cdp.Page, what: str, seconds: float, check: Callable, every: float = 0.25) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        check()
        try:
            if page.eval(f"!!({what})", timeout=10):
                return True
        except cdp.CDPError:
            pass                                    # the page is navigating; try again
        time.sleep(every)
    return False


def _first(page: cdp.Page, whats: dict, seconds: float, check: Callable, every: float = 0.25) -> str:
    """Which of several things the page shows first (its key), or "" if none in time. Waiting for the upload
    box OR the sign-in page answers "signed in?" as soon as the page is up, instead of always waiting 4 s
    for a sign-in page that isn't coming."""
    expr = "(() => {" + "".join(f"if ({w}) return {json.dumps(k)};" for k, w in whats.items()) + "return ''})()"
    end = time.time() + seconds
    while time.time() < end:
        check()
        try:
            got = page.eval(expr, timeout=10)
            if got:
                return got
        except cdp.CDPError:
            pass
        time.sleep(every)
    return ""


def _tab(port: int, url: str) -> cdp.Page:
    """A tab in Clip Radar's TikTok window: the one already on TikTok Studio, else any, else a new one
    (you closed the tab but not the window)."""
    try:
        return cdp.Page.connect(port, url_prefix=url.split("/tiktokstudio")[0])
    except cdp.CDPError:
        cdp.new_tab(port, url)
        return cdp.Page.connect(port)


def _set_file(page: cdp.Page, path: str, check: Callable) -> None:
    """Put the clip in TikTok's file box, and make sure TikTok took it: once in a while the page ignores
    the first file (its box was swapped while it loaded), so it's put in again, once."""
    for attempt in range(2):
        if not _wait(page, SELECTORS["file"], 60 if attempt == 0 else 10, check):
            raise StudioError("file box", "Couldn't find TikTok Studio's upload box. TikTok may have changed the page.")
        page.call("DOM.getDocument", depth=0)          # the DOM agent needs a document before files can be set
        node = page.call("Runtime.evaluate", expression=SELECTORS["file"])["result"]
        if node.get("subtype") == "null" or "objectId" not in node:
            raise StudioError("file box", "Couldn't find TikTok Studio's upload box. TikTok may have changed the page.")
        page.call("DOM.setFileInputFiles", files=[os.path.abspath(path)], objectId=node["objectId"])
        if _wait(page, SHOWN_CAPTION, 12 if attempt == 0 else 120, check):
            return
    raise StudioError("caption box", "The video went in, but the caption box didn't appear.")


def _type_caption(page: cdp.Page, caption: str) -> None:
    """Replace TikTok's caption (it starts with the file name) with ours. TikTok's editor sometimes drops
    or doubles typed text while it's still setting up, or opens a hashtag list that eats the end, so the
    result is read back and typed again (three tries) until it's exactly the caption."""
    want = caption.split()
    for _ in range(3):
        page.eval(f"(() => {{ const e = {SELECTORS['caption']}; e.focus(); return true }})()")
        page.call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=2,
                  windowsVirtualKeyCode=65, commands=["selectAll"])
        page.call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=2, windowsVirtualKeyCode=65)
        page.call("Input.dispatchKeyEvent", type="keyDown", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
        page.call("Input.dispatchKeyEvent", type="keyUp", key="Backspace", code="Backspace", windowsVirtualKeyCode=8)
        page.call("Input.insertText", text=caption)
        # Escape closes a hashtag or mention list without picking from it; then leave the box
        page.call("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        page.call("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        page.eval("document.activeElement && document.activeElement.blur && document.activeElement.blur(), true")
        # the editor settles a moment after typing (and can still change it): right for half a second in a row
        end, since = time.time() + 2.0, None
        while time.time() < end:
            if _caption_ok(page, want):
                since = since or time.time()
                if time.time() - since >= 0.5:
                    return
            else:
                since = None
            time.sleep(0.1)
    raise StudioError("caption", "The caption didn't go in as written. Paste it yourself (it's copied).")


def _caption_ok(page: cdp.Page, want: list) -> bool:
    return (page.eval(f"(({SELECTORS['caption']}).innerText || '').trim()") or "").split() == want


_one_at_a_time = threading.Lock()


def upload(path: str, caption: str, press_post: bool, url: str, step: Callable[[str], None],
           check: Callable[[], None] = lambda: None) -> dict:
    """Drive TikTok Studio's upload page. Returns {"posted": bool, "pressed": bool}; raises StudioError.
    One upload at a time (two would fight over the same tab). If the window or its tab goes away midway
    (closed, crashed), it starts again once in a fresh one; a step TikTok's page refused is not retried."""
    if not os.path.isfile(path):
        raise StudioError("file", "That clip's file isn't there any more. Render it again first.")
    if not _one_at_a_time.acquire(blocking=False):
        step("Waiting for the upload before it to finish")
        while not _one_at_a_time.acquire(timeout=1.0):
            check()
    try:
        for attempt in range(2):
            try:
                return _upload_once(path, caption, press_post, url, step, check)
            except (cdp.CDPError, OSError) as e:
                if attempt:
                    raise StudioError("browser", f"Lost Clip Radar's TikTok window ({e}). Try again.") from None
                step("The TikTok window went away; opening it again")
                _state["port"] = 0 if not cdp.alive(_state["port"]) else _state["port"]
    finally:
        _one_at_a_time.release()
    raise StudioError("browser", "Lost Clip Radar's TikTok window. Try again.")


def _upload_once(path: str, caption: str, press_post: bool, url: str, step: Callable[[str], None],
                 check: Callable[[], None]) -> dict:
    step("Opening TikTok Studio")
    port = open_browser(url)
    page = _tab(port, url)
    try:
        page.call("Page.enable")
        # a fresh upload page every time: after an upload, the window is still on the page but with that
        # clip in its editor (no upload box), so the next clip would wait for one that never comes
        page.call("Page.navigate", url=url)
        page.call("Page.bringToFront")
        # signed in? the first time, TikTok asks; wait for you to sign in, in that window
        first = _first(page, {"file": SELECTORS["file"], "login": SELECTORS["login"]}, 30, check)
        if first == "login":
            step("Sign in to TikTok in the window that opened. Clip Radar carries on when you're in.")
            if not _wait(page, SELECTORS["file"], LOGIN_WAIT, check, every=1.0):
                raise StudioError("sign-in", "TikTok Studio didn't open after signing in. Sign in in Clip Radar's "
                                             "TikTok window, then try again.")
        step("Putting the video in")
        _set_file(page, path, check)

        step("Typing the caption")
        if caption:
            _type_caption(page, caption)

        step("Uploading the video to TikTok")
        ready = (f"(() => {{ const b = {SELECTORS['post']}; return !!b && !b.disabled && "
                 "b.getAttribute('aria-disabled') !== 'true' && !/disabled/i.test(b.className) })()")
        if not _wait(page, ready, UPLOAD_WAIT, check, every=0.5):     # TikTok keeps Post off until the upload is in
            raise StudioError("upload", "TikTok didn't finish taking the video in time. Check the window.")
        if caption and not _caption_ok(page, caption.split()):
            _type_caption(page, caption)            # the page changed the caption while the video went up
        if not press_post:
            page.call("Page.bringToFront")
            return {"posted": False, "pressed": False}

        step("Pressing Post")
        page.eval(f"({SELECTORS['post']}).click(), true")
        # TikTok can ask to confirm (for example "Post now" after its content check): pressed as soon as it
        # shows, rather than after a fixed pause
        got = _first(page, {"posted": SELECTORS["posted"], "confirm": POST_NOW}, 10, check)
        if got == "confirm":
            page.eval(f"({POST_NOW}).click(), true")
        posted = got == "posted" or _wait(page, SELECTORS["posted"], 90, check, every=0.5)
        page.call("Page.bringToFront")
        return {"posted": posted, "pressed": True}
    finally:
        page.close()


def describe() -> dict:
    """For Settings and the Exports panel: whether a browser is there, and the window's state."""
    return {"available": available(), "open": bool(_state["port"] and cdp.alive(_state["port"]))}


def settings_path() -> str:
    import paths
    return os.path.join(str(paths.DATA_DIR), "studio.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {"press_post": bool(d.get("press_post", False))}


def save_settings(press_post: Optional[bool] = None) -> dict:
    d = load_settings()
    if press_post is not None:
        d["press_post"] = bool(press_post)
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, settings_path())
    return d
