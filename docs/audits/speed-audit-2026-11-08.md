# Speed audit (2026-11-08, Clip Radar 4.48.0)

A measured look at what one opened video and one rendered clip cost, in FFmpeg launches and CPU time. It's
the code-level half of the [efficiency audit plan](../plans/efficiency-audit.md). That plan needs the owner's PC
and real VODs; this audit covers what can be measured without them. Nothing in the app was changed.

## How it was measured, and what the numbers can't say

- **Machine:** the 4-core Linux development container, Debian's FFmpeg, no GPU, no Whisper (faster-whisper
  isn't installed) and no laughter model (`audio_events.available()` is false here).
- **Input:** `scripts/bench_pipeline.py`'s made-up stream: 30 minutes, 1280×720, 30 FPS, 1.7 GB, 3,562 chat messages.
- **Wall time is useless on this machine.** The same 30 s render of the same code took 21 s, 23 s, 48 s, 73 s
  and 139 s on five runs. 4.40.0 took 202 s on one run. So the before/after comparison with older versions
  in the first table below doesn't show a regression or an improvement; it shows noise.
- **What was used instead:**
  - every FFmpeg and ffprobe launch, logged through a wrapper put first on `PATH` (arguments, start, duration);
  - CPU time (user + system) of each child, median of three, from `getrusage`;
  - `cProfile` for the Python side.
- **The container exaggerates launch cost.** Starting FFmpeg just to print its version costs 0.9 s of CPU here
  (`true` costs 0.015 s), because Debian's FFmpeg loads dozens of shared libraries under a sandboxed kernel.
  On Windows, with the bundled build, a launch should cost far less. Absolute numbers below are this machine's.
  The ratios and the launch counts carry over; the seconds don't.

| Version | Find clips (s, wall) | Render 30 s (s, wall) |
|---|---|---|
| 4.8.0 | 9.9, 8.3, 8.9 | 30.2, 36.6, 57.6 |
| 4.40.0 | 5.7, 5.9, 8.5, 13.8 | 26.2, 23.9, 42.9, 202.0 |
| 4.48.0 | 9.8, 6.8, 6.5, 8.3, 7.3, 15.0 | 36.4, 21.0, 23.3, 73.5, 48.0, 138.8 |

## 1. Opening the video

| Step | Launches | Cost here |
|---|---|---|
| ffprobe | 1 | 0.4 s |
| Sound pass (`measure_audio`) | 1 | 10.3 s wall, 11.6 s CPU in FFmpeg, 1.9 s in Python |
| Thumbnails, 4 at a time (`server.clip_dicts`) | 8 (one per clip) | 1.2 to 1.75 s each, 1.9 s CPU each |
| Picking clips (`pick_clips`) | 0 | 0.02 s |

- **The sound pass is I/O, not code.** FFmpeg spends 8 to 10 s in the kernel and 3 s decoding, with `-f null` too:
  it has to read all 1.7 GB to get the sound out of an interleaved file. Python's part (numpy, chat scoring)
  is 1.9 s. For Twitch and YouTube downloads this pass already happens while the file downloads
  (`_early_sound`), so it only costs this much for files opened from disk. Nothing to fix.
- **Chat scoring is cheap at this size:** `chatsignal.feed` 0.12 s, 20,567 regex matches in 0.02 s. A real 3 h
  stream has 50 to 100 times the messages. Still under 10 s, but it should be profiled on input A of the plan.
- **Thumbnails decode to the exact frame.** `make_thumb` seeks with `-ss` before `-i`, which decodes from the
  keyframe before up to the frame asked for. Seeking to the nearest keyframe (`-noaccurate_seek -skip_frame
  nokey`, as `framing._spread_frame` already does) cost **1.31 s CPU against 1.94 s, 33% less**. The thumbnail
  is at 60% of the clip, so a frame up to one keyframe interval earlier (2 s on Twitch) changes nothing anyone
  would see.

## 2. Rendering one clip

The bench render (30 s, 9:16, captions on with no words, software encoder) started **15 processes**, plus 24
more for face finding because it was the project's first render. In order:

| # | Process | Where it's called | On the critical path? | Cost here (CPU) |
|---|---|---|---|---|
| 1 | Face finding: 24 frame grabs across the whole VOD | `framing._suggest` → `read_spread` | first render of a project only (cached) | 24 × ~1 s |
| 2 to 6 | Picture check: 5 frame grabs from the source | `server._picture_soon` | no: runs alongside the encode | 5 × ~1.5 s |
| 7 | **The render** | `engine.render_clip` → `encode` | yes | see §3 |
| 8 to 10 | Black first frame check (3 launches) | `render_clip`, first `if` | yes | 3 × 1.8 s |
| 11 to 13 | **The same check again** | `render_clip`, second `if` | yes | 3 × 1.8 s |
| 14 | ffprobe of the output | `server` output check | yes | 0.8 s |
| 15 | End-of-file check: decode the last 1.5 s | `server.py:1826` | yes | 7.9 s in one noisy run; see F2 |
| 16 | Volume check (`volumedetect`) | `server._check_sound` → `audio_peak` | yes | 1.2 s |

After the encode finished, the clip waited **9.1 s of wall time** here for steps 8 to 16, one after another.

### Findings, worst first

**F1. The black-frame check runs twice on every render, for nothing.** `engine.py:4631`:

```python
if code == 0 and black_start(tmp_path, length) and hwdec:
```

`black_start` is evaluated before `hwdec`, so it runs even on the CPU encoder, where the branch can't be taken.
Then `engine.py:4638` runs it again on the same file:

```python
if code == 0 and black_start(tmp_path, length):
```

The file hasn't changed between the two. That's 3 wasted FFmpeg launches on every clip, on the critical path.
**Fix:** work it out once, and again only after a re-encode:

```python
black = code == 0 and black_start(tmp_path, length)
if black and hwdec:
    ...re-encode...
    black = code == 0 and black_start(tmp_path, length)
if black:
    ...
```

The result stays the same; the cost is half. `tests/test_black_cover.py` covers the GPU-encoder branch. The
GPU-decoding branch (`hwdec`) has no test and needs one with the fix.

**F2. The end-of-file check decodes up to 8 seconds of 1080×1920 video.** `-sseof -1.5` seeks to the keyframe
before the last 1.5 s and decodes from there. The render doesn't set a keyframe interval, so x264 uses its default
of 250 frames: 8.3 s at 30 FPS. One run measured 7.9 s of CPU for it. Measured again on a real 30 s render, it
and the volume check together took 0.72 s, so that first figure was this machine's noise. It's still the costlier
check. What the check is for, a file cut short by a crash or a full disk, shows up when reading the packets,
without decoding them.

**And it didn't work.** The app writes renders with `+faststart`, so a file cut short keeps its index and its
length. FFmpeg then reads the end with errors but exits 0, and the check looked only at the exit code. Cut to
50%, 90%, 98% and 99.5% of its size, such a file passed every time. The existing test cut a file without
`+faststart`, which ffprobe already refuses, so it never showed. `-xerror` makes FFmpeg exit with an error
(183 here) on all four.
**Options**, to be measured against `tests/test_render_lifecycle.py`'s truncated-file cases:
- read only the packets: `-sseof -1.5 -i out -map 0:v:0 -c copy -f null -`;
- or decode keyframes only: `-skip_frame nokey`.

Either should cost about a launch.

**F3. The checks after the render run one after another.** The ffprobe, the end-of-file check and the volume
check don't depend on each other (only the black check has to come first, because it can lead to a re-render).
Running them at the same time, the way `_picture_soon` already runs the picture check during the encode, takes
the wait from their sum down to the longest one. Better still, the volume check could be folded into the
end-of-file check: one FFmpeg reading the output once instead of three.

**F4. Single-pass `loudnorm` costs more than encoding the audio itself.** The audio of 10 s took **4.5 s CPU with
`loudnorm` against 1.9 s without** (2.4×). Single-pass loudnorm resamples to 192 kHz inside. On a 60 s clip that's
about 15 s of CPU here. It's worth measuring on Windows before changing anything. The cheaper way to hit the
same target is to measure the clip's loudness (the sound pass already knows each second's level) and apply a
fixed `volume=` gain with a limiter. That's a quality question too, so the −14 LUFS and peak results must match:
`tests/test_quality.py` measures them.

**F5. Face finding on the first render does 24 separate seeks into the whole VOD.** `read_spread` starts one
FFmpeg per frame, 4 at a time (`SPREAD_WORKERS`). The result is cached per project, so it only costs once, but
it's in front of the first render. One FFmpeg with a `select` filter, or the frames saved during the thumbnails,
would do it in one pass. Low priority.

**F6. Thumbnails: see §1.** 33% less CPU per thumbnail, one changed argument.

## 3. The encode itself

CPU time of a 10 s stretch of the same render, median of three (FFmpeg 7, libx264, 4 cores, this machine):

| Variant | CPU |
|---|---|
| As the app does it: `-preset medium -crf 20`, Lanczos scaling, `loudnorm` | 172.7 s |
| Bicubic scaling instead of Lanczos | 175.0 s (no difference) |
| `-preset veryfast` | 136.8 s (21% less) |

- **Scaling isn't the cost.** Lanczos stays.
- **The x264 preset is.** But `docs/clip-radar-5-report.md` already measured the export presets (Fast, Balanced,
  High) with SSIM, and on the owner's PC renders go to AMF, not x264. This only matters for the CPU fallback.
  Leave it.
- **These numbers are very high for a 10 s 1080×1920 encode**, which points at the container again, not at the app.
  The AMF path can only be measured on the owner's PC (plan, §3.3).

## 4. Ranked list

Ranked by time saved per clip on the critical path, then by risk.

| # | Fix | Saves per clip | Risk | Effort |
|---|---|---|---|---|
| 1 | F1: black check once | 3 launches | none: same result | 10 lines |
| 2 | F2: end-of-file check without decoding | most of the costliest check | low: the truncated-file tests decide | 1 argument |
| 3 | F3: run the output checks at the same time, or fold them into one | 1 to 2 launches' wait | low | small |
| 4 | F6: thumbnails from the nearest keyframe | 33% of each thumbnail | none visible | 1 argument |
| 5 | F4: loudness without single-pass `loudnorm` | about 2.4× the audio's encode cost | medium: must match −14 LUFS | medium, measure first |
| 6 | F5: face finding in one pass | first render only | low | medium |

Fixes 1 to 3 take a default render from **15 launches to 11**, and the launches after the encode, which the clip
waits for, **from 9 to 4 or 5**. Fixes 1, 2, 4 and 6 don't change any output.

### Done in 4.48.1

- **1:** `black_start` runs once per file written. `tests/test_black_cover.py` counts the calls (it fails on 4.48.0)
  and now covers the GPU-decoding branch.
- **2 and 3:** `engine.read_through` does both checks in one FFmpeg run, `-xerror`, with the video copied and the
  sound through volumedetect. `_check_output` returns the peak to `_check_sound`. That's 0.15 s of CPU against
  0.72 s for the two old runs, and it refuses truncated `+faststart` files (new case in
  `tests/test_render_lifecycle.py`, which fails on 4.48.0). Launches after the encode: 9 to 5.
- **4:** `make_thumb(exact=False)` for clip thumbnails. `/api/frame`, the frame picker, stays exact.
- **5 and 6** are left: 5 needs measuring on the owner's PC, 6 costs once per project.

## 5. Not covered here (needs the owner's PC)

- Whisper: titles and captions, `beam_size`, model size, the lock (plan, §3.2). faster-whisper isn't installed here.
- The laughter detector's share of the sound pass. Its model isn't in this checkout.
- AMF encoding and hardware decoding.
- Live and hands-free latency.
- Real launch cost on Windows. Fixes 1 to 3 save launches whatever that cost turns out to be.

## 6. How to repeat it

1. Put a wrapper named `ffmpeg` and one named `ffprobe` first on `PATH`. Each appends its arguments, start time and
   duration to a log, then runs the real tool.
2. Run `python scripts/bench_pipeline.py`.
3. Read the log.

Phase 1 of the plan turns this into `scripts/audit_speed.py`.
