# Clip Radar 5 work — final report (4.1.5 → 4.7.0)

Baseline: Clip Radar 4.1.5 (`main` at `71822ad`, release `v4.1.5` checked on GitHub before
starting). Owner decision during the work: **no "Clip Radar 1.0" rebrand; the existing version
numbering continues.** The full audit, measurements and per-milestone notes are in
[`docs/audits/clip-radar-5-audit.md`](audits/clip-radar-5-audit.md).

## Releases and pull requests

| Milestone | Version | PR | What |
|---|---|---|---|
| 1 | 4.1.6 | allen1versonfr/vodclipper#15 | Audit, baseline measurements, lighter status check, CI size/startup reporting |
| 2 | 4.2.0 | allen1versonfr/vodclipper#16 | PyAV and hf_xet out of the build, Whisper memory released, UI polling paused while hidden, dead code, Deep Scan and retry fixes |
| 3 | 4.3.0 | allen1versonfr/vodclipper#17 | F-spam can't make clips, titles from chat, clip counts in steps of 5 (silent cap fixed), ranking "What it learned", live emote waves |
| 4 | 4.4.0 | allen1versonfr/vodclipper#18 | Hashtags in file names and posts, TikTok Studio handoff (API removed), safe automatic cleanup, sounds |
| 5 | 4.5.0 | allen1versonfr/vodclipper#19 | Source frame rate, quality presets, blur-fit fixes, watermark, caption animations |
| 6 | 4.6.0 | allen1versonfr/vodclipper#20 | Flat UI with the yellow accent, caption templates, reset style, TikTok guides, per-clip volume, UI smoke test |
| 7 | 4.7.0 | (this PR) | Update test from the previous Clip Radar release, user guide, this report |

Each release is published by the existing `build.yml` when its PR merges, after the Windows build,
packaged self-test, end-to-end test, VodClipper→Clip Radar upgrade test and install/uninstall test
pass.

## Measured results

From the Windows CI builds, unless marked otherwise.

| | 4.1.5 / 4.1.6 | After | Notes |
|---|---|---|---|
| Installer | 203 MB | **182 MB** (4.2.0) | PyAV (a third copy of FFmpeg) and hf_xet left out |
| Installed size | 570 MB | **494 MB** (4.2.0) | |
| First status request (source, Linux) | 162 ms, 85.8 MB after it | **26 ms, 39.9 MB** (4.1.6) | Heavy packages no longer imported just to say they're installed |
| Packaged app answers after start | 330 ms (one start) | 616 ms (median of 3, 4.2.0) | Not comparable: the first was a single start on a shared runner; CI now reports a median of three |
| Idle memory, packaged | 56 MB | 57 MB | |
| F-wave vs. laugh wave, same size (score) | 8.96 vs 10 | **1.73** vs 10 | 4.3.0; live no longer cuts an F wave as a moment |
| Quiet 2 h stream, 20 clips asked | **4** returned silently | 20 | 4.3.0 |
| Blur-fit, 360×800 source | render failed | renders at 1080×1920 | 4.5.0 |
| Frame rate of a 60 FPS stream's clips | 30 | 60 | 4.5.0; never above the source |
| Per-clip volume −9…+6 dB (pink noise) | — | −22.9…−10.6 LUFS, peaks ≤ −0.5 dBFS | 4.6.0 |

Not measured: render throughput per preset on real hardware (CI runners have no GPU, and timings on
shared runners vary too much to compare presets), and Deep Scan or full-VOD scan times on a
real multi-hour VOD (none available here).

## Implemented

**Debloat and speed**
- The status check no longer imports Whisper, yt-dlp or onnxruntime.
- PyAV and hf_xet are out of the build.
- Whisper models are released after 10 minutes idle, with at most two kept.
- Polling pauses while the window is hidden.
- The probe cache is capped, and ffprobe and audio extraction have timeouts.
- About ten unused helpers are removed.

**Reliability**
- Deep Scan reports unreadable audio as an error instead of "no gems".
- A Deep Scan crash on zero-length videos is fixed.
- Crop/filter failures get a plain-words message.
- Render failure → retry is tested through the real queue.
- A chat message without text no longer breaks titles.

**Ranking and chat**
- F-spam, salutes and poll votes count for nothing.
- Titles can come from chat, using distinct-chatter support, with spam, usernames and links
  filtered out. There's a setting for it, and failure falls back to the normal titles.
- Clip counts go 5–60 in steps of 5, then 70–100. A chosen count is respected, and a short
  result is explained.
- "What it learned" shows facts, the learned model and the built-in rules, and names the method
  as a logistic regression.
- Live clipping counts emote waves the same way VODs do.

**Hashtags**
- File names get literal `#Tag` text (Windows-safe, de-duplicated, length-capped, kept on retry).
- Post hashtags are configured separately: all posts, per streamer, per platform, with a preview.

**TikTok**
- Studio handoff: the caption is copied, the file selected in Explorer and the upload page opened.
- It never claims a post.
- The developer-API code, its settings and the stored sign-in are removed.

**Cleanup**
- Old temp files (on).
- Skipped hands-free renders (off).
- Finished VODs (off, with strict rules). Exports and reviews are never removed, and failures are
  reported.

**Sounds:** once on queue add (yours only) and once when a render is ready, also while minimised.

**Export**
- The stream's own frame rate (up to 60), never raised above it.
- Fast/Balanced/High/High-FPS presets.
- Blur-fit fixed for tall and narrow sources.
- Watermark: text or image, position, size, opacity, margin, box and time window, shown in the
  preview.

**Captions**
- Seven animation presets on the one caption pipeline.
- Speed, strong-word highlight, and options to drop filler and punctuation.
- Six templates and a reset to default.

**UI**
- A flat look with the restrained yellow accent and dark text on it.
- A "show what TikTok covers" guide.
- Per-clip volume.

**Tests**
- Ten new unit test files (`test_startup`, `test_memory`, `test_deepscan`, `test_retry`,
  `test_chattitles`, `test_ranking_explain`, `test_hashtags`, `test_cleanup`, `test_quality`,
  `test_captions_anim`) plus a browser smoke test (`test_ui`), all in CI.
- New e2e checks: tags in a real rendered file name, the Studio handoff, and removal of the old
  sign-in.
- A CI step that updates the previous Clip Radar release in place.

## Not done, or partly done

- **TikTok Studio URL** (`https://www.tiktok.com/tiktokstudio/upload`) couldn't be opened from the
  development environment, and no official page in search results confirms it. Click it once on a
  PC. It's one constant, also overridable with `CLIPRADAR_STUDIO_URL`.
- **Learned trim shifts are not applied in live mode.** A later end would delay every live clip.
- **Caption line spacing** isn't its own control, and there are no caption backgrounds beyond the
  box style.
- **No multitrack editor**, as the brief allowed.
- **`docs/images` screenshots** still show the older blue accent. They need a Windows capture with
  a project open.
- **Further size cuts** were considered and rejected after measuring: a shared FFmpeg build saves
  only 15 MB, and dropping one of the two yt-dlp copies saves under 5 MB (the exe is needed for
  YouTube chat).

## How it was tested

```
python tests/test_<name>.py        # each unit script; CI runs them all (build.yml › Unit tests)
python tests/test_ui.py            # needs Playwright and a Chromium-based browser
python tests/e2e.py --app "python src/app.py" --ffmpeg ffmpeg --out report.json --model tiny
python scripts/bench_startup.py --runs 5
```

- **Unit tests:** every unit script passed locally at each milestone (Linux, Python 3.13), with
  FFmpeg renders where a test needs them.
- **Local e2e:**
  - Milestone 1 code: 141 of 142.
  - Milestones 4 and 5: 135 of 136 each.
  - The one failure every time is a retry render that has to download a Whisper model, which is
    blocked in the development environment. It passes on Windows CI.
- **Windows CI** (build, packaged self-test, e2e, upgrade from VodClipper 3.11, install/update/
  uninstall) passed on every PR before it was merged.

Not done by hand: nothing here was tried on a Windows desktop, with a real Twitch/YouTube VOD or
stream, or on a PC with a GPU. Those are the manual checks below.

## Manual checks worth doing on a PC

1. Open a real Twitch VOD with chat. Check that an F-heavy moment doesn't top the list, and that a
   chat-agreed phrase shows up as a `Chat: "…"` title.
2. Keep a clip, then open Exports › Open in TikTok Studio. The caption should paste, Explorer
   should select the file, and the Studio upload page should open.
3. Render with the watermark on, one per corner. Render a 60 FPS stream and confirm the clip is
   60 FPS.
4. Turn on finished-VOD cleanup with 1 day, then confirm it only removes a VOD whose clips are
   all decided and rendered.
5. Update an installed 4.6 copy from Settings › Updates. Settings, Exports and the Twitch sign-in
   should carry over.

## Updating

Nothing to do. Installed copies update in the app as before. Settings, Exports, projects and
sign-ins carry over (CI checks this from both VodClipper 3.11 and the previous Clip Radar
release). The old TikTok developer-app sign-in is deleted on first start, because posting now goes
through TikTok Studio. Exports get `#` hashtags in their file names by default: Settings ›
Hashtags turns that off.
