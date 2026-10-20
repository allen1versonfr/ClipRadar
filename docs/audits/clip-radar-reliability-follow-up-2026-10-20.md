# Clip Radar reliability follow-up audit — 2026-10-20

## Scope and confidence

Reviewed the current `main` source files through GitHub and applied a focused patch on branch `audit/render-reliability-hardening`. This is a static code review plus regression-test additions; the patch has **not yet been run on Windows or against a real VOD**, and no CI result is claimed here. The user's supplied FFmpeg log is consistent with a crop filter receiving invalid geometry, but that exact run is not reproducible from the log alone.

## How many previous audit findings apply?

Of the prior audit's nine numbered findings:
- **Directly relevant:** F-01 (failure state can get out of sync with output), F-03 (completed outputs must be validated), F-04/F-09 (failure handling and visible persistence), and F-06 (coverage for long-running HLS/VOD behavior).
- **Related reliability hardening:** F-02 (validate malformed state/settings), F-05 (bounded input handling), F-07 (reproducible dependencies), F-08 (release/supply-chain checks).
- The new report adds distinct render geometry, watchdog, progress and worker lifecycle checks. The earlier static audit did not prove that these specific user-visible symptoms share one root cause.

## New findings

### R-01 — High: facecam crop can become zero-sized at the image boundary

**Root cause found in code:** `build_filter()` clamped the facecam origin to normalized 0..1, then calculated the remaining width/height from `source_size - origin`. At x=1 or y=1, the origin could equal the image dimension, leaving a zero-width/height crop. Very small user crop rectangles could also round down to zero after forcing even dimensions. FFmpeg then reports `Failed to configure input pad on Parsed_crop_0`; the encoder errors that follow are secondary because the filter graph never emitted frames.

**Patch:** a single bounded crop helper now clamps origin and dimensions to even, positive values within the usable source frame. Both split and camera-only layouts use it. The gameplay crop origin is also rounded to even coordinates.

**Regression test:** added a boundary case with `cam=(1.0, 1.0, 0.0, 0.0)` for both split and camera-only layouts. It checks generated crop dimensions and the first source crop's bounds.

**Remaining risk:** the supplied log does not identify which crop node failed. Other crop nodes, unusual source dimensions, malformed framing paths, or FFmpeg build differences still need the render matrix run. The fix prevents the concrete zero-sized facecam crop path found in source; it cannot guarantee that no FFmpeg filter error can ever occur.

### R-02 — High: VOD exploration can wait indefinitely for a quiet yt-dlp child process

**Root cause found in code:** the CLI download path iterated `p.stdout` and only checked cancellation after a line arrived. If yt-dlp remained alive but stopped producing output, the worker could remain stuck in the acquisition/exploration step without reaching the cancellation check.

**Patch:** added a watchdog that terminates a CLI download process after 180 seconds without output, then returns a clear retryable error. Added a 30-second socket timeout. The Python yt-dlp path now has socket, extractor and file-access retry limits as well.

**Remaining risk:** a quiet but legitimate merge/post-processing phase could be terminated if it emits no output for three minutes. Test with large Twitch/YouTube VODs, slow networks, and long final merges before release. The watchdog is a safety net, not a hard end-to-end deadline for the entire VOD workflow.

### R-03 — High: FFmpeg render can stall without updating progress

**Root cause found in code:** render progress is read from FFmpeg's `-progress pipe:1`; a hung child that stops emitting progress can block the reader loop indefinitely. Before this patch, no inactivity watchdog stopped that child.

**Patch:** added a 120-second FFmpeg no-progress watchdog. A stalled process is killed and the error is surfaced through the existing render failure path; temporary render output is cleaned by the existing `finally` block. Caption/safety preparation now exposes 5% then 25% queue progress instead of leaving the job at 0% for the whole preparation stage.

**Remaining risk:** Whisper/transcription and face tracking can legitimately take a long time and do not yet report granular progress in every code path. These stages still need cancellation and heartbeat coverage. Validate that no-progress thresholds suit slow hardware before release.

### R-04 — Medium: secondary queue worker can spin while memory is low and no work is queued

**Root cause found in code:** worker indexes above zero checked low memory before checking whether there was a queued item. The worker could sleep/recheck indefinitely with an empty queue instead of returning to its normal wait loop.

**Patch:** the secondary worker now waits for memory only when queued work exists and another render is already running; with no queued items, it returns to the outer wake loop.

### R-05 — Medium: retry resets visible state but cannot cure deterministic input failures by itself

The retry endpoint already requeues errored jobs and keeps the output name. The existing end-to-end test simulates a disk-full failure followed by a successful retry, but it does not exercise a real FFmpeg filter failure. A retry will predictably fail again if the same invalid crop/settings/source are passed unchanged.

**Patch:** crop geometry is normalized at its source so retries no longer repeat the specific zero-size facecam crop defect found. The retry handler also clears stale ETA/progress/error state while preserving the selected output name.

**Remaining risk:** do not advertise retry as a universal repair. Persistent disk-full, missing-source, corrupt-input or unsupported-encoder errors need a corrected cause or a deliberate fallback. Add a real failure-then-retry test for crop/filter failures.

## Optimization / debloating assessment

Do not delete features or folders blindly. The current architecture has a large `src/server.py` and `src/engine.py`, plus overlapping orchestration responsibilities. The safest optimization order is:

1. **Reliability first:** get the crop boundary test, existing export tests, persistence tests and full E2E suite green on CI; test on Windows with the bundled FFmpeg.
2. **Measure before simplifying:** capture per-stage timings, memory high-water marks, FFmpeg command/encoder, source dimensions, and last-progress timestamps. Avoid collecting private video contents or tokens.
3. **Remove proven waste:** profile repeated probes, repeated transcript/framing work, duplicate queue entries, and expensive per-render model loads. Preserve caches only when invalidation is correct.
4. **Split by responsibility without behavior changes:** separate HTTP route handling, queue lifecycle, and render orchestration from the large server module after characterization tests exist.
5. **Dependency/release hygiene:** lock release dependencies and test bundled FFmpeg/ffprobe versions. Do not strip modules or UI assets until imports, packaging and E2E tests prove they are unused.
6. **No silent failure:** all output creation must be temporary + validated + atomically promoted; all failed jobs must reach a terminal error state with a retry path; all long-running stages must expose progress, cancellation, and an inactivity/deadline policy.

## Patch summary

- `src/engine.py`: safe crop geometry; VOD CLI no-output watchdog; yt-dlp network/retry bounds; FFmpeg no-progress watchdog.
- `src/server.py`: nonzero preparation progress; fix secondary worker's low-memory idle loop; clear stale retry UI state.
- `tests/test_exports.py`: crop-boundary regression case.

## Verification status

- [x] Source reviewed and changes committed to a dedicated branch.
- [x] Regression test added for facecam crop geometry.
- [ ] Run `python tests/test_exports.py` and the other unit scripts.
- [ ] Run the full GitHub Actions CI and end-to-end suite.
- [ ] Test actual FFmpeg rendering for split/cam/blur/full, with captions on/off, odd source dimensions, crop rectangles at all edges, GPU enabled/disabled.
- [ ] Verify failed render → retry → valid MP4 on Windows, including the user's exact failure mode.
- [ ] Verify VOD exploration returns an actionable error after a stalled downloader and remains cancellable.
- [ ] Verify slow-PC behavior so the watchdog thresholds do not kill legitimate long-running operations.

## Recommendation

The crop boundary bug is a concrete and plausible cause of the supplied `Parsed_crop_0` error, and the source now guards that case. The watchdog and progress changes address two separate hang/stall paths found during review. **This is not yet a claim that every render bug is fixed**: merge only after CI and Windows end-to-end verification pass.
