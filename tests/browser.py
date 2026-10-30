"""The browser the Playwright tests run in: CLIPRADAR_BROWSER, Playwright's own Chromium (also the
pre-installed copy in /opt/pw-browsers), then Edge or Chrome (Windows CI has Edge, not Playwright's)."""

from __future__ import annotations

import os


def launch(p):
    exe = os.environ.get("CLIPRADAR_BROWSER")
    tries = [dict(executable_path=exe)] if exe else []
    if os.path.isdir("/opt/pw-browsers"):
        for d in sorted(os.listdir("/opt/pw-browsers")):
            for sub in ("chrome-linux", "chrome-linux64"):        # newer Playwright builds: chrome-linux64
                cand = os.path.join("/opt/pw-browsers", d, sub, "chrome")
                if d.startswith("chromium-") and os.path.isfile(cand):
                    tries.append(dict(executable_path=cand))
    tries += [{}, dict(channel="msedge"), dict(channel="chrome")]
    for kw in tries:
        try:
            return p.chromium.launch(**kw)
        except Exception:
            continue
    return None
