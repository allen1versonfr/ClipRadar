"""
Check TikTok Studio's (or YouTube Studio's) upload page without uploading anything (docs/plans/hardening.md, phase 2).
A developer tool, not part of the app.

It opens the upload page in Clip Radar's own browser window (the one uploads use, signed in to your account), waits
for it, takes the same fingerprint a failed upload records (studio_auto.fingerprint) and says, for each part an upload
looks for, PASS or FAIL. Nothing is typed and nothing is pressed.

Run it on the PC where Clip Radar uploads, before each release that touches uploads and whenever TikTok seems to have
changed its page:

  python scripts/studio_check.py                     TikTok, saved to tests/assets/studio_fingerprints/<date>.json
  python scripts/studio_check.py --site youtube      YouTube Studio's upload page
  python scripts/studio_check.py --file test.mp4     TikTok only: also put a short video in to see the caption box and
                                                     Post show up, then open the upload page again (the video is left
                                                     behind, never posted or saved as a draft)
  python scripts/studio_check.py --no-save           just print

Commit the saved file: tests/test_studio_fingerprints.py checks the newest one has every part an upload relies on, and
the difference between two of them shows what TikTok changed. Nothing personal is in it (see studio_auto.fingerprint).
Exit code 0 when everything passed, 1 when something didn't, 2 when the page couldn't be checked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
SAVE_DIR = os.path.join(ROOT, "tests", "assets", "studio_fingerprints")

URLS = {"tiktok": os.environ.get("CLIPRADAR_STUDIO_URL", "https://www.tiktok.com/tiktokstudio/upload"),
        "youtube": os.environ.get("CLIPRADAR_YT_URL", "https://www.youtube.com/upload")}


def saved_name(site: str, day: str, folder: str) -> str:
    """<date>.json for TikTok, <date>-youtube.json for YouTube; -2, -3 … when one is already there that day."""
    base = day + ("" if site == "tiktok" else f"-{site}")
    name, n = base + ".json", 2
    while os.path.exists(os.path.join(folder, name)):
        name, n = f"{base}-{n}.json", n + 1
    return name


def report(r: dict) -> str:
    lines = [f"{r['site']}: {r['url']}", "signed in" if r["signed_in"] else "NOT signed in: sign in in the window, then run it again"]
    for title, rows in (("upload page", r["results"]), ("with a video in", r["file_results"])):
        if rows:
            lines.append(f"  {title}:")
            lines += [f"    {'PASS' if ok else 'FAIL'}  {name:<14} {said}" for name, ok, said in rows]
    lines.append("PASS" if r["ok"] else "FAIL")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", choices=("tiktok", "youtube"), default="tiktok")
    ap.add_argument("--url", help="the upload page (default: the one the app uses)")
    ap.add_argument("--file", default="", help="TikTok only: a short test video to put in (off by default)")
    ap.add_argument("--wait", type=float, default=60, help="seconds to wait for the page (default 60)")
    ap.add_argument("--save-dir", default=SAVE_DIR)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--source", default="", help=argparse.SUPPRESS)      # tests: where the fingerprint came from
    a = ap.parse_args(argv)
    if a.file and a.site != "tiktok":
        ap.error("--file is for TikTok only: YouTube makes a draft video as soon as a file goes in")

    import studio_auto
    if not studio_auto.available():
        print("Clip Radar's browser window needs Microsoft Edge or Google Chrome.")
        return 2
    url = a.url or URLS[a.site]
    try:
        r = studio_auto.check_page(a.site, url, file=a.file, wait=a.wait, step=lambda s: print(f"  {s}", flush=True))
    except studio_auto.StudioError as e:
        print(f"Couldn't check the page ({e.step}): {e}")
        return 2
    print(report(r))
    if not a.no_save:
        os.makedirs(a.save_dir, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        out = {"taken": time.strftime("%Y-%m-%d %H:%M"), "site": a.site, "url": r["url"], "source": a.source or "studio_check",
               "signed_in": r["signed_in"], "ok": r["ok"], "page": r["page"], "file": r["file"]}
        p = os.path.join(a.save_dir, saved_name(a.site, day, a.save_dir))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"saved {p}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
