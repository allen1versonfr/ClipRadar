"""
Where a video comes from (3.10): Twitch, YouTube, X or a file on this PC.

  classify(text)      what a link or path is (platform, kind, id, start time) without going online
  describe(info)      what yt-dlp's metadata says it is: an ordinary video, a finished livestream (VOD),
                      a livestream happening now, or one that hasn't started
  explain(error)      yt-dlp's error, in plain words, with what to do about it
  link_at(meta, t)    a link back to the source at a time (attribution and timestamps)

Downloads go through yt-dlp, the same supported route VodClipper uses for Twitch.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

PLATFORMS = {"twitch": "Twitch", "youtube": "YouTube", "x": "X", "other": "Web", "local": "File"}
KINDS = {"vod": "past broadcast", "video": "video", "live": "livestream", "upcoming": "livestream that hasn't started",
         "clip": "clip", "channel": "channel", "short": "Short", "post": "post", "file": "video file"}

_YT_ID = r"([A-Za-z0-9_-]{11})"


def _yt_time(v: str) -> float:
    """YouTube/Twitch start times: 90, 90s, 1m30s, 1h2m3s."""
    if not v:
        return 0.0
    v = v.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?s?", v):
        return float(v.rstrip("s"))
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", v)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        return float(h * 3600 + mi * 60 + s)
    return 0.0


def classify(text: str) -> dict:
    """{"platform", "kind", "id", "url", "start", "label"} for a link, a Twitch name or a file path."""
    t = (text or "").strip().strip('"').strip("'")
    out = {"platform": "other", "kind": "video", "id": "", "url": t, "start": 0.0}
    if not re.match(r"^https?://", t, re.I):
        if re.match(r"^(www\.|m\.)?(twitch\.tv|youtube\.com|youtu\.be|x\.com|twitter\.com)/", t, re.I):
            t = "https://" + t
            out["url"] = t
        elif re.fullmatch(r"\d{6,12}", t):
            return _label(dict(out, platform="twitch", kind="vod", id=t, url=f"https://www.twitch.tv/videos/{t}"))
        elif re.fullmatch(r"@?[A-Za-z0-9_]{3,25}", t):
            name = t.lstrip("@").lower()
            return _label(dict(out, platform="twitch", kind="channel", id=name, url=f"https://www.twitch.tv/{name}"))
        else:
            return _label(dict(out, platform="local", kind="file", url=t))
    u = urllib.parse.urlparse(t)
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    q = urllib.parse.parse_qs(u.query)
    path = u.path.rstrip("/")
    start = _yt_time((q.get("t") or q.get("start") or [""])[0])
    if host in ("twitch.tv", "clips.twitch.tv"):
        m = re.match(r"^/videos/(\d+)", path)
        if m:
            return _label(dict(out, platform="twitch", kind="vod", id=m.group(1), start=start,
                               url=f"https://www.twitch.tv/videos/{m.group(1)}"))
        if host == "clips.twitch.tv" or "/clip/" in path:
            return _label(dict(out, platform="twitch", kind="clip", id=path.rsplit("/", 1)[-1]))
        m = re.match(r"^/([A-Za-z0-9_]{2,25})$", path)
        if m and m.group(1).lower() not in ("videos", "directory", "settings", "downloads", "p", "search"):
            return _label(dict(out, platform="twitch", kind="channel", id=m.group(1).lower()))
        return _label(dict(out, platform="twitch"))
    if host in ("youtube.com", "youtu.be", "music.youtube.com", "youtube-nocookie.com"):
        vid, kind = "", "video"
        if host == "youtu.be":
            m = re.match(r"^/" + _YT_ID, path)
            vid = m.group(1) if m else ""
        elif (q.get("v") or [""])[0]:
            vid = q["v"][0][:11]
        else:
            m = re.match(r"^/(shorts|live|embed|v)/" + _YT_ID, path)
            if m:
                vid = m.group(2)
                kind = {"shorts": "short", "live": "live"}.get(m.group(1), "video")
            elif re.match(r"^/(@[\w.-]+|c/[\w.-]+|channel/[\w-]+|user/[\w.-]+)/live$", path):
                kind = "live"                    # a channel's live page: whatever it's streaming now
                vid = path.split("/")[1]
            elif re.match(r"^/(@[\w.-]+|c/[\w.-]+|channel/[\w-]+|user/[\w.-]+)", path):
                return _label(dict(out, platform="youtube", kind="channel", id=path.split("/")[1]))
        url = f"https://www.youtube.com/watch?v={vid}" if re.fullmatch(_YT_ID, vid or "") else t
        return _label(dict(out, platform="youtube", kind=kind, id=vid, url=url, start=start))
    if host in ("x.com", "twitter.com", "mobile.twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"):
        m = re.match(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)", path)
        if m:
            return _label(dict(out, platform="x", kind="post", id=m.group(2), user=m.group(1),
                               url=f"https://x.com/{m.group(1)}/status/{m.group(2)}"))
        m = re.match(r"^/i/broadcasts/(\w+)", path)
        if m:
            return _label(dict(out, platform="x", kind="live", id=m.group(1)))
        return _label(dict(out, platform="x", kind="post"))
    return _label(dict(out, start=start))


def _label(d: dict) -> dict:
    d["label"] = f"{PLATFORMS.get(d['platform'], 'Web')} {KINDS.get(d['kind'], d['kind'])}" if d["platform"] != "local" else "Video file"
    return d


def workdir_name(c: dict, fallback: str) -> str:
    """A stable folder per source, so opening the same video again reuses the download."""
    if c["platform"] == "twitch" and c["kind"] == "vod" and c["id"]:
        return f"vod_{c['id']}"
    pre = {"youtube": "yt", "x": "x", "twitch": "tw"}.get(c["platform"])
    if pre and c.get("id") and c["kind"] not in ("channel",):
        return f"{pre}_{re.sub(r'[^A-Za-z0-9_-]', '', c['id'])[:40]}"
    return fallback


def describe(info: dict, c: Optional[dict] = None) -> dict:
    """Turn yt-dlp metadata into what this is: {"kind", "label", "live", "title", "uploader", "uploaded",
    "duration", "problem"}. problem is set (with what to do) when it can't be used."""
    c = c or {}
    plat = c.get("platform") or _platform_of(info)
    ls = info.get("live_status") or ("is_live" if info.get("is_live") else "was_live" if info.get("was_live") else "")
    kind = {"is_live": "live", "is_upcoming": "upcoming", "was_live": "vod", "post_live": "vod"}.get(ls)
    if not kind:
        kind = "short" if c.get("kind") == "short" else ("vod" if plat == "twitch" else "post" if plat == "x" else "video")
    up = str(info.get("upload_date") or info.get("release_date") or "")
    out = {"platform": plat, "kind": kind, "live": kind == "live",
           "label": f"{PLATFORMS.get(plat, 'Web')} {KINDS.get(kind, kind)}",
           "title": info.get("title") or info.get("fulltitle") or "", "duration": float(info.get("duration") or 0),
           "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
           "uploader_id": str(info.get("uploader_id") or info.get("channel_id") or ""),
           "uploaded": f"{up[:4]}-{up[4:6]}-{up[6:8]}" if re.fullmatch(r"\d{8}", up) else "",
           "problem": ""}
    if kind == "upcoming":
        out["problem"] = "That livestream hasn't started yet. Use Live clipping with 'Wait for the stream to start', or come back after it ends."
    elif ls == "post_live":
        out["problem"] = "That livestream just ended and YouTube is still processing it. Try again in a little while."
    elif info.get("availability") in ("private",):
        out["problem"] = "That video is private."
    elif info.get("availability") in ("premium_only", "subscriber_only"):
        out["problem"] = "That video is for members or subscribers only. Sign in under Home › Advanced › Sign in with, using the browser where you're a member."
    if plat == "x" and not info.get("formats") and not info.get("url") and not out["problem"] and info:
        out["problem"] = "That post doesn't have a video VodClipper can download."
    return out


def _platform_of(info: dict) -> str:
    ex = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    return "youtube" if "youtube" in ex else "twitch" if "twitch" in ex else "x" if "twitter" in ex else "other"


_ERRORS = [
    (r"private video|this video is private", "That video is private."),
    (r"sign in to confirm your age|age[- ]restricted|inappropriate for some users",
     "That video is age-restricted. Sign in under Home › Advanced › Sign in with, using a browser where you're signed in to {p}."),
    (r"members[- ]only|join this channel|subscriber[- ]only|only available to (channel )?members",
     "That video is for members or subscribers only. Sign in under Home › Advanced › Sign in with, using the browser where you're a member."),
    (r"video (is )?unavailable|has been removed|no longer available|account .* terminated|does not exist|404",
     "That video isn't available any more (removed, or the link is wrong)."),
    (r"not made this video available in your country|geo.?restrict|blocked it in your country",
     "That video isn't available in your country."),
    (r"copyright", "That video was taken down for copyright."),
    (r"no video could be found|no video formats found|does not contain (any )?video|unable to extract video|no media found",
     "That post doesn't have a video VodClipper can download."),
    (r"nsfw|sensitive content|log ?in|login required|authentication|cookies",
     "{p} needs you to be signed in to show this. Choose your browser under Home › Advanced › Sign in with."),
    (r"premieres? in|live event will begin|is_upcoming|scheduled",
     "That livestream hasn't started yet. Use Live clipping with 'Wait for the stream to start'."),
    (r"429|too many requests|rate.?limit", "{p} is limiting downloads right now. Wait a few minutes and try again."),
    (r"unsupported url", "VodClipper can't open that kind of link. Paste a Twitch, YouTube or X video link, or pick a file."),
    (r"is_live|is live|live stream|currently live",
     "That's a livestream happening now. Use Live clipping for it, or wait until it ends and use the video link."),
    (r"timed out|connection|network|getaddrinfo|ssl", "Couldn't reach {p}. Check your internet connection and try again."),
]


def explain(error: str, platform: str = "") -> str:
    """yt-dlp's error message in plain words."""
    p = PLATFORMS.get(platform, "the site") if platform not in ("other", "local", "") else "the site"
    low = (error or "").lower()
    for pat, msg in _ERRORS:
        if re.search(pat, low):
            return msg.format(p=p)
    first = re.sub(r"\x1b\[[0-9;]*m", "", error or "").replace("ERROR: ", "").strip().splitlines()
    return (first[0][:240] if first else "Something went wrong downloading it.")


def link_at(meta: dict, t: float) -> str:
    """A link to the source at time t (seconds into the video), for attribution."""
    url = meta.get("source") or ""
    c = classify(url) if url else {"platform": "local"}
    t = max(0, int(t))
    if c["platform"] == "youtube" and re.fullmatch(_YT_ID, c.get("id") or ""):
        return f"https://youtu.be/{c['id']}?t={t}"
    if c["platform"] == "twitch" and c["kind"] == "vod":
        h, m, s = t // 3600, t % 3600 // 60, t % 60
        return f"https://www.twitch.tv/videos/{c['id']}?t={h}h{m}m{s}s"
    if c["platform"] == "x":
        return c["url"]
    return ""


def link_template(meta: dict) -> str:
    """link_at as a template for the app: {s} is seconds, {hms} is 1h2m3s."""
    url = meta.get("source") or ""
    c = classify(url) if url else {"platform": "local"}
    if c["platform"] == "youtube" and re.fullmatch(_YT_ID, c.get("id") or ""):
        return f"https://youtu.be/{c['id']}?t={{s}}"
    if c["platform"] == "twitch" and c["kind"] == "vod":
        return f"https://www.twitch.tv/videos/{c['id']}?t={{hms}}"
    if c["platform"] == "x":
        return c["url"]
    return ""
