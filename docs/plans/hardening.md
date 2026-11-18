# Plan: harden what's there before adding more

**Goal:** fewer things that break, faster fixes when they do, and code where a change in one place doesn't
risk another. No new features while this runs.

Line numbers are from Clip Radar 4.51.0 and will drift. The function names are what to search for.

## 1. Why

- **Pace.** 99 releases. 139 commits since 1 October, about 17 of them fixes to recent work.
- **The TikTok upload is the part that breaks most.** It works by driving TikTok Studio's web page
  (`src/studio_auto.py`). It needed fixes in 4.29, 4.36, 4.37.1, 4.40, 4.45, 4.50.1 and 4.50.2. Each fix was
  worked out after the fact from what the owner saw, because a failure records the message and not what the
  page looked like. 4.51.0 added a second upload of the same kind, YouTube Studio (`upload_youtube`).
- **Two very big files.** `src/engine.py` is 4,805 lines and `src/server.py` 3,104. Each mixes downloading,
  analysis, picking, rendering and Whisper (engine), or projects, the render queue, listening, live and
  previews (server). Tests patch `engine.<name>` in 57 places, so moving code around is risky unless it's
  done with care.

## 2. What already works (don't rebuild it)

- **A failed upload already falls back to doing it by hand.** In `routes.py` (the `auto` branch near line
  815), a `StudioError` selects the clip in File Explorer and copies the caption. When the window is gone it
  opens TikTok Studio in the person's browser, sets `handoff.auto = "failed"` with the step, and the UI
  (`web/src/features/exports/postStatus.ts`, `handoffStatus`) tells them how to finish. It never tries again
  after Post was pressed (4.45).
- **Failures are logged.** `src/errorlog.py` writes `errors.jsonl` (secrets blanked, home folder as `~`).
- **A stand-in TikTok page.** `tests/fake_studio.py`, with modes for each past breakage ("nopaste", "laggy",
  etc.), and `tests/test_studio_auto.py` / `test_studio_retry.py` / `test_youtube_auto.py`.

What's missing is **evidence** (what TikTok's page looked like when it failed), **early warning** (finding out
TikTok changed before a real upload fails), **a measure of reliability in real use**, and **smaller modules**.

## 3. Phases (one pull request each, in this order)

### Phase 1: record what the page looked like when an upload fails

In `studio_auto`, before raising a `StudioError` from `_upload_once` or `upload_youtube`, take a
**page fingerprint** and record it with `errorlog.record("upload", message, step=..., site="tiktok"|"youtube",
fingerprint=...)`. The fingerprint has no personal content:
- `location.pathname` (no query string),
- for each entry in `SELECTORS` (and the YouTube ones), whether it matches, is visible, and is enabled,
- the sorted, de-duplicated `data-e2e` attribute values on the page (capped at 200),
- the text of up to 40 buttons, each cut to 30 characters, with anything that looks like an @handle,
  hashtag, number or e-mail blanked,
- `document.documentElement.lang`, the window size, and the Clip Radar version.

Taking the fingerprint must never raise: wrap it, give it a 2 s limit, and if it fails record without it.
The caption, the file name and the account name must never appear in it. Test that with a caption and an
account name put in the fake page.

**Tests:** `tests/test_studio_auto.py` against `fake_studio.py`. A failure records a fingerprint with the
expected selector results. A page whose script throws still records the error. No caption text, file name or
handle is in the record.

### Phase 2: a check of TikTok's page that uploads nothing

`scripts/studio_check.py` (developer tool, not shipped) and an internal `studio_auto.check_page(site)`. It
opens Clip Radar's own browser profile on the upload page, waits for it, runs the same fingerprint, and
reports PASS/FAIL per selector. Nothing is put in and nothing is pressed. With `--file` it may also put a
short test video in to check the caption box shows, then leave the page with Discard. That part is off by
default.

- The owner runs it before each release that touches uploads, and whenever TikTok seems to have changed.
- Save its output as a fixture (`tests/assets/studio_fingerprints/<date>.json`). A unit test checks that every
  selector `studio_auto` relies on was found in the latest saved fingerprint. When TikTok changes, the diff
  between two fingerprints shows what changed.
- Optional, owner's call: the app runs `check_page` silently when its TikTok window first opens in a session,
  and writes one log line. No UI.

### Phase 3: a reliability report from real use

Today only failures are recorded, so there is no failure *rate*. Add one `clipradar.log` line per outcome for
the features that touch the outside world: TikTok upload, YouTube upload, VOD download (per platform), live
session start, render, Discord send. Use the same shape as the existing `Timing` lines, e.g.
`Outcome upload site=tiktok result=failed step=caption`.

`scripts/reliability_report.py LOGDIR --days 14` reads `clipradar.log*` and `errors.jsonl` and writes a
Markdown table per feature: attempts, successes, failures by step, the top 5 messages, and the newest
fingerprint for each upload failure. It's the reliability twin of the efficiency audit
(`docs/audits/efficiency-audit-2026-11-08.md`). The owner runs it on their PC and commits the report under
`docs/audits/`.

**Tests:** the parser on a sample log (`tests/test_reliability_report.py`).

### Phase 4: split `engine.py` along the sections it already has

`engine.py` is already in sections (helpers, storage, 1. acquire, 2. analyze, 3. pick clips, 4. render with
captions and encoders). Move them into modules, **one section per PR**, least tangled first:

| PR | New module | From engine.py |
|---|---|---|
| 4a | `storage.py` | storage: `storage_summary`, `delete_vods`, `delete_clips`, `recent_projects`, `_project_dirs`, … |
| 4b | `acquire.py` | 1. acquire: `acquire`, `_download_video`, `_ytdlp_*`, chat download and loading |
| 4c | `whisper.py` | captions: `_load_whisper`, `transcribe`, `_hear`, `release_whisper`, … |
| 4d | `render.py` | 4. render and encoders: `RenderOpts`, `build_filter`, `render_clip`, `video_encoder`, checks |
| 4e | `analysis.py` | 2. analyze and 3. pick clips: `Analysis`, `analyze*`, `pick_clips`, `smart_bounds`, … |

Rules for every move:
- `engine.py` keeps re-exporting the moved names (`from render import RenderOpts, render_clip, …`), so callers
  and `scripts/` keep working. Remove the re-exports only in a final PR, once nothing uses them.
- **The patch trap:** a test that does `engine.build_filter = fake` stops working once `render_clip` lives in
  `render.py` and calls its own `build_filter`. In each PR, change every test patch of a moved name to patch
  the new module, and add a check to the PR (a test, or a one-line grep in CI) that no test still patches
  `engine.<moved name>`.
- No behaviour changes in these PRs: only moves and import fixes. The full suite and `tests/e2e.py` pass
  unchanged apart from the patch targets.
- PyInstaller: check the new modules are picked up (`scripts/build_exe.bat`, the spec in `build.yml`).

### Phase 5: split `server.py` the same way

Same rules. Likely modules: `render_queue.py` (`queue_add`, `queue_worker`, `_render_item`, the checks and
error classification), `listening.py` (transcripts, `ensure_words`, `start_listening`, prehear, deep scan),
`live_glue.py` (`start_live_session`, `live_on_moment`, `PriorityGate`), and `previews.py`. `server.py` keeps
project state, jobs and the HTTP server. Shared state (`S.` in routes) needs a clear home first; decide that
in the first PR of this phase and write it down at the top of `server.py`.

### Phase 6: YouTube Studio in its own module

Move the YouTube half of `studio_auto.py` (`upload_youtube`, `_yt_*`, its selectors) into `youtube_auto.py`,
sharing the browser plumbing (`open_browser`, `_tab`, `_set_file`, `_navigate`, the fingerprint). This matches
the existing `tests/test_youtube_auto.py`, and a change to one site's page can't break the other's code.

## 4. The freeze (owner's call)

Proposal: until phases 1–3 are released, ship only fixes. The changelog keeps its sections, and a release
during the freeze has no **Added** section. Phases 4–6 can run alongside new work again, because they change
no behaviour.

## 5. What "done" looks like

- An upload failure in the field comes with enough to fix it without asking the owner what they saw.
- `studio_check.py` tells you TikTok changed its page before a real upload fails.
- A dated reliability report exists, with a failure rate per feature.
- `engine.py` and `server.py` are each under ~1,200 lines, with no test patching a name through `engine`.

## 6. Risks

- **Privacy in fingerprints.** They go in a file people attach to bug reports. Phase 1's tests must prove no
  caption, file name or account name gets in.
- **The check script and TikTok's terms.** It only loads the upload page the app already loads; it never
  posts. Keep it manual: no schedule.
- **Moves breaking the frozen exe.** Lazy imports inside functions (`import twitchclips` inside `combine`)
  hide modules from PyInstaller's analysis. Run the install test in CI on every phase 4/5 PR (it already runs
  on PRs).
- **Merge conflicts with feature work.** Each move PR should be small, merged quickly, and announced in its
  title ("Move render code to render.py, no changes").
