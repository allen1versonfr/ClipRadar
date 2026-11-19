"""
YouTube for clips (5.1): the YouTube version of a moment (16:9, made when Moments' "YouTube too" is on), sent to
YouTube the way TikTok clips go to TikTok.

  - Open YouTube Studio (the pass-through, like Open TikTok Studio): the title and description copied, the video
    selected in File Explorer and YouTube's upload page opened in your browser.
  - Upload to YouTube (like Upload to TikTok): Clip Radar's own browser window puts the video in, types the title and
    description, sets who it's for, the tags and the visibility, and presses Publish only when "Publish for me" is on.
  - Settings › YouTube: those choices, ready-made and changeable, kept in youtube.json in the data folder. The title and
    description are templates filled in for each clip ({title}, {caption}, {streamer}, {source}, {hashtags}), and can be
    changed for one clip in Exports before it goes.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

import paths
import persist

UPLOAD_URL = os.environ.get("CLIPRADAR_YT_CLIP_URL", "https://www.youtube.com/upload")
VISIBILITY = ("public", "unlisted", "private")
DEFAULTS = {
    "visibility": "public",          # who can see it
    "kids": False,                   # made for kids? (YouTube asks every time; almost never yes for stream clips)
    "title": "{title}",
    "description": "{caption}\n\nFrom {streamer}'s stream: {source}",
    "tags": "",                      # comma-separated
    "publish": False,                # press Publish for me (off: you check it in YouTube Studio and press it)
}
TOKENS = ("title", "caption", "streamer", "source", "hashtags")
_lock = threading.Lock()


def _file() -> str:
    return os.path.join(str(paths.DATA_DIR), "youtube.json")


def load() -> dict:
    d = persist.read_json(_file(), {}) or {}
    return clean(dict(DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS}))


def clean(d: dict) -> dict:
    return {
        "visibility": d.get("visibility") if d.get("visibility") in VISIBILITY else DEFAULTS["visibility"],
        "kids": d.get("kids") is True,
        "title": str(d.get("title") if d.get("title") is not None else DEFAULTS["title"])[:200],
        "description": str(d.get("description") if d.get("description") is not None else DEFAULTS["description"])[:5000],
        "tags": ", ".join(x.strip() for x in str(d.get("tags") or "").split(",") if x.strip())[:500],
        "publish": d.get("publish") is True,
    }


def save(patch: dict) -> dict:
    with _lock:
        cur = load()
        cur.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
        cur = clean(cur)
        os.makedirs(str(paths.DATA_DIR), exist_ok=True)
        persist.write_json(_file(), cur)
        return cur


def _fill(template: str, it: dict) -> str:
    nf = it.get("name_fields") or {}
    post = (it.get("post") or "").strip()
    tags = " ".join(w for w in post.split() if w.startswith("#"))
    values = {"title": (it.get("title") or "").strip(), "caption": post or (it.get("title") or "").strip(),
              "streamer": str(nf.get("streamer") or ""), "source": it.get("link") or "", "hashtags": tags}
    out = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    # a token with nothing in it leaves no "From 's stream: " behind
    out = re.sub(r"From 's stream: ?\n?", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def texts(it: dict, cfg: Optional[dict] = None) -> dict:
    """The title and description a clip goes to YouTube with: what you typed for it in Exports, else Settings ›
    YouTube's templates filled in."""
    cfg = cfg or load()
    import youtube_auto
    yt = it.get("yt_text") or {}
    title = yt.get("title") if yt.get("title") else _fill(cfg["title"], it)
    desc = yt.get("description") if yt.get("description") is not None else _fill(cfg["description"], it)
    return {"title": youtube_auto.youtube_title(title or it.get("title") or "Clip"), "description": desc[:5000]}


def pass_through(it: dict) -> dict:
    """Open YouTube Studio: the video shown in File Explorer and YouTube's upload page opened in your browser.
    The page copies the title and description to the clipboard. 'Posted' is never claimed."""
    import studio
    path = it.get("path", "")
    if not path or not os.path.isfile(path):
        raise RuntimeError("That clip's file isn't there any more. Render it again first.")
    shown = studio.reveal(path)
    studio.open_url(UPLOAD_URL)
    it["yt"] = {"at": time.time(), "url": UPLOAD_URL}
    return dict(texts(it), url=UPLOAD_URL, file=path, revealed=shown)


def start_upload(S, it: dict) -> dict:
    """Upload to YouTube: Clip Radar's own browser window does it, as Settings › YouTube says. A step that fails leaves
    the rest to you, with the file shown in File Explorer."""
    import engine
    import outcome
    import studio
    import studio_auto
    import youtube_auto
    if not studio_auto.available():
        raise RuntimeError("Clip Radar needs Microsoft Edge or Google Chrome to upload for you.")
    cfg = load()
    t = texts(it, cfg)
    job = S.new_job("youtube")
    it["yt"] = {"at": time.time(), "url": UPLOAD_URL, "auto": "working", "job": job["id"]}
    S.save_queue_soon()

    def go():
        try:
            r = youtube_auto.upload_youtube(it.get("path", ""), t["title"], t["description"], UPLOAD_URL, S.job_step(job),
                                            S.job_check(job), opts={k: cfg[k] for k in ("kids", "tags", "visibility", "publish")})
        except studio_auto.StudioError as e:
            outcome.log("upload", "failed", site="youtube", step=e.step)
            studio.reveal(it.get("path", ""))
            where = "the YouTube window"
            if e.step == "browser":
                studio.open_url(UPLOAD_URL)
                where = "YouTube Studio, opening in your browser now"
            it["yt"].update(auto="failed", step=e.step, error=str(e))
            S.save_queue_soon()
            raise RuntimeError(f"{e} The video is selected in File Explorer, so you can finish in {where}.") from None
        except engine.Cancelled:
            outcome.log("upload", "cancelled", site="youtube")
            it["yt"].update(auto="failed", step="stopped", error="The upload was stopped before it finished.")
            S.save_queue_soon()
            raise
        except Exception as e:
            outcome.log("upload", "failed", site="youtube", step="error")
            it["yt"].update(auto="failed", step="browser", error=str(e)[:300] or type(e).__name__)
            S.save_queue_soon()
            raise
        outcome.log("upload", "ok", site="youtube", posted=bool(r.get("published")))
        it["yt"].update(auto="published" if r.get("published") else "ready", left=r.get("left", ""))
        S.save_queue_soon()
        return r
    S.run_job(job, go)
    return {"job": job["id"], "publish": cfg["publish"]}
