"""
4. render: a clip cut, framed to 9:16 and finished with captions, title and watermarks, then encoded on the graphics
card or the CPU (moved from engine.py, hardening phase 4d; no changes).

  RenderOpts                         everything a render can be asked for (layout, captions, text, encoder…)
  render_clip(video, start, end, out, opts, …)
  build_filter(info, opts, …)        the FFmpeg filter graph for a clip's picture
  video_encoder(opts)                the encoder to use, and its settings
  write_srt, black_start, picture_check …

engine.py re-exports every name here, so engine.render_clip and the rest still work. Code that calls these, and a
test that replaces one (render.build_filter = fake), uses this module: a fake put on engine wouldn't reach it. The
shared helpers (run, ffmpeg, probe, Cancelled…) stay in engine.py and are imported from it (imported on its own,
this module has engine import it first); a test that replaces one for the render code replaces render.<helper> too.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np

import paths
import procjob

if "engine" not in sys.modules:
    # imported before engine: engine imports this module itself once the helpers below are defined, so engine is
    # imported first and this copy steps aside (the import then gives back the copy engine loaded in full)
    del sys.modules[__name__]
    import engine  # noqa: E402,F401
if TYPE_CHECKING:
    from engine import Analysis  # noqa: F401  (punch_beats' annotation; engine.py's analysis moves in 4e)
from engine import (  # noqa: E402  engine imports this module after these are defined
    CREATE_NO_WINDOW, Cancelled, Check, Log, Progress, RenderInputError, _noop, ffmpeg, fmt_ts, probe, run,
)
import whisper  # noqa: E402  (after engine: whisper, imported first, would start engine with this module half-loaded)


@dataclass
class RenderOpts:
    layout: str = "split"           # split (cam + game) | full (whole frame) | blur | square (square crop on blur) | cam (facecam only)
    cam: tuple = (0.0, 0.0, 0.25, 0.25)   # facecam crop x, y, w, h as fractions of source
    cams: list = field(default_factory=list)   # more facecams (a duo, a podcast), each a box like cam; at most 3
    cam_height: float = 0.33        # fraction of output height used by facecam in split
    game_zoom: float = 1.0          # >1 zooms gameplay in
    game_x: float = 0.5             # horizontal focus of gameplay crop 0..1
    captions: bool = True
    caption_pos: str = "auto"       # auto | top | middle | bottom | custom (cap_x, cap_y)
    caption_color: str = "#FFE600"  # highlight color for the spoken word
    caption_size: int = 100         # percent
    words_per_line: int = 3
    uppercase: bool = True
    caption_style: str = "pop"      # pop | box | karaoke | glow | clean
    caption_font: str = "classic"   # key of FONTS
    caption_anim: bool = True       # words pop in (kept for saved styles; caption_motion says how)
    caption_motion: str = ""        # none | pop | word | bounce | fade | slide | reveal ("" = pop if caption_anim)
    caption_speed: int = 100        # animation speed, percent (higher = quicker)
    caption_emph: bool = False      # strong words ("no way", "insane", shouting) in the highlight colour
    caption_filler: bool = True     # keep "um", "uh" in captions
    caption_punct: bool = True      # keep punctuation in captions
    caption_spacing: int = 100      # gap between a caption's two lines, percent of normal (100-200)
    caption_offset: int = 0         # move captions earlier (-) or later (+), milliseconds
    cap_x: float = 0.5              # custom caption centre, fractions of the frame
    cap_y: float = 0.7
    title_style: str = "box"        # box | dark | accent | outline | yellow (yellow text, black border)
    title_font: str = "classic"
    title_size: int = 100           # percent (50-300)
    title_color: str = "#FFFFFF"    # the text's colour in the Outline style
    title_outline: int = 100        # border thickness of the Outline and Yellow styles, percent of normal (0-300)
    title_x: float = 0.5            # title centre x and top y, fractions of the frame
    title_y: float = 0.05           # top of the title box
    title_fit: bool = True          # Text › Title › Auto size: a short title bigger, a long one smaller (fit_text)
    caption_fit: bool = True        # Text › Captions › Auto size: a caption shrinks until its longest word fits
    whisper_model: str = "small"
    language: str = ""              # "" = auto-detect
    hw_encode: bool = True
    crf: int = 20
    fps: int = 0                    # 0 = the source's own frame rate (up to 60); never raised above the source
    speed: str = "balanced"         # fast | balanced | high: encoder effort (quality_args)
    aspect: str = "9:16"            # 9:16 vertical | 1:1 square | 16:9 wide
    # 3.7 export settings (presets in exports.py)
    scale: float = 1.0              # output size relative to the shape's full size (0.6667 = 720p)
    vcodec: str = "h264"            # h264 | hevc | vp9
    vbitrate: int = 0               # kbps; 0 = constant quality (crf)
    acodec: str = "aac"             # aac | opus
    abitrate: int = 192             # kbps
    container: str = "mp4"          # mp4 | mov | webm
    loudness: float = -14.0         # LUFS target; 0 = leave the volume as it is (peaks are still limited)
    gain_db: float = 0.0            # this clip louder or quieter (Clip tab › Volume), -12..+12 dB
    denoise: bool = False           # reduce steady background noise
    voice: bool = False             # make speech clearer (gentle compression) without burying game audio
    cap_outline: int = 100          # caption outline, percent of normal
    cap_shadow: int = 100           # caption shadow, percent of normal
    # 4.5 watermark (export only; the source is never changed)
    wm_text: str = ""               # text mark, or "" for none
    wm_image: str = ""              # path to a PNG/JPG mark, or "" (used instead of the text when set)
    wm_pos: str = "tl"              # tl | tr | bl | br | c
    wm_size: int = 100              # percent of the normal size
    wm_opacity: int = 70            # percent
    wm_margin: int = 40             # pixels from the side at 1080 wide
    wm_box: bool = False            # a dark box behind a text mark (easier to read on busy footage)
    wm_start: float = 0.0           # seconds into the clip it appears
    wm_end: float = 0.0             # seconds it disappears; 0 = until the end
    # 4.11 watermark layers (watermarks.py); when set, the single-mark wm_* settings above are ignored
    wm_marks: list = field(default_factory=list)
    # 3.9 framing
    track: str = "auto"             # auto: whole-frame crops follow the streamer's face | off
    track_target: str = "auto"      # auto | largest | left | right | point (nearest to game_x, focus_y)
    focus_y: float = 0.4            # vertical focus point (for "point")
    # 4.45 punch-in: a short push toward the subject on the clip's big beats (Layout › Punch-in)
    punch: str = "off"              # off | subtle | strong
    punch_at: list = field(default_factory=list)   # seconds into the clip (punch_beats), filled in at render time


W, H = 1080, 1920
SHAPES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def out_size(o: "RenderOpts") -> tuple:
    return SHAPES.get(getattr(o, "aspect", "9:16"), SHAPES["9:16"])


def final_size(o: "RenderOpts") -> tuple:
    """The exported frame size: the shape's size times the preset's scale (even numbers)."""
    w, h = out_size(o)
    k = min(1.0, max(0.25, float(getattr(o, "scale", 1.0) or 1.0)))
    return (_even(w * k), _even(h * k)) if k < 0.999 else (w, h)


def audio_filter(o: "RenderOpts") -> str:
    """Audio chain for an export: optional noise reduction and voice clarity, then loudness to the
    preset's target (or just a peak limiter), at 48 kHz."""
    f = []
    if getattr(o, "denoise", False):
        f.append("afftdn=nr=12:nf=-40")
    if getattr(o, "voice", False):
        f.append("highpass=f=80,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200:makeup=2")
    lufs = float(getattr(o, "loudness", -14.0) or 0)
    gain = max(-12.0, min(12.0, float(getattr(o, "gain_db", 0) or 0)))
    if lufs < 0:
        f.append(f"loudnorm=I={lufs:g}:TP=-1.5:LRA=11")
        if gain:
            # after evening out (which would undo it if it came first), then peaks kept under -1 dB.
            # Raising loudnorm's own target instead measured *quieter* on peaky audio (true-peak bound).
            f += [f"volume={gain:g}dB", "alimiter=limit=0.89:level=false"]
    else:
        if gain:
            f.append(f"volume={gain:g}dB")
        f.append("alimiter=limit=0.89:level=false")
    f.append("aresample=48000")
    return ",".join(f)


def effective_layout(o: "RenderOpts") -> str:
    """Cam + game stacks the facecam over gameplay; in a wide frame that doesn't fit, so it uses the whole frame."""
    return "full" if o.layout == "split" and getattr(o, "aspect", "9:16") == "16:9" else o.layout


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


MAX_FPS = 60


def output_fps(info: dict, o: RenderOpts) -> float:
    """The frame rate a clip renders at: the source's own (o.fps 0), or the one chosen, but never
    above the source's. Turning 30 FPS into 60 only repeats frames; it can't add motion that wasn't
    recorded, so Clip Radar doesn't do it."""
    src = float(info.get("fps") or 0)
    if not (1 <= src <= 240):
        src = 30.0
    want = float(getattr(o, "fps", 0) or 0)
    out = min(src, MAX_FPS) if want <= 0 else min(want, src)
    return round(out, 3)


SPEEDS = {   # x264 preset, quality offset (lower crf = better), NVENC preset
    "fast": ("veryfast", 2, "p3"),
    "balanced": ("medium", 0, "p5"),
    "high": ("slow", -2, "p7"),
}


def _num(v, default: float, lo: float, hi: float) -> float:
    """A saved setting as a number in lo..hi; the default when it's missing, not a number, NaN or infinite."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(f, lo), hi) if math.isfinite(f) else default


MAX_CAMS = 4            # facecams in one clip: cam and up to 3 more (cams)
MAX_ZOOM = 4.0          # the Layout tab goes to 2x; older or edited settings are held to this
MIN_CAM_PX = 8          # a facecam box smaller than this (source pixels, or 2% of a tiny video) is an empty box


def crop_filter(sw: int, sh: int, w: int, h: int, x, y: int) -> str:
    """crop=w:h:x:y for frames of sw x sh, once it's certain to fit: every crop in a layout is made here.
    w and h even and inside the frame, 0 <= x <= sw-w and 0 <= y <= sh-h. x may be a face-following
    expression from framing.x_expr, which clamps itself to 0..sw-w on every frame; it's checked for
    the nesting FFmpeg refuses. A crop that doesn't fit is a bug in how it was worked out: it fails here,
    named, instead of FFmpeg failing and the clip falling back to another layout."""
    import framing
    if isinstance(x, str) and x.isdigit():
        x = int(x)                                  # a path that holds still: a fixed crop
    ok = 2 <= w <= sw and 2 <= h <= sh and w % 2 == 0 and h % 2 == 0 and 0 <= y <= sh - h
    if isinstance(x, str):
        ok = ok and framing.expr_depth(x) <= framing.EXPR_MAX_DEPTH and x.endswith(f",0,{sw - w})'")
    else:
        ok = ok and 0 <= x <= sw - w
    if not ok:
        raise RenderInputError("layout", f"The layout's crop came out as {w}x{h} at x {str(x)[:40]}, y {y}, which doesn't "
                                         f"fit this {sw}x{sh} video. That's a bug in Clip Radar, not your video or settings: "
                                         "please report it (Exports › the clip › details).")
    return f"crop={w}:{h}:{x}:{y}"


PUNCH_ZOOM = {"subtle": 0.08, "strong": 0.14}   # how far in: past ~15% a face-framed crop starts losing the head
PUNCH_IN, PUNCH_HOLD, PUNCH_OUT = 0.18, 1.1, 0.7  # seconds: fast in, hold, slow out (reads as intent, not drift)
PUNCH_MAX = 4               # beats in one clip at most
PUNCH_GAP = 4.0             # seconds between two beats at least
PUNCH_EDGE = 1.5            # never in the first or last moments of a clip


def punch_beats(an: "Analysis", start: float, end: float, n: int = PUNCH_MAX) -> list:
    """Seconds into a clip (start..end of the video) to punch in on: the clip's own peaks of the ranking score
    (chat, loudness, laughter, viewers' clips...), the strongest first, apart from each other, at most n.
    A clip without a peak above its own middle gets none: a flat clip shouldn't twitch."""
    if an is None or getattr(an, "score", None) is None:
        return []
    sc = np.asarray(an.score, dtype=np.float32)
    a, b = int(max(0, math.floor(start))), int(min(len(sc), math.ceil(end)))
    if b - a < 4:
        return []
    seg = sc[a:b]
    floor = float(np.median(seg)) + max(0.6, 0.5 * float(np.std(seg)))
    cand = [i for i in range(1, len(seg) - 1) if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > floor]
    cand.sort(key=lambda i: -float(seg[i]))
    out: list = []
    length = end - start
    for i in cand:
        t = (a + i) - start
        if t < PUNCH_EDGE or t > length - PUNCH_EDGE - PUNCH_IN:
            continue
        if all(abs(t - u) >= PUNCH_GAP for u in out):
            out.append(round(float(t), 2))
        if len(out) >= n:
            break
    return sorted(out)


def punch_filter(o: RenderOpts, w: int, h: int, focus_y: float = 0.45, fps: str = "") -> str:
    """',fps,zoompan' that pushes a w x h picture in on o.punch_at and back out, or '' when it's off.
    zoompan, not scale + crop: a crop's size and the input size it centres against are fixed when FFmpeg
    starts, so a picture scaled up frame by frame was cropped from its top-left corner. zoompan numbers its
    output frames at its own rate, so the stream is brought to the clip's frame rate (fps) just before it,
    or a 30 fps source would play at the wrong speed."""
    amt = PUNCH_ZOOM.get(str(o.punch or "off"))
    beats = [float(t) for t in (o.punch_at or []) if isinstance(t, (int, float)) and math.isfinite(t) and t >= 0]
    if not amt or not beats or w < 4 or h < 4 or not fps:
        return ""
    beats = sorted(beats)[:PUNCH_MAX]

    def ease(x: str) -> str:            # 0..1, smooth at both ends
        return f"(({x})*({x})*(3-2*({x})))"
    env = []
    for t in beats:
        rise = f"clip((it-{t - PUNCH_IN:.3f})/{PUNCH_IN},0,1)"
        fall = f"clip(({t + PUNCH_HOLD + PUNCH_OUT:.3f}-it)/{PUNCH_OUT},0,1)"
        env.append(f"{ease(rise)}*{ease(fall)}")
    e = env[0]
    for x in env[1:]:
        e = f"max({e},{x})"
    fy = min(max(float(focus_y), 0.0), 1.0)
    rate = fps.split(":")[0]
    return (f",fps={fps},zoompan=z='1+{amt}*{e}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*{fy:.3f}'"
            f":d=1:s={w}x{h}:fps={rate},setsar=1")


def build_filter(info: dict, o: RenderOpts, path: Optional[list] = None, face: Optional[dict] = None) -> tuple:
    """Return (filter_complex producing [v] in yuv420p, caption_center_y).
    path: [(t, centre x)] for a whole-frame crop that follows a face (3.9 framing).
    face: {"face_y", "face_h"} so automatic captions don't cover the face."""
    sw, sh = int(info["width"]), int(info["height"])
    if sw < 2 or sh < 2:
        raise RenderInputError("unsupported", f"This video's picture is {sw}x{sh} pixels, too small to make a clip from.")
    z = _num(o.game_zoom, 1.0, 1.0, MAX_ZOOM)
    game_x = _num(o.game_x, 0.5, 0.0, 1.0)
    W, H = out_size(o)
    layout = effective_layout(o)
    # start_time=0: the first frame is repeated from 0:00 when the source's video starts a moment late,
    # so the file never opens on an empty (black) frame, which TikTok takes as the cover
    fr = f"{output_fps(info, o)}:start_time=0"
    max_w, max_h = sw - sw % 2, sh - sh % 2         # the largest even crop of the frame

    def safe_cam_crop(rect) -> tuple:
        """The facecam box (fractions of the source) as an even-sized crop inside the frame. A box that
        isn't four numbers, or is too small to be a camera, is a setting to fix, said in those words."""
        try:
            x, y, w, h = [float(v) for v in rect]
        except (TypeError, ValueError):
            x = y = w = h = math.nan
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            raise RenderInputError("layout_settings", "The facecam box in the Layout tab couldn't be read. Drag a box "
                                                      "around the facecam (or press Reset), then retry.")
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        w, h = min(max(w, 0.0), 1.0 - x), min(max(h, 0.0), 1.0 - y)    # the part of the box inside the picture
        if w * sw < max(2, min(MIN_CAM_PX, 0.02 * sw)) or h * sh < max(2, min(MIN_CAM_PX, 0.02 * sh)):
            raise RenderInputError("layout_settings", "The facecam box in the Layout tab is empty or outside the "
                                                      "picture. Drag a box around the facecam (or press Reset), then retry.")
        cx = min(int(x * sw), max_w - 2)
        cy = min(int(y * sh), max_h - 2)
        cx -= cx % 2
        cy -= cy % 2
        cw = min(_even(w * sw), max_w - cx)
        ch = min(_even(h * sh), max_h - cy)
        return cw - cw % 2, ch - ch % 2, cx, cy

    def cam_crop(rect) -> str:
        return crop_filter(sw, sh, *safe_cam_crop(rect))

    def game_crop(out_w: int, out_h: int, follow: Optional[list] = None) -> str:
        aspect = out_w / out_h
        cwf = min(sw, sh * aspect) / z
        cw = min(_even(cwf), max_w)
        ch = min(_even(cwf / aspect), max_h)
        cx = int(min(max(0, game_x * sw - cw / 2), sw - cw))
        cx -= cx % 2
        if follow and cw < sw - 2:
            import framing
            cx = framing.x_expr(follow, sw, cw) or cx      # moves with the face, smoothly
        cy = (sh - ch) // 2
        cy -= cy % 2
        return f"{crop_filter(sw, sh, cw, ch, cx, cy)},scale={out_w}:{out_h}:flags=lanczos,setsar=1"

    rects = [o.cam] + list(getattr(o, "cams", None) or [])[:MAX_CAMS - 1]

    def cells(total: int, n: int) -> list:
        """n even sizes that add up to total (the last takes what's left)."""
        each = _even(total / n)
        return [each] * (n - 1) + [total - each * (n - 1)]

    def cam_cell(rect, cw: int, ch: int) -> str:
        return (f"{cam_crop(rect)},scale={cw}:{ch}:force_original_aspect_ratio=increase:"
                f"flags=lanczos,crop={cw}:{ch},setsar=1")

    def cam_row(cw_total: int, ch_total: int, side_by_side: bool) -> tuple:
        """(filters reading [c0], [c1]..., the inputs' labels, the output label) for every facecam in one
        strip: side by side (above the gameplay) or stacked (Cam only)."""
        n = len(rects)
        if n == 1:
            return f"[c0]{cam_cell(rects[0], cw_total, ch_total)}[cam]", "[c0]"
        sizes = cells(cw_total if side_by_side else ch_total, n)
        parts = [f"[c{i}]{cam_cell(r, s, ch_total) if side_by_side else cam_cell(r, cw_total, s)}[k{i}]"
                 for i, (r, s) in enumerate(zip(rects, sizes))]
        stack = "hstack" if side_by_side else "vstack"
        parts.append("".join(f"[k{i}]" for i in range(n)) + f"{stack}=inputs={n}[cam]")
        return ";".join(parts), "".join(f"[c{i}]" for i in range(n))

    if layout == "split":
        top_h = _even(H * _num(o.cam_height, 0.33, 0.2, 0.5))
        bot_h = H - top_h
        cams, labels = cam_row(W, top_h, True)
        pc = punch_filter(o, W, top_h, 0.4, fr)       # on the streamer, not the game
        cams_out = f"[cam]null{pc}[camp];" if pc else ""
        f = (f"[0:v]split={len(rects) + 1}{labels}[b];{cams};[b]{game_crop(W, bot_h)}[game];{cams_out}"
             f"[{'camp' if pc else 'cam'}][game]vstack=inputs=2,fps={fr},format=yuv420p[v]")
        cap_y = top_h  # right on the seam reads best
    elif layout == "full":
        fy = 0.4
        if face and face.get("face_y") is not None:
            try:
                fy = float(face["face_y"]) / (H if float(face["face_y"]) > 1 else 1)
            except (TypeError, ValueError):
                fy = 0.4
        f = f"[0:v]{game_crop(W, H, path)}{punch_filter(o, W, H, fy, fr)},fps={fr},format=yuv420p[v]"
        cap_y = H * 0.70
        if face and path:
            import framing
            cap_y = framing.caption_y(face.get("face_y"), face.get("face_h"), H, cap_y)
    elif layout == "cam":
        # the facecam by itself, filling the frame; several, one above the other
        if len(rects) == 1:
            f = (f"[0:v]{cam_crop(o.cam)},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                 f"crop={W}:{H},setsar=1{punch_filter(o, W, H, 0.4, fr)},fps={fr},format=yuv420p[v]")
            cap_y = H * 0.78
        else:
            cams, labels = cam_row(W, H, False)
            f = f"[0:v]split={len(rects)}{labels};{cams};[cam]null{punch_filter(o, W, H, 0.4, fr)},fps={fr},format=yuv420p[v]"
            cap_y = cells(H, len(rects))[0]          # on the first seam, like Cam + game
    elif layout == "blur":
        # the whole picture, fitted inside the frame (never cropped or stretched), on a blurred,
        # darkened copy of itself that fills the rest. Works for wide, square and tall sources.
        fg_w, fg_h = W, _even(W * sh / sw)
        if fg_h > H:                                   # a tall source: fit the height instead
            fg_w, fg_h = min(W, _even(H * sw / sh)), H
        fg = game_crop(fg_w, fg_h) if z > 1 else f"scale={fg_w}:{fg_h}:flags=lanczos,setsar=1"
        bw, bh = max(2, _even(W / 4)), max(2, _even(H / 4))
        f = (f"[0:v]split=2[a][b];"
             f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=12:2,"
             f"eq=brightness=-0.12,scale={W}:{H},setsar=1[bg];"
             f"[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2{punch_filter(o, W, H, 0.5, fr)},fps={fr},format=yuv420p[v]")
        cap_y = min(H / 2 + fg_h / 2 + 170, H * 0.8)
    elif layout == "square":
        # a square crop of the picture (Focus and Zoom pick which part), centred in the frame on a
        # blurred, darkened copy of the picture: the square gameplay look, in a 9:16 (or any) frame
        side = _even(min(W, H))
        bw, bh = max(2, _even(W / 4)), max(2, _even(H / 4))
        f = (f"[0:v]split=2[a][b];"
             f"[a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=12:2,"
             f"eq=brightness=-0.12,scale={W}:{H},setsar=1[bg];"
             f"[b]{game_crop(side, side)}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2{punch_filter(o, W, H, 0.5, fr)},fps={fr},format=yuv420p[v]")
        cap_y = min(H / 2 + side / 2 + 170, H * 0.8)
    else:
        raise ValueError(f"unknown layout {o.layout}")
    # every frame at the size the layout was worked out for: a source that changes resolution part-way
    # (a live recording crossing a Twitch ad break) would otherwise fail the crop and lose the whole render.
    # Frames already that size pass straight through.
    f = f.replace("[0:v]", f"[0:v]scale={sw}:{sh},", 1)

    if o.caption_pos == "custom":
        return f, min(max(o.cap_y * H, 120), H - 120)
    pos = {"top": H * 0.22, "middle": H * 0.5, "bottom": H * 0.78}.get(o.caption_pos, cap_y)
    return f, min(max(pos, 260), H - 220)


# ---- captions --------------------------------------------------------------


def _ass_color(hex_rgb: str) -> str:
    h = (hex_rgb or "").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFE600"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " "))


# Caption and title fonts. "classic" is the system's heaviest sans; the rest ship in ui/fonts/captions
# (OFL / Apache licensed) and are handed to libass with fontsdir. Values: (file, family name in the file).
FONTS = {
    "classic": ("", ""),
    "montserrat": ("Montserrat-Black.ttf", "Montserrat Black"),
    "poppins": ("Poppins-Black.ttf", "Poppins Black"),
    "inter": ("Inter-Black.otf", "Inter Black"),
    "anton": ("Anton-Regular.ttf", "Anton"),
    "bebas": ("BebasNeue-Regular.ttf", "Bebas Neue"),
    "bangers": ("Bangers-Regular.ttf", "Bangers"),
    "luckiest": ("LuckiestGuy-Regular.ttf", "Luckiest Guy"),
}
# condensed faces look small at the same point size; scale them up to a similar visual size
FONT_SCALE = {"inter": 1.05, "anton": 1.3, "bebas": 1.4, "bangers": 1.25}
CAPTION_STYLES = ("pop", "box", "karaoke", "glow", "clean")
TITLE_STYLES = ("box", "dark", "accent", "outline", "yellow")
TITLE_YELLOW = "#FFE600"
MAX_TEXT_SIZE = {"title": 300, "caption": 250}     # percent: Text tab › Size goes this far
# average width of a capital (or digit) and of any other character, as a share of the font size, as libass draws
# each face (measured, plus about 5%; libass sizes a font by its full line height, so glyphs come out narrower
# than a browser's at the same size; "classic" is Arial Black on Windows): for Auto size, which has to agree with
# the preview without measuring the text. draw.ts and app.js keep the same table.
FONT_GLYPH = {"classic": (0.6, 0.51), "montserrat": (0.45, 0.38), "poppins": (0.36, 0.32), "inter": (0.54, 0.45),
              "anton": (0.26, 0.25), "bebas": (0.28, 0.28), "bangers": (0.23, 0.23), "luckiest": (0.44, 0.45)}
TITLE_GROW = 1.3                   # Auto size makes a short title at most this much bigger
FIT_MIN = 0.6                      # ... and a long one (or caption) at most this much smaller, unless a word won't fit


def text_width(text: str, fs: float, font: str) -> float:
    """About how wide text is at font size fs (pixels), from FONT_GLYPH."""
    up, lo = FONT_GLYPH.get(font, FONT_GLYPH["classic"])
    return fs * sum(up if (c != c.lower() or c.isdigit()) else lo for c in text)


def fit_text(text: str, fs: int, width: float, font: str, grow: float = 1.0, lines: int = 3) -> int:
    """Text › Auto size: the font size for text in a box `width` wide, starting from fs (Text › Size).
    Text that fits on one line grows to fill it (at most `grow` times fs); text that would need more than
    `lines` lines shrinks until it doesn't (to FIT_MIN of fs); and the longest word always fits on a line, which
    at a big size it didn't (a single word can't wrap, so it ran off the frame). Keep in step with fitText in
    web/src/features/review/draw.ts and src/ui/app.js."""
    words = text.split()
    if not words or width <= 0:
        return int(fs)
    one = width * 0.92 / max(1e-6, text_width(" ".join(words), 1.0, font))      # the size that fills one line
    if one >= fs:
        out = min(fs * grow, one)
    else:
        out = max(fs * FIT_MIN, min(float(fs), one * lines * 0.85))            # wrapping leaves gaps at line ends
    out = min(out, width * 0.96 / max(1e-6, max(text_width(w, 1.0, font) for w in words)))
    return max(20, int(out))


def fonts_dir() -> Path:
    return Path(paths.RES_DIR) / "ui" / "fonts" / "captions"


def _font(key: str = "classic") -> tuple:
    """(family name for ASS, bold flag, font file or '')"""
    file, family = FONTS.get(key, FONTS["classic"])
    if file and (fonts_dir() / file).is_file():
        return family, 0, file          # already a heavy cut: no fake bold
    return ("Arial Black" if sys.platform == "win32" else "DejaVu Sans"), -1, ""


def fonts_for(o: "RenderOpts") -> list:
    """Font files the ASS needs from the bundled set."""
    out = []
    keys = {o.caption_font, o.title_font} | {m.get("font", "classic") for m in marks_of(o) if m.get("kind") != "image"}
    for key in keys:
        f = _font(key)[2]
        if f:
            out.append(str(fonts_dir() / f))
    return out


CAPTION_MOTIONS = ("none", "pop", "word", "bounce", "fade", "slide", "reveal")
FILLER_WORDS = {"um", "uh", "uhh", "umm", "erm", "hmm", "mm", "uhm"}
EMPH_WORDS = {"no", "way", "what", "insane", "crazy", "wait", "yes", "never", "impossible", "clutch", "holy",
              "omg", "wtf", "bro", "stop", "run", "dead", "help", "why", "how"}


def caption_words(words: list, o: "RenderOpts") -> list:
    """The words captions show: filler words and punctuation dropped when you asked (timing unchanged)."""
    out = []
    for w in words:
        core = re.sub(r"[^\w']", "", w["word"]).lower()
        if not o.caption_filler and core in FILLER_WORDS:
            continue
        if not o.caption_punct:
            txt = re.sub(r"[.,!?;:…\"“”]+", "", w["word"]).strip()
            if not txt:
                continue
            w = dict(w, word=txt)
        out.append(w)
    return out


def motion_of(o: "RenderOpts") -> str:
    m = getattr(o, "caption_motion", "") or ""
    if m in CAPTION_MOTIONS:
        return m
    return "pop" if o.caption_anim else "none"


def _emphasised(word: str) -> bool:
    core = re.sub(r"[^\w']", "", word)
    return core.lower() in EMPH_WORDS or word.endswith("!") or (len(core) > 2 and core.isupper())


# a caption line doesn't end on a word that leads into the next ("I went to the | store"); an article, preposition
# or possessive is worse than a pronoun, a conjunction or "is" ("this is | the biggest burger" reads fine)
LINE_LEADS = {"the", "a", "an", "to", "of", "my", "your", "his", "her", "our", "their", "its", "in", "on", "at", "for",
              "with", "from"}
LINE_LEADS_SOFT = {"and", "or", "but", "this", "that", "i", "i'm", "we", "you", "he", "she", "they", "it's", "is", "was",
                   "are", "be", "if", "so", "as"}


def _line_cost(ws: list, i: int, j: int, m: int) -> float:
    """How bad ws[i:j] is as one caption line of at most m words (the same sums as the UI's preview)."""
    n, k = len(ws), j - i
    c = 1.0 + 0.15 * (m - k)                       # fewer, fuller lines
    last = ws[j - 1]["word"]
    bare = re.sub(r"[^\w']", "", last).lower()
    if j < n and last[-1:].isalnum():
        c += 2.0 if bare in LINE_LEADS else 0.8 if bare in LINE_LEADS_SOFT else 0.0      # "...to the" | "store"
    if k == 1:
        c += 1.2                                   # a word on its own
    if j < n and last[-1:] in ",;:":
        c -= 0.8                                   # a comma is a natural place for a new line
    return c


def _balance(ws: list, m: int) -> list:
    """One run of speech in lines of at most m words: the fewest, evenest lines that don't end on "the" or leave a
    word on its own (4.41: before, every m words, so "so I went | to the store | yesterday" and "...to the | store").
    Ties go to the longer first line, as the old cut did."""
    n = len(ws)
    if n <= m:
        return [ws]
    best, back = [0.0] + [float("inf")] * n, [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, max(0, j - m) - 1, -1):
            c = best[i] + _line_cost(ws, i, j, m)
            if c < best[j] - 1e-9:
                best[j], back[j] = c, i
    lines, j = [], n
    while j > 0:
        lines.append(ws[back[j]:j])
        j = back[j]
    return lines[::-1]


def _chunks(words: list, o: "RenderOpts", clip_len: float) -> list:
    runs: list = []
    cur: list = []
    for w in words:
        if w["start"] >= clip_len:
            break
        prev = cur[-1] if cur else None
        if prev and (w.get("g") or prev.get("g")):
            brk = w.get("g") != prev.get("g")      # a line typed in the app stays one caption
        else:
            brk = bool(cur) and (w["start"] - prev["end"] > 0.6 or prev["word"][-1:] in ".?!")
        if brk:
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    m = max(1, int(o.words_per_line))
    return [ln for r in runs for ln in ([r] if r[0].get("g") else _balance(r, m))]


WM_POSITIONS = ("tl", "tr", "bl", "br", "c")
WM_TOP, WM_BOTTOM = 0.13, 0.80   # top marks sit below TikTok's top tabs; bottom ones above its caption area


def marks_of(o: RenderOpts) -> list:
    """The watermark layers to draw: Settings › Export format › Watermark's layers, or the single
    mark of the older settings (an image was used instead of the text when both were set)."""
    if o.wm_marks:
        return [m for m in o.wm_marks if isinstance(m, dict)]
    common = {"pos": o.wm_pos, "size": o.wm_size, "opacity": o.wm_opacity, "margin": o.wm_margin,
              "box": o.wm_box, "start": o.wm_start, "end": o.wm_end}
    if o.wm_image:
        return [dict(common, kind="image", image=o.wm_image)]
    if (o.wm_text or "").strip():
        return [dict(common, kind="text", text=o.wm_text.strip())]
    return []


def _mpos(m: dict) -> str:
    return m.get("pos") if m.get("pos") in WM_POSITIONS + ("free",) else "tl"


def mark_anchor(m: dict, W: int, H: int, w: int = 0, h: int = 0) -> tuple:
    """Top-left corner (x, y) for a layer of size w×h, kept inside the frame. Shared by text and
    image layers and by the preview, so all three land in the same place. Corners sit below
    TikTok's top tabs or above its caption area; a free layer is centred on its x, y."""
    pos = _mpos(m)
    mg = int(max(0, min(200, float(m.get("margin", 40)))) * W / 1080)
    if pos == "free":
        x, y = int(float(m.get("x", .5)) * W - w / 2), int(float(m.get("y", .5)) * H - h / 2)
    elif pos == "c":
        x, y = (W - w) // 2, (H - h) // 2
    else:
        x = mg if pos[1] == "l" else W - w - mg
        y = int(H * WM_TOP) if pos[0] == "t" else int(H * WM_BOTTOM) - h
    return max(0, min(x, W - w)), max(0, min(y, H - h))


def watermark_anchor(o: RenderOpts, W: int, H: int, w: int = 0, h: int = 0) -> tuple:
    return mark_anchor({"pos": o.wm_pos, "margin": o.wm_margin}, W, H, w, h)


def mark_window(m: dict, clip_len: float) -> tuple:
    a = max(0.0, min(float(m.get("start") or 0), clip_len))
    b = float(m.get("end") or 0)
    b = clip_len if b <= a else min(b, clip_len)
    return a, b


def watermark_window(o: RenderOpts, clip_len: float) -> tuple:
    return mark_window({"start": o.wm_start, "end": o.wm_end}, clip_len)


def _mark_fs(m: dict) -> int:
    return int(44 * max(20, min(400, int(m.get("size", 100)))) / 100 * FONT_SCALE.get(m.get("font", "classic"), 1.0))


def _mark_dialogue(m: dict, W: int, H: int, clip_len: float) -> str:
    """One text layer as an ASS event: its font, colour, opacity, outline or box and rotation
    inline, so any number of layers can share two styles (Mark: outline, MarkBox: box)."""
    text = str(m.get("text") or "").strip()[:60]
    fam, bold, _ = _font(m.get("font", "classic"))
    fs = _mark_fs(m)
    alpha = _wm_alpha(int(m.get("opacity", 70)))
    tags = f"\\fn{fam}\\fs{fs}\\b{1 if bold else 0}\\1c{_ass_color(m.get('color') or '#FFFFFF')}\\alpha{alpha}"
    if m.get("box"):
        style, pad = "MarkBox", max(6, fs // 4)
        tags += f"\\3c{_ass_color(m.get('box_color') or '#141414')}\\bord{pad}\\shad0"
    else:
        style, pad = "Mark", 0
        tags += "\\3c&H000000&\\4c&H000000&" + (f"\\bord{max(2, fs // 14)}\\shad1" if m.get("outline", True) else "\\bord0\\shad0")
    rot = int(m.get("rotation") or 0)
    if rot:
        tags += f"\\frz{-rot}"
    pos = _mpos(m)
    if pos == "free":
        # centred on its point; the size is estimated so the text stays inside the frame
        ew = min(W, int(len(text) * fs * 0.62) + 2 * pad)
        eh = int(fs * 1.25) + 2 * pad
        x, y = mark_anchor(m, W, H, ew, eh)
        an, x, y = 5, x + ew // 2, y + eh // 2
    else:
        # with a zero-size box the anchor is the point the text hangs from (\an picks which corner)
        an = {"tl": 7, "tr": 9, "bl": 1, "br": 3, "c": 5}[pos]
        x, y = mark_anchor(m, W, H)
    a, b = mark_window(m, clip_len)
    return f"Dialogue: 2,{_ass_time(a)},{_ass_time(b)},{style},,0,0,0,,{{\\an{an}\\pos({x},{y}){tags}}}{_ass_escape(text)}"


def _wm_alpha(opacity: int) -> str:
    return f"&H{int(round(255 * (1 - max(5, min(100, opacity)) / 100))):02X}&"


def _shifted(words: list, o: "RenderOpts") -> list:
    """Caption words moved by Text › Timing (burned-in captions and the .srt alike)."""
    off = max(-1000, min(1000, int(getattr(o, "caption_offset", 0) or 0))) / 1000
    if not off:
        return words
    return [dict(w, start=max(0.0, w["start"] + off), end=max(0.05, w["end"] + off)) for w in words]


def _join_lines(parts: list, texts: list, fs: int, spacing: int, width: int, upper: bool) -> str:
    """The words of one caption. At normal line spacing libass wraps them itself; with more
    spacing, a caption too wide for one line is split into two balanced lines here, with a
    spacer line (its height is the extra space) between them."""
    if spacing <= 100 or len(parts) < 2:
        return " ".join(parts)
    glyph = 0.62 if upper else 0.54                    # average character width, share of the font size
    chars = sum(len(t) for t in texts) + len(texts) - 1
    if chars * fs * glyph <= width:
        return " ".join(parts)
    left = [sum(len(t) + 1 for t in texts[:k]) for k in range(1, len(texts))]
    k = 1 + min(range(len(left)), key=lambda i: abs(left[i] - chars / 2))
    gap = max(1, int(fs * (spacing - 100) / 100))
    return " ".join(parts[:k]) + f"\\N{{\\fs{gap}}}\\h{{\\fs{fs}}}\\N" + " ".join(parts[k:])


def title_border(o: "RenderOpts", title_fs: int) -> int:
    """The black border of an Outline or Yellow title, in pixels: a tenth of the text's size (at least 4)
    at 100%, scaled by Text tab › Title › Outline (0-300%). The yellow one starts a little thicker, so the
    yellow reads on bright footage."""
    k = max(0, min(300, int(getattr(o, "title_outline", 100) or 0))) / 100
    base = max(4, title_fs // 10) * (1.25 if o.title_style == "yellow" else 1.0)
    return int(round(base * k))


TITLE_DEFAULT_Y = 0.05              # RenderOpts.title_y: a title still here was never dragged
TITLE_TOP = 0.03                    # the platform's own buttons sit above this


def title_top(title: str, o: RenderOpts, H: int, fs: int, wrap: int, pad: int, ty: int,
              face: Optional[dict], caption_y: float) -> int:
    """Where the title's top goes: ty, unless the title is still where it starts and would cover the face of
    a clip that follows the streamer (4.45). It then sits above the face if there's room under the platform's
    top buttons, else below it if that stays clear of the captions, else where it was.
    face: framing's {"face_y", "face_h"}, fractions of the frame's height (a whole-frame crop keeps it)."""
    if not face or abs(float(o.title_y) - TITLE_DEFAULT_Y) > 1e-6:
        return ty
    try:
        fy, fh = float(face.get("face_y")), float(face.get("face_h"))
    except (TypeError, ValueError):
        return ty
    if not (0 < fy < 1 and 0 < fh < 1):
        return ty
    font = o.title_font if o.title_font in FONT_GLYPH else "classic"
    lines = max(1, min(3, math.ceil(text_width(title.strip(), fs, font) / max(1, wrap))))
    tall = int(lines * fs * 1.15 + 2 * pad)
    top, bot = (fy - fh * 0.9) * H, (fy + fh * 0.6) * H          # the face, with hair above and chin below
    if ty + tall <= top or ty >= bot:
        return ty                                                  # clear of it already
    above = int(top - 24 - tall)
    if above >= TITLE_TOP * H:
        return above
    below = int(bot + 24)
    if below + tall <= caption_y - 150:
        return below
    return ty


def build_ass(words: list, o: RenderOpts, center_y: float, clip_len: float, title: str = "",
              face: Optional[dict] = None) -> str:
    """face: where a followed face sits ({"face_y", "face_h"}), so a title that hasn't been moved isn't put on it."""
    W, H = out_size(o)
    hi = _ass_color(o.caption_color)
    cfont, cbold, _ = _font(o.caption_font)
    tfont, tbold, _ = _font(o.title_font)
    style = o.caption_style if o.caption_style in CAPTION_STYLES else "pop"
    tstyle = o.title_style if o.title_style in TITLE_STYLES else "box"
    base_fs = int(86 * max(50, min(MAX_TEXT_SIZE["caption"], o.caption_size)) / 100 * FONT_SCALE.get(o.caption_font, 1.0))
    title_fs = int(64 * max(50, min(MAX_TEXT_SIZE["title"], o.title_size)) / 100 * FONT_SCALE.get(o.title_font, 1.0))
    ob = lambda x: max(0, int(round(x * max(0, min(300, getattr(o, "cap_outline", 100))) / 100)))  # noqa: E731
    shd = lambda x: max(0, int(round(x * max(0, min(300, getattr(o, "cap_shadow", 100))) / 100)))  # noqa: E731
    bord = ob(max(4, base_fs // 12))
    white, black, dark = "&H00FFFFFF", "&H00000000", "&H00141414"
    if style == "clean":
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H64000000,{cbold},0,0,0,100,100,1,0,1,0,{shd(4)},5,70,70,0,1"
    else:
        cap_style = f"Style: Cap,{cfont},{base_fs},{white},{white},{black},&H96000000,{cbold},0,0,0,100,100,1,0,1,{bord},{shd(3)},5,70,70,0,1"
    tx = int(min(max(o.title_x, 0.3), 0.7) * W)
    th = min(tx, W - tx) - 90           # wrap the title inside the frame around its own centre
    if title.strip() and getattr(o, "title_fit", True):
        title_fs = fit_text(title.strip(), title_fs, 2 * th, o.title_font if o.title_font in FONT_GLYPH else "classic",
                            TITLE_GROW, 3)
    tpad = max(8, title_fs // 4)
    if tstyle in ("outline", "yellow"):
        tcol = _ass_color(TITLE_YELLOW if tstyle == "yellow" else getattr(o, "title_color", "#FFFFFF") or "#FFFFFF").rstrip("&")
        tob = title_border(o, title_fs)
        t_style = f"Style: Title,{tfont},{title_fs},{tcol},{tcol},{black},&H96000000,{tbold},0,0,0,100,100,0,0,1,{tob},3,8,90,90,0,1"
    else:
        fill, boxc = {"box": (dark, white), "dark": (white, "&H1A141414"), "accent": (dark, hi.rstrip("&"))}[tstyle]
        t_style = f"Style: Title,{tfont},{title_fs},{fill},{fill},{boxc},{boxc},{tbold},0,0,0,100,100,0,0,3,{tpad},0,8,90,90,0,1"
    # watermark text layers: each sets its own font, size, colours and opacity inline
    mark_style = (f"Style: Mark,{cfont},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{cbold},"
                  f"0,0,0,100,100,0,0,1,3,1,7,0,0,0,1\n"
                  f"Style: MarkBox,{cfont},44,&H00FFFFFF,&H00FFFFFF,&H00141414,&H00141414,{cbold},"
                  f"0,0,0,100,100,0,0,3,11,0,7,0,0,0,1")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cap_style}
{t_style}
{mark_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for m in marks_of(o):
        if m.get("kind") != "image" and str(m.get("text") or "").strip():
            lines.append(_mark_dialogue(m, W, H, clip_len))
    if title.strip():
        ty = int(min(max(o.title_y, 0.0), 0.92) * H) + (tpad if tstyle not in ("outline", "yellow") else 0)
        ty = title_top(title, o, H, title_fs, 2 * th, tpad, ty, face, center_y)
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(clip_len)},Title,,{tx - th},{W - tx - th},0,,"
                     f"{{\\an8\\pos({tx},{ty})}}{_ass_escape(title.strip())}")

    x = int(min(max(o.cap_x if o.caption_pos == "custom" else 0.5, 0.3), 0.7) * W)
    half = min(x, W - x) - 70               # wrap captions inside the frame around their own centre
    ml, mr = x - half, W - x - half
    y = int(center_y)
    motion = motion_of(o)
    if style == "clean" and motion == "pop":
        motion = "none"                    # clean captions never popped
    dur = int(120 * 100 / max(50, min(200, getattr(o, "caption_speed", 100))))    # ms
    words = _shifted(words, o)
    spacing = max(100, min(200, int(getattr(o, "caption_spacing", 100) or 100)))
    chunks = _chunks(caption_words(words, o), o, clip_len)
    for ci, ch in enumerate(chunks):
        texts = [_ass_escape(w["word"].upper() if o.uppercase else w["word"]) for w in ch]
        emph = [o.caption_emph and _emphasised(w["word"]) for w in ch]
        chars = sum(len(t) for t in texts) + len(texts) - 1
        # shrink long chunks so they stay on one or two lines
        fs = base_fs if chars <= 16 else max(int(base_fs * 0.75), int(base_fs * 18 / chars))
        if getattr(o, "caption_fit", True):
            fs = min(fs, fit_text(" ".join(w["word"].upper() if o.uppercase else w["word"] for w in ch), fs, 2 * half,
                                  o.caption_font if o.caption_font in FONT_GLYPH else "classic", 1.0, 2))
        chunk_end = ch[-1]["end"]
        if ci + 1 < len(chunks):
            chunk_end = min(max(chunk_end, ch[-1]["end"] + 0.3), chunks[ci + 1][0]["start"])
        else:
            chunk_end = ch[-1]["end"] + 0.4
        chunk_end = min(chunk_end, clip_len)
        for wi, w in enumerate(ch):
            st = max(0.0, w["start"])
            en = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
            if en <= st:
                en = st + 0.08
            parts = []
            for k, t in enumerate(texts):
                if emph[k] and k != wi and style in ("pop", "glow", "box", "clean"):
                    t = f"{{\\c{hi}}}{t}{{\\c{white}&}}"          # a strong word stays in the highlight colour
                if motion == "reveal" and k > wi:
                    parts.append(f"{{\\alpha&HFF&}}{t}{{\\alpha&H00&}}")   # not said yet: room kept, nothing shown
                    continue
                if k == wi and motion in ("word", "bounce"):
                    end_sc = 112 if style == "glow" else 100
                    if motion == "word":
                        grow = f"\\fscx132\\fscy132\\t(0,{dur},\\fscx{end_sc}\\fscy{end_sc})"
                    else:
                        grow = (f"\\fscx70\\fscy70\\t(0,{dur // 2},\\fscx122\\fscy122)"
                                f"\\t({dur // 2},{dur},\\fscx{end_sc}\\fscy{end_sc})")
                    inner = t
                    if style == "pop":
                        inner = f"{{\\c{hi}}}{t}{{\\c{white}&}}"
                    elif style == "karaoke":
                        inner = f"{{\\c{hi}}}{t}{{\\c{white}&}}"
                    elif style == "box":
                        inner = (f"{{\\3c{hi}\\bord{max(8, int(fs * 0.22))}\\c{dark}&}}{t}"
                                 f"{{\\3c{black}&\\bord{ob(max(4, fs // 12))}\\c{white}&}}")
                    parts.append(f"{{{grow}}}{inner}{{\\fscx100\\fscy100}}")
                    continue
                if style == "pop":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k == wi else t)
                elif style == "glow":
                    parts.append(f"{{\\fscx112\\fscy112}}{t}{{\\fscx100\\fscy100}}" if k == wi else t)
                elif style == "karaoke":
                    parts.append(f"{{\\c{hi}}}{t}{{\\c{white}&}}" if k <= wi else t)
                elif style == "box":
                    parts.append(f"{{\\3c{hi}\\bord{max(8, int(fs * 0.22))}\\c{dark}&}}{t}{{\\3c{black}&\\bord{ob(max(4, fs // 12))}\\c{white}&}}"
                                 if k == wi else t)
                else:
                    parts.append(t)
            lead = ""
            if style == "glow":
                lead = f"\\3c{hi}\\bord{ob(max(4, fs // 12))}\\blur{max(5, fs // 8)}"
            if style == "box":
                lead = f"\\bord{ob(max(4, fs // 12))}"
            pop = ""
            place = f"\\pos({x},{y})"
            if wi == 0:                    # how the chunk comes in
                if motion == "pop":
                    pop = f"{{\\fscx114\\fscy114\\t(0,{dur},\\fscx100\\fscy100)}}"
                elif motion == "fade":
                    lead += f"\\fad({dur * 2},0)"
                elif motion == "slide":
                    place = f"\\move({x},{y + int(fs * 0.6)},{x},{y},0,{dur * 2})"
                    lead += f"\\fad({dur * 2},0)"
            lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Cap,,{ml},{mr},0,,"
                         f"{{\\an5{place}\\fs{fs}{lead}}}{pop}{_join_lines(parts, texts, fs, spacing, 2 * half, o.uppercase)}")
    return header + "\n".join(lines) + "\n"


# ---- encoders --------------------------------------------------------------

_encoder_cache: dict = {}

RENDER_THREADS: list = []         # ["-threads", "N"] when Settings › Performance asks to keep the PC responsive
RENDER_PRIORITY = [0]             # Windows priority class for ffmpeg (BELOW_NORMAL when keeping the PC responsive)
_HW = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                    "-cq", "21", "-b:v", "0", "-profile:v", "high"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                  "-qp_i", "20", "-qp_p", "22", "-qp_b", "24"]),
]
# 4.36: H.265 on the GPU too (it was always x265 on the CPU, the slowest way to make a clip)
_HW_HEVC = [
    ("hevc_nvenc", ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "25", "-b:v", "0",
                    "-tag:v", "hvc1"]),
    ("hevc_qsv", ["-c:v", "hevc_qsv", "-preset", "medium", "-global_quality", "25", "-tag:v", "hvc1"]),
    ("hevc_amf", ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "24", "-qp_p", "26",
                  "-tag:v", "hvc1"]),
]
CPU_ENCODERS = {"libx264", "libx265", "libvpx-vp9"}
# GPU encodes at once: NVIDIA's consumer cards open 3 to 8 sessions (by driver), and one more fails to start.
# Renders past this encode on the CPU, side by side, instead of failing (and turning the GPU off, before 4.36)
HW_SLOTS = 3
_hw_slots = threading.BoundedSemaphore(HW_SLOTS)
_hw_fails = {"n": 0}
# FFmpeg's words when the GPU is busy rather than unusable: too many encodes, out of memory for a moment
_HW_BUSY = re.compile(r"OpenEncodeSession|incompatible client key|out of memory|No capable devices found|"
                      r"too many|session limit|resource temporarily|device busy|MFX_ERR_DEVICE_BUSY", re.I)
# decode the source on the GPU (D3D11, CUDA, QSV...: FFmpeg picks), frames back in memory for the filters
HW_DECODE = ["-hwaccel", "auto"]


def hw_failed(name: str, err: str) -> bool:
    """A GPU encode failed: True when the GPU should be left alone for the rest of this session. One failure,
    or the GPU being busy, isn't that: the render is redone on the CPU and the next one tries the GPU again.
    Before 4.36 any failure turned the GPU off until Clip Radar restarted."""
    if _HW_BUSY.search(err or ""):
        return False
    _hw_fails["n"] += 1
    if _hw_fails["n"] >= 2:
        _encoder_cache.update(hw=None, hevc=None)
        return True
    return False


def hw_worked() -> None:
    _hw_fails["n"] = 0


def _rate_args(o: RenderOpts) -> list:
    kb = int(getattr(o, "vbitrate", 0) or 0)
    return ["-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k"] if kb > 0 else []


def video_encoder(o: RenderOpts) -> tuple:
    """(name, args). H.264 tries GPU encoders once (fast) and falls back to x264. H.265 and VP9
    (for .webm) use the CPU encoders. A bitrate from the preset replaces constant quality."""
    vc = getattr(o, "vcodec", "h264")
    if vc == "vp9" or getattr(o, "container", "mp4") == "webm":
        kb = int(getattr(o, "vbitrate", 0) or 0)
        q = ["-b:v", f"{kb}k"] if kb else ["-crf", str(min(63, o.crf + 12)), "-b:v", "0"]
        return "libvpx-vp9", ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", *q]
    if vc == "hevc":
        x_pre, dq, _nv = SPEEDS.get(getattr(o, "speed", "balanced"), SPEEDS["balanced"])
        q = _rate_args(o) or ["-crf", str(o.crf + 4 + dq)]
        x265 = ("libx265", ["-c:v", "libx265", "-preset", x_pre, *q, "-tag:v", "hvc1"])
        hw = _probe_hw("hevc", _HW_HEVC) if o.hw_encode else None
        if not hw:
            return x265
        return _with_rate(hw, _rate_args(o), _nv)
    rate = _rate_args(o)
    x_pre, dq, _nv = SPEEDS.get(getattr(o, "speed", "balanced"), SPEEDS["balanced"])
    crf = min(max(o.crf + dq, 12), 36)
    x264 = ("libx264", ["-c:v", "libx264", "-preset", x_pre, *(rate or ["-crf", str(crf)]),
                        "-profile:v", "high"])
    if not o.hw_encode:
        return x264
    hw = _probe_hw("hw", _HW)
    return _with_rate(hw, rate, _nv) if hw else x264


def _probe_hw(key: str, choices: list):
    """The first of choices this PC's FFmpeg and GPU can encode with (name, args), checked once a session."""
    if key not in _encoder_cache:
        _encoder_cache[key] = None
        if "listing" not in _encoder_cache:
            try:
                _encoder_cache["listing"] = run([ffmpeg(), "-hide_banner", "-encoders"], timeout=20).stdout
            except Exception:
                _encoder_cache["listing"] = ""
        for name, args in choices:
            if name not in _encoder_cache["listing"]:
                continue
            try:
                run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=1080x1920:d=0.2",
                     "-pix_fmt", "yuv420p", *args, "-f", "null", "-"], timeout=30)
                _encoder_cache[key] = (name, args)
                break
            except Exception:
                continue
    return _encoder_cache[key]


def _with_rate(hw: tuple, rate: list, nv_preset: str) -> tuple:
    name, args = hw
    if rate:          # a target bitrate: swap the GPU encoder's quality setting for it
        keep, skip = [], {"-cq", "-global_quality", "-qp_i", "-qp_p", "-qp_b", "-b:v", "-rc"}
        i = 0
        while i < len(args):
            if args[i] in skip:
                i += 2
                continue
            keep.append(args[i])
            i += 1
        return name, keep + rate
    if name.endswith("_nvenc"):       # the effort setting picks NVENC's preset too (p3 / p5 / p7)
        return name, [nv_preset if a == "p5" else a for a in args]
    return name, args


def write_srt(words: list, o: "RenderOpts", path: str, clip_len: float):
    """Captions as a subtitle file next to the video (times relative to the clip)."""
    def ts(t):
        t = max(0.0, t)
        h, m, sec = int(t // 3600), int(t % 3600 // 60), t % 60
        return f"{h:02d}:{m:02d}:{int(sec):02d},{int(round((sec - int(sec)) * 1000)) % 1000:03d}"
    lines = []
    chunks = _chunks(caption_words(_shifted(words, o), o), o, clip_len)
    for i, ch in enumerate(chunks, 1):
        end = min(clip_len, chunks[i][0]["start"] if i < len(chunks) else ch[-1]["end"] + 0.4)
        text = " ".join(w["word"] for w in ch)
        lines.append(f"{i}\n{ts(ch[0]['start'])} --> {ts(max(end, ch[0]['start'] + 0.3))}\n{text.upper() if o.uppercase else text}\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


DARK_LUMA = 38          # average brightness (0-255) under this is a dim picture ...
SEEN_LUMA = 50          # ... and it's too dark to watch when under SEEN_SHARE of it is brighter than this. Dim on average
SEEN_SHARE = 0.03       # isn't enough: a streamer lit by their screen in a dark room averages about 31, a night scene
                        # with a HUD 18, and both are easy to watch (10% and 4% of the picture bright). Nearly black is 0-1%
BLUR_SHARP = 20.0      # edge strength (Laplacian variance, 192 px wide) under this is a smear (sharp video: 200-1500)


def picture_check(video: str, start: float, end: float, samples: int = 5) -> dict:
    """How much of a clip is too dark or too blurry to watch, from a few frames of the source (before
    captions and titles are drawn on, so they don't count as detail). {"dark": 0-1, "blurry": 0-1}."""
    w, h = 192, 108
    dark = blurry = got = 0
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        try:
            p = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.2f}", "-i", video,
                                "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                               capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        except Exception:
            continue
        if len(p.stdout) != w * h:
            continue
        g = np.frombuffer(p.stdout, np.uint8).reshape(h, w).astype(np.float32)
        got += 1
        if float(g.mean()) < DARK_LUMA and float((g > SEEN_LUMA).mean()) < SEEN_SHARE:
            dark += 1
            continue                                   # a dark frame is low-detail anyway: count it once
        top = float(np.percentile(g, 99))
        if top < 200:
            g = g * (255.0 / max(top, 32.0))           # a dim picture's edges are as sharp as a bright one's, just fainter
        lap = g[1:-1, 2:] + g[1:-1, :-2] + g[2:, 1:-1] + g[:-2, 1:-1] - 4 * g[1:-1, 1:-1]
        if float(lap.var()) < BLUR_SHARP:
            blurry += 1
    if not got:
        return {"dark": 0.0, "blurry": 0.0}
    return {"dark": round(dark / got, 2), "blurry": round(blurry / got, 2)}


def audio_peak(path: str) -> Optional[float]:
    """Loudest point of a file's audio in dB (None if it has no audio track)."""
    try:
        if not probe(path)["has_audio"]:
            return None
        out = subprocess.run([ffmpeg(), "-v", "info", "-nostdin", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW).stderr     # ffmpeg echoes the file name in UTF-8
        m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", out)
        return -200.0 if m and m.group(1) == "-inf" else float(m.group(1)) if m else None
    except Exception:
        return None


def read_through(path: str, audio: bool) -> tuple:
    """Read a finished render to its end in one pass: every video packet (copied, not decoded) and, with audio,
    the whole sound through volumedetect. Returns (plays, peak dB or None). plays is False when FFmpeg hits an error
    on the way: a render cut short by a crash or a full disk keeps its +faststart header and the right length, and
    FFmpeg exits 0 there unless -xerror makes it stop. Before 4.49 this was two launches (decoding the last 1.5 s,
    which missed that, then audio_peak), about five times the CPU (docs/audits/speed-audit-2026-11-08.md)."""
    cmd = [ffmpeg(), "-v", "info", "-xerror", "-nostdin", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    if audio:
        cmd += ["-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)   # ffmpeg echoes the file name in UTF-8
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    if p.returncode != 0:
        return False, None
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", p.stderr)
    return True, (-200.0 if m and m.group(1) == "-inf" else float(m.group(1)) if m else None)


def _crop_failed(err: str) -> bool:
    """FFmpeg couldn't set up a crop of the source (it doesn't fit the frames that arrived, or its
    expression was refused)."""
    low = err.lower()
    return "parsed_crop" in low and ("failed to configure input pad" in low or "invalid too big" in low)


def _graph_failed(err: str) -> bool:
    """FFmpeg couldn't build the filter graph. That happens before any frame reaches the encoder, so
    the same graph on another encoder fails the same way."""
    low = err.lower()
    return _crop_failed(err) or "error initializing complex filter" in low or (
        "[parsed_" in low and "failed to configure" in low)


LAYOUT_NAMES = {"split": "Cam + game", "full": "Whole frame", "cam": "Facecam only", "blur": "Blur-fit",
                "square": "Square blur"}


def _first_reason(err: str) -> str:
    """The line that says why FFmpeg failed: the first from a filter, else the last line."""
    lines = [ln.strip() for ln in err.strip().splitlines() if ln.strip()]
    why = [ln for ln in lines if "[Parsed_" in ln]
    return (why or lines or ["no error output"])[0][:200]


def _frame_luma(path: str, at: float = 0.0, ignore_editlist: bool = False) -> Optional[float]:
    """Average brightness (0-255) of the frame at `at` seconds, or None if it can't be read.
    ignore_editlist reads the file the way tools that skip MP4 edit lists do (TikTok's cover picker)."""
    cmd = [ffmpeg(), "-v", "error", "-nostdin"]
    if ignore_editlist:
        cmd += ["-ignore_editlist", "1"]
    if at > 0:
        cmd += ["-ss", f"{at:.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or len(p.stdout) < 1024:
        return None
    return sum(p.stdout[:1024]) / 1024


def _start_lumas(path: str, at: float, editlist: bool) -> Optional[tuple]:
    """(frame at `at`, first frame, first frame with the edit list ignored or None) in one FFmpeg run, the same frames
    _frame_luma reads in three. None if that run fails."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="vc_luma_")
    try:
        outs = [os.path.join(tmp, f"{n}.gray") for n in range(3 if editlist else 2)]
        cmd = [ffmpeg(), "-v", "error", "-nostdin", "-i", path]
        if editlist:
            cmd += ["-ignore_editlist", "1", "-i", path]
        graph = (f"[0:v]split=2[r][f];[r]trim=start={at:.3f},scale=32:32,format=gray[o0];"
                 "[f]scale=32:32,format=gray[o1]" + (";[1:v]scale=32:32,format=gray[o2]" if editlist else ""))
        cmd += ["-filter_complex", graph]
        for n, out in enumerate(outs):
            cmd += ["-map", f"[o{n}]", "-frames:v", "1", "-f", "rawvideo", out]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW).returncode != 0:
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None
        vals = []
        for out in outs:
            try:
                with open(out, "rb") as f:
                    raw = f.read()
            except OSError:
                raw = b""
            vals.append(sum(raw[:1024]) / 1024 if len(raw) >= 1024 else None)
        return vals[0], vals[1], (vals[2] if editlist else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def black_start(path: str, length: float) -> bool:
    """The clip opens on a black frame although the picture right after it isn't black: what TikTok
    shows as a black cover. Checked both as players read the file and with its edit list ignored.
    One FFmpeg run since 4.49 (three before: efficiency audit 2026-11-08, F5); the three runs are the fallback."""
    at = min(0.4, max(0.1, length / 4))
    editlist = path.lower().endswith((".mp4", ".mov", ".m4v"))
    got = _start_lumas(path, at, editlist)
    if got is None:
        ref = _frame_luma(path, at)
        if ref is None or ref < 40:
            return False
        firsts = [_frame_luma(path)] + ([_frame_luma(path, ignore_editlist=True)] if editlist else [])
    else:
        ref, firsts = got[0], [got[1]] + ([got[2]] if editlist else [])
    if ref is None or ref < 40:
        return False                    # the clip really starts dark (or can't be read): nothing to fix
    first = min((f for f in firsts if f is not None), default=None)
    return first is not None and first < 24 and ref - first > 25


def _ffmpeg_reason(err: str) -> str:
    """The end of FFmpeg's errors, plus the filter lines that say why (they come before the
    generic 'Error reinitializing filters' and encoder lines, so the last few alone lose them)."""
    lines = err.strip().splitlines()
    why = [ln for ln in lines[:-10] if "[Parsed_" in ln][:3]
    return "\n".join(why + lines[-10:])


SPANS_INLINE = 24      # more muted spans than this go to a commands file (mute_commands), not the command line


def mute_commands(spans: list) -> str:
    """FFmpeg asendcmd commands that silence each (start, end, mode) span, and for "bleep" turn the tone
    up over it. Overlapping spans are joined first (a bleep wins over a mute), so one span's end can't
    switch the sound back on inside another."""
    merged: list = []
    for a, b, m in sorted((float(a), float(b), m) for a, b, m in spans):
        if merged and a <= merged[-1][1] + 0.01:
            pa, pb, pm = merged[-1]
            merged[-1] = (pa, max(pb, b), "bleep" if "bleep" in (pm, m) else pm)
        else:
            merged.append((a, b, m))
    out = []
    for a, b, m in merged:
        cmds = ["[enter] volume@cm volume 0", "[leave] volume@cm volume 1"]
        if m == "bleep":
            cmds += ["[enter] volume@bp volume 0.6", "[leave] volume@bp volume 0"]
        out.append(f"{a:.3f}-{b:.3f} " + ", ".join(cmds) + ";")
    return "\n".join(out) + "\n"


def mute_graph(spans: list, length: float, tail: str, ain: str = "0:a:0", cmd_name: str = "mute.cmd") -> tuple:
    """The audio part of a filter graph that silences (start, end, mode) spans of input `ain`, lays a 1 kHz
    tone over the "bleep" ones, runs `tail` (audio_filter) and ends in [a]. Returns (graph, commands): commands
    is what to write to the file `cmd_name` in FFmpeg's working folder, or "" when the spans fit in the graph."""
    if len(spans) > SPANS_INLINE:
        # many spans (a whole VOD's muting): written as timed commands to a file the graph reads, since
        # thousands of between() terms would pass Windows' 32,767-character command line limit
        if any(m == "bleep" for _, _, m in spans):
            return (f"[{ain}]asendcmd=f={cmd_name},volume@cm=1[am];"
                    f"sine=frequency=1000:sample_rate=48000:duration={length:.3f},volume@bp=0[bp];"
                    f"[am][bp]amix=inputs=2:duration=first:normalize=0,{tail}[a]"), mute_commands(spans)
        return f"[{ain}]asendcmd=f={cmd_name},volume@cm=1,{tail}[a]", mute_commands(spans)
    on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b, _ in spans)
    beeps = [(a, b) for a, b, m in spans if m == "bleep"]
    if beeps:
        on_b = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in beeps)
        return (f"[{ain}]volume=0:enable='{on}'[am];"
                f"sine=frequency=1000:sample_rate=48000:duration={length:.3f},volume=0.6,"
                f"volume=0:enable='not({on_b})'[bp];"
                f"[am][bp]amix=inputs=2:duration=first:normalize=0,{tail}[a]"), ""
    return f"[{ain}]volume=0:enable='{on}',{tail}[a]", ""


def render_clip(video: str, clip_start: float, clip_end: float, out_path: str, o: RenderOpts,
                title: str = "", log: Log = _noop, progress: Progress = _noop,
                check: Check = _noop, words: Optional[list] = None,
                censor: Optional[list] = None, censor_mode: str = "off", framing: Optional[dict] = None,
                warn: Log = _noop, diag: Optional[dict] = None, audio_src: str = "") -> str:
    """words: transcript (times relative to clip_start) to caption with; transcribed here if None.
    censor: [(start, end)] spans (relative) to mute ("mute") or bleep ("bleep").
    audio_src: a sound file to use instead of the video's own sound, starting where the clip starts
    (Export VOD: the sound with its music removed, voicekeep.keep_voice).
    warn: told when the clip came out differently from what was asked (a layout that had to fall back).
    diag: filled in with what FFmpeg was asked and said (cmd, encoder, exit, stderr, fallback), for the
    render's attempt history, whether it worked or not."""
    diag = diag if diag is not None else {}
    info = probe(video)
    clip_start = max(0.0, clip_start)
    if not clip_end > clip_start:
        raise RenderInputError("source_range", "This clip ends before it starts.")
    if info["duration"] and clip_start > info["duration"] - 0.5:
        # FFmpeg would "succeed" here with an empty file
        raise RenderInputError("source_range", f"This clip starts at {fmt_ts(clip_start)}, after the end of the video "
                                               f"({fmt_ts(info['duration'])}). The recording may have been replaced "
                                               "by a shorter one.")
    clip_end = min(info["duration"] or clip_end, clip_end)
    length = max(1.0, clip_end - clip_start)
    fr = framing or {}
    work = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(work, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    # ASCII names: the subtitles filter can't take quotes or other specials from a clip title
    ass_name = f"vc_{tag}.ass"
    ass_path = os.path.join(work, ass_name)
    font_dir_name = f"vc_fonts_{tag}"
    font_dir = os.path.join(work, font_dir_name)

    if words is not None:
        words = list(words) if o.captions else []
    else:
        words = []
        if o.captions and info["has_audio"]:
            if not whisper.whisper_available():
                log("Captions skipped: faster-whisper isn't installed (run install.bat).")
            else:
                # decode with our ffmpeg straight to 16 kHz mono float samples; Whisper takes the array
                # directly, so captions don't depend on PyAV's file-reading API
                audio = whisper.extract_audio(video, clip_start, length)
                progress(0.08)
                check()
                if audio is None or audio.size < 1600:
                    log("Captions skipped: couldn't read this clip's audio.")
                else:
                    log("Transcribing speech for captions...")
                    try:
                        # Whisper can take a while on a long clip: move the bar as it goes
                        words = whisper.transcribe(audio, o.whisper_model, o.language, log,
                                                   progress=lambda f: progress(0.08 + 0.22 * f))
                    except Exception as e:
                        log(f"Captions skipped: {str(e)[:200]}")
                        words = []
                if not words:
                    log("No speech found in this clip.")
    progress(0.3)
    check()

    use_ass = bool(words) or bool(title.strip()) or any(m.get("kind") != "image" and str(m.get("text") or "").strip()
                                                        for m in marks_of(o))
    sub = ""
    if use_ass:
        # bare filenames + cwd=work sidestep Windows drive-letter escaping in the subtitles filter
        sub = f"subtitles=filename={ass_name}"
        need = fonts_for(o)
        if need:
            os.makedirs(font_dir, exist_ok=True)
            for fp in need:
                shutil.copy2(fp, font_dir)
            sub += f":fontsdir={font_dir_name}"

    # censoring: silence flagged words, and for "bleep" lay a 1 kHz tone over them
    # spans are (start, end) using censor_mode, or (start, end, mode) for a fix made on one clip
    wm_imgs = [m for m in marks_of(o) if m.get("kind") == "image" and m.get("image") and os.path.isfile(m["image"])]
    ain = f"{len(wm_imgs) + 1}:a:0" if audio_src else "0:a:0"       # inputs: the video, image watermarks, then audio_src
    af = ""
    spans = []
    for sp in censor or []:
        a, b = float(sp[0]), float(sp[1])
        m = sp[2] if len(sp) > 2 else censor_mode
        if m in ("mute", "bleep") and b > a and a < length:
            spans.append((max(0.0, a), min(length, b), m))
    cmd_path = os.path.join(work, f"vc_{tag}.cmd")
    if spans and info["has_audio"]:
        graph, commands = mute_graph(spans, length, audio_filter(o), ain, f"vc_{tag}.cmd")
        if commands:
            with open(cmd_path, "w", encoding="utf-8") as f:
                f.write(commands)
        af = ";" + graph

    def filters(lo: RenderOpts, path: Optional[list]) -> str:
        """The whole filter_complex for layout lo (captions, censoring, watermark, export size)."""
        vf, cap_y = build_filter(info, lo, path, fr)
        if use_ass:
            with open(ass_path, "w", encoding="utf-8") as f:
                # a followed face (whole frame): the title isn't put on it (title_top)
                face = fr if effective_layout(lo) == "full" and path else None
                f.write(build_ass(words, o, cap_y, length, title, face))
            vf = vf.replace("format=yuv420p[v]", f"format=yuv420p,{sub}[v]")
        vf = vf + af
        if wm_imgs:
        # image watermark layers: overlaid on the finished frame (after captions), scaled with the frame,
        # at the same anchor as a text layer; the source video isn't changed
            W_, H_ = out_size(o)
            i = vf.rfind("[v]")
            chain, prev = vf[:i] + "[vm0];", "vm0"
            for k, m in enumerate(wm_imgs):
                mw = _even(W_ * 0.2 * max(20, min(400, int(m.get("size", 100)))) / 100)
                a, b = mark_window(m, length)
                op = max(5, min(100, int(m.get("opacity", 70)))) / 100
                rot = int(m.get("rotation") or 0)
                turn = (f",rotate=a={rot * math.pi / 180:.5f}:c=none:ow='rotw({rot * math.pi / 180:.5f})'"
                        f":oh='roth({rot * math.pi / 180:.5f})'") if rot else ""
                # x/y from the anchor for an unknown size: ffmpeg's overlay evaluates W/H/w/h itself, so
                # pass expressions that keep the layer inside the frame
                pos = _mpos(m)
                mg = int(max(0, min(200, float(m.get("margin", 40)))) * W_ / 1080)
                if pos == "free":
                    xe, ye = f"{float(m.get('x', .5)) * W_:.1f}-w/2", f"{float(m.get('y', .5)) * H_:.1f}-h/2"
                elif pos == "c":
                    xe, ye = "(W-w)/2", "(H-h)/2"
                else:
                    xe = str(mg) if pos[1] == "l" else f"W-w-{mg}"
                    ye = str(int(H_ * WM_TOP)) if pos[0] == "t" else f"{int(H_ * WM_BOTTOM)}-h"
                last = k == len(wm_imgs) - 1
                out = "v" if last else f"vm{k + 1}"
                chain += (f"[{k + 1}:v]scale={mw}:-2:flags=lanczos,format=rgba,colorchannelmixer=aa={op:.2f}{turn}[wm{k}];"
                          f"[{prev}][wm{k}]overlay=x='max(0,min({xe},W-w))':y='max(0,min({ye},H-h))':"
                          f"enable='between(t,{a:.3f},{b:.3f})'" + (",format=yuv420p" if last else "") + f"[{out}]"
                          + ("" if last else ";"))
                prev = out
            vf = chain + vf[i + 3:]
        # export size below the shape's full size: scale the finished frame (captions included)
        fw, fh = final_size(o)
        if (fw, fh) != out_size(o):
            i = vf.rfind("[v]")
            vf = vf[:i] + f",scale={fw}:{fh}:flags=lanczos[v]" + vf[i + 3:]
        return vf

    try:
        vf = filters(o, fr.get("path") or None)
    except Exception:
        shutil.rmtree(font_dir, ignore_errors=True)
        for p_ in (ass_path, cmd_path):
            try:
                os.remove(p_)
            except OSError:
                pass
        raise
    cont = getattr(o, "container", "mp4") if getattr(o, "container", "mp4") in ("mp4", "mov", "webm") else "mp4"
    if cont == "webm":
        acodec = ["-c:a", "libopus", "-b:a", f"{int(getattr(o, 'abitrate', 128))}k"]
    else:
        acodec = ["-c:a", "aac", "-b:a", f"{int(getattr(o, 'abitrate', 192))}k"]
    tmp_name = f"vc_{tag}.part.{cont}"
    tmp_path = os.path.join(work, tmp_name)

    def encode(enc_args: list, hwdec: bool = False):
        cmd = [ffmpeg(), "-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
               *(HW_DECODE if hwdec else []), "-ss", f"{clip_start:.3f}", "-t", f"{length:.3f}", "-i", video]
        for m in wm_imgs:
            cmd += ["-i", m["image"]]
        if audio_src:
            cmd += ["-t", f"{length:.3f}", "-i", audio_src]
        cmd += ["-filter_complex", vf, "-map", "[v]"]
        if info["has_audio"] and af:
            cmd += ["-map", "[a]", *acodec, "-ar", "48000"]
        elif info["has_audio"]:
            cmd += ["-map", ain, *acodec, "-af", audio_filter(o), "-ar", "48000"]
        cmd += [*enc_args, "-pix_fmt", "yuv420p", "-r", str(output_fps(info, o))]
        if cont != "webm":
            # negative_cts_offsets: the first frame shows at exactly 0:00 even where the edit list is ignored
            # (TikTok's cover picker does; without it the first frame sits one frame in and the cover is black)
            cmd += ["-movflags", "+faststart+negative_cts_offsets"]
        cmd += [*RENDER_THREADS, tmp_name]
        diag["cmd"] = subprocess.list2cmdline(cmd)
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW | RENDER_PRIORITY[0])
        procjob.adopt(p)      # ends with Clip Radar, even if it crashes (Windows)
        # read errors on the side: a full stderr pipe would otherwise freeze ffmpeg mid-render
        err_lines: list = []
        last_output = [time.monotonic()]
        stalled = [False]

        def drain():
            for ln in p.stderr:
                err_lines.append(ln)
                del err_lines[:-60]

        def watch_render():
            # FFmpeg's -progress stream normally updates several times per second.
            # A long silence indicates a hung filter/encoder or a wedged input.
            while p.poll() is None:
                if time.monotonic() - last_output[0] > 120:
                    stalled[0] = True
                    try:
                        p.kill()
                    except OSError:
                        pass
                    return
                time.sleep(2)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        threading.Thread(target=watch_render, daemon=True).start()
        try:
            for line in p.stdout:
                last_output[0] = time.monotonic()
                if line.startswith("out_time_us="):
                    try:
                        t = int(line.split("=")[1]) / 1e6
                        progress(0.3 + 0.7 * min(1.0, max(0.0, t) / length))
                    except ValueError:
                        pass
                try:
                    check()
                except Cancelled:
                    p.kill()
                    raise
        finally:
            p.wait()
            reader.join(timeout=5)
        if stalled[0]:
            err = "".join(err_lines) + "\nFFmpeg made no progress for 120 seconds and was stopped."
            code = p.returncode or -1
        else:
            code, err = p.returncode, "".join(err_lines)
        diag.update(exit=code, stderr=err[-2000:], stalled=stalled[0])
        return code, err

    enc_name, enc_args = video_encoder(o)
    cpu_name, cpu_args = video_encoder(replace(o, hw_encode=False))      # same codec, speed and quality
    slot = False
    if enc_name not in CPU_ENCODERS:
        slot = _hw_slots.acquire(blocking=False)
        if not slot:
            # the GPU is already making HW_SLOTS clips: this one on the CPU, alongside, rather than failing
            diag["fallback"] = f"{HW_SLOTS} renders already on the GPU, used the CPU"
            enc_name, enc_args = cpu_name, cpu_args
    # the GPU decodes the source too when it's allowed to encode (4.36): a 1080p60 or 4K recording's decoding
    # was all on the CPU before
    hwdec = bool(o.hw_encode) and _encoder_cache.get("hwdec", True)
    layout = effective_layout(o)
    following = layout == "full" and "'clip(" in vf          # a crop that moves (framing.x_expr)
    chosen = LAYOUT_NAMES.get(layout, layout) + (", following the face" if following else "")
    diag.update(encoder=enc_name, fallback=diag.get("fallback", ""), layout_used=chosen, hw_decode=hwdec)
    try:
        code, err = encode(enc_args, hwdec)
        if code != 0 and hwdec and not _graph_failed(err):
            # decoding on the GPU didn't work for this video: again with the CPU decoding, same encoder
            log("Decoding on the GPU failed for this video; decoding on the CPU.")
            hwdec = False
            diag.update(hw_decode=False, fallback=(diag["fallback"] + "; " if diag["fallback"] else "")
                        + "GPU decoding failed, decoded on the CPU")
            code, err = encode(enc_args, hwdec)
            if code == 0:
                _encoder_cache["hwdec_fails"] = _encoder_cache.get("hwdec_fails", 0) + 1
                if _encoder_cache["hwdec_fails"] >= 2:
                    _encoder_cache["hwdec"] = False       # twice: this PC's GPU decoding isn't worth trying
        if code != 0 and enc_name not in CPU_ENCODERS and not _graph_failed(err):
            off = hw_failed(enc_name, err)
            log(f"GPU encoder ({enc_name}) failed, re-encoding on CPU" + (" (and for the rest of this session)." if off else "."))
            enc_name, enc_args = cpu_name, cpu_args
            diag.update(encoder=cpu_name, fallback=(diag["fallback"] + "; " if diag["fallback"] else "")
                        + f"{diag['encoder']} failed, used the CPU")
            code, err = encode(enc_args, hwdec)
        elif code == 0 and enc_name not in CPU_ENCODERS:
            hw_worked()
        if code != 0 and _crop_failed(err) and (layout != "blur" or _num(o.game_zoom, 1.0, 1.0, MAX_ZOOM) > 1):
            # Every crop is checked before FFmpeg runs (crop_filter), so this is FFmpeg refusing a crop that
            # fits: a source it can't handle the way it's set up. The first failure is kept as the attempt's
            # (graph, exit code, FFmpeg's words); then the same layout without following the face, and
            # last the whole picture on a blurred background, which has no crop of the source.
            first = {"layout": chosen, "exit": code, "reason": _ffmpeg_reason(err)[-1500:], "filter": vf[:4000]}
            diag["first_failure"] = first
            kept = {k: diag.get(k) for k in ("cmd", "exit", "stderr")}
            tries = []
            if following:
                tries.append((LAYOUT_NAMES["full"] + ", not following the face", lambda: filters(o, None),
                              "This clip couldn't follow the face, so it shows the middle of the picture (or your Focus "
                              "point). Exports › the clip › details has FFmpeg's reason."))
            tries.append((LAYOUT_NAMES["blur"], lambda: filters(replace(o, layout="blur", game_zoom=1.0), None),
                          f"This clip's layout ({LAYOUT_NAMES.get(layout, layout)}) couldn't be built from the video, so "
                          "it shows the whole picture on a blurred background. Exports › the clip › details has "
                          "FFmpeg's reason; please send it to us so this can be fixed."))
            used, failed_vf = "", vf
            for used, graph, msg in tries:
                vf = graph()          # (this also writes the captions for that layout)
                if vf == failed_vf:
                    continue          # the same graph would fail the same way
                log(f"The layout couldn't be built ({_first_reason(err)}); trying {used}.")
                code, err = encode(enc_args, hwdec)
                if code == 0 or not _crop_failed(err):
                    break
            note = f"{chosen} failed (exit {first['exit']}: {_first_reason(first['reason'])})"
            diag["fallback"] = (diag["fallback"] + "; " if diag.get("fallback") else "") + (
                f"{note}, used {used}" if code == 0 else f"{note}, and {used} failed too (exit {code})")
            if code == 0:
                diag["layout_used"] = used
                warn(msg)
            else:
                diag["fallback_stderr"] = err[-1500:]
                diag.update(kept)               # the attempt shows the first failure, not the fallback's
                raise RuntimeError(f"ffmpeg render failed: the layout ({chosen}) couldn't be built, and {used} failed "
                                   f"too.\n{first['reason']}\n\n{used}:\n{_ffmpeg_reason(err)}")
        # Looked at once per file written: before 4.49 this ran twice on every render (black_start was called
        # before the hwdec test, then again on the same file), 3 FFmpeg launches each time, while the clip waited
        black = code == 0 and black_start(tmp_path, length)
        if black and hwdec:
            # first without decoding on the GPU (a decoder that drops the first frame), same encoder
            log("The first frame came out black; decoding on the CPU instead.")
            hwdec = False
            diag.update(hw_decode=False, fallback=(diag.get("fallback", "") + "; " if diag.get("fallback") else "")
                        + "GPU decoding gave a black first frame, decoded on the CPU")
            code, err = encode(enc_args, hwdec)
            black = code == 0 and black_start(tmp_path, length)
        if black:
            # TikTok takes the frame at 0:00 as the cover: a black one there is a black cover
            used = diag.get("encoder", enc_name)
            if used not in CPU_ENCODERS and cont != "webm":
                log(f"The GPU encoder ({used}) made a black first frame; rendering on the CPU instead.")
                _encoder_cache["hw" if used.startswith("h264") else "hevc"] = None   # and for the rest of this session
                _encoder_cache["black_start"] = used
                enc_args = cpu_args
                diag.update(encoder=cpu_name, fallback=(diag.get("fallback", "") + "; " if diag.get("fallback") else "")
                            + f"{used} made a black first frame, used the CPU")
                code, err = encode(enc_args, hwdec)
                black = code == 0 and black_start(tmp_path, length)
            diag["black_start"] = black
            if black:
                warn("This clip's first frame came out black, so TikTok may show a black cover. Pick a cover "
                     "when you upload, and tell us: Exports › the clip › details has what's needed to fix it.")
        if code != 0:
            raise RuntimeError("ffmpeg render failed:\n" + _ffmpeg_reason(err))
        os.replace(tmp_path, out_path)
    finally:
        if slot:
            _hw_slots.release()
        shutil.rmtree(font_dir, ignore_errors=True)
        for p in (tmp_path, ass_path, cmd_path):
            try:
                os.remove(p)
            except OSError:
                pass
    progress(1.0)
    return out_path
