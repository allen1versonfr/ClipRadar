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
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

import cdp

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
PROFILE = "studio-browser"
LOGIN_WAIT = 15 * 60          # time to sign in to TikTok the first time
UPLOAD_WAIT = 15 * 60         # a long clip on a slow connection
CAPTION_MAX = 4000            # TikTok Studio's caption limit (characters); longer ones are shortened to fit
_state: dict = {"proc": None, "port": 0}

# How the upload page is recognised. Each is a JavaScript expression run in the page.
SELECTORS = {
    # the hidden file box TikTok's "Select video" button opens
    "file": "document.querySelector('input[type=file][accept*=video]') || document.querySelector('input[type=file]')",
    # the caption box: a rich text editor (contenteditable), in the caption area when it can be told apart
    "caption": ("document.querySelector('[data-e2e=caption_container] [contenteditable=true]')"
                " || document.querySelector('.public-DraftEditor-content[contenteditable=true]')"
                " || document.querySelector('[contenteditable=true]')"),
    # the Post button (not "Save draft", not "Discard"), and whether it can be pressed yet. By TikTok's own mark
    # first; by its word in the languages TikTok Studio is in after that (4.36: English only before)
    "post": ("document.querySelector('button[data-e2e=post_video_button]') || [...document.querySelectorAll('button')]"
             ".find(b => /^\\s*(post|publicar|publier|posten|veröffentlichen|pubblica|postar|опубликовать|"
             "投稿|게시|发布|發佈|yayınla|opublikuj|publiceren|đăng|โพสต์|kirim|posting)\\s*$/i.test(b.innerText || ''))"),
    # TikTok asking you to sign in first
    "login": "/\\/login/.test(location.pathname) || !!document.querySelector('[data-e2e=login-modal], #login-modal')",
    # TikTok saying the post went through
    "posted": ("/\\/tiktokstudio\\/content/.test(location.pathname) || /(video|post) (has been|was) "
               "(uploaded|posted|published)|your video is being uploaded/i.test(document.body.innerText || '')"),
    # TikTok saying it couldn't take the video (4.36: before, Clip Radar waited the full 15 minutes for Post)
    "upload_error": ("(() => { const t = document.body.innerText || ''; const m = t.match(/[^\\n]*(upload failed|"
                     "couldn.t upload|could not upload|couldn.t be uploaded|unable to upload|failed to upload|"
                     "file is too (large|big)|exceeds the (maximum|size)|(format|file type) (isn.t|is not) supported|"
                     "unsupported (video|file|format))[^\\n]*/i); return m ? m[0].trim().slice(0, 200) : '' })()"),
}
# the video went in: the caption box is showing (it can be in the page, hidden, before TikTok takes the file)
SHOWN_CAPTION = f"((e) => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length))({SELECTORS['caption']})"
# TikTok's "Post now", when it asks to confirm a post (in the languages TikTok Studio is in)
POST_NOW = ("[...document.querySelectorAll('button')].find(x => /^\\s*(post now|publicar ahora|publier maintenant|"
            "jetzt posten|jetzt veröffentlichen|pubblica ora|postar agora|publicar agora|опубликовать сейчас|"
            "今すぐ投稿|지금 게시|立即发布|立即發佈|şimdi yayınla|opublikuj teraz|nu publiceren|đăng ngay|"
            "posting sekarang)\\s*$/i.test(x.innerText || ''))")



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


PORT_FILE = "clipradar-port"


def _profile_port() -> int:
    """The DevTools port of a browser already open with Clip Radar's TikTok profile, or 0: the one Clip Radar
    noted in the profile when it opened the window, or the browser's own note (DevToolsActivePort), if it
    still answers."""
    for name in (PORT_FILE, "DevToolsActivePort"):
        try:
            with open(os.path.join(_profile_dir(), name), encoding="utf-8") as f:
                port = int((f.readline() or "0").strip() or 0)
        except (OSError, ValueError):
            continue
        if port and cdp.alive(port):
            return port
    return 0


def _note_port(port: int) -> None:
    try:
        with open(os.path.join(_profile_dir(), PORT_FILE), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass


def _profile_processes() -> list:
    """(pid, is the main process) of every browser process on Clip Radar's TikTok profile. Told apart by the
    exact profile in their command line, so your everyday Edge or Chrome is never one of them."""
    name = os.path.basename(find_browser())
    if sys.platform == "win32":
        ps = (f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
              "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
    else:
        cmd = ["ps", "-eo", "pid=,args="]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                             stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    mine = re.compile(r"--user-data-dir=\"?" + re.escape(_profile_dir()) + r"\"?(\s|$)", re.I)
    found = []
    for line in out.splitlines():
        pid, _, rest = line.strip().partition(" ")
        if pid.isdigit() and mine.search(rest):
            found.append((int(pid), "--type=" not in rest))
    return found


def _end_process(pid: int, force: bool) -> bool:
    """Ask a browser process to close (force: end it). False if that couldn't be done."""
    try:
        if sys.platform == "win32":
            cmd = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
            return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
                                  creationflags=CREATE_NO_WINDOW).returncode == 0
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _close_profile_browser() -> bool:
    """Close whatever holds the TikTok profile without a DevTools port, so the window can open again. Asked
    nicely first (TikTok stays signed in), then ended: it may have no window to answer with. True if it's gone."""
    found = _profile_processes()
    if not found:
        return False
    mains = [pid for pid, main in found if main] or [pid for pid, _ in found]
    asked = [_end_process(pid, force=False) for pid in mains]
    end = time.time() + (6 if all(asked) else 0)
    while time.time() < end:
        if not _profile_processes():
            return True
        time.sleep(0.5)
    for pid, _ in _profile_processes():
        _end_process(pid, force=True)
    end = time.time() + 5
    while time.time() < end:
        if not _profile_processes():
            return True
        time.sleep(0.5)
    return False


def open_browser(url: str) -> int:
    """The DevTools port of Clip Radar's TikTok window, opening it (at url) if it isn't open."""
    port = _state["port"]
    if port and cdp.alive(port):
        return port
    # 4.36: the window may still be open from before Clip Radar restarted. Starting the browser again with the
    # same profile only hands the link to that window and exits, so the new port never answered and it failed
    port = _profile_port()
    if port:
        _state["port"] = port
        return port
    exe = find_browser()
    if not exe:
        raise StudioError("browser", "Clip Radar needs Microsoft Edge or Google Chrome to upload for you.")
    # 4.38: a browser can hold the profile with no DevTools port (nothing Clip Radar can drive, often nothing
    # on screen either), and every start is handed to it and lost until it goes. It's closed, then started again
    for attempt in range(2):
        port = _start_browser(exe, url)
        if port:
            _state["port"] = port
            return port
        if attempt or not _close_profile_browser():
            break
    code = _state["proc"].poll() if _state["proc"] else None
    print(f"  TikTok window: the browser didn't open a DevTools port (exit code {code})", flush=True)
    raise StudioError("browser", "The browser wouldn't open Clip Radar's TikTok window.")


def _start_browser(exe: str, url: str) -> int:
    """Start the TikTok window; its DevTools port, or 0 if it never gave one."""
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
            _note_port(port)
            return port
        if _state["proc"].poll() is not None:
            # it handed over to a window already open with this profile, or restarted itself (a browser update):
            # either takes a moment to show, so look for a few seconds, not once
            end = min(end, time.time() + 4)
            got = _profile_port()
            if got:
                return got
        time.sleep(0.3)
    return 0


def _answer_dialogs(page: cdp.Page) -> bool:
    """Answer a question the page put up ("Leave site?", an alert): it stops the page, and every script run in
    it, until someone clicks. True if there was one."""
    asked = [e for e in page.events if e.get("method") == "Page.javascriptDialogOpening"]
    if not asked:
        return False
    page.events = [e for e in page.events if e.get("method") != "Page.javascriptDialogOpening"]
    try:
        page.call("Page.handleJavaScriptDialog", timeout=5, accept=True)
    except cdp.CDPError:
        pass                                        # already gone
    return True


def _clear_dialog(page: cdp.Page) -> None:
    """A question left open on the page from before (it stops every script, and even Page.enable): answered."""
    try:
        page.call("Page.handleJavaScriptDialog", timeout=3, accept=True)
    except cdp.CDPError:
        pass                                        # none showing: the usual case


def _navigate(page: cdp.Page, url: str, check: Callable, seconds: float = 30) -> None:
    """Open url in the tab. Started from inside the page, so a "Leave site?" question (a clip left in TikTok's
    editor) can be answered as soon as it shows: Page.navigate doesn't return until someone answers it, and
    answering only after it gave up left the page half-way (4.36)."""
    page.eval("window.__clipradar_old = true", timeout=5)
    page.eval(f"setTimeout(() => location.assign({json.dumps(url)}), 0), true", timeout=5)
    end = time.time() + seconds
    while time.time() < end:
        check()
        _answer_dialogs(page)
        try:
            if page.eval("!window.__clipradar_old && document.readyState !== 'loading'", timeout=1.5):
                return
        except cdp.CDPError:
            pass                                    # navigating, or held by a question (answered next time round)
        time.sleep(0.2)
    raise cdp.CDPError(f"The TikTok page didn't open ({url}).")


def _wait(page: cdp.Page, what: str, seconds: float, check: Callable, every: float = 0.25) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        check()
        _answer_dialogs(page)
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
        _answer_dialogs(page)
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


def fit_caption(caption: str, limit: Optional[int] = None) -> str:
    """The caption within TikTok's limit: the words before the hashtags are shortened (ending on a whole word,
    with '…'), the hashtags kept; only when the hashtags alone don't fit do the last ones go. TikTok's editor
    cuts a longer caption off wherever it hits the limit, and the caption then never read back as written."""
    limit = limit or CAPTION_MAX
    caption = (caption or "").strip()
    if len(caption) <= limit:
        return caption
    words = caption.split(" ")
    tags_at = len(words)
    while tags_at > 0 and words[tags_at - 1].lstrip("\n").startswith("#"):
        tags_at -= 1
    head, tags = " ".join(words[:tags_at]).rstrip(), " ".join(words[tags_at:]).strip()
    sep = "\n\n" if tags and "\n" in caption else " "
    room = limit - (len(tags) + len(sep) if tags else 0) - 1
    if room < 10:                                   # the hashtags alone don't fit: drop the last ones
        keep = []
        for t in tags.split():
            if len(" ".join(keep + [t])) > limit:
                break
            keep.append(t)
        return " ".join(keep)
    cut = head[:room].rsplit(" ", 1)[0].rstrip(" ,.;:-") if " " in head[:room] else head[:room]
    return (cut + "…" + (sep + tags if tags else "")).strip()


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
    caption = fit_caption(caption)
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
        _clear_dialog(page)
        page.call("Page.enable")
        # a fresh upload page every time: after an upload, the window is still on the page but with that
        # clip in its editor (no upload box), so the next clip would wait for one that never comes. A clip left
        # in the editor makes the page ask "Leave site?" (4.36: answered, see _navigate)
        _navigate(page, url, check)
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
        # TikTok keeps Post off until the upload is in, or says it couldn't take the video
        got = _first(page, {"ready": ready, "error": SELECTORS["upload_error"]}, UPLOAD_WAIT, check, every=0.5)
        if got == "error":
            said = ""
            try:
                said = page.eval(SELECTORS["upload_error"], timeout=5) or ""
            except cdp.CDPError:
                pass
            raise StudioError("upload", f"TikTok couldn't take the video: {said or 'the upload failed'}. "
                                        "Render it again, or try it in the window yourself.")
        if got != "ready":
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
