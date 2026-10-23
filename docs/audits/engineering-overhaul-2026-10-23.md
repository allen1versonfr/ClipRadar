# Clip Radar engineering audit and render-pipeline hardening — 2026-10-23 (4.13.0)

Scope: the brief asked for an audit of the whole app, a hardened render pipeline, and an evaluation of
a React UI, SQLite, Pydantic and the desktop shell. It asked for no rewrites without evidence. This
round puts reliability first. It builds on the earlier audits in this folder
([clip-radar-5](clip-radar-5-audit.md), [reliability follow-up](clip-radar-reliability-follow-up-2026-10-20.md),
[React readiness](react-readiness-2026-10-22.md)) rather than redoing them.

Environment: Linux container, 4 cores, 15 GB RAM, Python 3.13, FFmpeg 6.1.1 (Ubuntu), no GPU.
Windows was checked through the repo's own CI (windows-latest), which builds the exe and installer,
installs them, updates over the previous release and runs `tests/e2e.py` against the packaged app.
**Not tested here:** the user's Ryzen 5 5600X / RX 7600 XT PC, AMD's AMF encoder, and code signing.

## 1. What the app is (measured from the repository)

| Part | What it is |
|---|---|
| Desktop shell | pywebview window (`src/app.py`) on a local HTTP server (`src/server.py` + `src/routes.py`, 97 routes, JSON only, localhost-only with an Origin check) |
| UI | One hand-written page: `src/ui/app.js` (3.6k lines), `index.html`, `app.css`. A React build is served instead when `src/web/` exists (groundwork from 4.12.0; no `web/` yet) |
| Analysis | NumPy signal analysis of audio, chat and audio events (`engine.py`, `detect.py`, `signals`, `gems.py`), ONNX laughter detector, faster-whisper for captions and Deep Scan, face framing (`framing.py`, ONNX UltraFace) |
| Render | FFmpeg filter graphs built in `engine.build_filter` / `render_clip`, with a `-progress` reader, a 120 s no-progress watchdog, GPU→CPU and layout→blur-fit fallbacks |
| Jobs | In-process render queue (`server.queue`, workers bounded by Settings › Performance and a free-memory check), separate analysis jobs (`server.jobs`) |
| Persistence | Atomic JSON (`persist.py`: write-to-temp + replace, versioned queue file, per-project `project.json`, `analysis.npz` cache) |
| Packaging | PyInstaller one-folder build, Inno Setup installer, GitHub Releases. The updater checks each download's SHA-256 against the digest GitHub reports for the asset |
| Tests | 29 suites of plain-`python` scripts (not pytest), a Playwright UI smoke test, and `tests/e2e.py` against the packaged exe on Windows CI |

## 2. Baseline (before this round, `main` at 4.12.2)

- **Tests:** all 29 suites passed, including `test_ui` with Playwright 1.56. In CI the unit tests run
  before FFmpeg is fetched, and the runner has no FFmpeg of its own, so the FFmpeg parts of
  `test_quality` and `test_retry` were skipped on Windows. Real renders were only checked by `e2e.py`.
- **New real-media matrix** (`tests/test_media_matrix.py`, written first and run on the old code):
  72 of 73 passed. Every layout (split, full, cam, blur, zoomed, a crop at the very edge) rendered
  correctly from 1080p60, portrait 1080×1920, odd 853×479 (4:4:4), tiny 160×90, silent,
  variable-frame-rate, HEVC 10-bit, VP9/Opus, rotated phone video, and a source whose size changes
  mid-clip. Every export format passed as well (HEVC, VP9 webm, mov, 0.667×, 0.5× at 30 fps with a
  bitrate, 16:9, 1:1, watermark with loudness and volume, denoise with voice). Each output was checked
  for size, yuv420p, sound only when the source has it, length, a first frame at 0:00 with the edit
  list ignored, a clean decode to the end, and no temporary files left behind.
  **The one failure:** a clip starting after the end of the video made FFmpeg "succeed" with a
  near-empty file. In the app, that spent the automatic retry and then reported "came out broken twice".
- **Known symptoms the brief asked about:**
  - *Crop "Failed to configure input pad" / "Error reinitializing filters" / -22 / encoder "before EOF".*
    The encoder lines come after the filter error: the graph never produced a frame, so the encoder
    never opened. They aren't separate bugs. The concrete cause in code (a zero-size facecam crop at
    the frame edge) was fixed on 2026-10-20 and is covered by the edge-crop rows of the matrix. Sources
    that change size mid-recording are covered by the `scale` the graph applies before the crop (matrix
    row "size change mid-clip"). When a crop still can't be built, the render falls back to blur-fit
    and says so.
  - *Stuck at 0%.* The watchdog stops a silent FFmpeg after 120 s. The preparation stages showed
    invented 5% and 25% values, so "stuck at 5%" while Whisper ran looked like a hang.
  - *Retry keeps failing.* Retry re-ran deterministic failures unchanged, with no record of earlier
    attempts. You couldn't tell "same failure again" from "a new failure".
  - *Source exploration never finishes.* yt-dlp has a 180 s no-output watchdog and socket timeouts
    (2026-10-20). Not changed here.

Performance baseline on this machine (CPU only, `scripts/bench_startup.py --runs 5`, `scripts/bench_pipeline.py`):

| | 4.12.2 |
|---|---|
| Server import → first `/api/status` | 124 ms (import 92 ms) |
| Memory after start | 40.6 MB |
| Find clips, 30-minute 720p stream | 6.11 s, 67.6 MB peak |
| Render 30 s clip, 1080×1920, x264, through the queue | 18.53 s (1.62× real time), 171.7 MB peak |

## 3. Changes in this round

### Render lifecycle (`server.py`, `persist.py`, `routes.py`, `ui/app.js`)

- **Explicit stages:** `stage` is `queued → validating → preparing → rendering → verifying → done`,
  or `error` / `cancelled`, with `stage_at`. `status` keeps its old values, so the API contract, the
  UI and the tests that read it are unchanged.
- **Honest progress:** `progress` is FFmpeg's own `out_time` divided by the clip's length, and is set
  only while rendering. The UI shows a percentage only in that stage. The other stages show their step
  and elapsed time, with a moving bar that respects reduced motion.
- **Attempt history:** each attempt records its number, start and end, outcome
  (`done`/`error`/`cancelled`/`interrupted`), the stage it reached, its error kind, the encoder, the
  exit code and any fallback. A failure also records the FFmpeg command and the end of stderr, with
  tokens redacted and the home folder written as `~`. The last 8 attempts are kept and saved with the
  queue. Exports › details lists every attempt.
- **Error kinds:** `classify_render_error` returns one of `layout`, `encoder`, `captions`,
  `source_missing`, `source_damaged`, `source_range`, `output_folder`, `disk_full`, `file_locked`,
  `memory`, `stalled`, `broken_output`, `interrupted` or `unknown`, with a `retryable` flag. Only a
  stall is retried by itself, and only once. Everything else needs a cause fixed first and says what
  to change. The user-facing messages are the existing ones.
- **Validation before any work:** the source exists and probes, the clip lies inside the video, it's at
  least 0.5 s long, and the clips folder accepts a test write. `engine.render_clip` also refuses a range
  outside the video (`RenderInputError`) instead of letting FFmpeg write an empty file. This fixes the
  matrix failure.
- **Verification:** the output must have the right length, sound when the source has sound, and a last
  1.5 s that decodes. A failing file is deleted and never shown as finished.
- **Crash-loop guard:** a clip interrupted mid-render by a crash or forced close starts again on the
  next launch. If it's interrupted twice in a row, it waits for Retry, so a clip that crashes the app
  can't do it on every start.
- **Retry** clears the error kind, the retryable flag and the interruption count, and keeps the file name.

### Tests and CI

- `tests/test_media_matrix.py` (new, 73 renders, about 2.5 min) and `tests/test_render_lifecycle.py`
  (new, 45 checks with real FFmpeg through the real queue and HTTP API). The lifecycle test covers the
  stage order, no percentage outside encoding, the attempt record, a clip past the end, a deleted
  recording, a real FFmpeg filter failure keeping its command, exit code and stderr, Retry after the
  cause is fixed, stall retried once then stopped, cancel stopping FFmpeg (checked on the process
  table) and leaving no files, a truncated output, an output missing its sound, the crash-loop guard,
  and redaction.
- **CI:** a new step runs both, plus `test_quality`, with the **bundled Windows FFmpeg** on PATH. Before
  this, real renders on Windows were only tested end to end.
- **Lint:** `ruff check` with a deliberately small rule set (`pyproject.toml`: syntax errors, pyflakes,
  mutable defaults, return-in-finally, duplicate set items). It removed eight imports left in `server.py`
  by the routes split (`routes.py` imports what it uses itself, so the PyInstaller bundle is
  unaffected), a duplicate stop-word, and unused variables.

## 4. After

- **Tests:** all 31 suites pass here (29 existing plus 2 new), including the UI smoke test. The matrix
  passes 73 of 73. Windows CI: see the result recorded below.
- **Performance** (same machine and scripts; the pipeline benchmark was run twice after the change):

  | | 4.12.2 | 4.13.0 |
  |---|---|---|
  | Server ready (median of 5) | 124 ms | 140 ms |
  | Memory after start | 40.6 MB | 40.5 MB |
  | Find clips, 30-minute stream | 6.11 s | 8.13 s, 6.71 s |
  | Render 30 s clip through the queue | 18.53 s (1.62×) | 19.52 s, 19.39 s (1.55×) |
  | Peak memory after the render | 171.7 MB | 173.9 MB, 174.0 MB |

  The scan code didn't change, so its spread (6.1 to 8.1 s) is this container's noise, and so is the
  startup difference. Rendering costs about 0.9 s more per clip (about 5%). That's the new
  verification, which decodes the last 1.5 s of the output, plus one cached `ffprobe` during
  validation. No optimisation was claimed or made in this round.

## 5. Decisions on the brief's proposed stack

| Proposal | Decision | Why |
|---|---|---|
| Keep pywebview | **Kept** | Every native need goes through the server (no pywebview-specific UI code); startup is 124 ms to a served UI; nothing measured points at the shell. Electron/Tauri would add a second runtime and a new updater for no measured gain. |
| React + TS + Vite | **Not started this round** | The server side is ready (4.12.0). Porting 3.6k lines of UI, including the canvas preview that must match FFmpeg pixel for pixel, is a multi-step project best done screen by screen behind `CLIPRADAR_UI=web` (order in `react-migration.md`). Starting it half-finished would add npm to every build with nothing shipped. Reliability came first. |
| Zustand / TanStack Query / Zod / Vitest | With React, if it's done | Only useful with the React app. |
| SQLite | **Not adopted** | The data is a few hundred queue items and one JSON file per project, written atomically and versioned, and never queried relationally. SQLite would add a migration and a locking model with nothing gained today. Revisit if history or search across projects is wanted. |
| Pydantic | **Not adopted** | Not a runtime dependency today (several MB more in the frozen app). Request bodies are validated in the route handlers, and restored queue items in `persist.restore_queue`. |
| Ruff | **Adopted** (CI) | Cheap, found real dead code. |
| mypy | Not adopted | The code is untyped dict-passing in most places; a meaningful mypy pass is a refactor of its own. |
| pytest | Not migrated | The 31 script-style suites run with plain `python` in CI and locally. Wrapping them in pytest adds a dependency without catching anything more. New tests follow the same style. |
| OpenCV | Not added | Framing uses ONNX UltraFace with NumPy, which works (matrix rows for full and zoomed layouts pass). OpenCV would add tens of MB to the bundle. |

## 6. Data, backup, recovery and rollback

- **No format changes** to any saved file. The queue file is still `v: 1`. The new fields (`stage`,
  `stage_at`, `history`, `error_kind`, `retryable`, `interrupted`) are optional; an older Clip Radar
  reading them ignores them, so rolling back to 4.12.x keeps every queued and finished clip.
- **Rollback:** download the previous release's `ClipRadar-Setup.exe` from GitHub Releases and run it.
  The in-app version list only installs newer versions. Installing an older version over a newer one
  wasn't tested in this round. Settings and projects are in the data folder, which the installer doesn't touch.
- **Recovery:** interrupted renders restart once on their own and then wait for Retry. Temporary render
  files (`vc_*.part.*`, `vc_*.ass`, caption fonts) live in the clips folder, and a crash's leftovers are
  removed on the next start (`_clean_leftovers`). The folder-write check's `.vc_write_*` file is deleted
  as soon as it's written.

## 7. Build and run

```sh
pip install -r requirements.txt pywebview          # plus FFmpeg on PATH (Linux/macOS) or tools/ (Windows)
python src/app.py                                   # the app window
python src/app.py --browser                         # in a browser, http://127.0.0.1:7860
python tests/test_render_lifecycle.py               # any suite: python tests/<name>.py
python tests/test_media_matrix.py                   # real-media render matrix (CLIPRADAR_MATRIX=quick for a short run)
python -m ruff check src tests scripts
scripts\build_exe.bat                                # Windows: exe + installer (needs Inno Setup)
```

## 8. Known limitations and next steps

1. **Not measured on the target PC.** AMF (RX 7600 XT) against x264 is the most useful number still
   missing. The encoder probe picks AMF automatically when it works, and a failed GPU encode falls back
   to the CPU. Run `scripts/bench_pipeline.py --presets` on that PC to get the trade-off.
2. **FFmpeg can outlive a crashed app on Windows.** The child process isn't tied to the parent. The
   fix is a Windows Job Object with kill-on-close, which needs a breakaway flag for the updater's
   installer, or the installer dies with the app. It wasn't done blind; it needs the Windows e2e
   update test to prove it.
3. **Preparation stages have no percentage** (by design). Whisper and face framing report their stage
   and elapsed time only.
4. **React port, project/editor model work, autoframing and auto-effects** from the brief haven't been
   started in this round. They should come after this pipeline has had a release cycle of real use.
5. **No code signing**, so SmartScreen warns on first install. That needs a certificate.
