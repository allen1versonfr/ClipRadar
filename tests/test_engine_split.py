"""
engine.py is being split into modules, one section at a time (docs/plans/hardening.md, phase 4). This checks the moves
so far kept their promises:

- engine.py still re-exports every moved name, as the same object, so callers and scripts/ keep working;
- engine.py no longer defines any of them itself (one copy of each);
- no test, script or module replaces a moved name through engine (engine.X = fake, setattr(engine, "X", …)): that
  stops working once the code calling X lives in the new module, which looks X up there. Patch the new module;
- each new module can be imported on its own, before engine, and is then the same module engine re-exports;
- engine.py imports each new module at its top level, so PyInstaller's analysis (scripts/build_exe.bat and
  .github/workflows/build.yml, which freeze src/app.py) finds it without a --hidden-import.

When a section moves, add its module and names to MOVED.

  python tests/test_engine_split.py
"""

from __future__ import annotations

import glob
import importlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, os.path.join(ROOT, "src"))

import engine  # noqa: E402

# the sections moved out of engine.py so far: module -> the names it took
MOVED = {
    "storage": ["storage_summary", "delete_vods", "delete_clips", "recent_projects", "_project_dirs", "_vod_files",
                "_has_vod", "_clip_files", "_size", "_dir_size", "_remove", "_prune"],
    "acquire": ["Source", "local_workdir", "_meta", "acquire", "LOW_HEIGHT", "low_quality", "_video_format",
                "_write_info", "_sound_part", "_size_of", "_Soon", "_check_download_space", "probe_url",
                "_youtube_chat", "_download_video", "_Stepper", "_finish_download", "_ytdlp_cli", "FRAGMENTS",
                "DOWNLOAD_STALL_S", "SOUND_GRACE_S", "NO_FASTSTART", "TDL_PART", "_watch_stall", "_container",
                "_reusable", "_download_bytes", "_join_progress", "kill_tree", "_ytdlp_module",
                "_clean_ytdlp_error", "_download_chat", "_with_info", "_early_sound", "_hand_over",
                "_sidecar_chat", "load_chat", "_chat_games", "game_at", "_yt_chat", "_yt_emoji", "chat_times"],
    "whisper": ["_whisper_lock", "_whisper_used", "_whisper_busy", "_busy_lock", "WHISPER_IDLE", "WHISPER_KEEP",
                "whisper_cpu_threads", "release_whisper", "_release_timer", "_release_later",
                "whisper_available", "_load_whisper", "CARD_REST", "_card_rest", "_CARD_ERROR",
                "whisper_device", "FEW_WORDS", "CARD_GAIN", "REHEAR_FROM", "_HALLUCINATION", "transcribe",
                "_transcribe", "_hear", "extract_audio", "_whisper_cache", "_whisper_device", "_whisper_said"],
    "render": ["RenderOpts", "SHAPES", "out_size", "final_size", "audio_filter", "effective_layout", "_even",
               "MAX_FPS", "output_fps", "SPEEDS", "_num", "MAX_CAMS", "MAX_ZOOM", "MIN_CAM_PX", "crop_filter",
               "PUNCH_ZOOM", "PUNCH_MAX", "PUNCH_GAP", "PUNCH_EDGE", "punch_beats", "punch_filter",
               "build_filter", "_ass_color", "_ass_time", "_ass_escape", "FONTS", "FONT_SCALE",
               "CAPTION_STYLES", "TITLE_STYLES", "TITLE_YELLOW", "MAX_TEXT_SIZE", "FONT_GLYPH", "TITLE_GROW",
               "FIT_MIN", "text_width", "fit_text", "fonts_dir", "_font", "fonts_for", "CAPTION_MOTIONS",
               "FILLER_WORDS", "EMPH_WORDS", "caption_words", "motion_of", "_emphasised", "LINE_LEADS",
               "LINE_LEADS_SOFT", "_line_cost", "_balance", "_chunks", "WM_POSITIONS", "marks_of", "_mpos",
               "mark_anchor", "watermark_anchor", "mark_window", "watermark_window", "_mark_fs",
               "_mark_dialogue", "_wm_alpha", "_shifted", "_join_lines", "title_border", "TITLE_DEFAULT_Y",
               "TITLE_TOP", "title_top", "build_ass", "_encoder_cache", "RENDER_THREADS", "RENDER_PRIORITY",
               "_HW", "_HW_HEVC", "CPU_ENCODERS", "HW_SLOTS", "_hw_slots", "_hw_fails", "_HW_BUSY", "HW_DECODE",
               "hw_failed", "hw_worked", "_rate_args", "video_encoder", "_probe_hw", "_with_rate", "write_srt",
               "DARK_LUMA", "SEEN_LUMA", "SEEN_SHARE", "BLUR_SHARP", "picture_check", "audio_peak",
               "read_through", "_crop_failed", "_graph_failed", "LAYOUT_NAMES", "_first_reason", "_frame_luma",
               "_start_lumas", "black_start", "_ffmpeg_reason", "SPANS_INLINE", "mute_commands", "mute_graph",
               "render_clip"],
    "analysis": ["HYPE", "_TOKEN", "REACT", "STRETCHED", "REACT_PHRASES", "LAUGH_TEXT", "_STRETCH",
                 "TOKEN_REPEAT", "_react_points", "CONFUSED", "CRINGE", "CONFUSED_PHRASES", "CRINGE_PHRASES",
                 "_UHH", "_HMM", "_CONFUSED_ASK", "KINDS", "_EMOTE_TOK", "_KIND_OF", "_awkward_points",
                 "reaction_kind", "MOOD_LABEL", "MOOD_MIN", "chat_mood", "_awkward_word", "chat_weight",
                 "is_reaction", "FINE_HZ", "measure_audio", "_rolling", "_robust_z", "_fit", "EVENT_WEIGHTS",
                 "AGREE_WEIGHT", "Analysis", "analyze", "ANALYSIS_VERSION", "_analysis_key", "_save_analysis",
                 "_load_analysis", "analyze_cached", "_finish", "chat_rate", "_audio_z", "score_signals",
                 "Clip", "estimate", "MAX_AUTO_CLIPS", "STANDOUT_GAP", "standout_bar", "_snap", "smart_bounds",
                 "fixed_bounds", "_ends_sentence", "refine_with_words", "clip_features", "moment_scores",
                 "NO_CHAT_JOIN", "NO_CHAT_SCALE", "strength", "grades", "WEAK_QUALITY", "MIN_AUTO_CLIPS",
                 "pick_clips", "_reason", "make_thumb"],
}

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


with open(os.path.join(ROOT, "src", "engine.py"), encoding="utf-8") as f:
    engine_src = f.read()
files = sorted(glob.glob(os.path.join(ROOT, "tests", "*.py")) + glob.glob(os.path.join(ROOT, "scripts", "*.py"))
               + glob.glob(os.path.join(ROOT, "src", "*.py")))
texts = {}
for p in files:
    with open(p, encoding="utf-8", errors="replace") as f:
        texts[os.path.relpath(p, ROOT)] = f.read()

for module, names in MOVED.items():
    mod = importlib.import_module(module)
    missing = [n for n in names if not hasattr(mod, n)]
    check(f"{module}.py has every name it took", not missing, missing)
    not_same = [n for n in names if getattr(engine, n, None) is not getattr(mod, n, object())]
    check(f"engine re-exports {module}'s names, the same objects", not not_same, not_same)
    still = [n for n in names if re.search(rf"^(def|class) {re.escape(n)}\b|^{re.escape(n)} *=", engine_src, re.M)]
    check(f"engine.py doesn't define {module}'s names any more", not still, still)
    check(f"engine.py imports {module} at its top level (PyInstaller finds it)",
          re.search(rf"^(from {module} import|import {module}\b)", engine_src, re.M) is not None)
    # imported on its own (a script, a test), before engine: the same module engine re-exports, not a half-loaded copy
    alone = subprocess.run([sys.executable, "-c", f"import {module}, engine; "
                            f"assert all(getattr(engine, n) is getattr({module}, n) for n in {names!r})"],
                           cwd=os.path.join(ROOT, "src"), capture_output=True, text=True, timeout=120)
    check(f"{module} imports on its own, before engine", alone.returncode == 0, alone.stderr[-600:])
    alt = "|".join(re.escape(n) for n in names)
    patch = re.compile(rf"\bengine\.({alt})\s*=(?!=)|setattr\(\s*engine\s*,\s*['\"]({alt})['\"]")
    found = [f"{path}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}"
             for path, text in texts.items() for m in patch.finditer(text)]
    check(f"nothing replaces one of {module}'s names through engine (patch {module}.<name> instead)", not found,
          "; ".join(found))

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
