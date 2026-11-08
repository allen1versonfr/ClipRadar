# Efficiency and speed audit: plan

A harsh audit of where Clip Radar spends time, CPU, GPU, memory, disk and process launches. The rule for
every step is **guilty until measured**. A step that can't show it needs its cost gets cut, cached or
merged into another step. "It's only a second" doesn't count when the step runs once per clip, per moment
or per poll.

Read [`README.md`](README.md) in this folder first. Issue #47 already has the timer (`src/timing.py`, #55)
and the first numbers (comments of 2026-11-01). This plan builds on those. It doesn't redo them.

**Target machine:** the owner's PC. Ryzen 5 5600X (6 cores, 12 threads), RX 7600 XT. AMD means no CUDA:
faster-whisper runs on the CPU, the card runs whisper.cpp over Vulkan (`whispercpp.py`), and encoding uses
AMF. CI numbers on Linux runners count only where they're the only numbers available, and are always marked.

## 1. Pass and fail budgets

These are set before anything is measured, so the audit can't grade itself. A row that misses its budget
gets a fix, or a written reason why it can't be fixed.

| Path | Budget | Today (#47, 2026-11-01) |
|---|---|---|
| Open a 3 h Twitch VOD from disk: find the moments | ≤ 60 s (≥ 180× real time) | 117 s |
| Titles for 8 clips, after opening | ≤ 60 s | 176 s |
| Per title, time not inside Whisper | ≤ 0.5 s | 3 to 5 s, not accounted for |
| Render a 30 s clip, GPU encoder, captions already heard | ≤ 10 s | 8.5 to 23.5 s |
| Render a 30 s clip that still needs its captions heard | ≤ 25 s | 104 to 127 s, seen in the app |
| Live: moment aired to finished clip, hands-free | ≤ 90 s | not measured |
| Live: decoding, detection and cutting, CPU while a game runs | ≤ 15% of one core on average | not measured |
| FFmpeg processes started per rendered clip | ≤ 4 | about 12 to 15 (counted in §3.3) |
| Times the same stretch of the source is decoded per clip | 1 for sound, 1 for picture | 3 to 4 |
| Idle app: CPU | < 0.5% | not measured |
| Idle app: memory, Whisper released | ≤ 120 MB | 57 MB (4.2.0, before many features) |
| Peak memory, hands-free session plus Deep scan | ≤ 4 GB | not measured |
| Window opened until Home is usable (packaged, warm disk) | ≤ 1.5 s | 616 ms until the server answers (4.2.0) |
| UI requests to the server per minute, nothing running | ≤ 15 | about 32 (status every 5 s, queue every 3 s) |

## 2. How it's measured

### 2.1 Fixed inputs, kept outside the repository

- **A:** a Twitch VOD of 3 h or more with chat, on disk.
- **B:** an ordinary 20-minute YouTube video.
- **C:** a YouTube video of 4 h or more.
- **D:** a recorded hands-free live session, replayed with `tests/live_replay.py` and `tests/fake_twitch.py`
  at real speed.
- **E:** 10 kept clips from A, each 25 to 75 s, three of them noisy (the clips the 2026-11-01 comment found slow).

Each run starts from a cold app (fresh process, Whisper not loaded) and is run three times. The table
records the median. A first run that has to download models doesn't count.

### 2.2 Instruments

1. **`timing.Timer` lines** already cover opening, titles and renders. Add a step wherever the audit
   finds unaccounted time. Per title, that's "writing the title" and "checks". Per live moment, it's every
   stage in #47's list.
2. **Process launch counter.** Wrap `subprocess.Popen` and `subprocess.run` for the duration of a run
   (a test helper, not app code). Record each FFmpeg or ffprobe call's arguments, how long it ran, and which
   seconds of which file it read. That gives the launch counts and the repeated-decode counts directly.
3. **Resource sampler.** A script records, once a second, the CPU of the app and each child process, the
   RAM, the GPU's 3D, video-decode and video-encode load, VRAM and disk reads (`psutil` plus
   `Get-Counter "\GPU Engine(*)\Utilization Percentage"` on Windows). Written next to the timing lines,
   so overlaps show up: Whisper and the encoder fighting over the card, or two FFmpegs fighting over cores.
4. **py-spy** (`py-spy record --native -o flame.svg --pid ...`) on the slowest step of each path, to see
   time spent in Python itself rather than in FFmpeg, ONNX or Whisper.
5. **The browser's performance panel** on Review with 60 clips, and on Exports with 200, for render cost
   and requests.

All of this goes in `scripts/audit_speed.py`, so the whole audit can be rerun after each fix and in the
future, with the same inputs.

## 3. What gets audited, and the suspects already found

Each item says what to check and what the code does today. Line numbers are from 4.48.0.

### 3.1 Opening a video (`engine.acquire` → `analyze_cached` → `pick_clips` → thumbnails)

- **`measure_audio`** (`engine.py:1942`) decodes the whole sound once and runs the laughter detector
  alongside, which is good. Check: the detector's share of the 117 s, and whether ONNX Runtime uses more than one
  thread (`audio_events.Scanner`). Check whether 16 kHz f32 is needed, or whether the detector's own rate would do.
- **Chat scoring** (`chat_times`, `chat_weight`, `reaction_kind`) works in Python, message by message, with
  regular expressions. A 3 h stream has hundreds of thousands of messages. Profile with py-spy. Precompile and
  merge the expressions, or cache by message text (copypasta repeats).
- **The analysis cache** (`_analysis_key`, `ANALYSIS_VERSION`): check that reopening a VOD really skips
  analysis, and how long loading the cache takes.
- **Thumbnails** (`server.py:656`, `THUMB_WORKERS`): one FFmpeg per clip, seeking into a multi-gigabyte file.
  Check: a single FFmpeg with a `select` filter for all thumbnails, against the current pool.

### 3.2 Titles (`server.start_listening` → `_title_clip` → `engine.transcribe`)

This is the biggest cost after opening (about 80% of it is Whisper).

- **Beam size.** `_hear` (`engine.py:3520`) always uses `beam_size=5`. Titles need the gist, not every word.
  Measure `beam_size=1` (greedy) for titles only: speed, and the title win rate with
  `scripts/eval_titles.py --baseline main`. Captions keep 5 unless greedy is also measured equal.
- **Model size for titles.** large-v3 on the card is about 5× `small` (#47). Run the same blind test with
  `small` against large-v3 for titles.
- **One Whisper at a time.** `_whisper_lock` serializes every transcription, so Deep scan, titles and
  captions queue behind each other. On this PC the CPU (faster-whisper) and the card (whisper.cpp) are
  separate engines. Check whether one can title while the other captions.
- **Hearing twice.** A title hears its stretch, then the render hears captions again if the words aren't
  passed on (`render_clip`, `engine.py:4362`). Check in the launch log how often a kept clip is heard twice,
  and pass the title's words on whenever the stretch covers the clip.
- **The 3 to 5 s per title not yet accounted for.** Time each part of `_title_clip`: chat titles, viewers'
  clip titles, content check, spelling, emotes, saving. Anything over 0.5 s gets cached per project.
- **The model's warm-up** (`_load_whisper`: a dry run on one second of silence on every load) and release
  after 10 minutes idle (`WHISPER_IDLE`). Measure the cost of a cold load in a hands-free session. That
  happens on the first moment after any quiet 10 minutes.
- **The re-hear on the card** (`transcribe`, `FEW_WORDS`): on noisy clips the CPU pass runs first and then
  the card runs it again. Count how often both run, and whether the card alone would have been enough.

### 3.3 Rendering one clip (`render_clip`, `framing.track`, the checks in `server.py`)

The FFmpeg launches for one kept clip, from the code:

| Step | Launches | Reads |
|---|---|---|
| Title audio (`extract_audio`, `server.py:1207`) | 1 | sound of the stretch |
| Caption audio, if the words weren't passed on (`engine.py:4362`) | 0 to 1 | the same sound again |
| Face tracking: `stream_frames` at 4 fps, plus `read_spread` (24 seeks, pooled) and `cut_time` per cut | 1 + up to 24 + cuts | the picture, then 24 seeks |
| The render itself | 1 (2 if the GPU attempt fails) | the sound and picture again |
| `black_start` on the output | 2 to 3, and again after a re-encode | |
| `picture_check` (`server.py:2178`): 5 seeks into the source | 5 | 5 more seeks |
| `audio_peak` on the output (`server.py:2158`): a full volumedetect pass | 1 | the output's whole sound |

On Windows each launch costs about 50 to 150 ms before FFmpeg does anything, and each seek into a large VOD
costs more. The suspects:

- **Merge the checks into the render.** `picture_check` reads 5 source frames that the render decodes anyway.
  `audio_peak` re-reads sound the render just encoded. `volumedetect` and a frame tap (`split` → small
  `scale` → second output) can run inside the render's own FFmpeg. Target: render, plus at most one small
  output check.
- **Face tracking reads the picture a second time.** Measure against the budget: 15 to 25% of a render
  (#47). Options: one FFmpeg for the 4 fps stream and the spread frames (the spread are frames of the same
  stretch), and the clip's tracking cached across retries and layout changes (`cache_dir` exists; check it's used).
- **Hardware decoding.** Check that `HW_DECODE` is used on AMF for each layout, and that the filter graph
  doesn't copy frames back to system memory and up again more than once.
- **x264 or AMF preset and threads**: check the "Balanced" preset on AMF against the measured SSIM rows in
  `docs/clip-radar-5-report.md`. A slower preset that looks the same on TikTok's re-encode is wasted.
- **Caption ASS size**: with word-by-word animation (`build_ass`), count dialogue lines per clip. Libass cost
  grows with them.
- **The render queue** (`server.py`, the render queue section): one render at a time. On this PC, check two at a
  time: one on AMF and one on the CPU, the way `GPU_SESSIONS` already allows on NVIDIA.

### 3.4 Live and hands-free (`live.py`, `autopilot.py`)

- **The whole chain, timed per moment**: aired → arrived → found → cut → `live_gate` wait → title → check →
  queue wait → render → checks → Discord. `lat` in `live.py` and `server.live_on_moment` already records part.
  Fill in the rest as `Timer` steps.
- **Constant cost while streaming.** The recording, sound measuring and chat reader run for hours while you're
  playing a game. Measure their CPU and disk writes per hour. Anything above budget competes with the game and OBS.
- **The cut** (FFmpeg concat of segments): check it copies streams and never re-encodes.
- **`ThreadPoolExecutor(max_workers=2)`** in `autopilot.py`: check what waits on it during a burst of moments.
- **Polling loops** (`time.sleep` in `live.py:860`, `studio_auto.py`, `engine.py:1503`): list each one, how
  often it wakes, and what it does while there's nothing to do.

### 3.5 The app sitting idle and the UI

- **Polling** (`web/src/App.tsx`): status every 5 s and queue every 3 s, even when nothing is running, plus
  `/api/ping` every 60 s and update every 60 s. Measure the server's cost of `/api/status` and `/api/queue`
  (they may read files from disk on each call). Target: back off to 15 to 30 s when idle, or one
  long-poll or server-sent events endpoint that answers only when something changes.
- **Background threads** in `server.py`: the memory monitor (`MEMORY_EVERY`), the updater (`AUTO_EVERY`),
  the Whisper release timer (every 150 s). Each wakes the CPU. Count wake-ups per minute at idle.
- **Review and Exports with many clips**: re-render cost per poll, image sizes (thumbnails are 480 px wide for
  a card shown much smaller), and whether lists are virtualised.
- **Startup**: imports on the path to the first request (`python -X importtime`), and the packaged exe's
  unpack time.

### 3.6 Disk and memory

- **Writes per clip**: temp files (`framing` cache, `.tmp` renders, ASS files) and whether they're cleaned.
- **`project.json` and `queue.json` saves** (`persist.py`): how often each is rewritten in full during a
  session, and its size with 200 clips.
- **Memory**: the probe cache (64 entries), analysis arrays kept per open project, Whisper's two kept models,
  and ONNX sessions. Check for growth over a 4 h hands-free session (`tests/test_memory.py` is the start).

## 4. Phases (one pull request each)

1. **Instruments.** `scripts/audit_speed.py`: the launch counter, the resource sampler, extra `Timer`
   steps. No behaviour changes. Output: a filled-in §1 table and a ranked list. The ranking is time saved per
   week of the owner's real use, not time saved per run.
2. **Fewer launches and decodes per clip** (§3.3). Merge `picture_check` and `audio_peak` into the render,
   one FFmpeg for face tracking, pass title words to captions. Tests: the launch counter in a test asserts
   ≤ 4 per clip, so it can't creep back.
3. **Whisper** (§3.2). Beam size and model for titles, decided by `eval_titles.py`'s blind test with the
   usual 60% ship rule. The CPU and card running side by side, if phase 1 shows the lock waits.
4. **Opening** (§3.1). Chat scoring and the laughter detector, from py-spy.
5. **Live and idle** (§3.4, §3.5). Polling backoff, idle wake-ups, constant cost while streaming.
6. **Re-run the whole audit** on the same inputs. Publish the before/after table in
   `docs/audits/efficiency-audit-<date>.md` and close #47.

## 5. Rules for every fix

- No fix ships without a before and after on the same inputs, from the same script, median of three.
- A fix that's faster but changes output must show it doesn't matter: the same clips found, titles by the blind
  test, and renders by SSIM ≥ 0.995 against the old output.
- No quality setting goes down to meet a budget without the owner's say.
- Each budget that's met gets a test or a CI check where one is cheap (launch counts, request counts, idle wake-ups),
  so it stays met.

## 6. Risks

- **Merging checks into the render** makes the render's FFmpeg command longer and its failures harder to read.
  Keep the old checks as a fallback when the merged graph fails.
- **Greedy decoding** can make Whisper repeat itself on music or noise. The hallucination filter (`_HALLUCINATION`)
  and the blind test have to cover noisy clips (input E).
- **Two renders at once** on this PC may slow both, because the game, AMF and whisper.cpp share the card.
  Only ship it if the sampler shows a gain.
- **Numbers from one PC.** Budgets are for the owner's PC, which is the only one that matters here.
