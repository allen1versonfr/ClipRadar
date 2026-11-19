"""
The saved fingerprints of TikTok Studio's upload page (tests/assets/studio_fingerprints/, written by
scripts/studio_check.py on the owner's PC) still have every part an upload relies on. No browser needed.

The newest TikTok one is checked against studio_auto.EXPECT: the empty upload page, and the page with a video in when
the check put one in (--file). The newest YouTube one (<date>-youtube.json), if there is one, likewise. When TikTok
changes a part an upload looks for, the next saved fingerprint fails here, before a real upload does.

0000-fake_studio.json was taken from the stand-in page (tests/fake_studio.py), not TikTok: it's only used until a real
one is saved, and a note says so. The first real fingerprint has to come from the owner's PC, signed in to TikTok.

  python tests/test_studio_fingerprints.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import studio_auto  # noqa: E402

FOLDER = os.path.join(HERE, "assets", "studio_fingerprints")
failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def newest(site: str) -> str:
    """The newest saved fingerprint for a site: real ones (named by date) sort after the stand-in's (0000-…)."""
    names = sorted((n for n in os.listdir(FOLDER) if n.endswith(".json") and (("-youtube" in n) == (site == "youtube"))),
                   key=lambda n: n[:-len(".json")])          # 2026-11-17 before 2026-11-17-2 (a second check that day)
    return os.path.join(FOLDER, names[-1]) if names else ""


for site in ("tiktok", "youtube"):
    path = newest(site)
    if not path:
        print(f"  no saved {site} fingerprint yet (scripts/studio_check.py --site {site})")
        continue
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    name = os.path.basename(path)
    if "fake_studio" in name:
        print(f"  {name} is from the stand-in page, not {site}: save a real one with scripts/studio_check.py")
    check(f"{name}: the page was signed in when it was taken", saved.get("signed_in") is True, saved.get("signed_in"))
    sel = (saved.get("page") or {}).get("selectors", {})
    wanted = studio_auto.YT if site == "youtube" else studio_auto.SELECTORS
    check(f"{name}: every selector the upload has was checked", set(wanted) <= set(sel), sorted(set(wanted) - set(sel)))
    if site == "youtube":
        later = sorted(set(studio_auto.YT_DIALOG) - set(sel))
        if later:       # 5.1's clip upload looks for more of the dialog; a fingerprint taken before can't have them
            print(f"  {name} was taken before Upload to YouTube for clips (5.1): {', '.join(later)} not checked against YouTube "
                  "yet. Save a new one with scripts/studio_check.py --site youtube")
    for part, ok, said in studio_auto.check_fingerprint(saved.get("page"), site, "page"):
        check(f"{name}: upload page: {part}", ok, said)
    if saved.get("file"):
        for part, ok, said in studio_auto.check_fingerprint(saved["file"], site, "file"):
            check(f"{name}: with a video in: {part}", ok, said)
    elif site == "tiktok":
        print(f"  {name} was taken without a video (--file): the caption box and Post weren't checked")

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
