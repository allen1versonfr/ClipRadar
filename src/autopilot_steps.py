"""
The steps Clip Radar runs on its own, each a small function calling the code that does the work.

Each imports server inside the function (as engine is imported across the codebase), so autopilot.py
never imports server and there's no import cycle. register() runs when server is imported, and in tests.

    docs/plans/issue-50-automation-core.md, section 4.2
"""

from __future__ import annotations

import time

import autopilot
from autopilot import CLIP_TITLED, MOMENT_FOUND, RENDER_DONE, Ctx, Step


def _live(ctx: Ctx) -> bool:
    return ctx.sess is not None and ctx.d is not None


# ---------------------------------------------------------------- moment_found

def title_moment(ctx: Ctx):
    """Title the moment: Whisper, the cut refined to what was said, a title, the content check."""
    import server
    extra = ctx.sess.extra
    server.start_listening(ctx.pid, [ctx.d["id"]], extra.get("model") or "small", extra.get("language") or "")


# ---------------------------------------------------------------- clip_titled

def latency_title(ctx: Ctx):
    """How long the moment took to title; with hands-free off, it's ready to look at, so the whole way too."""
    d, sess = ctx.d, ctx.sess
    now = time.time()
    d["lat"]["title"] = round(now - d["created"], 2)
    sess.note_latency("title", d["lat"]["title"])
    if ctx.aired and not sess.extra.get("auto_render"):
        d["lat"]["total"] = round(now - ctx.aired, 2)       # ready to look at: cut, captioned and titled
        sess.note_latency("total", d["lat"]["total"])
        sess.last_lat = dict(d["lat"])


def hands_free_render(ctx: Ctx):
    """Hands-free: queue the moment for rendering, unless the content check holds it back for you."""
    import server
    d, sess, mid = ctx.d, ctx.sess, ctx.pid
    extra = sess.extra
    number = d.get("moment")
    hold = autopilot.gate(d)
    if hold and hold.rule == "content_check":
        d["held"] = hold.why
        sess.log(f"Moment {number} held back by the content check")
        return "held", d["held"]
    if hold:
        d["held"] = "Held for review: flags not reviewed yet"
        sess.log(f"Moment {number} held back: {hold.why}")
        return "held", hold.why
    c = dict(d, rank=number)
    c["post"] = d.get("post", "")
    it = server.queue_add(mid, c, extra.get("opts") or {})
    it["prio"] = server._live_prio(d)
    it["auto"] = True                      # rendered on its own (hands-free), not by you
    it["lat"], it["aired"] = dict(d["lat"]), ctx.aired
    d["auto_queued"] = True
    return "ran"


# ---------------------------------------------------------------- render_done

def ready_sound(ctx: Ctx):
    """The 'ready' sound (play_sound checks Settings › Exports › sounds itself)."""
    import server
    server.play_sound("ready", ctx.it)


def discord(ctx: Ctx):
    """A campaign set to send each clip as it renders: auto_discord checks the switch and the webhook, and
    sends on its own thread, so a slow upload never holds up the next render."""
    import server
    server.auto_discord(ctx.it)


def cleanup_vods(ctx: Ctx):
    """A finished VOD may now be cleaned up (Settings › Storage, off by default)."""
    import errorlog
    import server
    try:
        server._after_render(ctx.it)
    except Exception as e:
        errorlog.exc("after-render", clip=ctx.it.get("title", ""))      # as queue_worker logged it
        return "failed", f"{type(e).__name__}: {e}"


def latency_render(ctx: Ctx):
    """Hands-free: how long rendering took, and the whole way from the moment on stream to the file."""
    import server
    server._live_rendered(ctx.it)


STEPS = (
    Step("title_moment", MOMENT_FOUND, enabled=lambda ctx: True, run=title_moment, concerns=_live),
    # the title's latency first: a hands-free item carries it (it["lat"]), and a held clip still has it
    Step("latency_title", CLIP_TITLED, enabled=lambda ctx: True, run=latency_title, concerns=_live),
    Step("hands_free_render", CLIP_TITLED, enabled=lambda ctx: bool(ctx.sess.extra.get("auto_render")),
         run=hands_free_render, concerns=_live),
    # after a render, in the order queue_worker ran them; each checks its own switch, as before
    Step("ready_sound", RENDER_DONE, enabled=lambda ctx: True, run=ready_sound),
    Step("discord", RENDER_DONE, enabled=lambda ctx: True, run=discord),      # outward: gated in phase 4
    Step("cleanup_vods", RENDER_DONE, enabled=lambda ctx: True, run=cleanup_vods),
    Step("latency_render", RENDER_DONE, enabled=lambda ctx: True, run=latency_render),
)


def register():
    """Register every step, in order. Safe to call more than once."""
    for s in STEPS:
        autopilot.register(s)
