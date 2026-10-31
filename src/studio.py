"""
TikTok Studio handoff (replaces posting through TikTok's developer API, removed in 4.4).

Clip Radar gets a finished clip ready and hands it over; you post it in TikTok Studio yourself:

  1. the caption (title or description, plus the post hashtags) is copied, ready to paste
  2. File Explorer opens with the video selected, so it can be dragged into the upload page
  3. TikTok Studio's upload page opens in your browser

Clip Radar can't see what happens in the browser, so it only ever records that it opened TikTok
Studio for a clip, never that the clip was uploaded or posted.

CLIPRADAR_NO_BROWSER=1 makes it record what it would open without opening anything (tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser

STUDIO_URL = os.environ.get("CLIPRADAR_STUDIO_URL", "https://www.tiktok.com/tiktokstudio/upload")
opened: list = []          # (what, target) when CLIPRADAR_NO_BROWSER is set


def _quiet() -> bool:
    return os.environ.get("CLIPRADAR_NO_BROWSER") == "1"


def reveal(path: str) -> bool:
    """Show the file selected in File Explorer (Windows), or open its folder elsewhere."""
    if not path or not os.path.exists(path):
        return False
    if _quiet():
        opened.append(("reveal", path))
        return True
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return True
    except OSError:
        return False


def open_studio(browser: str = "") -> bool:
    """TikTok Studio's upload page in your browser, or in the browser given (a path, with your usual profile)."""
    if _quiet():
        opened.append(("url", STUDIO_URL))
        return True
    if browser:
        try:
            subprocess.Popen([browser, STUDIO_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    threading.Thread(target=lambda: webbrowser.open(STUDIO_URL), daemon=True).start()
    return True


def caption(title: str, post: str) -> str:
    """What to paste into TikTok's caption box: the post text you have, or the title."""
    return (post or "").strip() or (title or "").strip()


def handoff(path: str, title: str, post: str, reveal_file: bool = True) -> dict:
    """Get one clip ready for TikTok Studio. Returns what was done; 'posted' is never claimed."""
    if not path or not os.path.isfile(path):
        raise RuntimeError("That clip's file isn't there any more. Render it again first.")
    shown = reveal(path) if reveal_file else False
    open_studio()
    return {"caption": caption(title, post), "url": STUDIO_URL, "file": path, "revealed": shown,
            "opened_at": time.time()}
