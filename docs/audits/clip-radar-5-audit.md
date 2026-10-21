# Clip Radar 5 — Milestone 1: audit, baseline and plan (2026-10-21)

Starting point: `main` at `71822ad` (Clip Radar **4.1.5**). Checked on GitHub, not assumed: release
`v4.1.5` exists (published 2026-10-20 19:50 UTC with installer and portable zip), and the
"Build and test" run on that commit passed every step, including the packaged self-test,
end-to-end test, VodClipper→Clip Radar upgrade test and install/uninstall test.

How this audit was done: read the source, ran every unit test, measured startup locally (Linux,
Python 3.13, from source), and read the 4.1.5 CI log for Windows numbers. Nothing here was tested
on a Windows desktop by hand. Statements marked *measured* have numbers behind them; the rest
come from reading the code.

## 1. What the codebase is

| Area | Files | Lines | Notes |
|---|---|---|---|
| HTTP API, queue, jobs, project state | `src/server.py` | 3,089 | One module does routing, render queue, job lifecycle, Deep Scan, TikTok, storage. Largest maintenance cost. |
| Download, analysis, picking, render | `src/engine.py` | 2,488 | FFmpeg filter graph, encoders with hardware→software fallback, Whisper, yt-dlp. |
| Live clipping | `src/live.py` | 1,208 | Own recorder and scorer loop; shares `detect`, `chatsignal`, `engine.smart_bounds`, `learning`. |
| UI | `src/ui/app.js`, `index.html`, `app.css` | 3,054 / 734 / 672 | Plain JS, no framework, no build step. Good for size. |
| Detection and ranking | `detect.py`, `chatsignal.py`, `emotes.py`, `audio_events.py`, `gems.py`, `learning.py` | ~1,600 | Heuristic scoring plus a per-streamer learned adjuster (logistic fit from keep/skip decisions; not a neural model). |
| Titles, captions, safety | `titles.py`, `safety.py`, `checkflow.py` | ~1,000 | Template titles from chat and speech; caption styles pop/box/karaoke/glow/clean. |
| Packaging and updates | `build.yml`, `clipradar.iss`, `updater.py`, `tools_setup.py` | | PyInstaller one-folder build, Inno Setup installer, in-app updater from GitHub Releases. |
| Tests | 15 unit scripts, `e2e.py` with fake Twitch/IRC/TikTok/GitHub | | CI runs all of them on Windows plus the packaged app. |

## 2. Findings

### Bloat and dead code

- **No dead modules.** Every module in `src/` is imported. `vulture` (60% confidence) finds only
  small leftovers: `engine.twitch_vod_id`, `engine.audio_loudness`, `Scorer.event_bonus`,
  `detect.top_reason`, `titles.post_for`, `learning.enabled`, `chatsignal.NOTICE_KINDS/GIFT_KINDS`,
  `persist.USER_KEYS`, `live.t0_wall`, `audio_events.seconds_done`. Each has one reference (its
  definition). Removal is cheap but saves almost nothing; scheduled for Milestone 2 after a
  grep of the UI and tests for dynamic use.
- **App assets are lean** (~1.4 MB fonts, every caption font referenced in `engine.FONTS` and the
  CSS; 1.3 MB face model). `docs/images` (3.6 MB) is in the repository but not packaged.
- **Where the installer's 203 MB goes** (*measured*: `ClipRadar-Setup.exe v4.1.5 (203 MB)` in the
  CI log). From the wheel sizes in the same log, the bulk is FFmpeg + ffprobe, ctranslate2,
  onnxruntime, PyAV, the 19 MB laughter model and TwitchDownloaderCLI; the app's own code and UI
  are under 2 MB. A per-folder breakdown is now printed by every build (see §4) so Milestone 2
  can cut against real numbers.
- **Two copies of yt-dlp.** The build ships `tools/yt-dlp.exe` *and* the `yt_dlp` Python package
  (`--collect-submodules yt_dlp`). The exe is preferred and the package is the fallback when the
  exe is missing. Candidate for Milestone 2: keep one, after checking which one the self-updater
  and live HLS path depend on.
- **PyAV is bundled with `--collect-all av`**. faster-whisper uses PyAV to decode audio; check in
  Milestone 2 whether feeding it FFmpeg-decoded PCM (FFmpeg is already shipped) lets PyAV go.
  That is a 28 MB wheel. Not removed yet: needs a captions accuracy comparison first.
- **Direct TikTok API posting** (`tiktok.py`, 390 lines, encrypted tokens, PKCE, a mock server
  in tests) is the obsolete path the brief asks to replace with a TikTok Studio handoff.
  Milestone 4. `updater.get_token` reuses `tiktok._unprotect` for DPAPI, so that helper must move
  before `tiktok.py` can go.

### Startup and idle

- *Measured, fixed in this milestone:* the window calls `/api/status` as soon as it opens and
  every 5 s. That handler called `engine.whisper_available()`, `audio_events.available()` and
  `_has("yt_dlp")`, each of which **imports** the package just to report it as installed. The
  first call therefore loaded faster-whisper, ctranslate2, PyAV, onnxruntime and yt-dlp — about
  half a second and ~50 MB on this machine even if the user never transcribes anything.
- UI polling is reasonable: the 1.2–1.5 s pollers return immediately unless a live session, a
  Deep Scan, background listening or a render is active. `loadStatus` (5 s) and the 60 s ping run
  always; they keep running while the window is minimised. Low cost, worth gating on
  `document.hidden` in Milestone 2.
- Render workers wake every 2 s to look at the queue (one list scan each). Cheap; left as is.

### Reliability (render, Deep Scan, queue)

- 4.1.5 already added watchdogs for stalled yt-dlp (180 s without output) and FFmpeg (120 s
  without progress), crop clamping, and the low-memory idle-loop fix. The open items from
  `clip-radar-reliability-follow-up-2026-10-20.md` still stand: no real crop/filter
  failure→retry test, transcription and face tracking don't report progress in every path, and the
  watchdog thresholds haven't been checked on a slow PC.
- `queue_worker` handles cancel, shutdown and error into terminal states; retry keeps the output
  name. Milestone 2 adds a failing-filter→retry regression test.

### Features the brief asks for that already exist

Recorded so later milestones improve these instead of adding duplicates:
trimming with frame steps, undo/redo and an exact preview (3.4); aspect presets 9:16/1:1/16:9 and
split/whole/blur/cam layouts; face tracking with a smoothed crop (3.9); five caption styles with
word pop-in, colour, size, outline, shadow, font, position, words per line; title overlays with
style/font/position; export presets for TikTok/Shorts/Reels/X with codec, bitrate, scale, fps and
loudness (3.7); hardware encoding with a software fallback; per-streamer learning from keep/skip;
"Clips to suggest" (Automatic, 5, 10, 15, 20, 30, 40, 60 — 25 is missing).

### Problems found that belong to later milestones

- **Frame rate:** every render is forced to `fps=30` by default (`RenderOpts.fps = 30`, all four
  export presets say 30). A 60 FPS stream is halved; picking 60 for a 30 FPS source duplicates
  frames silently. Milestone 5 makes "same as source" the default and explains the limits.
- **Filenames:** `exports.py` builds names from a template (`{title}` by default); `#` survives
  sanitising today but there is no hashtag source. Milestone 4.
- **Version 1.0:** see §5 — a blocker for publishing anything numbered 1.0.

## 3. Plan (ordered)

Each milestone ends with: unit tests + local e2e run, version bump, CHANGELOG section, PR with
green Windows CI. Merging to `main` publishes the release (the existing `build.yml` behaviour).

| # | Version | Contents |
|---|---|---|
| 1 | 4.1.6 | This audit; startup/idle fix; CI size and startup reporting; `scripts/bench_startup.py`. |
| 2 | 4.2.0 | Debloat with measurements: dead helpers, one yt-dlp, PyAV decision, PyInstaller excludes, UI pollers paused while hidden, cache Whisper/face models between renders with release on idle; failing-filter→retry test; Deep Scan cancel/terminate test; progress heartbeat for transcription. |
| 3 | 4.3.0 | Ranking view of learned history (facts vs heuristics, reset); F-spam can't rank alone; chat-derived titles (deterministic n-gram/emote clustering, spam/bot/username filters, never blocks render); suggestion counts in steps of 5 up to 60+; live/VOD parity audit and shared pieces. |
| 4 | 4.4.0 | Filename hashtags (literal `#`, Windows-safe, dedupe, length-capped, streamer mappings) separate from post hashtags (global/streamer/platform lists, preview); TikTok Studio handoff replacing the API; safe cleanup rules; sounds fire once per event. |
| 5 | 4.5.0 | Caption animation presets on the existing ASS pipeline (shared timing/animation parameters); watermark (text/image, position, opacity, time range) as one more overlay in the existing filter graph; quality presets Fast/Balanced/High/High-FPS with source-FPS default; blur-fit fixes. |
| 6 | 4.6.0 | Editing improvements on what exists (caption templates, safe-margin guides, clip audio gain, multi-clip export, reset); flat UI pass with the yellow accent. |
| 7 | 1.0 (see §5) | Version migration, upgrade/uninstall tests from 4.x and VodClipper, release notes. |

## 4. Baseline measurements

Local, from source (`python scripts/bench_startup.py --runs 5`, median of five fresh processes,
throwaway data folders, Linux, Python 3.13):

| | 4.1.5 | 4.1.6 |
|---|---|---|
| Import the server | 99 ms | 115 ms (noise) |
| First `/api/status` after start | 162 ms | 26 ms |
| Ready (process start → first status answered) | 265 ms | 155 ms |
| Memory after first status | 85.8 MB | 39.9 MB |

The packaged Windows app bundles more (ctranslate2 and PyAV DLLs), so its saving should be at
least as large; the CI step "Smoke test" now prints *startup: app server answered after N ms; M MB
in memory once idle* and "Report package size" prints the biggest folders. The first CI run on this
branch gives the Windows baseline for later milestones.

Unit tests (local): all 15 scripts pass; `test_live.py` takes 31 s, the rest under 1.5 s each.

## 5. Versioning: Clip Radar 1.0 conflicts with real history

- Tags `v1.0.0`, `v1.1.0`, `v1.2.0`, `v1.3.0` **already exist** as VodClipper archive releases.
  A `v1.0.0` Clip Radar release can't be created without deleting or renaming those, which the
  brief rules out.
- There are no 0.5 or 0.8 releases, tags or changelog entries, so there is nothing to keep as
  "legacy 0.5/0.8".
- Every installed 4.x copy compares versions numerically (`updater.newer`). A release numbered
  1.0 is "older" and will never be offered; `build.yml` publishes based on `src/version.py`; the
  installer has no downgrade guard of its own.

Options, for the owner to choose before Milestone 7:
1. **Keep numbering, rebrand the product line:** internal version continues (e.g. 5.0.0) so
   updates just work; the UI and release title say "Clip Radar 1.0" as a product generation. No
   migration code, no broken updaters. *Recommended.*
2. **Real 1.0 with a new tag scheme** (e.g. `cr-1.0.0`) plus an updater change shipped first in a
   4.x release that understands it. Every copy older than that 4.x release stays stuck on 4.x
   until the user installs by hand.
3. Delete or rename the VodClipper 1.x tags. Loses history; not recommended.

## 6. Changes made in this milestone

- `src/server.py`: `/api/status` checks whether faster-whisper, yt-dlp and onnxruntime are
  installed without importing them (`_has(..., load=False)`: already loaded, or
  `importlib.util.find_spec`). Real use still imports them exactly as before. Trade-off: if a
  package is present but broken (a missing DLL), status now says "installed" and the failure
  shows when it's first used, through the existing error paths.
- `tests/test_startup.py` (new, in CI): importing the server and answering status load none of
  the heavy packages. Fails on 4.1.5 (checked).
- `scripts/bench_startup.py` (new): the numbers in §4.
- `build.yml`: package size breakdown; startup time and idle memory of the packaged app.
- `CHANGELOG.md`: the introduction had ended up below the 4.1.5 section; moved back to the top.

## 7. Not verified here

- Windows installer, upgrade and uninstall: covered by CI on this branch, not by hand.
- Real Twitch/YouTube VODs, slow PCs, GPUs: not available in this environment.

## 8. Milestone 2 (4.2.0) — what changed and what was measured

Windows baseline from the 4.1.6 CI run (first build with the new reporting): **570 MB unpacked,
installer 203 MB; the packaged app answered after 330 ms and held 56 MB once idle.** Biggest parts:
ffmpeg.exe 100.5, ffprobe.exe 100.3, TwitchDownloaderCLI 65.8, PyAV's FFmpeg libraries
(`av.libs`) 64.1, ctranslate2 59.4, onnxruntime 41.6, laughter model 19.9, yt-dlp.exe 17.0,
hf_xet 8.9, tokenizers 7.5 MB.

Done:
- **PyAV left out of the build** (a third copy of FFmpeg). faster-whisper imports it but only uses
  it to read files; every Clip Radar call passes samples decoded by the bundled FFmpeg, and
  `engine.transcribe` now decodes a path with FFmpeg too. `src/stubs/av` stands in for it in
  the build only; running from source is unchanged. The build fails if `av.libs` sneaks back.
  Checked with a local PyInstaller build: the frozen self-test imports faster-whisper with the
  stand-in and renders a clip. Captions through Whisper could not be checked locally (Hugging
  Face downloads are blocked here); the Windows CI self-test and e2e check them.
- **hf_xet excluded** (optional accelerator; huggingface_hub falls back to plain HTTP). The CI
  self-test downloads the Whisper model, which checks this.
- **Whisper memory:** at most two model sizes (Deep Scan and title listening can use two at
  once; one slot would reload on every alternation), released after 10 minutes idle, never
  mid-transcription. Covered by `tests/test_memory.py` with a stand-in model.
- **UI pollers rest while hidden** (verified in headless Chromium: 3 status calls in 11 s
  visible, 0 hidden, one immediately on return). The browser-mode keep-alive ping still runs.
- **Dead code:** the helpers listed in §2 removed after checking UI, tests and workflows.
- **Reliability:** Deep Scan reports unreadable audio as an error, survives a zero-length
  video (it crashed with `'gems'`), and `extract_audio`/`probe` have timeouts; crop/filter
  failures get a plain-words message. `tests/test_deepscan.py` and `tests/test_retry.py` (the
  latter uses FFmpeg's real crop error text).
- **Probe cache bounded** (live recordings added an entry per probe).

Measured on this PR's Windows CI: **493.6 MB unpacked (was 570), installer 182 MB (was 203)**;
`av.libs` and `hf_xet` gone; the self-test's Whisper captions passed (model downloaded without
hf_xet, speech transcribed with the PyAV stand-in), and so did the e2e suite. Idle memory 56 MB
(same as 4.1.6). Startup read 1093 ms against 330 ms on 4.1.6, but nothing on the startup path
changed and both were single starts on a shared runner; the smoke test now reports the median of
three starts so later builds can be compared properly.

Looked at and not done:
- **Shared FFmpeg build instead of two static exes.** Measured: the BtbN GPL shared build is
  186 MB without ffplay against 201 MB for gyan's two static exes. A 15 MB saving doesn't
  justify changing the FFmpeg build every render depends on.
- **One yt-dlp.** The exe is required (YouTube chat replay only works through it) and the
  Python package is under 5 MB in the build (not in the top 15). Kept as the fallback.

## 9. Milestone 3 (4.3.0) — ranking, chat titles, clip counts, live/VOD parity

### F-spam (measured)
`ChatScorer` is shared by VODs and live, so the fix sits there: "F" (any number of F's), "o7", a
lone digit (poll votes) and a lone "+", "-", "." or "^" now count for nothing, like bot posts.
"W", "L", "?" and "!" still count. The emote signal and the titles' mood ignore them too.
- VOD test (30 min, calm chat, 60 chatters typing F vs 60 laughing): the F wave scored
  **8.96 against 10.0** before (a top clip); **1.73** after.
- Live test: with weight 0.15 and then 0.05 an F wave in a calm chat was still cut as a live
  moment ("chat explodes", quality 62–86); at 0 it isn't. Hence 0.
- Covered in `test_signals.py` and `test_live.py`.

### Clips to suggest
- The menu goes 5 to 60 in steps of 5, then 70, 80, 90, 100.
- **Silent cap found and fixed:** with a number chosen, `pick_clips` still dropped
  every moment under `max(1.0, 15% of the top score)`. On a quiet 2-hour stream with three clear
  moments, asking for 20 returned **4**, without a word. Now a chosen number fills down to
  anything above the stream's normal level and returns 20. When a video really has fewer separate
  moments (60 asked of a 10-minute video), the app says "Found N of the 60…" instead of cutting
  the list short quietly. Automatic is unchanged.

### Titles from chat (`src/chattitles.py`)
Plain counting, no model or network:
- **Support is distinct chatters.** A phrase needs at least 3 of them and 12% of everyone who
  chatted in the window, so one viewer can't make a title however often they type.
- **Phrases are 2–5 words.** A longer phrase wins when nearly as many people typed it.
- **Never counted:** bots, commands, notices, F-spam, links, @mentions, numbers and chatters'
  usernames. Emote-only or filler-only phrases and everyday reactions ("let's go", "no way") are
  skipped as well.
- **The title says whose words they are:** `Chat: "…"`.
- **Where it ranks:** a strong line said on stream stays title A; chat's phrase comes next, ahead
  of the templates.
- **Settings:** Settings › Titles › "Titles from what chat said" (on by default). Titles stay
  editable as before.
- **Failure:** any error returns no chat title, and the normal titles are used.

### Clip ranking › What it learned
The ranking adjuster is a logistic regression (a small statistical model) fitted on the PC
from keep/skip decisions. The new view says that in so many words and never calls it AI. It
separates:
- **facts:** stored decisions, posts, clips added by hand, skip reasons, trims, and how kept and
  skipped clips differed on each signal;
- **learned:** what the fitted model moves up or down, and how strongly;
- **built in:** the fixed scoring rules.

It says exactly what's missing when there isn't enough data ("Needs 5 more decisions, 2 more
skipped clips"), when another streamer's picks are borrowed, and when learning is off. Forget
already existed and empties it. `test_ranking_explain.py` covers this.

### Live and VOD: what's shared, what isn't

| Part | VOD | Live | Notes |
|---|---|---|---|
| Chat cleaning (bots, raids, spam, F-spam) | ✓ | ✓ | same `ChatScorer` |
| Loudness, laughter/screams, chat surge, sudden-reaction, lone-noise | ✓ | ✓ | same `score_signals` and `detect` code |
| Channel emote waves | ✓ | ✓ **new** | live counted none before; now shares `detect.emote_in` (test: same per-second count as a VOD) |
| Chat delay worked out | ✓ | ✓ | live re-estimates as the stream goes |
| Clip start/end (`smart_bounds`) | ✓ | ✓ | live waits until the reaction has died down |
| Learned ranking from keeps/skips | ✓ | ✓ | same adjuster; refreshed when you decide |
| Learned trim shifts | ✓ | ✗ | a later end would make every live clip wait longer; left out on purpose |
| Titles (speech, chat reaction, **chat phrase**) | ✓ | ✓ | same `_title_clip` |
| Content check, captions, layout, framing, export presets, render queue | ✓ | ✓ | same render path |
| Number of clips | chosen or automatic | as they happen | live uses sensitivity and a minimum score instead |
| Deep Scan (quiet gems) | ✓ | ✗ | needs the whole stream's transcript |
| Ranking against the whole stream | ✓ | partial | live can only compare with what's been recorded so far |

## 10. Milestone 4 (4.4.0) — hashtags, TikTok Studio, cleanup, sounds

Version decision (owner, 2026-10-21): **no "Clip Radar 1.0" rebrand; numbering continues** (4.x,
then 5.0 when warranted). §5's migration work is dropped from Milestone 7.

- **Hashtags (`src/hashtags.py`).** File names and post captions have separate lists.
  - **File-name tags:** literal `#Tag` joined with `_`, and only Windows-forbidden characters
    removed. Duplicates are dropped ignoring case. Names are capped at 150 characters before
    the extension; tags drop off the end before the title is cut, and the title never goes
    below 24 characters.
  - **Kept on retry:** the name is chosen once in `_file_name`, so a retry keeps it, and a clash
    adds " (2)" after the tags.
  - **Post tags:** global, per-streamer and per-platform lists, with Clip Radar's own tags
    optional.
  - **Tests:** `test_hashtags.py` covers duplicates, Unicode, long names, forbidden characters,
    retry, clashes, separation of the two lists and persistence. The e2e checks a real render's
    file name. Only checked on Linux here; `#` is a legal NTFS filename character, and Windows CI
    runs the same tests.
- **TikTok Studio handoff (`src/studio.py`).** The caption is copied, the file is selected in
  Explorer (`explorer /select,`) and the upload page is opened.
  - The app records only `handoff.at`, never "posted".
  - **Removed:** `tiktok.py` (OAuth/PKCE, Content Posting API), its settings, hands-free "send to
    drafts" and `mock_tiktok.py`. The stored `tiktok.dat` (client secret and tokens) is deleted on
    start.
  - **Moved:** the DPAPI helpers Twitch sign-in and the updater used went to `src/secret.py`
    with the same blob format, so saved sign-ins still decrypt.
  - **Upload URL:** `https://www.tiktok.com/tiktokstudio/upload` (overridable with
    `CLIPRADAR_STUDIO_URL`). It **could not be loaded from this environment**, because TikTok is
    blocked by the proxy and no official page in search results states it. Check it on a PC.
- **Cleanup (`src/cleanup.py`).**
  - **Temp:** files untouched for 3 days, plus downloads that stopped over a day ago (on).
  - **Skipped:** the file of a hands-free render you skip (off). Only items marked `auto`.
  - **VODs (off):** deleted only when every clip is decided, every kept clip rendered, no render
    is queued, rendering or failed, the VOD isn't open or live, and it hasn't been opened for N days.
  - **What's removed:** only the video and its analysis; the clip folders are excluded explicitly.
  - **Failures:** reported in Settings and the log, never raised.
  - **Tests:** `test_cleanup.py`, 24 cases.
- **Sounds.** None existed for the queue or finished renders before this; only the hotkey had one.
  They play from the server at the single place each event happens: queue add (not for duplicates
  or hands-free adds) and render done. So it's once per event, even while minimised. They're
  logged for tests (`test_retry.py`).
