"""
The render queue (moved from server.py, hardening phase 5a; no changes): clips waiting to render, the workers that
render them one or more at a time, hearing the next clips' captions while one encodes, the checks of a finished
file, and why a render failed (and whether trying again could help).

  queue_add(pid, clips, …)        clips kept in Review, queued
  queue_worker(idx)               a render worker (server.main starts MAX_WORKERS of them)
  _render_item(it)                one clip rendered and checked
  classify_render_error(e)        (kind, message, detail) of a failed render; RETRYABLE kinds are retried

The queue itself and everything shared (server.queue, queue_lock, queue_wake, projects, jobs, WORKROOT…) stays in
server.py and is read here as S.<name> at call time, so a test that swaps one on server reaches this code. A test
that swaps a name defined here swaps render_queue.<name>.
"""

from __future__ import annotations

import sys
if "server" not in sys.modules:
    # imported before server: server imports this module itself (at its end), so server is imported first
    # and this copy steps aside (the import then gives back the copy server loaded in full)
    del sys.modules[__name__]
    import server  # noqa: E402,F401
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from typing import Optional
import engine
import render
import whisper
import safety
import campaigns
import ytextra
import cleanup
import checkflow
import errorlog
import exports
import outcome
import sources
import hashtags
import learning
import persist
import timing
import locations

import autopilot  # noqa: E402
import server as S  # noqa: E402  shared state and helpers, read as S.<name> at call time
import previews  # noqa: E402
import listening  # noqa: E402


def queue_public(it: dict) -> dict:
    d = {k: v for k, v in it.items() if k not in ("opts", "cancel", "path", "words", "sig", "_diag")}
    d["duration"] = round(it["end"] - it["start"], 2)
    if it.get("status") == "done":
        import ytclips
        # the title and description it goes to YouTube with (5.1; every clip since 5.1.3: a TikTok clip goes up as a Short)
        d["yt_texts"] = ytclips.texts(it)
    return d


SOUND_LOG: list = []         # (event, item id) of every sound played, for the tests


def play_sound(event: str, it: dict):
    """One short Windows sound per event: 'queued' when you add a clip, 'ready' when a render
    finishes. Called from the one place each event happens, so it can't play twice."""
    if not exports.load().get("sounds", True):
        return
    SOUND_LOG.append((event, it.get("id")))
    import sounds
    sounds.play("lock" if event == "ready" else "contact")       # radar pings (4.41), not Windows' beeps


def youtube_opts(opts: dict, info: Optional[dict] = None) -> dict:
    """The YouTube edit of a moment (5.1): the same clip in standard YouTube shape, 16:9, the whole picture (on a
    blurred background when the stream isn't 16:9 itself), with nothing cropped, followed or zoomed. Captions and their
    style stay as set for the clip."""
    sw, sh = (info or {}).get("width") or 1920, (info or {}).get("height") or 1080
    wide = abs(sw / max(1, sh) - 16 / 9) < 0.03
    return dict(opts or {}, aspect="16:9", layout="full" if wide else "blur", track="off", punch="off")


def queue_add(pid: str, c: dict, opts: dict, sound: bool = False, platform: str = "tiktok", made: bool = False,
              yt_text: Optional[dict] = None) -> dict:
    """platform: "tiktok" (the clip as set in Moments) or "youtube" (5.1: its YouTube edit, youtube_opts).
    made (5.4): the YouTube edit as you made it in Moments, its settings used as they are (always 16:9);
    yt_text: its YouTube title and description, when you wrote them there."""
    start_, end_ = float(c["start"]), float(c["end"])
    if end_ - start_ < 1:
        raise RuntimeError("That clip is too short.")
    pr = S.projects.get(pid) or {}
    if pr and "layout" not in (opts or {}):
        opts = dict(opts or {}, **previews._auto_layout(pr))
    if platform == "youtube":
        opts = dict(opts or {}, aspect="16:9") if made else youtube_opts(opts, pr.get("info"))
    d = S._find_clip(pr, c.get("id")) if pr else None
    if d:
        why = checkflow.blocks_render(d)
        if why:
            raise RuntimeError(why)
        if d.get("orig") and not pr.get("live") and not d.get("manual") and (
                abs(start_ - d["orig"][0]) > 0.3 or abs(end_ - d["orig"][1]) > 0.3):
            # how you trimmed it teaches where this streamer's clips should start and end
            try:
                learning.record_trim(pr.get("streamer", ""), f"{pr.get('source', '')}|{int(d.get('peak', start_))}",
                                     start_ - d["orig"][0], end_ - d["orig"][1])
            except Exception:
                pass
    it = {"id": uuid.uuid4().hex[:8], "pid": pid, "clip_id": c.get("id"), "rank": int(c.get("rank") or 0),
          "start": start_, "end": end_, "title": str(c.get("title") or "")[:120],
          "post": str(c.get("post") or "")[:2200], "thumb": c.get("thumb", ""),
          "gain": max(-12.0, min(12.0, float(c.get("gain") or 0))),
          "source_title": pr.get("title", ""),
          # enough to render it after a restart, when the project isn't open any more
          "video": pr.get("video", ""), "workdir": pr.get("workdir", ""), "live": pr.get("live", ""),
          "opts": dict(opts or {}), "status": "queued", "stage": "queued", "progress": 0.0, "step": "Waiting",
          "error": "", "cancel": False, "created": time.time(),
          "words": S._clean_words(c.get("words")),
          # content check: fixes you applied, and flags you chose to leave alone (not censored)
          "fix_spans": checkflow.render_spans(d) if d else [],
          "keep_fps": [f["fp"] for f in ((d or {}).get("safety") or {}).get("flags", [])
                       if f.get("status") in ("accepted", "dismissed")]}
    try:
        it["link"] = sources.link_at(engine._meta(pr.get("workdir", "")), start_)    # back to the source, at this moment
    except Exception:
        it["link"] = ""
    prefs = exports.load()
    # the YouTube edit is made with the YouTube export settings (1920 x 1080), whatever Settings › Export format says
    ex = exports.settings(dict(prefs, preset="youtube", custom={}) if platform == "youtube" else prefs)
    it["platform"] = "youtube" if platform == "youtube" else "tiktok"
    if platform == "youtube" and isinstance(yt_text, dict):
        # what you wrote for YouTube in Moments: used as Exports' own YouTube text (ytclips.texts)
        t = {k: str(yt_text[k])[:5000 if k == "description" else 100] for k in ("title", "description") if isinstance(yt_text.get(k), str)}
        if t:
            it["yt_text"] = t
    camp = campaigns.active()
    if camp:
        # Settings › Campaigns: the active campaign's watermark and post rules go with the clip
        ex["watermark"] = campaigns.watermark_for(camp, ex["watermark"])
        it["campaign"] = {k: camp[k] for k in ("id", "name", "caption", "caption_where", "tags")}
    if c.get("no_watermark"):
        # Moments › No watermark (5.6): this clip without Settings › Watermark's (or the campaign's) marks
        ex["watermark"] = {"on": False, "marks": []}
    if c.get("no_captions"):
        # Moments › No captions (5.6): this clip without burned-in captions (bleeping and muting still hear the words)
        it["opts"]["captions"] = False
    it["export"] = ex
    it["name_fields"] = exports.fields_for(dict(d or {}, title=it["title"]), pr, ex["preset"], number=it["clip_id"])
    it["sig"] = json.dumps([pid, it["clip_id"], round(start_, 2), round(end_, 2), it["title"], it["opts"], ex], sort_keys=True,
                           default=str)
    mx = exports.PRESETS[ex["preset"]].get("max_len")
    if mx and end_ - start_ > mx:
        it["warning"] = (f"{exports.PRESETS[ex['preset']]['label']} allows up to {engine.fmt_ts(mx)}; "
                         f"this clip is {engine.fmt_ts(end_ - start_)}. Trim it, or pick another preset.")
    cw = campaigns.length_warning(camp, end_ - start_)
    if cw:
        it["warning"] = (it.get("warning", "") + " " + cw).strip()
    with S.queue_lock:
        # the same clip sent twice (double click, two windows): render it once
        dup = next((x for x in S.queue if x.get("sig") == it["sig"] and x["status"] in ("queued", "rendering")), None)
        if dup:
            return dup                         # already queued: no second sound either
        S.queue.append(it)
    S.queue_wake.set()
    _prehear_wake.set()
    S.save_queue_soon()
    if sound:
        play_sound("queued", it)
    return it


_render_projects: dict = {}


def _render_project(it: dict) -> dict:
    """The project a queued clip comes from. After a restart it isn't open, so rebuild the little
    a render needs (the video, its folder and any transcript made before)."""
    pr = S.projects.get(it["pid"])
    if pr:
        return pr
    video = it.get("video") or ""
    if not video or not os.path.exists(video):
        raise RuntimeError("The VOD for this clip was deleted.")
    # one copy per project: a clip's captions heard ahead and a render of the same project must share its transcript
    with _render_projects_lock:
        pr = _render_projects.get(it["pid"])
        if not pr or pr["video"] != video:
            info = engine.probe(video)
            pr = {"video": video, "workdir": it.get("workdir") or os.path.dirname(video),
                  "title": it.get("source_title", ""), "info": info, "duration": info["duration"],
                  "tx_lock": threading.Lock(), "live": it.get("live", "")}
            if not pr["live"]:
                listening._load_transcripts(pr)
            _render_projects[it["pid"]] = pr
    return pr


_render_projects_lock = threading.Lock()


def queue_busy() -> bool:
    return any(it["status"] in ("queued", "rendering") for it in S.queue)


def _file_name(it: dict, outdir: str) -> str:
    """The clip's file is named after its title, chosen once: a retry replaces its own file, and only
    another clip with the same title gets " (2)"."""
    if it.get("name") and os.path.normcase(it.get("folder") or "") == os.path.normcase(outdir):
        return it["name"]
    ex = it.get("export") or {}
    ext = ex.get("container", "mp4") if ex.get("container") in ("mp4", "mov", "webm") else "mp4"
    if it.get("name_fields"):
        base = exports.file_name(ex.get("template", exports.DEFAULT_TEMPLATE), dict(it["name_fields"], title=it["title"] or "clip"))
    else:
        base = S._safe_name(it["title"]) if it["title"].strip() else f"clip {engine.fmt_ts(it['start']).replace(':', '-')}"
    if it.get("platform") == "youtube":
        base += " (YouTube)"            # its TikTok edit is in the same folder
    # Settings › Hashtags › In file names: the real tags, '#' and all, chosen with the name (so a retry keeps them)
    nf = it.get("name_fields") or {}
    base = hashtags.with_tags(base, hashtags.file_tags(nf.get("streamer", ""), platform=nf.get("platform", "")))
    with S.queue_lock:
        claimed = {os.path.normcase(x["name"]) for x in S.queue if x is not it and x.get("name")
                   and os.path.normcase(x.get("folder") or "") == os.path.normcase(outdir)}
    name, n = f"{base}.{ext}", 2
    while os.path.normcase(name) in claimed or os.path.exists(os.path.join(outdir, name)):
        name = f"{base} ({n}).{ext}"
        n += 1
    it["name"], it["folder"] = name, outdir
    S.save_queue_soon()
    return name


MB_PER_SECOND = 1.5      # generous upper bound for a 1080x1920 clip


def _check_space(outdir: str, seconds: float):
    need = seconds * MB_PER_SECOND * 1e6 + 200e6
    try:
        free = shutil.disk_usage(outdir).free
    except OSError:
        return
    if free < need:
        raise OSError(28, f"No space left on device ({free // 1_000_000} MB free, "
                          f"about {int(need // 1_000_000)} MB needed)")


def _check_output(path: str, expect: float, want_audio: bool = False) -> Optional[float]:
    """A finished render must be a real video of the right length, with sound when the source has
    it, that reads to its end, before it counts as done. A broken one is deleted, never
    shown as a finished clip. Returns the sound's peak in dB when want_audio (None if it couldn't be
    measured), read in the same pass, for _check_sound."""
    why = ""
    try:
        size = os.path.getsize(path)
        info = engine.probe(path) if size > 1000 else None
    except Exception:
        info = None
    if not info:
        why = ""
    elif abs(info["duration"] - expect) > max(1.0, expect * 0.05):
        why = f" ({info['duration']:.1f}s instead of {expect:.1f}s)"
    elif want_audio and not info["has_audio"]:
        why = " (no sound)"
    else:
        # the file must read to its end: a render cut short by a crash or a full disk often
        # still has a readable header and the right length
        plays, peak = render.read_through(path, audio=bool(want_audio and info["has_audio"]))
        if plays:
            return peak
        why = " (its end doesn't play)"
    try:
        os.remove(path)
    except OSError:
        pass
    raise BrokenOutput(f"The rendered file came out broken{why}.")


class BrokenOutput(RuntimeError):
    pass


# ---- captions heard ahead: the next kept clip's words while this one encodes ------------------------------------
# A render used to hear its captions itself, then encode: the CPU sat idle while the GPU encoded, and the GPU while the
# CPU listened. On the owner's PC that hearing was 79% of render time (efficiency audit 2026-11-08, F1, 188 min a week)
# and clips waited behind it (F4, 56 min). Now, while one clip renders, the clips queued after it are heard in order,
# one at a time, through the same ensure_words, so the render finds them done. The words are the same; only when
# they're heard changes. A clip with nothing ahead of it isn't touched: its render starts at once and hears it as before.
_prehear: dict = {}                 # queue item id -> Event: set once its captions are heard (or hearing gave up)


_prehear_wake = threading.Event()


_prehear_started = [False]


def _needs_captions(it: dict, pr: dict, ro) -> bool:
    """The render will hear this clip (the same test as _render_inputs)."""
    cfg = safety.load_config()
    censoring = cfg.get("enabled", True) and cfg.get("censor", "off") != "off"
    return (it.get("words") is None and bool(ro.captions or censoring) and bool(pr["info"].get("has_audio"))
            and whisper.whisper_available())


def _prehear_one() -> bool:
    """Hear the next queued clip that waits behind a render. False when there's nothing to do."""
    with S.queue_lock:
        for k in [k for k in _prehear if not any(x["id"] == k for x in S.queue)]:
            _prehear.pop(k).set()                       # gone from the queue: nobody waits for it
        if not any(x["status"] == "rendering" for x in S.queue):
            return False
        it = next((x for x in S.queue if x["status"] == "queued" and x["id"] not in _prehear and not x.get("cancel")), None)
        if it is None:
            return False
        ev = _prehear[it["id"]] = threading.Event()
    try:
        pr = _render_project(it)
        ro = S._ropts(it["opts"], it.get("export") or {})
        if not _needs_captions(it, pr, ro):
            return True

        def check():
            if it.get("cancel") or S.SHUTTING_DOWN[0] or not any(x is it for x in S.queue):
                raise engine.Cancelled()
        timer = timing.Timer("captions ahead", it.get("title") or "")
        listening.ensure_words(pr, it["start"], it["end"], ro.whisper_model, ro.language, check, timer=timer)
        timer.done()
    except engine.Cancelled:
        pass
    except Exception as e:                              # the render hears it again itself, and reports what failed
        print(f"  Couldn't hear the captions of \"{it.get('title') or 'a clip'}\" ahead: {str(e)[:140]}", flush=True)
    finally:
        ev.set()
    return True


def _prehear_loop():
    while not S.SHUTTING_DOWN[0]:
        _prehear_wake.wait(2.0)
        _prehear_wake.clear()
        while not S.SHUTTING_DOWN[0] and _prehear_one():
            pass


def start_prehear():
    if not _prehear_started[0]:
        _prehear_started[0] = True
        threading.Thread(target=_prehear_loop, name="captions-ahead", daemon=True).start()


def _wait_prehear(it: dict, check, timer: Optional[timing.Timer]):
    """A clip whose captions are being heard ahead waits for that instead of hearing them a second time."""
    ev = _prehear.get(it["id"])
    if ev is None or ev.is_set():
        return
    t0 = time.time()
    while not ev.wait(0.5):
        check()
    if timer:
        timer.add("waiting for captions heard ahead", time.time() - t0)


def _render_inputs(it: dict, pr: dict, ro, check, timer: Optional[timing.Timer] = None) -> tuple:
    """Captions, title and censoring for a render of it['start']..it['end'] (a clip, or a few seconds
    of one for the exact preview). Returns (words, title, post, spans, mode)."""
    cfg = safety.load_config()
    censoring = cfg.get("enabled", True) and cfg.get("censor", "off") != "off"
    words = None
    if it.get("words") is not None:
        # captions corrected by hand in the app
        words = [dict(w, start=w["start"] - it["start"], end=w["end"] - it["start"])
                 for w in it["words"] if it["start"] - 0.2 <= w["start"] < it["end"]]
    elif (ro.captions or censoring) and pr["info"]["has_audio"] and whisper.whisper_available():
        set_stage(it, "preparing", "Listening for captions")
        try:
            _wait_prehear(it, check, timer)
            abs_words = listening.ensure_words(pr, it["start"], it["end"], ro.whisper_model, ro.language, check, timer=timer)
        except engine.Cancelled:
            raise
        except Exception as e:
            # Whisper couldn't run (often: its first download failed). YouTube's own captions if the video
            # has them; otherwise the clip renders without captions and says so. Censoring needs the
            # words, so with censoring on the render stops rather than skip the bleeps.
            abs_words = ytextra.words_in(pr.get("yt") or {}, it["start"], it["end"])
            if not abs_words and censoring:
                raise RuntimeError(f"Couldn't transcribe the clip to censor it ({str(e)[:120]}). "
                                   "Check your internet connection (the speech model downloads the first time), then retry.")
            if not abs_words:
                it["warning"] = (f"Rendered without captions: the speech model couldn't run ({str(e)[:100]}). "
                                 "Check your internet connection (it downloads the first time), then press Retry.")
                it["captions_skipped"] = True
        words = [{"start": w["start"] - it["start"], "end": w["end"] - it["start"], "word": w["word"]}
                 for w in abs_words if w["start"] < it["end"]]
    # content check: flag, and (if switched on) mask and bleep what it finds
    title, post = it["title"], it.get("post", "")
    spans, mode = [], "off"
    if cfg.get("enabled", True):
        res = safety.scan(words or [], title, post, cfg)
        it["safety"] = {"level": res["level"], "flags": res["flags"][:20]}
        if censoring:
            # flags you left alone or dismissed in Review aren't censored
            keep = set(it.get("keep_fps") or [])
            res_c = dict(res, flags=[f for f in res["flags"]
                                     if checkflow.fingerprint(f, {"start": it["start"]}) not in keep])
            words, spans, mode = safety.censored(words or [], res_c, cfg)
            title, post = safety.mask_text(title, cfg), safety.mask_text(post, cfg)
            it["censored"] = sum(1 for f in res_c["flags"] if f["cat"] in cfg.get("censor_categories", []))
    # fixes applied in Review: mute or bleep those words, and mask them in the captions
    spans = [(a, b, mode) for a, b in spans]
    for a, b, kind in it.get("fix_spans") or []:
        ra, rb = a - it["start"], b - it["start"]
        if rb > 0 and ra < it["end"] - it["start"]:
            spans.append((max(0.0, ra), rb, kind))
            if words:
                words = [dict(w, word=safety.mask(w["word"])) if ra - 0.02 <= w["start"] <= rb else w for w in words]
            it["censored"] = it.get("censored", 0) + 1
    if words:
        words = exports.replace_words(words, exports.load().get("replace"))   # Settings › Captions › Replace words
    if not ro.captions:
        words = []
    if it.get("platform") == "youtube":
        title = ""                      # YouTube shows the title under the video: no TikTok title card on it
    return words, title, post, spans, mode


def _validate_render(it: dict, pr: dict, outdir: str):
    """Before any work: the source is there and readable, the clip lies inside it, and the clip's
    folder can be written. Each failure names its cause, so it isn't retried blindly."""
    if not os.path.exists(pr["video"]):
        raise RuntimeError("The VOD for this clip was deleted.")
    try:
        info = engine.probe(pr["video"])
    except RuntimeError as e:
        raise engine.RenderInputError("source_damaged", str(e)) from None
    except Exception as e:
        raise engine.RenderInputError("source_damaged", "The source video couldn't be read: "
                                      + (str(e).strip().splitlines() or [type(e).__name__])[-1][:200]) from None
    if not it["end"] - it["start"] >= 0.5:
        raise engine.RenderInputError("source_range", "This clip is too short to render.")
    if info["duration"] and it["start"] > info["duration"] - 0.5 and not (it.get("live") or pr.get("live")):
        raise engine.RenderInputError("source_range", f"This clip starts at {engine.fmt_ts(it['start'])}, after the end "
                                                      f"of the video ({engine.fmt_ts(info['duration'])}).")
    try:
        os.makedirs(outdir, exist_ok=True)
        probe_file = os.path.join(outdir, f".vc_write_{it['id']}.tmp")
        with open(probe_file, "wb") as f:
            f.write(b"ok")
        os.remove(probe_file)
    except OSError as e:
        if getattr(e, "errno", None) in (28, 112):
            raise
        raise engine.RenderInputError("output_folder", f"Clip Radar can't save clips in {outdir} ({e.strerror or e}). "
                                                       "Choose another folder in Settings › Storage.") from None


def _render_item(it: dict):
    set_stage(it, "validating", "Checking the source")
    it["progress"] = 0.0
    timer = timing.Timer("render", it.get("title") or "")
    for k, v in (it.get("lat") or {}).items():            # a hands-free moment: the way here, from the moment it aired
        if k not in ("render", "total"):
            timer.add(k, v)
    timer.add("waiting to render", timer.started - it.get("created", timer.started))
    timer.mark("checking the source")
    diag = it["_diag"] = {}
    pr = _render_project(it)
    ex = it.get("export") or {}
    ro = S._ropts(it["opts"], ex)
    ro.gain_db = max(-12.0, min(12.0, float(it.get("gain") or 0)))       # Clip tab › Volume
    subs = ex.get("subtitles", "burn")
    outdir = locations.clips_dir(pr["workdir"])          # Settings › Storage › Rendered clips
    _validate_render(it, pr, outdir)
    name = _file_name(it, outdir)
    length = it["end"] - it["start"]
    _check_space(outdir, length)
    enc = render.video_encoder(ro)[0]
    skey = exports.speed_key(enc, ex)
    known = exports.speed(skey)
    it["eta"] = round(length / known + 3, 1) if known else None      # before it starts: from earlier renders
    enc_t0 = [None]

    def check():
        if it["cancel"]:
            raise engine.Cancelled()

    def prog(x):
        # the engine reports 0.3..1.0 while FFmpeg encodes (from FFmpeg's own out_time); only that part
        # is a real measurement, so it's the only part shown as a percentage
        if x < 0.3:
            return
        frac = max(0.0, min(1.0, (x - 0.3) / 0.7))
        it["progress"] = round(frac, 3)
        now = time.time()
        if enc_t0[0] is None:
            enc_t0[0] = now
        el = now - enc_t0[0]
        if frac > 0.04 and el > 1.5:                   # time left from the speed so far
            it["eta"] = round((1 - frac) * el / frac + 2, 1)

    if TEST_HOOKS.get("fail_next_render"):
        TEST_HOOKS["fail_next_render"] = False
        raise OSError(28, "No space left on device")

    it["source_title"] = pr["title"]
    if ro.punch != "off":                    # Layout › Punch-in: on this clip's own peaks
        ro.punch_at = render.punch_beats(pr.get("analysis"), it["start"], it["end"])
    fr = None
    if render.effective_layout(ro) == "full" and ro.track == "auto":
        set_stage(it, "preparing", "Finding the face")
        timer.mark("finding the face")
        fr = previews._framing_for(pr, it["start"], it["end"], ro)
        check()
        if fr is not None:
            it["framing"] = {"followed": bool(fr.get("path")), "reason": fr.get("reason", ""), "confidence": fr.get("confidence", 0)}
    set_stage(it, "preparing", "Getting captions ready")
    timer.mark("captions and content check")
    words, title, post, spans, mode = _render_inputs(it, pr, ro, check, timer)
    srt_words = list(words or []) if ro.captions else []
    if subs == "srt":
        words = []                                  # subtitles as a file only, not burned in
    path = os.path.join(outdir, name)
    expect = min(it["end"], pr["info"]["duration"] or it["end"]) - max(0.0, it["start"])
    picture = _picture_soon(it, pr)
    for attempt in (1, 2):
        set_stage(it, "rendering", "Rendering" if attempt == 1 else "Rendering again")
        timer.mark("encoding")
        it["progress"] = 0.0
        t_start = time.time()
        enc_t0[0] = None
        render.render_clip(pr["video"], it["start"], it["end"], path, ro, title=title,
                           progress=prog, check=check, words=words, censor=spans, censor_mode=mode,
                           log=lambda m: print("  " + m, flush=True), framing=fr,
                           warn=lambda m: it.__setitem__("warning", m), diag=diag)
        took = time.time() - (enc_t0[0] or t_start)
        if took > 0.5:
            exports.record_speed(skey, length / took)
        set_stage(it, "verifying", "Checking the file")
        timer.mark("checking the file")
        it["eta"] = 1
        if TEST_HOOKS.get("break_next_output"):
            TEST_HOOKS["break_next_output"] = False
            with open(path, "r+b") as f:
                f.truncate(500)
        try:
            peak = _check_output(path, expect, want_audio=bool(pr["info"].get("has_audio")))
            _check_sound(it, pr, path, peak)
            _check_picture(it, pr, picture)
            break
        except BrokenOutput as e:
            print(f"  {e}" + (" Trying once more." if attempt == 1 else ""), flush=True)
            if attempt == 2:
                raise
    if subs in ("burn_srt", "srt") and srt_words:
        try:
            render.write_srt(srt_words, ro, os.path.splitext(path)[0] + ".srt", length)
            it["srt"] = True
        except OSError:
            pass
    it["eta"] = 0
    # the campaign's required text and hashtags, added after the content check so they're never masked
    post = campaigns.apply_post(post, it.get("campaign"), title)
    # caption to paste when posting, saved next to the video
    if post:
        it["post"] = post
        try:
            persist.write_text(os.path.splitext(path)[0] + ".txt", post + "\n")
        except OSError:
            pass
    it["path"] = path
    it["url"] = f"/export/{it['id']}?v={int(time.time() * 1000)}"
    timer.done()


TEST_HOOKS = {"fail_next_render": False, "break_next_output": False}


_UNMEASURED = object()


def _check_sound(it: dict, pr: dict, path: str, peak=_UNMEASURED):
    """A clip from a video with sound must come out with sound; one that's nearly silent gets a warning.
    peak: what _check_output measured (dB or None), so the file isn't read again."""
    if not pr["info"].get("has_audio"):
        return
    if peak is _UNMEASURED:
        peak = render.audio_peak(path)
    if peak is None:
        try:
            os.remove(path)
        except OSError:
            pass
        raise BrokenOutput("The rendered file came out without sound.")
    if peak < -60:
        it["warning"] = ("This clip came out almost silent. If it shouldn't be, check the stream at this point "
                         "(Twitch mutes some music).")


def _picture_soon(it: dict, pr: dict):
    """Start looking at the clip's picture now; returns a wait() that gives the result (None if it failed).
    It reads the source, not the render, so it runs while FFmpeg encodes: after the render it added about
    2 s to every clip (measured: five seeks into a 32 GB, 9-hour VOD), a quarter of a short GPU render."""
    box: dict = {}

    def look():
        try:
            box["pc"] = render.picture_check(pr["video"], it["start"], it["end"])
        except Exception:
            pass
    t = threading.Thread(target=look, daemon=True)
    t.start()

    def wait():
        t.join()
        return box.get("pc")
    return wait


def _check_picture(it: dict, pr: dict, measured=None):
    """A clip that's mostly too dark or too blurry to watch on a phone gets a warning (it still renders).
    measured: a wait() from _picture_soon, when the check already started alongside the render."""
    pc = (measured or _picture_soon(it, pr))()
    if pc is None:
        return
    it["picture"] = pc
    if it.get("warning"):
        return
    if pc["dark"] >= 0.6:
        it["warning"] = "Most of this clip is very dark: it may be hard to see on a phone."
    elif pc["blurry"] >= 0.6:
        it["warning"] = "Most of this clip is blurry (fast movement or out of focus): check it before posting."


# Why a render failed, as (kind, retryable). Retryable kinds can come right on a second try with nothing
# changed (a stall, a file another program had open); the rest need something fixed first, so they're
# never retried automatically and the message says what to change.
RETRYABLE = {"stalled", "broken_output", "file_locked", "memory", "gpu_memory", "ffmpeg_memory", "interrupted"}


RAM_SHORT = 0.75 * 1024 ** 3        # free memory under this when something failed for memory: the PC really was short


_CARD_WORDS = re.compile(r"cuda|cublas|cudnn|nvenc|openencodesession|d3d11|dxva|surface pool|vram|vulkan|"
                         r"device memory|graphics card|\bgpu\b", re.I)


def classify_render_error(e: Exception) -> tuple:
    """(kind, what went wrong and what to do in plain words, technical details)."""
    raw = str(e) or type(e).__name__
    low = raw.lower()
    if isinstance(e, engine.RenderInputError):
        return e.kind, raw, raw
    errno_ = getattr(e, "errno", None) or getattr(e, "winerror", None)
    mem = S.memory_error(e)
    rules = [
        ("disk_full", errno_ in (28, 112) or "no space left" in low or "not enough space" in low,
         "Your drive is full. Free up space (Settings › Storage: Clear temporary files, Delete VODs) or choose another "
         "folder for rendered clips, then retry."),
        ("file_locked", errno_ in (13, 32) or "permission denied" in low or "being used by another process" in low,
         "The clip's file is open in another program, like a video player. Close it, then retry."),
        ("source_missing", "vod for this clip was deleted" in low or "no such file" in low and "input" not in low,
         "The stream recording for this clip was deleted, so it can't be rendered any more."),
        ("memory", mem is not None and mem[0] != "ffmpeg_memory", mem[1] if mem else ""),
        ("captions", "error initializing filter 'subtitles'" in low or "libass" in low or "fontconfig" in low,
         "The captions couldn't be drawn. Try the Classic font in the Text tab, then retry."),
        ("encoder", "error while opening encoder" in low or "nvenc" in low or "qsv" in low or "amf" in low,
         "The video encoder failed. Turn off \"Use the GPU to encode\" in the Layout tab, then retry."),
        ("layout", "failed to configure input pad" in low or "error reinitializing filters" in low
         or "error initializing complex filter" in low or "invalid too big or non positive size" in low,
         "The clip's layout couldn't be built from this video (a crop or the framing lands outside the picture). "
         "Press Reset in the Layout tab or choose another layout, then retry."),
        ("broken_output", "came out broken" in low,
         "The finished file came out broken twice. Retry; if it keeps happening, turn off "
         "\"Use the GPU to encode\" in the Layout tab."),
        ("stalled", "made no progress" in low or "didn't finish the file" in low,
         "FFmpeg stopped making progress and was stopped. Retry; if it keeps happening, turn off "
         "\"Use the GPU to encode\" in the Layout tab or try a shorter clip."),
        ("ffmpeg_memory", mem is not None, mem[1] if mem else ""),
        ("source_damaged", "invalid data found" in low or "moov atom" in low or "end of file" in low
         or "no video track" in low,
         "The source video is damaged or incomplete, so this part couldn't be read."),
        ("stalled", "timed out" in low or "timeout" in low,
         "Rendering took too long and was stopped. Retry; if it keeps happening, try a shorter clip."),
    ]
    for kind, hit, msg in rules:
        if hit:
            return (mem[0] if kind == "memory" else kind), msg, raw[-1500:]
    first = raw.strip().splitlines()[-1] if raw.strip() else type(e).__name__
    return "unknown", f"Rendering stopped with an error: {first[:200]}", raw[-1500:]


def friendly_render_error(e: Exception) -> tuple:
    """(what went wrong and what to do, in plain words; technical details)."""
    return classify_render_error(e)[1:]


redact = errorlog.redact       # diagnostics without secrets (tokens and keys blanked, the home folder as ~)


def set_stage(it: dict, stage: str, step: str):
    """Where a render is: queued, validating, preparing, rendering, verifying, then done, error or
    cancelled. The UI shows a percentage only while rendering, where FFmpeg reports real progress;
    the other stages show their step and how long they've taken."""
    it["stage"], it["step"], it["stage_at"] = stage, step, time.time()
    if it.get("history") and it["history"][-1].get("outcome") == "running":
        it["history"][-1]["stage"] = stage


HISTORY_KEEP = 8


def _attempt_start(it: dict):
    hist = it.setdefault("history", [])
    hist.append({"n": (hist[-1]["n"] + 1) if hist else 1, "started": time.time(), "outcome": "running",
                 "stage": "validating"})
    del hist[:-HISTORY_KEEP]


def _attempt_end(it: dict, outcome: str, kind: str = "", error: str = "", diag: Optional[dict] = None):
    hist = it.get("history") or []
    if not hist or hist[-1].get("outcome") != "running":
        return
    a = hist[-1]
    a.update(ended=time.time(), outcome=outcome)
    if kind:
        a["kind"] = kind
    if error:
        a["error"] = error[:300]
    d = diag or {}
    for k in ("encoder", "exit", "fallback", "black_start", "layout_used"):
        if d.get(k) not in (None, ""):
            a[k] = d[k]
    ff = d.get("first_failure")
    if ff:
        # a layout that had to fall back: what failed first, even though the clip came out
        a["first_failure"] = {"layout": ff.get("layout", ""), "exit": ff.get("exit"),
                              "reason": redact(ff.get("reason", ""))[-1500:], "filter": redact(ff.get("filter", ""))[:4000]}
    if outcome != "done":
        if d.get("cmd"):
            a["cmd"] = redact(d["cmd"])[:2000]
        if d.get("stderr"):
            a["stderr"] = redact(d["stderr"])[-1500:]
        if d.get("fallback_stderr"):
            a["fallback_stderr"] = redact(d["fallback_stderr"])[-1500:]


def _after_render(it: dict):
    """Once a clip is ready: a finished VOD may now be cleaned up (Settings › Storage, off by default)."""
    if cleanup.load()["vods"]:
        S.run_cleanup()


MAX_WORKERS = [1]


def apply_perf(prefs: Optional[dict] = None):
    """Settings › Performance: renders at once, and keeping the PC responsive versus finishing faster."""
    p = prefs or exports.load()
    MAX_WORKERS[0] = p["renders"]
    if p["priority"] == "responsive":
        render.RENDER_PRIORITY[0] = 0x00004000 if sys.platform == "win32" else 0     # BELOW_NORMAL_PRIORITY_CLASS
        # leave one core for you; the lower priority (Windows) already lets what you're doing go first.
        # Half the cores cost a third of the speed (measured: 30 s clip, 4 cores, 23.8 s vs 18.2 s)
        render.RENDER_THREADS[:] = ["-threads", str(max(2, (os.cpu_count() or 4) - 1))]
    else:
        render.RENDER_PRIORITY[0] = 0
        render.RENDER_THREADS[:] = []
        if _lowered[0]:                     # "Finish faster": back to normal
            _set_priority(0x00000020)       # NORMAL_PRIORITY_CLASS
            _lowered[0] = False
    start_prehear()
    while len(_workers) < MAX_WORKERS[0]:
        i = len(_workers)
        _workers.append(threading.Thread(target=queue_worker, args=(i,), daemon=True))
        _workers[-1].start()
    S.queue_wake.set()


_workers: list = []
_lowered = [False]
# held until Clip Radar's window is open (app.py): a job started before it (reopening last session's videos, a render
# queued when it closed) would otherwise lower the window's own processes, which start after and inherit it
_priority_gate = {"open": False, "wanted": False}


def open_priority_gate() -> None:
    """The window is up (or there is none): background_priority can lower the process now, and does at once if a job
    asked while it waited."""
    _priority_gate["open"] = True
    if _priority_gate["wanted"]:
        background_priority()


def _set_priority(cls: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        # the handle is pointer-sized: left to ctypes' default (a 32-bit int) it's cut short, and the call fails
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.SetPriorityClass.restype = wintypes.BOOL
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), cls))
    except Exception:
        return False


def background_priority() -> None:
    """Settings › Performance › Keep the PC responsive (the default): Clip Radar's own background work runs below
    normal priority too, not only the render's FFmpeg (5.4.2). Captions (Whisper), finding the face, the music scan,
    the song eraser and the checks after a render ran at normal priority on most of the cores, and the whole PC
    stuttered while a clip was made. Done when the first background job starts and the window is open, so the window's
    own processes (started with it, before) keep normal priority; everything started after inherits it, FFmpeg
    included."""
    if _lowered[0] or render.RENDER_PRIORITY[0] == 0:
        return
    if not _priority_gate["open"]:
        _priority_gate["wanted"] = True
        return
    if _set_priority(0x00004000):           # BELOW_NORMAL_PRIORITY_CLASS
        _lowered[0] = True


def queue_eta() -> Optional[float]:
    """Time until everything in the queue is rendered (None when there's nothing to go on yet)."""
    total, unknown = 0.0, False
    with S.queue_lock:
        items = [x for x in S.queue if x["status"] in ("queued", "rendering")]
    for x in items:
        if x["status"] == "rendering":
            if x.get("eta") is None:
                unknown = True
            else:
                total += x["eta"]
        else:
            ex = x.get("export") or {}
            sp = exports.speed(exports.speed_key(render.video_encoder(S._ropts(x.get("opts") or {}, ex))[0], ex))
            if not sp:
                unknown = True
            else:
                total += (x["end"] - x["start"]) / sp + 3
    if not items:
        return 0.0
    return None if unknown and total == 0 else round(total / max(1, min(MAX_WORKERS[0], len(items))), 1)


MEM_RESERVE = 1.0 * 1024 ** 3      # leave this much memory free before starting another render at once


def _mem_short() -> bool:
    m = locations.memory()
    return m["free"] is not None and m["free"] < MEM_RESERVE


def queue_worker(idx: int = 0):
    while True:
        S.queue_wake.wait(2.0)
        S.queue_wake.clear()
        if idx >= MAX_WORKERS[0]:
            continue                                  # fewer renders at once now: this worker rests
        while True:
            with S.queue_lock:
                has_queued = any(x["status"] == "queued" for x in S.queue)
                has_rendering = any(x["status"] == "rendering" for x in S.queue)
            if not has_queued:
                break
            if idx > 0 and has_rendering and _mem_short():
                # Wait only when there is actual competing work; don't pin an idle worker
                # in a memory loop when the queue is empty.
                time.sleep(3)
                continue
            with S.queue_lock:
                it = next((x for x in S.queue if x["status"] == "queued"), None)
                if it and it.get("live"):
                    # live moments waiting together: the strongest renders first
                    it = max((x for x in S.queue if x["status"] == "queued" and x.get("live")),
                             key=lambda x: x.get("prio", 0), default=it)
                if it:
                    it["status"] = "rendering"
                    it["progress"] = 0.0
                    _attempt_start(it)
            _prehear_wake.set()                       # the clips behind this one can be heard now
            if not it:
                break
            background_priority()
            if S.SHUTTING_DOWN[0]:
                it["status"] = "queued"
                it["history"].pop()
                return
            S.save_queue_soon()
            t0 = time.time()
            print(f"  Rendering \"{it.get('title') or 'clip'}\" ({it['end'] - it['start']:.0f} s)...", flush=True)
            try:
                _render_item(it)
                it["status"] = "done"
                it["progress"] = 1.0
                set_stage(it, "done", f"Done in {time.time() - t0:.0f}s")
                _attempt_end(it, "done", diag=it.pop("_diag", None))
                print(f"  Rendered {it.get('name') or 'the clip'} in {time.time() - t0:.0f} s.", flush=True)
                outcome.log("render", "ok", live=bool(it.get("live")))
                S.save_queue_soon(0)
                autopilot.render_done(it)      # sound, Discord, cleanup, latency: autopilot_steps
            except engine.Cancelled:
                outcome.log("render", "cancelled", live=bool(it.get("live")), closing=bool(S.SHUTTING_DOWN[0]))
                with S.queue_lock:
                    _attempt_end(it, "interrupted" if S.SHUTTING_DOWN[0] else "cancelled", diag=it.pop("_diag", None))
                    if S.SHUTTING_DOWN[0]:
                        it.update(status="queued", progress=0.0, step="Waiting", stage="queued", cancel=False)
                    elif it in S.queue:
                        it["stage"] = "cancelled"
                        S.queue.remove(it)
                S.save_queue_soon(0)
                continue
            except Exception as e:
                kind, msg, detail = classify_render_error(e)
                retryable = kind in RETRYABLE
                diag = it.pop("_diag", None)
                _attempt_end(it, "error", kind=kind, error=msg, diag=diag)
                it["attempts"] = it.get("attempts", 0) + 1
                fails = [a for a in it.get("history", []) if a.get("outcome") == "error"]
                if kind == "stalled" and len(fails) == 1 and not it.get("cancel"):
                    # a stall is the one failure a second try usually gets past by itself: once
                    print(f"  Render stalled; trying once more ({detail[-200:]})", flush=True)
                    outcome.log("render", "failed", kind=kind, live=bool(it.get("live")), retry=True)
                    errorlog.record("render:stalled", "Render stalled; trying once more", detail, clip=it.get("title", ""))
                    it.update(status="queued", progress=0.0, eta=None, stage="queued", step="Waiting to try again")
                    S.save_queue_soon(0)
                    S.queue_wake.set()
                    continue
                it["status"] = "error"
                it["stage"] = "error"
                it["error"], it["detail"] = msg, redact(detail)
                it["error_kind"], it["retryable"] = kind, retryable
                print(f"  Render failed [{kind}]: {it['error']} ({it['detail'][:300]})", flush=True)
                outcome.log("render", "failed", kind=kind, live=bool(it.get("live")))
                errorlog.record(f"render:{kind}", msg, detail, clip=it.get("title", ""), source=it.get("source_title", ""),
                                attempt=it["attempts"], retryable=retryable)
                if not isinstance(e, (RuntimeError, OSError, engine.RenderInputError)):
                    traceback.print_exc()
                S.save_queue_soon(0)
                autopilot.render_failed(it, kind)
                continue
