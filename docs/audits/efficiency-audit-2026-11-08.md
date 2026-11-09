# Efficiency audit on the owner's PC (2026-11-08, Clip Radar 4.48.1)

Phase 1 of the [efficiency audit plan](../plans/efficiency-audit.md): real numbers from the owner's PC against the
plan's §1 budgets, and a ranked list of what to fix. It builds on the [cloud speed audit](speed-audit-2026-11-08.md),
which counted launches without this PC. Nothing in the app was changed.

Two sources, and they disagree in an important way:

- **Measured runs** of `scripts/audit_speed.py` on one real video (below). They show where a clip's time goes
  when nothing else is running and Whisper `small` runs on the CPU.
- **The app's own `Timing` lines from a week of real use** (`clipradar.log`, 1 to 8 November: 556 titles,
  124 renders, 22 opens). In real use, titles run on the graphics card and everything uses `large-v3`. That turns a
  cost the measured runs don't show at all into the biggest one: **every rendered clip hears its captions a second
  time, on the CPU, with `large-v3`** (finding 1).

The ranking below goes by the real week, as the plan asks.

## 1. Machine, inputs and what was busy

| | |
|---|---|
| CPU | AMD Ryzen 5 5600X, 6 cores, 12 threads |
| GPU | AMD Radeon RX 7600 XT, 16 GB, driver 32.0.32015.7029 |
| RAM | 16 GB |
| Windows | 11 Pro, build 26200 |
| Disks | Projects (`D:\`): WD10EADS, a 1 TB SATA hard drive (871 GB free). System: Kingston NV2 1 TB NVMe (24 GB free) |
| Python, FFmpeg | 3.12.10; the repo's `tools\ffmpeg.exe` |
| Encoder | `h264_amf` (the app's own `perf.json` records AMF at 2.9 to 3.0× real time, x264 at 0.5 to 0.8×) |

**Inputs.** Only one usable video was on disk:

| Input | Length | Source | Chat | Size |
|---|---|---|---|---|
| B (stand-in): *CORE $1 VS $10,000 RESTAURANTS IN JAPAN* | 68.2 min | YouTube, `D:\yt_ANoZNWQXYZ4\vod.mp4` | no | 2.16 GB |

- **Input A (a Twitch VOD of 3 h or more, with chat) couldn't be measured.** The 11 Twitch VOD projects on
  `D:\` kept their chat (up to 47 MB) and transcripts, but not the video: Settings › Storage removes VODs after 3 days
  (`cleanup.json`: `vods: true, vod_days: 3`). The other two projects with a video are 11 s and 103 s long.
- **Input C (4 h or more)** doesn't exist on this PC either.
- **For the next audit:** download one Twitch VOD of 3 h or more with chat, and one YouTube video of 4 h or more,
  and keep them out of the cleanup (or copy them elsewhere first).

**What was busy.** The installed Clip Radar was open the whole time (idle, about 1.8 GB of memory) and Firefox used
5 to 6% CPU. Nothing heavy, and no game.

**Runs.** Three full runs of the same input (open, titles with Whisper `small`, three renders, 60 s idle):

- Run 1 through `scripts/audit_speed.py` (launch counts, CPU and memory samples). The file came from the hard drive cold.
- Runs 2 and 3 through a small driver that calls the same endpoints in the same order, *without* the audit's
  `subprocess.Popen` wrapper (see §6: the wrapper hung 3 of 4 runs). The video was in Windows' file cache by then.

`large-v3` wasn't measured on this input: every attempt at the full `--auto` matrix hung before it got there.
Real use runs `large-v3` throughout, so the real-use lines stand in for it.

## 2. The budget table (plan §1)

Medians of the three runs unless marked. "Real use" is the week of `Timing` lines.

| Path | Budget | Measured | Result |
|---|---|---|---|
| Open a 3 h Twitch VOD from disk: find the moments | ≤ 60 s | Not measured on a 3 h VOD (§1). 68 min from the hard drive cold: **57.2 s** (71× real time); from the file cache: **21.5 s** (190×). Scaled to 3 h cold: about 150 s | **Fail** from disk. Real use: a download already measures its sound while it downloads, so "Finding the best moments" has a median of 0.6 s there |
| Titles for 8 clips, after opening | ≤ 60 s | 157 s for 14 clips with `small` on the CPU, so about 90 s for 8. Real use (card, `large-v3`): median 29 s per titles run, but runs range from 1 clip to a whole VOD | **Fail** (measured) |
| Per title, time not inside Whisper | ≤ 0.5 s | (157.1 − 154.6) s / 14 = **0.18 s**. Real use: reading the sound median 0.1 s, writing the title 0.0 s | **Pass** |
| Render a 30 s clip, GPU encoder, captions already heard | ≤ 10 s | 15.4 s for a 35 s clip, so **13.2 s** for 30 s with face tracking; about 9 s without it | **Fail** with face tracking, pass without. Real use never gets here: captions are heard again in 114 of 124 renders (finding 1) |
| Render a 30 s clip that still needs its captions heard | ≤ 25 s | Real use: median **74.2 s**, slowest 777 s. 119 of 124 renders over 25 s | **Fail** |
| Live: moment aired to finished clip | ≤ 90 s | No live chain lines in the log: the `Timing` lines don't name the live stages | Not measured |
| Live: CPU while a game runs | ≤ 15% of a core | Needs a recorded session (input D) | Not measured |
| FFmpeg processes per rendered clip | ≤ 4 | **15** (12 plus one per scene cut); **26** on the first render of a session | **Fail** |
| Times the same stretch is decoded per clip | 1 sound, 1 picture | Picture: **2** full reads (face stream, encode) plus 5 seeks and one short read per cut. Sound: **2** (title, encode), **3** in real use (captions again) | **Fail** |
| Idle app: CPU | < 0.5% | 0.0% average, 0.5% highest, over 60 s with no browser open | Pass (60 s only; UI polling not included) |
| Idle app: memory, Whisper released | ≤ 120 MB | 524 MB, but Whisper isn't released until 10 minutes idle and the run idled 60 s | Not measured |
| Peak memory, hands-free plus Deep scan | ≤ 4 GB | Highest seen in these runs: 1.1 GB (titles) | Not measured (no hands-free session) |
| Window opened until Home is usable | ≤ 1.5 s | | Not measured (the audit doesn't open the window) |
| UI requests per minute, idle | ≤ 15 | | Not measured (no browser in the run) |

## 3. Where the time goes

### The measured runs

| Step | Run 1 | Run 2 | Run 3 | Median | Inside it (app's own timing, median) |
|---|---|---|---|---|---|
| Open | 57.2 s (cold) | 21.9 s | 21.0 s | 21.9 s | Finding the best moments 21.1 s, thumbnails 0.8 s |
| Titles (14 clips) | 155.7 s | 162.8 s | 157.1 s | 157.1 s | Whisper (CPU) 154.6 s, reading the sound 2.2 s, writing titles 0.3 s |
| Render 1 (35.0 s clip) | 15.4 s | 16.3 s | 15.3 s | 15.4 s | encoding 10.1 s, finding the face 4.5 s, checks 0.8 s |
| Render 2 (18.8 s clip) | 8.4 s | 8.9 s | 8.2 s | 8.4 s | encoding 6.2 s, finding the face 1.9 s |
| Render 3 (9.2 s clip) | 6.5 s | 6.1 s | 6.2 s | 6.2 s | encoding 3.7 s, finding the face 2.4 s |

- **Opening is mostly waiting on the hard drive.** The sound pass is one FFmpeg reading all 2.16 GB: 51.3 s cold,
  which is 42 MB/s, with the app at 8.7% of the PC's CPU on average. From the file cache the same step took 21 s.
- **Titles are Whisper**, 98% of the step. Captions reused the title's words in these runs ("captions and content
  check": 0.0 s), because the CPU heard the titles. That's not what happens in real use (finding 1).
- **Encoding runs at 3.5× real time on AMF** (10.1 s for 35 s), close to the 3.7× measured in October.
- **Face finding is 23 to 39% of a render here**, over the plan's 15 to 25%. In real use it ran in only 15 of 124
  renders, because the usual layout doesn't track a face.

### A week of real use (1 to 8 November)

Total time per step over the week, from `clipradar.log`:

| Step | Times | Total per week | Median |
|---|---|---|---|
| Titles: Whisper on the card | 539 | **328.5 min** | 26.9 s |
| Render: captions heard again on the CPU (`large-v3`) | 114 | **188.1 min** | 52.3 s |
| Open: downloading the VOD | 20 | 64.5 min | 5.9 s |
| Render: waiting in the queue | 124 | 56.2 min | 0.0 s (slowest 485 s) |
| Open: "Putting the video together" after a Twitch download | 7 | **47.4 min** | 539.8 s |
| Render: encoding | 124 | 37.2 min | 15.1 s |
| Titles: Whisper on the CPU (card stopped or busy) | 20 | 34.4 min | 74.8 s |
| Everything else together | | under 15 min | |

Renders took 236.8 minutes that week. **188 of them, 79%, were the second caption pass**, and the encode was 37.

## 4. Findings, worst first

**F1. Every render hears its captions again on the CPU, with `large-v3`. 188 min a week.**

- **Evidence:** 114 of 124 real renders have a "Whisper (CPU)" step: median 52.3 s, slowest 760.7 s ("L EGO",
  777 s in all). It's 79% of all render time, and it's why 119 of 124 renders missed the 25 s budget.
- **Code:** it's on purpose. Words heard on the card are marked rough (`src/server.py:1174-1175`, `:1224`), and a
  caption takes only the stretches that aren't rough (`src/server.py:1190`). So when a title was heard on the card
  (about 540 of 560 titles that week; the counts differ by a few lines depending on how the log is parsed), the render (`_render_inputs`, `src/server.py:1853`) hears the same stretch again with
  faster-whisper on the CPU, holding the render worker. The measured runs heard titles on the CPU, so they didn't
  show it.
- **Fix, from cheapest:**
  1. Hear a kept clip's captions in the background as soon as it's kept, while nothing else is using the CPU, so
     the render finds them done. That doesn't change any output; it takes the 52 s off the render.
  2. Let captions use the card's `large-v3` words where the card's pass looks sound, and hear again only where it
     doesn't (few words a second, the `FEW_WORDS` test in `engine.py:3452`, already exists the other way round).
     That changes captions, so it needs the plan's blind test.
- **How sure:** the cost is certain (it's the app's own timer). Fix 1 is safe. Fix 2 is a quality question for the owner.

**F2. Titles on the card take 329 min a week.**

- **Evidence:** 539 titles heard on the card, median 26.9 s; the slowest whole-VOD titles run was 1470 s on the card,
  plus 610 s on the CPU after the card stopped.
- **Code:** `whispercpp.py:202` runs whisper.cpp with beam 5, like faster-whisper (`engine.py:3524`), and titles use
  `large-v3`.
- **Fix:** the plan's §3.2: greedy decoding (`-bs 1`) and a smaller model for titles only, decided by
  `scripts/eval_titles.py`'s blind test. A title needs the gist, not every word.
- **How sure:** medium. Most of this runs in the background while you review, so the minutes saved are less
  visible than F1's. It's the largest block of machine time all the same.

**F3. A Twitch download spends about 9 minutes rewriting itself after it's finished. 47 min a week.**

- **Evidence:** "Putting the video together" took 530 to 609 s on 5 of 7 Twitch VODs (16 s and 40 s on the two
  short ones). On one VOD the join took 530 s after a 164 s download.
- **Code:** `engine.py:1112-1115` (the step), `engine.py:1176-1183` (`+faststart` is already off). yt-dlp's
  `FixupM3u8` still remuxes the whole joined stream into MP4: one full read and one full write of a 10 to 30 GB file,
  on the projects drive, which here is a hard drive.
- **Fix:** measure, before changing anything, a download kept as MPEG-TS (`--hls-use-mpegts`, no fixup) against
  today's MP4. Seeking in a TS has no index, so thumbnails and renders must be timed too. Putting projects on the
  NVMe would also help, but it has 24 GB free.
- **How sure:** the cost is certain; the fix is not.

**F4. Renders wait in the queue for 56 min a week.**

- **Evidence:** "waiting to render" median 0 s, slowest 485 s, 56.2 min in all.
- **Code:** the render worker is busy with F1's Whisper pass, so the next clip waits.
- **Fix:** F1's first fix removes most of it. Nothing separate.

**F5. 15 FFmpeg launches per rendered clip, against a budget of 4.**

From `launches.csv`, run 1, render 2 (render 1 adds 4 for the session's encoder probe, `engine.py:4149-4168`):

| Launches | What | Code | On the clip's path? |
|---|---|---|---|
| 1 | The encode | `engine.render_clip` | yes |
| 1 | Face stream at 4 fps | `framing.stream_frames`, `framing.py:127` | yes |
| 1 per scene cut (3 here, 9 in render 1) | `cut_time`: a short 30 fps read around each cut, one after another | `framing.py:297`, called at `framing.py:536` | yes: 1.9 s of render 1's 4.2 s face step |
| 5 | Picture check: 5 seeks into the source | `engine.picture_check`, `engine.py:4213`, from `server._picture_soon`, `server.py:2169` | no: runs during the encode |
| 3 | Black first frame | `engine.black_start`, `engine.py:4322`, called at `engine.py:4656` | yes, about 0.35 s |
| 1 + 1 | `read_through` and ffprobe of the output | `server.py:1825` | yes, about 0.15 s |

- **What it costs:** little time per clip on this PC (each small launch 0.1 to 0.35 s, and the picture check overlaps
  the encode), about 2 minutes a week. It still fails the budget, and every launch is another seek into a
  multi-gigabyte file on a hard drive.
- **Fix, in order of time saved:** read all cut windows in one FFmpeg, or take the cut time from the 4 fps stream
  (finding the face drops by half on cut-heavy video); fold the picture check into the face stream, which already
  decodes the same stretch; read the black first frame from the output with one launch instead of three. That's
  15 to about 6. Getting to 4 needs the plan's phase 2 (checks inside the render).
- **How sure:** high for the counts. The time saved is small.

**F6. Opening a video from disk is limited by the hard drive.**

- **Evidence:** the sound pass took 51.3 s cold and 21 s from cache: about 35 s of the 57 s open is waiting for
  `D:\`. CPU was 8.7% of the PC.
- **Code:** `measure_audio`, `engine.py:1942`.
- **Fix:** none needed for downloads, which already measure sound while downloading (real median 0.6 s). For local
  files, the budget can only be met on an SSD.
- **How sure:** high.

**F7. Every local file named `vod.mp4` shares one project folder.** Not a speed finding, but this audit hit it.

- **Evidence:** opening `D:\yt_ANoZNWQXYZ4\vod.mp4` as a local file wrote its project into `D:\vod`, an older,
  unrelated project for another `vod.mp4`. Its `meta.json` was replaced.
- **Code:** `engine.py:466-467`: the project folder is the slug of the file's name, and every Clip Radar download is
  called `vod.mp4`.
- **Fix:** name a local file's project after its folder too (or a short hash of its full path) when the name is `vod`.
- **How sure:** high.

### The plan's §3 suspects

| Suspect | What the numbers showed |
|---|---|
| Whisper's share of titles; `small` against `large-v3` | 98% of titles (154.6 of 157.1 s). `large-v3` wasn't measured here (§1); in real use it's 26.9 s median per title on the card |
| Time per title not inside Whisper | 0.18 s. Passes; nothing to do |
| Times each clip's stretch is decoded | Picture 2 full reads plus seeks; sound 2, and 3 in real use (F1) |
| AMF encode time; which GPU engine is the limit | 3.5× real time. The engine load couldn't be measured: §6, `typeperf` |
| Face finding's share of a render | 23 to 39% here, but only 15 of 124 real renders track a face |
| Sound pass and laughter detector's share of opening | The sound pass is 90% of opening (51.3 of 57.2 s cold), mostly disk. The laughter detector's share couldn't be split out: the py-spy profile wasn't written (§6) |
| Idle CPU and launches | 0.0% average over 60 s, no launches |
| Hearing twice | Not in the measured runs; in real use, 114 of 124 renders (F1) |
| The re-hear on the card for few-word captions | Not seen: real captions run on the CPU only |

## 5. Ranked fix list

By time saved per week of real use, then risk.

| # | Fix | Saves per week | Risk | Effort |
|---|---|---|---|---|
| 1 | F1: hear a kept clip's captions in the background before its render | up to 188 min of render time, and most of F4's 56 min of queue | low: same words, different moment | medium |
| 2 | F2: greedy decoding and a smaller model for titles, if the blind test agrees | a share of 329 min of card time | medium: title quality, blind test decides | small code, a test run |
| 3 | F1, second half: use the card's `large-v3` words for captions where they're sound | the rest of F1 | medium: caption quality, owner's call | medium |
| 4 | F3: Twitch downloads without the MP4 rewrite, if seeking holds up | up to 47 min | medium: measure seeking first | small, measure first |
| 5 | F5: one launch for scene cuts, picture check from the face stream, black check in one launch | about 2 min, and 15 → 6 launches a clip | low | small |
| 6 | F7: local `vod.mp4` files get their own project folder | none (correctness) | low | small |
| 7 | F6: opening from disk | nothing to do in code; an SSD for local files | none | none |

Each fix goes in its own PR with before and after numbers from the same script (plan §5). Fixes 1, 4 and 5 can be
measured with `scripts/audit_speed.py` once its own problems below are fixed. Fixes 2 and 3 need
`scripts/eval_titles.py` and a caption comparison.

## 6. Problems with the audit script itself

`scripts/audit_speed.py` (from PR #85) needs these fixed before the next run. None of them is app code.

1. **The run hangs (3 of 4 runs).** The app froze where titles start, while the background face warm-up
   (`framing-warm`, `framing.read_spread`, four FFmpeg frame grabs at once) was running. The main thread was waiting
   for the GIL, and the frame grabs outlived their 60 s timeout by over 20 minutes. The same steps without the
   script's `subprocess.Popen` replacement (`audit_speed.py:75-112`) finished 4 times out of 4. So it's most likely the
   wrapper, but 4 clean runs can't fully clear the app. Fix: record launches without subclassing `Popen` (a
   `sys.addaudithook` on `subprocess.Popen` gives the arguments; the timing can come from a thread that waits on the
   handle), and give each run a deadline.
2. **It writes into the owner's projects and loads the owner's render queue.** `fresh_data` (`audit_speed.py:227-237`)
   copies every `*.json`, including `locations.json` (so projects went to the real `D:\`, see F7) and `queue.json`
   (717 KB of real queue, restored on start; nothing rendered from it this time). Fix: copy only the settings files.
3. **The card's Whisper is never available**, because only `models` and `tools` are linked, not `whisper-vulkan`
   (`audit_speed.py:236`). So titles always run on the CPU, unlike real use, and F1 can't show up. Fix: link it too.
4. **GPU load is always 0.** `typeperf` expands `GPU Engine(*)` once, when it starts (`audit_speed.py:279-286`), so
   the FFmpeg processes started later are never counted. Fix: sample `Get-Counter` each second, or restart `typeperf`
   per phase.
5. **No flame graph.** py-spy is stopped with `terminate()` when the open step ends (`audit_speed.py:369`), which on
   Windows kills it before it writes the SVG. Fix: `--duration`, or send Ctrl+C.
6. **A relative `--out` breaks every run**, because the app runs from its temp folder (`audit_speed.py:336, 343`).
   Fix: resolve `--out` to an absolute path.
7. **The input picker can't find Twitch VODs** once Storage has removed their video. Fix: say so in `SUMMARY.md` and
   name which projects have chat but no video.

## Appendix

### A. Setup (from `setup.json` and the run log)

- Machine: Windows-11-10.0.26200-SP0, Python 3.12.10, AMD Ryzen 5 5600X 6-Core Processor, AMD Radeon RX 7600 XT
  (driver 32.0.32015.7029), 15.9 GB RAM, 6 cores, 12 threads.
- FFmpeg: already in `tools`. Laughter model: already in `src/models`. Whisper: faster-whisper installed.
- Projects found with a video: 3 (11 s, 103 s and 68 min). Input given by hand: the 68-minute one (§1).
- Busy at the start: Firefox 6% CPU, 1.4 GB. The installed Clip Radar was open and idle throughout.
- Settings used: the owner's settings files (`export.json`, `safety.json`, `titles.json` and the rest), without
  `locations.json` and `queue.json` (§6, problem 2). Models and tools linked.

### B. Runs 2 and 3 (driver without the `Popen` wrapper)

```
run 2:  open 21.9 s, 14 clips | titles 162.8 s | render 1 16.3 s for 35.0 s | render 2 8.9 s for 18.8 s | render 3 6.1 s for 9.2 s
run 3:  open 21.0 s, 14 clips | titles 157.1 s | render 1 15.3 s for 35.0 s | render 2 8.2 s for 18.8 s | render 3 6.2 s for 9.2 s
two more runs of open and titles only: titles 155.5 s and 164.6 s, no hang
```


### C. `whisper_small/report.md` (run 1, from `scripts/audit_speed.py`)

Paths shortened; the throwaway folder was deleted after the run.

#### Speed audit results

2026-11-08 20:47. Whisper `small`, 3 clip(s) rendered per input, GPU encoder allowed, 3 run(s) per input. CPU % is of the whole PC (100% = every core busy).

Settings copied from `<a trimmed copy of the settings folder>`: cleanup.json, export.json, hashtags.json, hotkey.json, learning.json, perf.json, safety.json, studio.json, titles.json, update.json. Models and tools: models junction, tools junction.

#### vod.mp4

68.2 minutes, chat: no, 14 clips found, Clip Radar 4.48.1.

##### Steps (wall time, each run)

| Step | run 1 |
|---|---|
| open | 57.5 s |
| titles | 155.8 s |
| render 1 | 15.4 s for 35.0 s |
| render 2 | 8.6 s for 18.8 s |
| render 3 | 6.5 s for 9.2 s |

##### The app's own timing (run 1, GET /api/timings)

- **open** (57.2 s): Finding the best moments 51.8 s, Making thumbnails 5.4 s, Picking clips 0.1 s, Getting the VOD 0.0 s
- **titles** (155.7 s): Whisper (CPU) 153.2 s, reading the sound 2.2 s, writing the title 0.3 s, waiting for other listening 0.0 s
- **render** (15.4 s): encoding 10.3 s, finding the face 4.2 s, checking the source 0.7 s, checking the file 0.1 s, captions and content check 0.0 s, waiting to render 0.0 s, waiting for other listening 0.0 s
- **render** (8.4 s): encoding 6.3 s, finding the face 1.9 s, checking the file 0.1 s, waiting to render 0.0 s, checking the source 0.0 s, captions and content check 0.0 s, waiting for other listening 0.0 s
- **render** (6.5 s): encoding 3.8 s, finding the face 2.5 s, checking the file 0.2 s, checking the source 0.0 s, captions and content check 0.0 s, waiting to render 0.0 s, waiting for other listening 0.0 s

##### Launches and load per step (run 1)

| Step | Launches | of them reading the source | tools | time in them | CPU avg / max | RAM max | GPU 3D / compute / decode / encode | VRAM max |
|---|---|---|---|---|---|---|---|---|
| open | 24 | 23 | ffprobe 1, ffmpeg 23 | 65.7 s | 8.7% / 22.0% | 440 MB | 4 / 0 / 0 / 0 | 3953 MB |
| titles | 30 | 30 | ffmpeg 30 | 5.2 s | 32.5% / 34.1% | 1116 MB | 4 / 0 / 0 / 0 | 3953 MB |
| render 1 | 26 | 17 | ffmpeg 25, ffprobe 1 | 16.8 s | 19.1% / 68.8% | 939 MB | 4 / 0 / 0 / 0 | 4090 MB |
| render 2 | 15 | 10 | ffmpeg 14, ffprobe 1 | 9.7 s | 15.4% / 32.3% | 985 MB | 4 / 0 / 0 / 0 | 4090 MB |
| render 3 | 15 | 10 | ffmpeg 14, ffprobe 1 | 7.8 s | 16.2% / 28.9% | 941 MB | 4 / 0 / 0 / 0 | 4090 MB |
| idle | 0 | 0 |  | 0.0 s | 0.0% / 0.5% | 524 MB | n/a | n/a |
| start | 0 | 0 |  | 0.0 s | 0.0% / 0.0% | 47 MB | n/a | n/a |

##### Slowest launches (run 1)

| Step | Tool | Seconds | Arguments |
|---|---|---|---|
| open | ffmpeg | 51.3 | `-v error -nostdin -i <source> -map 0:a:0 -vn -sn -dn -ac 1 -ar 32000 -f f32le -` |
| render 1 | ffmpeg | 10.0 | `-v error -y -nostdin -progress pipe:1 -nostats -hwaccel auto -ss 3805.610 -t 35.040 -i <source> -filter_complex [0:v]scale=1920:1080,crop=608:1080:'clip(if(lt(t,0.000),691.3,if(gte(t,34.750),427.3,if(` |
| render 2 | ffmpeg | 6.0 | `-v error -y -nostdin -progress pipe:1 -nostats -hwaccel auto -ss 3278.250 -t 18.850 -i <source> -filter_complex [0:v]scale=1920:1080,crop=608:1080:'clip(if(lt(t,0.000),603.6,if(gte(t,18.500),983.2,if(` |
| render 3 | ffmpeg | 3.4 | `-v error -y -nostdin -progress pipe:1 -nostats -hwaccel auto -ss 2648.260 -t 9.240 -i <source> -filter_complex [0:v]scale=1920:1080,crop=608:1080:'clip(if(lt(t,0.000),209.4,if(gte(t,9.000),1033.5,if(l` |
| render 1 | ffmpeg | 2.1 | `-v error -nostdin -ss 3805.610 -t 35.040 -i <source> -an -vf fps=4.0,scale=320:240:flags=bilinear -pix_fmt rgb24 -f rawvideo -` |
| render 3 | ffmpeg | 1.3 | `-v error -nostdin -ss 2648.260 -t 9.240 -i <source> -an -vf fps=4.0,scale=640:480:flags=bilinear -pix_fmt rgb24 -f rawvideo -` |
| open | ffmpeg | 1.3 | `-v error -y -nostdin -noaccurate_seek -skip_frame nokey -ss 192.49 -i <source> -frames:v 1 -vf scale=480:-2 -q:v 4 <temp>\work\vod\thumbs\t_192.jpg.` |
| open | ffmpeg | 1.2 | `-v error -y -nostdin -noaccurate_seek -skip_frame nokey -ss 1449.51 -i <source> -frames:v 1 -vf scale=480:-2 -q:v 4 <temp>\work\vod\thumbs\t_1449.jp` |
| open | ffmpeg | 1.2 | `-v error -y -nostdin -noaccurate_seek -skip_frame nokey -ss 3374.25 -i <source> -frames:v 1 -vf scale=480:-2 -q:v 4 <temp>\work\vod\thumbs\t_3374.jp` |
| open | ffmpeg | 1.2 | `-v error -y -nostdin -noaccurate_seek -skip_frame nokey -ss 2707.11 -i <source> -frames:v 1 -vf scale=480:-2 -q:v 4 <temp>\work\vod\thumbs\t_2707.jp` |
| render 2 | ffmpeg | 1.2 | `-v error -nostdin -ss 3278.250 -t 18.850 -i <source> -an -vf fps=4.0,scale=320:240:flags=bilinear -pix_fmt rgb24 -f rawvideo -` |
| open | ffmpeg | 1.2 | `-v error -y -nostdin -noaccurate_seek -skip_frame nokey -ss 3419.37 -i <source> -frames:v 1 -vf scale=480:-2 -q:v 4 <temp>\work\vod\thumbs\t_3419.jp` |

### D. `real_timings.md` (a week of real use)

#### Real-use timings from clipradar.log

702 lines from clipradar.log.

#### titles (556 times)

Total: median 29.0 s, slowest 2199.9 s.

| Step | Times seen | Median | Slowest |
|---|---|---|---|
| Whisper (CPU) | 20 | 74.8 s | 609.6 s |
| Whisper (card) | 530 | 26.9 s | 1470.2 s |
| reading the sound | 550 | 0.1 s | 57.4 s |
| writing the title | 556 | 0.0 s | 1.4 s |
| waiting for other listening | 549 | 0.0 s | 0.0 s |
| waiting for other moments | 539 | 0.0 s | 86.8 s |

#### render (124 times)

Total: median 74.2 s, slowest 777.3 s.

| Step | Times seen | Median | Slowest |
|---|---|---|---|
| captions and content check | 124 | 52.5 s | 761.4 s |
| Whisper (CPU) | 114 | 52.3 s | 760.7 s |
| encoding | 124 | 15.1 s | 73.1 s |
| checking the file | 124 | 0.5 s | 1.0 s |
| reading the sound | 114 | 0.4 s | 8.1 s |
| waiting to render | 124 | 0.0 s | 484.8 s |
| checking the source | 124 | 0.0 s | 1.5 s |
| waiting for other listening | 121 | 0.0 s | 0.0 s |
| finding the face | 15 | 0.0 s | 8.1 s |

#### open (22 times)

Total: median 10.9 s, slowest 1883.6 s.

| Step | Times seen | Median | Slowest |
|---|---|---|---|
| Putting the video together | 7 | 539.8 s | 609.0 s |
| Loading | 6 | 6.4 s | 18.3 s |
| Downloading the VOD | 20 | 5.9 s | 1313.0 s |
| Checking the link | 14 | 2.8 s | 7.3 s |
| Checking the VOD | 4 | 2.3 s | 2.3 s |
| Downloading the video | 15 | 1.5 s | 2.7 s |
| Making thumbnails | 18 | 1.0 s | 9.0 s |
| Finding the best moments | 22 | 0.6 s | 4.1 s |
| Getting chat replay | 22 | 0.1 s | 3.5 s |
| Getting the VOD | 22 | 0.0 s | 0.5 s |
| Picking clips | 11 | 0.0 s | 0.1 s |

#### The 10 slowest

- 2199.9 s, titles "🇰🇷 IRL SOUTH KOREA 🇰🇷 LACY x BRAEDEN 🇰🇷 KOREA NIGHTLIFE 🇰🇷 FULL DAY TOMORROW 🇰🇷 !CLIPPING": waiting for other listening 0.0 s, reading the sound 57.4 s, Whisper (card) 1470.2 s, writing the title 1.1 s, Whisper (CPU) 609.6 s
- 1883.6 s, open "🇰🇷 IRL SOUTH KOREA 🇰🇷 LACY x BRAEDEN 🇰🇷 KOREA NIGHTLIFE 🇰🇷 FULL DAY TOMORROW 🇰🇷 !CLIPPING": Getting the VOD 0.0 s, Checking the VOD 2.3 s, Downloading the VOD 1313.0 s, Putting the video together 539.8 s, Getting chat replay 0.0 s, Finding the best moments 2.0 s, Loading 18.3 s, Picking clips 0.1 s, Making thumbnails 8.1 s
- 1583.3 s, open "🎮GAMING GAUNTLET W JOE BART🎮 IM HERE🎮| !comm !discord !clip !snap !kode": Getting the VOD 0.1 s, Checking the VOD 2.3 s, Downloading the VOD 1009.0 s, Putting the video together 560.5 s, Getting chat replay 0.0 s, Finding the best moments 1.3 s, Loading 4.7 s, Picking clips 0.1 s, Making thumbnails 5.4 s
- 1421.8 s, open "YO YO YO 🍂 | !comm !discord !clip !snap !kode": Getting the VOD 0.0 s, Checking the VOD 2.3 s, Downloading the VOD 857.1 s, Putting the video together 546.2 s, Getting chat replay 0.0 s, Finding the best moments 1.5 s, Loading 8.1 s, Picking clips 0.1 s, Making thumbnails 6.5 s
- 1296.5 s, titles "MESSY TWITTER + COMPEH REACTS + 5 HEARTS UNDER 1 ROOF SEASON 2": waiting for other listening 0.0 s, reading the sound 29.2 s, Whisper (card) 1194.5 s, writing the title 1.1 s
- 1251.4 s, titles "YO YO YO 🍂 | !comm !discord !clip !snap !kode": waiting for other listening 0.0 s, reading the sound 49.9 s, Whisper (card) 1137.5 s, writing the title 0.8 s
- 1180.1 s, titles "🎮GAMING GAUNTLET W JOE BART🎮 IM HERE🎮| !comm !discord !clip !snap !kode": reading the sound 33.3 s, Whisper (card) 898.9 s, writing the title 1.4 s
- 777.3 s, render "L EGO": waiting to render 131.6 s, checking the source 0.0 s, waiting for other listening 0.0 s, reading the sound 0.6 s, Whisper (CPU) 760.7 s, captions and content check 761.4 s, encoding 15.2 s, checking the file 0.7 s
- 737.1 s, render "w life": waiting to render 19.6 s, checking the source 0.0 s, waiting for other listening 0.0 s, reading the sound 0.5 s, Whisper (CPU) 718.6 s, captions and content check 719.1 s, encoding 17.4 s, checking the file 0.7 s
- 713.8 s, open "MESSY TWITTER + COMPEH REACTS + 5 HEARTS UNDER 1 ROOF SEASON 2": Getting the VOD 0.1 s, Checking the VOD 2.3 s, Downloading the VOD 163.8 s, Putting the video together 530.5 s, Getting chat replay 0.0 s, Finding the best moments 2.8 s, Loading 9.8 s, Picking clips 0.1 s, Making thumbnails 4.3 s

### E. `whisper_large-v3/report.md`

Not produced: every full `--auto` run hung (§6, problem 1) before reaching it.
