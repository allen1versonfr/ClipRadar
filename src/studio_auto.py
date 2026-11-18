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
import unicodedata
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


def _fresh_page(page: cdp.Page, url: str, check: Callable) -> bool:
    """Whether the tab already shows an empty upload page (4.40): the window just opened on it, or nothing was put
    in since it loaded. Loading TikTok Studio again would only cost a few seconds. Still loading counts: waited for,
    up to 15 s. TikTok sending it to sign in counts too (the next step waits for that). A clip in TikTok's editor,
    or any other page, is not fresh."""
    path = json.dumps(re.sub(r"[?#].*$", "", url))
    try:
        if not page.eval(f"(location.origin + location.pathname) === {path}", timeout=5):
            return False
    except cdp.CDPError:
        return False
    # the editor first: a page with a clip in it can still have a file box
    got = _first(page, {"editor": SHOWN_CAPTION, "file": SELECTORS["file"], "login": SELECTORS["login"]}, 15, check)
    return got in ("file", "login")


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


def _key(page: cdp.Page, key: str, code: str, vk: int, text: str = "", modifiers: int = 0, commands=None) -> None:
    down = dict(type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers)
    if text:
        down["text"] = text
    if commands:
        down["commands"] = commands
    page.call("Input.dispatchKeyEvent", **down)
    page.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers)


def _box_empty(page: cdp.Page, seconds: float) -> bool:
    end = time.time() + seconds
    while True:
        if not _caption_words(_read_caption(page)):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


def _clear_caption(page: cdp.Page) -> None:
    """Empty the caption box, and make sure it's empty before anything is typed (4.50.2). TikTok's editor can learn
    about a select-all a moment late, and Backspace then deletes one letter: the caption went in after what was
    there, once per try, and a clip was posted with its title three times. Ctrl+A with a longer pause each time,
    then the box's contents selected directly; if it still isn't empty, nothing is typed."""
    focus = f"(() => {{ const e = {SELECTORS['caption']}; e.focus(); return true }})()"
    for pause in (0.0, 0.15, 0.5):
        page.eval(focus)
        _key(page, "a", "KeyA", 65, modifiers=2, commands=["selectAll"])
        if pause:
            time.sleep(pause)                       # let the editor take in the selection
        _key(page, "Backspace", "Backspace", 8)
        if _box_empty(page, 0.4):
            return
    page.eval(f"(() => {{ const e = {SELECTORS['caption']}; e.focus(); getSelection().selectAllChildren(e); return true }})()")
    time.sleep(0.5)
    _key(page, "Backspace", "Backspace", 8)
    if not _box_empty(page, 0.5):
        raise StudioError("caption", "Couldn't empty TikTok's caption box, so the caption wasn't typed (it would have "
                                     "gone in after what's there). Clear the box and paste it yourself (it's copied).")


def _ends_on_tag(text: str) -> bool:
    """Whether the text ends on a #hashtag or @mention: TikTok's suggestion list is open there."""
    return bool(re.search(r"[#@][^\s#@]+$", text))


# The ways the caption is put in, one per try (4.50.1). Trying the same way three times failed the same way three
# times. Each ends with a space after a closing hashtag or mention, so TikTok's suggestion list closes without
# picking from it: left open, leaving the box picks its top suggestion ("#fyp" became another tag), and Escape
# doesn't close TikTok's list.
def _put_typed(page: cdp.Page, caption: str) -> None:
    """All at once, as typing (an input method committing the text)."""
    page.call("Input.insertText", text=caption + (" " if _ends_on_tag(caption) else ""))


def _put_pasted(page: cdp.Page, caption: str) -> None:
    """As a paste: TikTok's editor (Draft.js) puts pasted text in itself, line breaks kept, where typed line breaks
    can be dropped and glue the hashtags to the words before them."""
    page.eval("(() => { const e = " + SELECTORS["caption"] + "; const d = new DataTransfer(); "
              f"d.setData('text/plain', {json.dumps(caption)}); "
              "e.dispatchEvent(new ClipboardEvent('paste', { clipboardData: d, bubbles: true, cancelable: true })); "
              "return true })()")
    if _ends_on_tag(caption):
        page.call("Input.insertText", text=" ")


def _put_lines(page: cdp.Page, caption: str) -> None:
    """A line at a time, with Enter between lines, as a person types it. A line ending on a hashtag gets its space
    first, or Enter would pick from the suggestion list."""
    for i, line in enumerate(caption.split("\n")):
        if i:
            _key(page, "Enter", "Enter", 13, text="\r")
        if line:
            page.call("Input.insertText", text=line + (" " if _ends_on_tag(line) else ""))


PUT_CAPTION = (_put_typed, _put_pasted, _put_lines)


def _type_caption(page: cdp.Page, caption: str) -> None:
    """Replace TikTok's caption (it starts with the file name) with ours. TikTok's editor sometimes drops
    or doubles typed text while it's still setting up, picks a hashtag suggestion, or drops line breaks, so the
    result is read back and put in again, a different way each time (PUT_CAPTION), until it's exactly the caption."""
    want = _caption_words(caption)
    for put in PUT_CAPTION:
        _clear_caption(page)
        put(page, caption)
        # Escape closes a hashtag or mention list without picking from it (where it does); then leave the box
        _key(page, "Escape", "Escape", 27)
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
    # what TikTok's box held, for the log: the next difference to allow for is in it (4.40)
    try:
        # ascii(): escaped, since an emoji can't be printed where the log isn't UTF-8 (and it's the emoji that matter)
        print(f"  TikTok caption: typed {ascii(caption)}, the box shows {ascii(_read_caption(page)[:300])}", flush=True)
    except (cdp.CDPError, OSError):
        pass
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


_INVISIBLE = set("\u200b\u200c\u200d\u2060\ufeff\ufe0e\ufe0f")


def _caption_words(text: str) -> list:
    """The caption's words, as compared between what was typed and what TikTok's box shows (4.40). TikTok's editor
    doesn't give back exactly what went in, and every difference that isn't a real one stopped an upload at
    "The caption didn't go in as written". Not differences: invisible characters (around hashtags), how a letter
    is encoded, and emoji, which an editor can draw as pictures that its text leaves out. Words and hashtags are."""
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if ch not in _INVISIBLE and unicodedata.category(ch) not in ("So", "Sk", "Cs"))
    return text.split()


def _read_caption(page: cdp.Page) -> str:
    return page.eval(f"(({SELECTORS['caption']}).innerText || '').trim()") or ""


def _caption_ok(page: cdp.Page, want: list) -> bool:
    return _caption_words(_read_caption(page)) == want


_one_at_a_time = threading.Lock()


def upload(path: str, caption: str, press_post: bool, url: str, step: Callable[[str], None],
           check: Callable[[], None] = lambda: None) -> dict:
    """Drive TikTok Studio's upload page. Returns {"posted": bool, "pressed": bool}; raises StudioError.
    One upload at a time (two would fight over the same tab). If the window or its tab goes away midway
    (closed, crashed), it starts again once in a fresh one; a step TikTok's page refused is not retried.
    A failure is recorded in the error log with what the page looked like (fingerprint)."""
    at: dict = {}
    try:
        return _upload(path, caption, press_post, url, step, check, at)
    except StudioError as e:
        _record_failure("tiktok", e, at.get("fingerprint"))
        raise


def _upload(path: str, caption: str, press_post: bool, url: str, step: Callable[[str], None],
            check: Callable[[], None], at: dict) -> dict:
    if not os.path.isfile(path):
        raise StudioError("file", "That clip's file isn't there any more. Render it again first.")
    caption = fit_caption(caption)
    if not _one_at_a_time.acquire(blocking=False):
        step("Waiting for the upload before it to finish")
        while not _one_at_a_time.acquire(timeout=1.0):
            check()
    try:
        for attempt in range(2):
            at.clear()
            at.update(step="browser", pressed=False)        # how far it got, for what a failure means

            def where(s: str):
                at["step"] = STEP_OF.get(s, at["step"])
                step(s)
            try:
                return _upload_once(path, caption, press_post, url, where, check, at)
            except (cdp.CDPError, OSError) as e:
                if at["pressed"]:
                    # 4.45: Post was pressed. Starting again would upload, and maybe post, the clip a second time
                    raise StudioError("post", "Lost Clip Radar's TikTok window after pressing Post. Check your TikTok "
                                              "profile before uploading it again: it may have been posted.") from None
                if str(e).startswith(PAGE_SCRIPT):
                    # 4.45: TikTok's page didn't have what a step looks for. That isn't the window going away: starting
                    # again uploaded the video a second time, then said the window was lost and opened TikTok in
                    # your own browser too
                    raise StudioError(at["step"], f"TikTok Studio's page didn't respond as expected while "
                                                  f"{STEP_WORDS.get(at['step'], 'uploading')}. TikTok may have changed "
                                                  "the page: finish it in the TikTok window.") from None
                if attempt:
                    raise StudioError("browser", f"Lost Clip Radar's TikTok window ({e}). Try again.") from None
                step("The TikTok window went away; opening it again")
                _state["port"] = 0 if not cdp.alive(_state["port"]) else _state["port"]
    finally:
        _one_at_a_time.release()
    raise StudioError("browser", "Lost Clip Radar's TikTok window. Try again.")


PAGE_SCRIPT = "Page script failed"        # cdp.Page.eval's error when a script on the page throws
# the step names a failure carries (the UI says them: postStatus.ts TT_STEP), from the step lines shown
STEP_OF = {"Opening TikTok Studio": "browser", "Putting the video in": "file box", "Typing the caption": "caption box",
           "Uploading the video to TikTok": "upload", "Pressing Post": "post"}
STEP_WORDS = {"browser": "opening it", "file box": "putting the video in", "caption box": "typing the caption",
              "upload": "TikTok was taking the video in", "post": "pressing Post"}


def _upload_once(path: str, caption: str, press_post: bool, url: str, step: Callable[[str], None],
                 check: Callable[[], None], at: Optional[dict] = None) -> dict:
    """at: filled in as it goes; at["pressed"] once Post has been pressed (upload() never starts again after that)."""
    at = at if at is not None else {}
    step("Opening TikTok Studio")
    port = open_browser(url)
    page = _tab(port, url)
    try:
        _clear_dialog(page)
        page.call("Page.enable")
        # a fresh upload page every time: after an upload, the window is still on the page but with that
        # clip in its editor (no upload box), so the next clip would wait for one that never comes. A clip left
        # in the editor makes the page ask "Leave site?" (4.36: answered, see _navigate). A page that's still empty
        # is used as it is (4.40): a window that just opened loaded it twice
        if not _fresh_page(page, url, check):
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
        if caption and not _caption_ok(page, _caption_words(caption)):
            _type_caption(page, caption)            # the page changed the caption while the video went up
        if not press_post:
            page.call("Page.bringToFront")
            return {"posted": False, "pressed": False}

        step("Pressing Post")
        at["pressed"] = True                    # set before the click: a click that went through can't be undone
        page.eval(f"({SELECTORS['post']}).click(), true")
        # TikTok can ask to confirm (for example "Post now" after its content check): pressed as soon as it
        # shows, rather than after a fixed pause
        got = _first(page, {"posted": SELECTORS["posted"], "confirm": POST_NOW}, 10, check)
        if got == "confirm":
            page.eval(f"({POST_NOW}).click(), true")
        posted = got == "posted" or _wait(page, SELECTORS["posted"], 90, check, every=0.5)
        page.call("Page.bringToFront")
        return {"posted": posted, "pressed": True}
    except (StudioError, cdp.CDPError) as e:
        # a step the page didn't have: what the page looked like, for the error log (upload() records it)
        if isinstance(e, StudioError) or str(e).startswith(PAGE_SCRIPT):
            at["fingerprint"] = fingerprint(page, "tiktok", private=(caption, path))
        raise
    finally:
        page.close()


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
    want = _caption_words(text)
    for _ in range(2):
        ok = page.eval(f"(() => {{ const e = {box}; if (!e) return false; e.focus(); const r = document.createRange(); "
                       "r.selectNodeContents(e); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return true })()",
                       timeout=10)
        if not ok:
            return False
        page.call("Input.insertText", text=text)
        time.sleep(0.3)
        if _caption_words(page.eval(f"(({box}) || {{}}).innerText || ''", timeout=10) or "") == want:
            return True
    return False


def upload_youtube(path: str, title: str, description: str, url: str, step: Callable[[str], None],
                   check: Callable[[], None] = lambda: None) -> dict:
    """Drive YouTube Studio's upload dialog in Clip Radar's own browser window: the video put in, the title and
    description typed. It stops there. Audience, visibility and Publish are choices only you can make, so they
    are never pressed. Returns {"ready": True, "title": typed?, "description": typed?}; raises StudioError."""
    if not os.path.isfile(path):
        e = StudioError("file", "The exported file isn't there any more. Export the video again first.")
        _record_failure("youtube", e, None)
        raise e
    title = youtube_title(title)
    if not _one_at_a_time.acquire(blocking=False):
        step("Waiting for the upload before it to finish")
        while not _one_at_a_time.acquire(timeout=1.0):
            check()
    page = None
    try:
        step("Opening YouTube Studio")
        port = open_browser(url)
        page, fresh = _yt_tab(port, url)
        _clear_dialog(page)
        page.call("Page.enable")
        if not fresh:
            _navigate(page, url, check)             # a new upload dialog, not the last video's
        page.call("Page.bringToFront")
        first = _first(page, {"file": YT["file"], "login": YT["login"]}, 45, check)
        if first == "login":
            step("Sign in to YouTube in the window that opened. Clip Radar carries on when you're in.")
            if not _wait(page, YT["file"], LOGIN_WAIT, check, every=1.0):
                raise StudioError("sign-in", "YouTube Studio didn't open after signing in. Sign in in Clip Radar's "
                                             "browser window, then try again.")
        elif first != "file":
            raise StudioError("file box", "YouTube Studio's upload box didn't appear. If YouTube is asking something in "
                                          "the window (to create a channel, for example), answer it and try again.")
        step("Putting the video in")
        page.call("DOM.getDocument", depth=0)          # the DOM agent needs a document before files can be set
        node = page.call("Runtime.evaluate", expression=YT["file"])["result"]
        if node.get("subtype") == "null" or "objectId" not in node:
            raise StudioError("file box", "Couldn't find YouTube Studio's upload box. YouTube may have changed the page.")
        page.call("DOM.setFileInputFiles", files=[os.path.abspath(path)], objectId=node["objectId"])
        if not _wait(page, YT["title"], 120, check):
            raise StudioError("details", "The video went in, but YouTube's title box didn't appear. Finish it in the "
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
    except StudioError as e:
        _record_failure("youtube", e, fingerprint(page, "youtube", private=(title, description, path)) if page else None)
        raise
    except (cdp.CDPError, OSError) as e:
        if str(e).startswith(PAGE_SCRIPT):
            err = StudioError("details", "YouTube Studio's page didn't respond as expected. YouTube may have changed "
                                         "the page: finish it in the YouTube window.")
            _record_failure("youtube", err, fingerprint(page, "youtube", private=(title, description, path)) if page else None)
            raise err from None
        err = StudioError("browser", f"Lost Clip Radar's browser window ({e}). Try again.")
        _record_failure("youtube", err, None)
        raise err from None
    finally:
        if page is not None:
            page.close()
        _one_at_a_time.release()


# ---------------------------------------------------------------- what the page looked like (when a step fails)
#
# A failed upload is recorded in errors.jsonl with a fingerprint of the page: which of the selectors above it has
# (and whether each is visible and can be pressed), TikTok's own data-e2e marks, the words on its buttons, its
# path, language and window size. Enough to see what changed on TikTok's (or YouTube's) side without asking what
# the person saw. Nothing personal: the caption, the title, the file's name and the account are never in it.
# Button words that look like a @handle, #hashtag, number or e-mail are blanked, and so is every word that's in
# the caption or title box, the file box, anything marking the account (its name, avatar, profile), or the
# caption, title and file name Clip Radar put in. Taking it never raises and takes at most 2 s.

FINGERPRINT_WAIT = 2.0
FP_MARKS = 200                # data-e2e marks kept
FP_BUTTONS = 40               # buttons kept, each cut to 30 characters
# elements whose words are the account's (its name, avatar, profile link), on TikTok and YouTube
_ACCOUNT = ("[data-e2e*=user i],[data-e2e*=nick i],[data-e2e*=profile i],[data-e2e*=account i],[data-e2e*=avatar i],"
            "[data-e2e*=author i],[class*=avatar i],[class*=nickname i],[class*=username i],[class*=user-name i],"
            "[class*=account i],[class*=profile i],[aria-label*=account i],[aria-label*=profile i],[aria-label*=channel i],"
            "[id*=account i],[id*=channel-name i],[id*=avatar i],ytcp-entity-name,ytd-channel-name")
_FP_JS = r"""(() => {
  const blank = t => String(t || '').replace(/\s+/g, ' ').trim()
    .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '…')          // e-mail
    .replace(/[@#][^\s@#]+/g, '…')                         // @handle, #hashtag
    .replace(/\d[\d.,:]*/g, '…');                          // numbers
  const words = t => String(t || '').toLowerCase().split(/[\s,.;:!?"'()\[\]{}<>\/\\|_-]+/).filter(w => w.length >= 3);
  const own = new Set();
  const keep = t => words(t).forEach(w => own.add(w.replace(/^[#@]+/, '')));
  document.querySelectorAll('[contenteditable], textarea, input:not([type=file]):not([type=hidden])')
    .forEach(e => keep(e.innerText || e.value));
  document.querySelectorAll('input[type=file]').forEach(e => [...(e.files || [])].forEach(f => keep(f.name)));
  document.querySelectorAll(%ACCOUNT%).forEach(e => {
    keep(e.innerText); keep(e.getAttribute('aria-label')); keep(e.getAttribute('title')); keep(e.getAttribute('alt'));
    e.querySelectorAll('img').forEach(i => keep(i.alt));
  });
  const scrub = t => blank(t).replace(/[^\s,.;:!?"'()\[\]{}<>\/\\|_-]+/g, w => own.has(w.toLowerCase().replace(/^[#@]+/, '')) ? '…' : w);
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const out = {
    path: location.pathname.split('/').map(p => /^@|^(?=.*\d).{12,}$|^UC[\w-]{20,}$/.test(p) ? '…' : p).join('/'),
    lang: document.documentElement.lang || '', window: [innerWidth, innerHeight], selectors: {}, marks: [], buttons: [],
  };
  const SEL = {%SELECTORS%};
  for (const k of Object.keys(SEL)) {
    try {
      const v = SEL[k]();
      out.selectors[k] = v instanceof Element ? { match: true, visible: shown(v),
        enabled: !v.disabled && v.getAttribute('aria-disabled') !== 'true' } : { match: !!v };
    } catch (e) { out.selectors[k] = { match: false, error: true }; }
  }
  try {
    out.marks = [...new Set([...document.querySelectorAll('[data-e2e]')].map(e => scrub(e.getAttribute('data-e2e')).slice(0, 60)))]
      .sort().slice(0, %MARKS%);
  } catch (e) { out.marks_error = true; }
  try {
    out.buttons = [...document.querySelectorAll('button, [role=button]')].filter(b => !b.closest('[contenteditable]'))
      .slice(0, %BUTTONS%).map(b => scrub(b.innerText || b.getAttribute('aria-label')).slice(0, 30));
  } catch (e) { out.buttons_error = true; }
  return out;
})()"""


def _fingerprint_js(selectors: dict) -> str:
    sel = ", ".join(f"{json.dumps(k)}: () => ({v})" for k, v in selectors.items())
    return (_FP_JS.replace("%SELECTORS%", sel).replace("%ACCOUNT%", json.dumps(_ACCOUNT))
            .replace("%MARKS%", str(FP_MARKS)).replace("%BUTTONS%", str(FP_BUTTONS)))


def _site_selectors(site: str) -> dict:
    if site == "youtube":
        return dict(YT)
    return dict(SELECTORS, shown_caption=SHOWN_CAPTION, post_now=POST_NOW)


_WORD = re.compile(r"[^\s,.;:!?\"'()\[\]{}<>/\\|_-]+")


def _scrub(value, own: set):
    """Every word Clip Radar put in (the caption, the title, the file's name) blanked, wherever it is."""
    if isinstance(value, str):
        return _WORD.sub(lambda m: "…" if m.group(0).lower().lstrip("#@") in own else m.group(0), value)
    if isinstance(value, list):
        return [_scrub(v, own) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, own) for k, v in value.items()}
    return value


def fingerprint(page, site: str, private=()) -> Optional[dict]:
    """What the page looks like, without anything personal (see above); None if it couldn't be taken. private:
    the caption, title, description and file path that went in, blanked here too. Never raises."""
    try:
        fp = page.eval(_fingerprint_js(_site_selectors(site)), timeout=FINGERPRINT_WAIT)
        if not isinstance(fp, dict):
            return None
        own = set()
        for text in private:
            text = str(text or "")
            if os.sep in text or "/" in text:
                text = os.path.splitext(os.path.basename(text))[0]
            own.update(w.lower().lstrip("#@") for w in _WORD.findall(text) if len(w.lstrip("#@")) >= 3)
        fp = _scrub(fp, own)
        fp["site"] = site
        try:
            from version import VERSION
            fp["version"] = VERSION
        except Exception:
            pass
        return fp
    except Exception:
        return None


def _record_failure(site: str, err: StudioError, fp: Optional[dict]) -> None:
    """A failed upload in errors.jsonl, with the page's fingerprint when there was a page."""
    try:
        import errorlog
        errorlog.record("upload", str(err), step=err.step, site=site, fingerprint=fp)
    except Exception:
        pass


# ---------------------------------------------------------------- a check of the page that uploads nothing
#
# check_page() opens the upload page in Clip Radar's own browser window, waits for it, takes the fingerprint above
# and says, selector by selector, whether the page still has what an upload looks for (scripts/studio_check.py prints
# it). Nothing is typed and nothing is pressed. With a file (TikTok only: YouTube makes a draft on your channel as
# soon as a file goes in), the video is also put in to see the caption box and Post show up; then the upload page is
# opened again (answering "Leave site?"), so the video is left behind, never posted or saved as a draft.
#
# EXPECT: what an empty upload page shows ("page"), and once a video is in ("file"). True: must be there; False: must
# not be. A selector left out isn't checked (TikTok's "posted" page, its error text, YouTube's title box before a file).
EXPECT = {
    "tiktok": {"page": {"file": True, "login": False, "upload_error": False, "posted": False},
               "file": {"caption": True, "shown_caption": True, "post": True, "login": False, "upload_error": False}},
    "youtube": {"page": {"file": True, "login": False}},
}


def check_fingerprint(fp: Optional[dict], site: str, stage: str = "page") -> list:
    """[(selector, ok, what the page had)] for a fingerprint, against EXPECT[site][stage]."""
    sel = (fp or {}).get("selectors", {})
    out = []
    for name, want in EXPECT[site][stage].items():
        got = sel.get(name)
        if got is None:
            out.append((name, False, "not in the fingerprint"))
            continue
        ok = bool(got.get("match")) == want and not got.get("error")
        said = "error" if got.get("error") else ("found" if got.get("match") else "not found")
        if got.get("match") and "visible" in got:
            said += (", visible" if got["visible"] else ", hidden") + ("" if got.get("enabled", True) else ", off")
        out.append((name, ok, said))
    return out


def check_page(site: str, url: str, file: str = "", wait: float = 60, step: Callable[[str], None] = lambda s: None,
               check: Callable[[], None] = lambda: None) -> dict:
    """Open site's upload page (url) in Clip Radar's browser window and check it, uploading nothing. Returns
    {"site", "url", "signed_in", "page": fingerprint, "results": [(selector, ok, said)], "file": fingerprint or None,
    "file_results": [...], "ok": bool}. Raises StudioError if the window can't be opened or the page doesn't load."""
    if site not in EXPECT:
        raise ValueError(f"site is one of {', '.join(EXPECT)}")
    if file and site != "tiktok":
        # YouTube makes a draft video on your channel as soon as a file goes in: never for a check
        raise ValueError("A test video is only put in on TikTok's page.")
    if file and not os.path.isfile(file):
        raise StudioError("file", f"{file} isn't there.")
    sels = YT if site == "youtube" else SELECTORS
    if not _one_at_a_time.acquire(blocking=False):
        step("Waiting for the upload before it to finish")
        while not _one_at_a_time.acquire(timeout=1.0):
            check()
    page = None
    try:
        step(f"Opening the {site} upload page")
        port = open_browser(url)
        page = _yt_tab(port, url)[0] if site == "youtube" else _tab(port, url)
        _clear_dialog(page)
        page.call("Page.enable")
        _navigate(page, url, check)
        page.call("Page.bringToFront")
        first = _first(page, {"file": sels["file"], "login": sels["login"]}, wait, check)
        out = {"site": site, "url": re.sub(r"[?#].*$", "", url), "signed_in": first != "login", "file": None,
               "file_results": []}
        out["page"] = fingerprint(page, site)
        out["results"] = check_fingerprint(out["page"], site, "page")
        if file and first == "file":
            step("Putting the test video in")
            try:
                _set_file(page, file, check)
            except StudioError:
                pass                                # the fingerprint says what's missing
            out["file"] = fingerprint(page, site, private=(file,))
            out["file_results"] = check_fingerprint(out["file"], site, "file")
            step("Leaving the video behind (the upload page again)")
            _navigate(page, url, check)
        out["ok"] = out["signed_in"] and all(ok for _, ok, _ in out["results"] + out["file_results"])
        return out
    except (cdp.CDPError, OSError) as e:
        raise StudioError("browser", f"Couldn't check the page: {e}") from None
    finally:
        if page is not None:
            page.close()
        _one_at_a_time.release()


def describe() -> dict:
    """For Settings and the Exports panel: whether a browser is there, and the window's state."""
    return {"available": available(), "open": bool(_state["port"] and cdp.alive(_state["port"]))}


def settings_path() -> str:
    import paths
    return os.path.join(str(paths.DATA_DIR), "studio.json")


_settings_lock = threading.Lock()


def load_settings() -> dict:
    """Settings › TikTok. A read that meets a save replacing the file (Windows: PermissionError) tries again
    instead of falling back to the defaults, which would read Press Post for me as off."""
    d = {}
    for i in range(5):
        try:
            with open(settings_path(), encoding="utf-8") as f:
                d = json.load(f)
            break
        except PermissionError:
            if i == 4:
                break
            time.sleep(0.05 * (i + 1))
        except (OSError, ValueError):
            break
    return {"press_post": bool(d.get("press_post", False))}


def save_settings(press_post: Optional[bool] = None) -> dict:
    """Saved the way the app's other files are (persist.write_json): atomically, and retried when Windows
    has the file open for a moment. Before, one os.replace that met a read failed the save (a 500 from
    /api/studio)."""
    import persist
    with _settings_lock:
        d = load_settings()
        if press_post is not None:
            d["press_post"] = bool(press_post)
        persist.write_json(settings_path(), d)
    return d
