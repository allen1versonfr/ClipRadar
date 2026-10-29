# Changelog

All notable changes to Clip Radar (called VodClipper before 4.0) are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- Review: **the clip's inspector is back on the right**, as in the previous UI: Previous / Next, Skip and a larger Keep and render, the Clip / Captions / Text / Layout tabs and the title sit beside the preview, not under it. The preview and timeline stay in the middle (a little shorter, so the timeline is in view), and the clip list and inspector narrow on smaller windows; below 1000 px wide the inspector goes under the player.

### Fixed
- Review: "This video needs a copy the window can play" showed at the bottom of the window instead of on the player.

## [4.26.0] - 2026-10-29

Settings is grouped, with a menu that follows you and a search, and a new TikTok section.

### Changed
- **Settings is easier to find your way around.** Its sections are in four groups (Clips, Posting, Finding clips, App), with a menu beside them that follows where you are and jumps to any section, and a search that finds a section by its name, the words people use for it ("logo", "turbo", "webhook") or what it says. On a narrow window the menu sits above the sections. `#settings/<section>` opens Settings at that section.

### Added
- Settings › Posting › **TikTok**: how Upload to TikTok works on this PC, and Press Post for me (also in Exports).
- Settings › About: **Open the previous UI**.

### Tests
- `web/`: 139 Vitest tests, including the search and the TikTok section. `tests/test_web_settings.py` jumps with the menu, searches, opens `#settings/tiktok` and sets Press Post for me there.

## [4.25.0] - 2026-10-29

The new UI is now Clip Radar's UI. The previous one stays at `/legacy/` for a while ("Previous UI" in the top bar), with your clips and settings shared. Before the switch, an audit checked that everything the previous UI did is in the new one, and that it's accessible (`docs/audit-new-ui.md`).

### Changed
- **The new UI opens by default** (at `/`, in the app window). "Previous UI" in its top bar opens the previous one, and "Back to the new UI" there comes back.

### Added
- New UI, Exports: **posting**. TikTok: the caption with its count, Open TikTok Studio, Upload to TikTok in Clip Radar's own TikTok window, Press Post for me, and what happened last time. A campaign's clip: the link to your post (saved as you type), Copy submission and Send to Discord. A Posted column (Opened in Studio, In Studio, Posted, Upload stopped, Discord). Copy caption, Copy source link and Remove in the clip's panel, and the active campaign at the top.
- New UI: **Quit** in the top bar, and closing the window while something runs asks first, as in the previous UI. Before, closing the window during a render or download did nothing.
- New UI: badges on Review (clips to review) and Exports (renders running), and the status bar shows what's running (each a link to it) and an update when there is one.
- New UI, Review: a clip's own **volume** (Much quieter to Louder) and the **Hashtags** switch by the post caption.
- New UI: live clipping running when the window opens is shown, and Keep watching's next stream is followed, as in the previous UI.

### Fixed
- Upload to TikTok could hang after an earlier upload: it only loaded TikTok Studio's upload page if Clip Radar's TikTok window wasn't already on it, so the next clip waited for an upload box that wasn't there and stopped after a minute. Every upload now starts from a fresh upload page.
- New UI: text boxes, dropdowns and video players show a clear focus ring, and on touch screens every control is at least 44 px each way.
- New UI: "Sign in to Twitch" on Home opens the new UI's Settings at Twitch sign-in.

### Tests
- `tests/test_web_a11y.py` (in CI): axe-core's WCAG 2.2 A and AA rules on every screen and dialog of the new UI, the focus ring on every control Tab reaches, and 44 px controls on a touch screen.
- `test_ui.py`, `test_live_switch.py` and `test_studio_handoff.py` run on both UIs.

## [4.24.0] - 2026-10-29

Home in the new UI now opens videos and live sessions in the new UI's own Review. Nothing changes unless you open the new UI (`/next/`).

### Changed
- New UI: Home opens videos and live sessions in the new UI's Review, no longer in the current UI's. The new Review keeps the whole UI test contract (`tests/test_ui.py` passes on both UIs).
- New UI: the Exports preview plays at the level you set for the Review preview, and its own volume control sets that level, as in the current UI. The mouse wheel over Review's volume turns it up or down.

### Fixed
- New UI: leaving Review while it was still loading (the editing timeline's data, for example) logged "Clip Radar sent an answer this screen doesn't understand". An answer cut off on the way is now treated as a lost request, and an answer that really doesn't match says which fields in the log.

### Tests
- `tests/test_ui.py` checks the preview volume through `data-testid`s and `ClipRadarTest.volume()`, so the same checks run on both UIs. `web/`: 128 Vitest tests, including an answer cut off on the way.

## [4.23.0] - 2026-10-29

Every part of Review is now in the new UI: Exact preview and live sessions join it. Nothing changes unless you open the new UI (`/next/`).

### Added
- New UI, Review: **live sessions** (`#review/live/<session>`, also listed under Review). The session's state, what's recorded, chat per minute, moments and ads skipped; lowest score, how many clips, Hands-free, Clip last 30s (C) and Stop; new moments appear as they're clipped, and the overview shows the whole stream so far with the live edge.
- New UI, Review: **Exact preview**. A few seconds around the playhead rendered exactly like the export, played over the preview until you close it (or press Esc).

### Tests
- `web/`: 127 Vitest tests. `tests/test_web_review.py` makes an exact preview and closes it, and opens a live session on the real `/api/live` routes (Clip last 30s, how many clips, Hands-free, Stop).

## [4.22.0] - 2026-10-29

Review in the new UI now has the content check and the whole stream: the overview, detection settings, deep scan and title A / B. Nothing changes unless you open the new UI (`/next/`).

### Added
- New UI, Review: the **content check**. A Flagged tab (with a filter, and Leave all, Dismiss all, Exclude all), and on each clip its flags: try a fix before making it (mute, bleep, cut, hide in the title or post caption), dismiss, undo a fix, leave as is, review and edit the words, exclude or include, remove the clip (with Undo), and the history. Muted and bleeped words sound that way in the preview, as they'll render.
- New UI, Review: **the whole stream**. Under the clip, the stream's hype from start to end with every clip on it: click a clip to open it, or any other moment to preview it and Make a clip here. The header's facts (where it's from, chat, about how many strong moments, Tuned to your picks), Detection settings (stream type, how many moments, clip lengths, how many clips, chat or audio) with Try to compare before Apply, Suggest 5 more, Deep scan with its progress (quiet gems arrive in the Gems tab as it goes), and title A or B with the streamer's name.

### Tests
- `web/`: 123 Vitest tests. `tests/test_web_review.py` blocks a word in a suggested title and tries, makes and undoes the fix, excludes and includes the clip, removes it and brings it back; previews a moment from the overview and makes a clip there, opens a clip from it, tries and applies other detection settings (your clip stays), and sets the streamer's name for title B.

## [4.21.0] - 2026-10-28

Review in the new UI can now trim, preview the clip as it will render, and change text, layout and captions. Nothing changes unless you open the new UI (`/next/`).

### Added
- New UI, Review: the **Captions** tab. Fix what Whisper heard line by line, add a caption at the playhead, delete one, or Undo all edits; the line being said is highlighted as the clip plays, flagged lines are marked, and the content check looks again after each edit.
- New UI, Review: the **Text** and **Layout** tabs. Title and caption styles, fonts, highlight colour, sizes, words at once, outline, shadow, animation, line spacing, timing, templates and saved styles; the shape, layout, Auto-frame, following the face, the camera box (drag it on a frame), focus, zoom and saved framings. The same settings as the current UI, shown in the preview as you change them. Keep and Skip stay in view on every tab.
- New UI, Review: the **clip preview** shows the clip as it will render (every layout and shape, following the face, the title, captions with their style and animation, the watermark), drawn exactly like the current UI's. Drag the title, the captions or a watermark layer to move it, and Show what TikTok covers.
- New UI, Review: **trimming and the timeline**. Drag the clip's edges on the timeline (they snap to words and the playhead; Alt doesn't), or use Start / End (±1 s, ±1 frame, Set at the playhead), Restore original cut, Undo / Redo (Ctrl+Z, Ctrl+Shift+Z), frame steps and playback speed. The timeline shows loudness, the words heard, laughter, screams, cheers and chat. The current UI's editor keys work too.

### Fixed
- New UI, Review: an edit saved after a short pause (a title, a trim) could undo a Keep or Skip made within that half second, so the clip showed as to review again. The delayed save now sends the clip as it is when it's saved.
- New UI, Review: while titles were still being written, Restore original cut (or another edit) could be undone by the server's older copy of the clip arriving before the edit was saved. Background updates now leave a clip alone while one of your edits to it is waiting to be saved.

### Tests
- `web/`: 111 Vitest tests. `tests/test_web_review.py` also compares the new preview with the current UI's on the same frame (identical, with a title style, font and layout picked in the new UI), drags the title, sets the camera box and saves a framing, adds and fixes a caption line and undoes it, trims a clip (buttons, undo / redo, dragging the end, I, Restore) and checks Keep renders the trimmed cut.

## [4.20.0] - 2026-10-28

Every Settings section and the first part of Review in the new UI. Nothing changes unless you open the new UI (`/next/`).

### Added
- New UI, **Review** (first part): open a video from the list of open videos, go through its clips with the player, change the title or post caption, Keep and render, Skip, and keep or skip many at once (with Undo). The same keys as the current UI. Trimming, the layout preview, captions and live sessions open in the current UI on the same clip (Edit in the current UI).
- New UI, Settings › **Campaigns**: choose the active campaign, make and edit one (rules, caption text and hashtags, clip length, its own watermark or a copy of yours, the Discord webhook with Test, the message), Delete (asks first), Share, Import and From link. Every Settings section is now in the new UI.
- New UI, Settings › **Watermark**: text and image layers, where they go, size, opacity, rotation, font, colours, box and when they show, with a preview of a 9:16 clip you can drag a layer on to place it. Saved watermarks, Import…, From link… and Share… as in the current UI, and while the active campaign has its own watermark, that one is edited.
- New UI, Settings › **Export format**: the preset and quality with their notes, Advanced (your changes on top of the preset, with a way back to it; VP9 makes the file WebM), captions, loudness and voice clean-up, and the file-name template with token buttons and a preview.
- New UI, Settings › **Caption words**: replacements in captions and the words to recognise, saved when you leave the box.
- New UI, Settings › **Titles and captions**: which title new clips get, titles from what chat said, and hashtags on post captions (the Hashtags preview follows it).
- New UI, Settings › **Hashtags**: tags for file names and for post captions, per streamer and per platform, the suggestions, and the preview for a streamer. What you type is saved after a short pause, with the same settings as the current UI's.

### Tests
- `web/`: 96 Vitest tests. New `tests/test_web_review.py` (real server, generated video, real browser). `tests/test_web_settings.py` also checks the watermark (made, placed, dragged on the preview, shown by the current UI), a campaign (made with a copy of the watermark, active, Use mine instead), Export format, Caption words, Titles and captions and Hashtags against the real server and the current UI.

## [4.19.0] - 2026-10-28

Content check in the new UI's Settings. Nothing changes unless you open the new UI (`/next/`).

### Added
- New UI, Settings › **Content check**: what's checked and censored for each kind of word, sensitivity, mute, bleep or hide in captions, holding back flagged hands-free clips, how flags are reviewed, and your own blocked words. The same settings as the current UI's.

### Tests
- `web/`: 58 Vitest tests. `tests/test_web_settings.py` also checks Content check against the real server and the current UI.

## [4.18.3] - 2026-10-25

### Fixed
- Update checks, downloads and model provisioning use the renamed ClipRadar repository.
- Releases publish only the ClipRadar installer, portable ZIP and source archive. Removed the duplicate VodClipper-named installer; release validation rejects unexpected filenames before publication.
- Existing 4.x releases have a maintenance job to remove duplicate VodClipper executables when the ClipRadar installer exists. Genuine pre-4.0 archives remain intact.
- Updates no longer offer installation while a release is still missing its installer.

### Compatibility
- Copies still branded VodClipper (before 4.0) must install ClipRadar-Setup.exe manually to move to current releases. Their original installers remain archived.

### Tests
- Added release asset validation and cleanup policy tests; extended updater availability checks. React tests and production build pass.

## [4.18.2] - 2026-10-24

### Fixed
- With two streams being clipped at once, opening the second one (from Running now) jumped back to the first after a few seconds, so its clips couldn't be reviewed. The page took "a live stream is running and you're on another one" to mean that Keep watching had moved on to a new stream. Now the stream you open stays open; the page only moves on when the stream you're looking at ends and Keep watching starts its next one.

### Tests
- `tests/test_live_switch.py`: two streams running at once in a real browser: the second one, once opened, stays open through several status checks, switching back works, and Keep watching's next stream is still followed.

## [4.18.1] - 2026-10-24

### Fixed
- **The Review preview didn't play some videos**: it showed a still frame ("this video can't play in the preview"), or played the sound without the picture. Twitch's enhanced streams send HEVC or AV1 video, and so do OBS recordings made with NVENC or AV1, which the app window can only play with Windows' own codec for it. Rendering was never affected. Now:
  - Live clipping asks Twitch for H.264 first (as VOD downloads already did), so live recordings play in the preview. Only a stream that has nothing else uses HEVC or AV1.
  - When the preview can't play a video, Clip Radar makes a copy it can play (480p H.264, made once, in its temporary files) and switches to it at the same moment, showing its progress meanwhile. Clips still render from the original, at full quality.

### Tests
- `tests/test_preview_fallback.py`: a video no browser plays (MPEG-4 in an AVI) gets a preview copy that plays, without asking; the copy is made once, in the temp files, never larger than the source; clips still render from the original; a video that can't be read fails with a reason.

## [4.18.0] - 2026-10-24

Upload to TikTok for you, a Square blur layout, and a volume slider for the preview.

### Added
- **Upload to TikTok** (Exports): Clip Radar opens TikTok Studio in a browser window of its own (Edge, or Chrome), puts the video in and types the caption, so there's nothing to drag in. Sign in to TikTok in that window the first time; it remembers you, apart from your everyday browser. You check the post and press Post yourself, or turn on **Press Post for me** (off by default) and Clip Radar presses it too, confirming TikTok's "Post now" if it asks. Exports shows each step while it works, then "In Studio, press Post", or "Posted" only once TikTok confirmed it. TikTok's page isn't made for this: if a step stops working (when TikTok changes its page, for example), Clip Radar says which step, selects the video in File Explorer and copies the caption so you can finish by hand. TikTok's terms don't allow automated use, so heavy use with Press Post on could get an account flagged. **Open TikTok Studio** still does it the manual way.
- **Square blur** layout (Review › Layout): a square crop of the picture, centred on a blurred copy of itself, in a 9:16 frame (or any shape). Focus and Zoom pick which part of the picture goes in the square.
- A **volume slider** for the preview in Review, beside the speaker: drag it, use the arrow keys, or scroll over it. The speaker shows the level, mute keeps it (unmuting comes back to the same level), and it's remembered. The Exports preview plays at the same level. Only the preview: a clip renders at its own Volume setting.

### Changed
- The caption you edit in Exports is kept with the clip, and is the one TikTok gets.

### Tests
- `tests/test_studio_auto.py`: the TikTok upload against a stand-in for TikTok Studio's page in a real browser (Edge on Windows CI): waiting for you to sign in, the clip's own file going in, the caption replacing TikTok's, Post left for you or pressed and confirmed, and a changed page named as the step that stopped.
- `tests/test_studio_handoff.py`: Upload to TikTok from Exports, end to end, through the real server and the current UI.
- Square blur renders in every shape from every source in `tests/test_layout_geometry.py` and the media matrix, with its square's position read from the pixels; the smoke test covers the volume slider.

## [4.17.0] - 2026-10-23

Most of Settings in the new UI. Nothing changes unless you open the new UI (`/next/`).

### Added
- The new UI's **Settings** screen (at `/next/#settings`): Performance, Twitch sign-in, Clip ranking (what was learned for each streamer, "What it learned", Forget), the clip hotkey, Downloads and tools, Storage (folders, Delete VODs and clips, automatic cleanup), Updates (including All versions and a private-repository token) and About. The other sections, such as Export format and Hashtags, open in the current UI. Both UIs use the same settings, so a change in one shows in the other.
- New UI: actions that can't be undone, like Uninstall, ask first in a dialog that starts on Cancel and closes with Escape.

### Changed
- New app icon: the green radar logo, in the window, the taskbar, the installer and the README banner.

### Tests
- `web/`: 54 Vitest tests, including what each Settings panel saves, Storage's folder changes and deletes, Clip ranking's explain and forget, and the confirm dialog's keyboard behaviour.
- `tests/test_web_settings.py`: the new Settings in a real browser against the real server: choices saved on the server and shown by the current UI, a rejected Twitch token explained, a failed update check, Clip ranking explained and forgotten, Delete VODs (asks first, deletes only the VODs), and a 420 px window without sideways scrolling.

## [4.16.0] - 2026-10-23

A bug-fix release: black TikTok covers from GPU renders, and face-following clips that fell back to a blurred background. It also brings the new electric-green look.

### Fixed
- TikTok covers could still come out black on a PC that renders on the GPU (AMD). The fix in 4.12.2 had only been tested on the CPU encoder, so as a safeguard every render now checks its own first frame, read the way TikTok reads it. If it's black while the picture right after it isn't, that clip is rendered again on the CPU, and the GPU encoder isn't used for the rest of the session. The clip's details in Exports say when this happened. If a first frame is still black after that, Exports warns you, so you can pick a cover when you upload. If your clips' first frames look right in a video player but TikTok Studio still shows a black cover, try uploading from a private browser window: a browser extension can block the cover upload.
- Clips in the **Whole frame** layout that follow the streamer's face came out as the whole picture on a blurred background, with "This clip's layout couldn't be built from the video", whenever the clip was longer than about 48 seconds. The cause was the crop that follows the face. It was one long FFmpeg expression with a step every half second, and the FFmpeg builds Clip Radar now downloads refuse expressions nested more than about 100 levels deep. The expression now picks its step by halving the clip (a few levels even for an hour-long clip) and keeps the crop inside the picture on every frame. Retry a clip that showed the warning to get it in its layout.
- Every crop a layout makes is checked before FFmpeg runs: inside the picture, with an even size, and a face-following crop held inside it for the whole clip. A crop that doesn't fit is reported as a Clip Radar bug instead of quietly falling back to blur-fit. Face paths with repeated times, missing or broken values are cleaned first.
- A facecam box that's empty or unreadable (from older or edited settings) now fails with "The facecam box in the Layout tab is empty…", which says what to fix, instead of rendering a few blown-up pixels. A zoom, focus or camera size saved as something other than a number falls back to the default; zoom is held to 4x.
- When FFmpeg still refuses a layout whose crop fits, a Whole frame clip first tries again without following the face, and only then uses blur-fit. The warning names the layout. The clip's attempt keeps the first failure (the layout, FFmpeg's exit code and reason, the filter graph), so it's no longer replaced by the fallback's. A finished clip with a warning shows these under Technical details in Exports. If blur-fit fails too, the error says both failed.
- A filter graph FFmpeg can't build no longer counts as a GPU encoder failure. Before, the same graph was run again on the CPU, and the GPU encoder was turned off for the rest of the session.
- **Delete VODs** and cleanup could leave a live moment or a video behind as "in use" when its preview had been loaded in the app. While a video streamed to the page, Clip Radar kept the file open until the page let go of it, which on Windows means it can't be deleted. Clip Radar now only opens the file to read each piece it sends. Found by the busy-file diagnostics added in 4.15.0, which named Clip Radar itself as the program holding the file.
- Opening a screen of the current UI from the new UI, or from a link that only changes the `#`, now always lands on that screen.

### Changed
- **New look: electric green.** Clip Radar's accent is now electric green instead of yellow, in both the current and the new UI. As before, it marks only the main action on a screen, what's selected and progress. Ready and done states use the same green. The design direction is written down in `DESIGN.md`.
- Dim text is brighter, so labels and hints meet the WCAG AA contrast minimum on every panel. They were below it before.
- Links are underlined white text rather than coloured, so the green only ever means "do this" or "this is selected".
- On the timeline, laughter is drawn in cyan, so it can't be mistaken for the selection.
- Home has one main action: **Find clips** stays green and **Start live clipping** is now an outlined button, so the two no longer compete.
- New UI: bigger type (14 px body, bolder screen titles) and short transitions when you switch screens, which turn off when Windows is set to reduce motion.

### Tests
- `tests/test_file_serving.py`: a video request that stalls like a paused `<video>` doesn't keep the file open, and the file can be deleted meanwhile; whole and ranged requests still get exactly their bytes.
- `tests/test_black_cover.py`: a stand-in GPU encoder that blacks the first frame is caught and the clip re-rendered on the CPU; a GPU encoder that works is left alone; a clip that really opens on black isn't touched.
- `tests/test_layout_geometry.py` (real FFmpeg, generated videos): every layout and shape on landscape, portrait, square, odd, tiny, 59.94 fps VFR, rotated and size-changing sources, checked to render in the chosen layout; facecam boxes at every edge, tiny, empty and malformed; face paths from first to last frame, with repeated times, long gaps, the picture's edges and broken keyframes, with the crop's position read from the pixels; a 54.5 s 1:1 clip following a face; the original failure, then the corrected render; an invalid crop reported before FFmpeg; blur-fit when FFmpeg refuses a crop; no GPU retry for a broken graph; nothing left behind after success, cancel or failure.
- `tests/test_media_matrix.py` now fails a row that fell back to blur-fit (the output is the same size, so before it passed). `tests/test_render_lifecycle.py` checks a fallback's attempt record, and a facecam box that's a setting to fix.

## [4.15.0] - 2026-10-23

### Added
- The new UI's **Home** screen (at `/next/`; "Try the new Exports" in Exports gets you there): Find clips with every option, Running now, Recent and live clipping, as in the current UI. The options you pick are shared with the current UI. Opening a video or a live session takes you to the current UI's Review, which isn't in the new UI yet, so you can already go from Home to finished clips.
- The current UI opens a video, a live session or a screen from a link (`#open=`, `#live=`, `#view=`), which the new UI uses to hand over.

### Fixed
- When Delete VODs or a cleanup can't remove a file because it's open, the log now names the program that has it open (Windows), so a file that stays busy can be traced.

### Tests
- `web/`: 33 Vitest tests, including what Home sends to Clip Radar and a check that the new UI's render defaults match the current UI's exactly.
- `tests/test_web_home.py`: the new Home against a real video in a real browser, through to Review with its clips, and Recent opening it again.
- `tests/test_filelocks.py` (Windows CI): the program holding a file is named.
- The full UI smoke test now passes against the new UI too, and CI fails if it doesn't.

## [4.14.0] - 2026-10-23

The first screen of the new UI, to try alongside the current one. Nothing changes unless you open it.

### Added
- **Try the new Exports** (a button in Exports): the render list in the new UI, being rebuilt screen by screen in React. It shows each render's stage, a percentage only while FFmpeg is encoding, every attempt of a failed render with what went wrong, and Retry, Retry failed, Cancel, Remove and Show in folder. Home, Review and Settings link back to the current UI for now. Posting to TikTok Studio and Discord stays in the current UI.
- The new UI checks every answer from Clip Radar before showing it, and says so plainly if it can't reach Clip Radar or gets an answer it doesn't understand.

### Changed
- The new UI is opt-in until it covers every screen: the current UI stays at `/`, the new one is at `/next/`. `CLIPRADAR_UI=web` puts it at `/`; `CLIPRADAR_UI=legacy` turns it off. `/api/status` reports `next`.

### Tests
- `web/`: 21 Vitest tests (API client, answer checks, polling that drops out-of-order answers, the Exports screen).
- `tests/test_web_exports.py`: the built Exports screen in a real browser against real renders: stages through to Ready, no percentage outside encoding, a real FFmpeg failure explained and retried, a reload showing the same clips once each.
- CI runs the Vitest tests before building the UI, and the browser test with the bundled FFmpeg.

## [4.13.1] - 2026-10-23

### Fixed
- If Clip Radar crashed or was ended from Task Manager on Windows, FFmpeg, yt-dlp and TwitchDownloader kept running: a render went on writing a file nobody would use, a live recording kept the drive busy, and the leftovers couldn't be cleaned up on the next start. They now end with Clip Radar, however it ends. The update installer, File Explorer and browser windows Clip Radar opens are not affected. Tested by `tests/test_child_cleanup.py` on Windows.
- CI: a suite that failed in the middle of the unit-test or render-test step went unnoticed, because only the step's last command decided its result. Each step now stops at the first failing suite.

## [4.13.0] - 2026-10-23

Renders you can trust and diagnose. See `docs/audits/engineering-overhaul-2026-10-23.md`.

### Changed
- Exports show where each render is: checking the source, getting captions ready, rendering, checking the file. The percentage is FFmpeg's own progress and appears only while it encodes; the other steps show how long they've taken, so a long Whisper run no longer looks like a render stuck at 5%.
- A failed render's details list every attempt: how it ended, how far it got, the encoder, FFmpeg's exit code, any fallback, and for failures FFmpeg's command and last messages (sign-in tokens removed, your user folder shown as ~).
- Failures are sorted by cause. Only a stalled FFmpeg is tried again by itself, and only once. A layout, encoder, caption or damaged-source failure would fail the same way again, so it waits for you, with what to change.

### Fixed
- A clip starting after the end of its video (a recording replaced by a shorter one) "rendered" into an empty file, was retried, and then reported "came out broken twice". It now fails at once and says why.
- Before rendering, Clip Radar checks that the source can be read, the clip lies inside it, and the clips folder can be written to.
- A finished file must also have sound when the source has it and play to its last frame. A broken file is deleted, never shown as ready.
- A clip that was rendering when Clip Radar crashed or was closed twice in a row is no longer restarted on every launch; it waits for Retry.

### Tests
- `tests/test_media_matrix.py`: 73 real renders across portrait, odd-size, tiny, silent, variable-frame-rate, HEVC 10-bit, VP9, rotated and size-changing sources, every layout and export format, plus damaged files.
- `tests/test_render_lifecycle.py`: stages, attempt records, failure kinds, automatic retry limits, cancelling (FFmpeg stops, nothing left behind), broken-output refusal, crash-loop guard, token redaction.
- CI runs both with the FFmpeg the Windows app ships, and lints with Ruff.

## [4.12.2] - 2026-10-23

### Fixed
- TikTok covers came out black. TikTok takes the cover from the frame at 0:00 and ignores the MP4 edit list, and without it the first frame sat one frame in. Exports now have a frame at exactly 0:00 either way, and a clip whose video starts a moment after its sound repeats the first frame from 0:00 instead of opening on nothing. Clips already exported keep the black cover: pick a cover in TikTok, or render them again.

## [4.12.1] - 2026-10-23

### Fixed
- Hashtags in exported file names are separated by spaces, not underscores: `Chat lost it #KaiCenat #Mafia #TwitchClips.mp4`.

## [4.12.0] - 2026-10-22

Groundwork for the new React UI. Nothing changes on screen.

### Added
- The app can serve a React build of its UI (`src/web`, built from `web/`) at `/`, with the current page kept at `/legacy/`. `CLIPRADAR_UI=legacy` or `web` picks one. CI and `build_exe.bat` build and package it once `web/` exists. See `docs/react-migration.md`.
- `/api/status` reports the API version and which UI is served.
- `tests/test_api_contract.py`: every API path a UI calls exists, every GET answers JSON, and the server's request checks and file serving behave. `scripts/api_routes.py` lists the routes (`docs/api-routes.md`).

### Changed
- The app server's request handling (every route) moved from `server.py` to `routes.py`. Routes and answers are unchanged.
- `/api/job/{id}` returns only a job's progress and outcome (`id`, `kind`, `status`, `step`, `progress`, `eta`, `log`, `result`, `error`, `started`), not the app's internal flags or the request it started from.
- `tests/test_ui.py` talks to the page only through `data-testid` attributes and `window.ClipRadarTest`, so it runs unchanged against the new UI. Its skip-by-score check no longer depends on where clips sit in the list.

### Fixed
- JavaScript, CSS and font files are served with fixed content types. Windows sometimes reports `.js` as plain text, which would stop a React build from loading on those PCs.
- Requests to the app's server must send JSON and stay under 16 MB; larger or other bodies are refused without being read.
- A test of the render queue's "ready" sound could fail now and then because it checked a moment too early.

## [4.11.1] - 2026-10-22

### Fixed
- A clip could start a few hundredths of a second into a word (after a pause), clipping its first sound. Cuts now move to just before the word, and likewise past a word at the end.
- CI: the app server smoke test waits at most 90 seconds in all, so a server that accepts connections but never answers fails quickly with its diagnostics instead of running the job out of time.

## [4.11.0] - 2026-10-22

Working with clipping campaigns and clipping Discords, and watermarks you can move and share.

### Added
- **Campaigns** (Settings › Campaigns): one clipping organisation's rules kept together, and one click to switch between them.
  - Text and hashtags every post must carry, at the end or the start of the post caption. They're added after the content check, so they're never masked, and hashtags you already have aren't repeated.
  - The shortest and longest clip it accepts: clips outside get a warning in Exports.
  - Its own watermark, used instead of yours while it's active.
  - Its Discord channel (a webhook). Finished clips can be sent there as soon as they render, or from Exports › Submit, with the video attached (up to 10 MB) until you paste the link to your post, then with the link. The message is a template ({title}, {caption}, {post_url}, {campaign}, {source}, {file}) and never pings anyone. Test sends a message to check the webhook.
  - Share a campaign as one file (rules and watermark images inside) to hand to clippers, and import it from a file or a Discord link. The webhook is only included when you tick Include the webhook.
- **Watermark layers:** up to 6, each text or an image, instead of one.
  - Drag a layer on the clip preview in Review to put it anywhere (Where › Anywhere); corners and centre still work as before.
  - Rotation for every layer; font, colour, outline and box colour for text.
  - Save as… keeps a set of layers under a name; Share… saves them as one file with the images inside; Import… and From link… bring in a watermark file, a campaign file or a logo image (PNG, JPG, WebP).

- **Skip many clips at once** (Review › To review): Skip clips skips every clip left in the list, or only those scored under 40, 50, 60 or 75. Clips you made yourself stay, Undo brings them back, and a bulk skip isn't taught to the ranking.

### Changed
- Watermark settings from earlier versions become one layer, in the same place. Clips already in the render queue keep their watermark.
- Watermark images are kept in Clip Radar's own folder and only those are used or shown, so a watermark can't point at another file on your PC.

### Fixed
- Text watermarks in the clip preview could be drawn half their width to the left of where the export puts them.

## [4.10.1] - 2026-10-22

### Fixed
- A clip whose layout FFmpeg couldn't build ("Failed to configure input pad on Parsed_crop" / "Error reinitializing filters") no longer fails. It renders with the whole picture on a blurred background, and Exports says so, with Reset + Retry for the layout you chose.
- When a render does fail, the error keeps FFmpeg's reason line instead of only the last 10 lines, which left it out.

## [4.10.0] - 2026-10-22

Fixes from the first real-world test: a 6-hour Twitch VOD (89,208 chat messages) and a 28-minute YouTube video, opened, titled and rendered on a Windows PC.

### Changed
- New videos are auto-framed when they open (unless you picked a layout or drew a camera box yourself). Before, they started in the stacked camera + game layout, and on a stream without a webcam the top panel showed the chat printed on the video.
- Titles: garbled speech ("5, 4, 5, 5…") is never used as a title; titles from YouTube's captions are whole sentences; template titles take turns instead of repeating (the most repeated title on the test stream went from 16 clips to 4).
- YouTube clips get #YouTubeClips (not #TwitchClips), and their post text no longer mentions Twitch.
- Every line of the app's log has a time, and titling and each render are logged.
- Automatic clip counts for videos without chat (ordinary YouTube videos, files) no longer pad with weak moments: after the first 3, clips scoring under 50 are left out. A number you choose is still filled.
- `scripts/real_world_test.py` renders as soon as the clips it renders are titled, instead of waiting for every suggested clip.

### Fixed
- Long VODs: after 100% yt-dlp can go quiet for minutes while it joins the file (3 minutes for the 18 GB test VOD, 7 s short of the stall limit), and the download was called stalled. A download whose file is still growing now counts as progress, and the status says the video is being put together.
- A stalled or cancelled download is stopped with everything it started. Before, only yt-dlp's launcher was stopped and the real download carried on in the background.
- Chat delay on long streams: a 6-hour stream whose chat clearly followed it 4 s behind read as "couldn't tell how late chat reacts". The check now allows for a reaction spread over several seconds and for how little a long stream shows by chance. Random data is still never "sure" of a delay.
- Clips that are mostly very dark or blurry get a warning in Exports. They still render.
- A live recording that changes resolution part-way (crossing a Twitch ad break) no longer fails its clips' renders: every layout first brings frames back to the size it was worked out for.
- Live clipping: a moment passed on (too weak, a repeat, only one or two chatters) is logged and counted once, not again on every rescan of the last minute.
- Automatic cleanup of stalled downloads only touches Clip Radar's own download files in its own project folders, even when the projects folder is a whole drive, and skips folders it can't read (System Volume Information) instead of stopping.
- A download the watchdog stopped right as it finished is no longer reported as failed, and Cancel also stops TwitchDownloader's FFmpeg.

## [4.9.0] - 2026-10-22

Better clips from YouTube, and a script for testing on real links.

### Added
- Ordinary YouTube videos (no chat) are ranked with what YouTube knows about them, next to the sound:
  - the Most replayed graph: where viewers rewatch;
  - timestamps in the top comments ("3:45 when he fell"): each commenter counts once per moment and likes count as agreement, so one comment can't make a clip but a moment many people point at stands out;
  - chapters, named in each clip's reason.
  Each clip's reason says which it came from ("most replayed", "5 comments point here").
- Titles from YouTube comments: a phrase several commenters used about a moment can title it, shown as Comments: "...", the same way chat phrases do (Settings › Titles from chat turns both off).
- YouTube's own captions (uploaded or automatic) title clips and place their cuts straight away, without waiting for Whisper. Burned-in captions still use Whisper.
- YouTube livestreams: their chat is read while clipping live (through yt-dlp), the same way as Twitch chat. Before, YouTube streams were clipped from their sound only.
- `scripts/real_world_test.py`: give it one or more links; it opens each in a throwaway copy of the app, finds and titles the clips, renders the best few and writes a report with times, scores, reasons, titles and the rendered files.

### Fixed
- YouTube chat emoji were read as their names (":face-with-tears-of-joy:"), so laughing emoji didn't count. Standard emoji are now the real character (😂 counts like on Twitch) and a channel's own emoji keep their name.
- A render no longer fails when the speech model can't run (often because its first download failed): the clip renders without captions, says why, and has a Retry button. A YouTube video's own captions are used instead when it has them. With automatic censoring on, the render still stops, since censoring needs the words.

## [4.8.0] - 2026-10-22

Closing the gaps left against the Clip Radar 5 brief, with measurements against 4.1.5.

### Added
- Keep the top 3, 5, 10, 15, 20 or all clips in one go (To review list › Keep and render): each renders with the current export settings, in the order shown, with one queue sound for the batch. Clips with content flags to review are left for you.
- Captions: Line spacing for two-line captions, and Timing, which moves every caption earlier or later (up to a second) when they don't line up with the voice. The preview shows both.
- Auto-framing with more than one person: two people of similar size who fit in the crop together are framed together. When they don't fit, it stays on one person and only moves to the other once the first has gone, instead of jumping between them when a face is missed for a moment.
- The render bar moves while captions are being transcribed (it used to sit still until Whisper finished).
- `scripts/bench_pipeline.py`: scan and render time, peak memory and the export presets on a made-up 30-minute stream, to compare two versions on one PC.

### Changed
- Keep the PC responsive (Settings › Performance) now leaves one CPU core free instead of using half of them. Renders still run at lower priority on Windows. A 30 s clip on a 4-core PC: 23.8 s before, 18.2 s now.
- High frame rate is folded into Balanced: both made the same file, since Balanced already keeps 50 and 60 FPS. A saved High frame rate setting becomes Balanced.
- The quality notes give measured differences (High takes about 1.5 times as long as Balanced; Fast is about a third quicker).

### Fixed
- On Windows, a clip whose title has letters like é or 名前 failed with "came out without sound": the sound check couldn't read FFmpeg's answer, which repeats the file name. Everything Clip Radar reads from FFmpeg and the other tools is now read as UTF-8.

## [4.7.0] - 2026-10-21

Release preparation: updating is tested from the previous Clip Radar, and the guide covers everything new.

### Changed
- The user guide (README.txt next to the app) covers what changed since 4.1: titles from chat, What it learned, F-spam, clip counts, automatic cleanup, quality and frame rate, the watermark, caption animations and templates, Show what TikTok covers, and each clip's volume.
- Every build now also installs the previous Clip Radar release, uses it a little, updates it from inside the app and checks that settings, the Exports list and the old clips carry over, the old TikTok sign-in is removed, and the new settings start at their defaults. Updating from VodClipper 3.11 was already tested this way.

### Fixed
- Delete VODs (Settings › Storage) gives a file Windows still has open, such as a live moment that just finished, a few more seconds before reporting it couldn't be deleted. It used to try only once more.

## [4.6.0] - 2026-10-21

A cleaner, flatter look with a yellow accent, and quicker editing.

### Added
- Caption templates (Text tab): Signature yellow, Karaoke, Boxed, Bold bounce, Clean subtitles and Word by word. Pick one, then change anything; your own saved styles sit next to them.
- Reset to default (Text tab) puts captions and the title back to how they look out of the box, without moving them.
- Show what TikTok covers (Text tab): keep the areas under TikTok's buttons, caption and top bar shaded in the preview while you place text and watermarks, not only while dragging.
- Volume for each clip (Clip tab): from much quieter to louder, on top of Even out volume. Peaks stay under -1 dB, so the loudest steps are held back a little.
- More yellow highlight colours for captions.

### Changed
- A flat look: the accent is now a restrained signature yellow (primary buttons, selection, progress, switches) with dark text on it, and decorative gradients and heavy shadows are gone.
- The leftover TikTok posting settings code is removed.

## [4.5.0] - 2026-10-21

Better-looking exports: the stream's real frame rate, quality presets, a watermark, animated captions, and blur-fit that works with any video shape.

### Added
- Quality (Settings › Export format): Fast export, Balanced (recommended), High quality and High frame rate, each with a note on what it trades. High quality renders take about twice as long.
- Watermark (Settings › Export format): text or an image, in a corner or the centre, with size, opacity, distance from the side, an optional dark box behind text, and when it appears and disappears. It's added to exports only, never to your videos, and shows in the clip preview. Top positions sit below TikTok's top bar and bottom ones above its caption area, so it doesn't land under TikTok's own buttons.
- Caption animations (Text tab): pop in, word pop, bounce, fade in, slide up and word by word, with a speed setting, plus Highlight strong words ("no way", "insane", shouted words) in your highlight colour, and options to leave out "um"/"uh" and punctuation. They work with every caption style and layout, and the preview shows them.

### Changed
- Clips render at the stream's own frame rate (up to 60) instead of always 30, so 60 FPS streams stay smooth. A higher frame rate than the stream had is never used: turning 30 FPS into 60 only repeats frames, it can't add motion. Advanced › Frame rate has "Same as the stream" and still lets you pick a lower one.

### Fixed
- Blur-fit with a tall or narrow video (phone recordings, vertical streams): the picture was cut off at the top and bottom, and very narrow videos failed to render. It's now fitted inside the frame, centred, on its blurred background.

## [4.4.0] - 2026-10-21

Hashtags, posting through TikTok Studio, automatic cleanup, and sounds.

### Added
- Hashtags in file names: each exported file's name ends with the real tags, # included, like `Chat lost it_#KaiCenat_#Mafia_#TwitchClips.mp4`. The streamer's own tag, your tags for that streamer (so a streamer's community tags only go on their clips), then your general tags; no repeats; up to the number you set. Long names drop tags from the end before the title is cut. A retry keeps the same name. Settings › Hashtags (on by default; the tags are #TwitchClips plus the streamer's name until you change them).
- Default hashtags for posts, separate from the file-name ones: your tags for every post, per streamer and per platform (TikTok, Shorts, Reels, X), on top of Clip Radar's own tags for each clip (which you can leave out). A preview shows the file name and each platform's tags.
- Clean up automatically (Settings › Storage): old temporary files (on), the file of a hands-free clip you then skip (off), and a downloaded VOD once you're finished with it (off): every clip kept or skipped, every kept clip rendered, no render waiting or failed, and not opened for the days you choose. Rendered clips and your review always stay. The last run's result, and anything it couldn't remove, is shown there.
- A short sound when you add a clip to the render queue and when a clip is ready, once each, also while Clip Radar is minimised. Settings › Export format turns them off.

### Changed
- Posting to TikTok now goes through TikTok Studio, TikTok's own upload page, with nothing to set up: in Exports, Open in TikTok Studio copies the caption, selects the video in File Explorer and opens the upload page. Clip Radar only says it opened TikTok Studio, never that a clip was posted. Posting through a TikTok developer app, its settings and hands-free "send to drafts" are gone, and the old TikTok sign-in is removed from your PC when you update. Twitch and GitHub sign-ins are kept.

## [4.3.0] - 2026-10-21

Better picks and titles: F-spam no longer makes clips, titles can come from what chat said, and the number of clips you ask for is the number you get.

### Added
- Titles from what chat said: when at least three different chatters type the same short phrase about a moment, it's offered as a title, shown as Chat: "…". One viewer spamming, emote walls, F's, links, numbers and usernames never count. A strong line said on stream still comes first. Settings › Titles and captions › Titles from what chat said (on by default).
- Settings › Clip ranking › What it learned, for each streamer: the decisions stored, how the clips you kept differed from the ones you skipped, what ranking moves up or down because of it, and the rules every clip is scored on. It says when there isn't enough data yet and what's missing, and that it's a small statistical model worked out on your PC, not an AI.
- Clips to suggest goes from 5 to 60 in steps of 5, then 70, 80, 90 and 100.
- Live clipping counts waves of the channel's 7TV, BTTV and FFZ emotes, like videos already did.

### Fixed
- A wave of "F" (or o7, or poll votes like "1" and "2") in chat no longer makes a moment on its own, for videos and live. Before, 60 chatters typing F scored almost as high as 60 chatters laughing. It no longer decides a clip's mood ("fail") for titles either.
- Choosing a number of clips could quietly return fewer: on a quiet stream, asking for 20 gave 4. Now you get the number you asked for, and when a video really doesn't have that many separate moments, Clip Radar says so.
- A chat message with no text could stop titles being written for a clip.

## [4.2.0] - 2026-10-21

Lighter and steadier: a smaller download, less memory held between jobs, and Deep Scan that says when something's wrong.

### Changed
- Smaller app: the installer is 182 MB instead of 203 MB (494 MB instead of 570 MB once installed). It no longer carries an extra copy of FFmpeg's libraries (PyAV) or Hugging Face's optional download accelerator; speech recognition gets its audio from the FFmpeg that was already included.
- The speech recogniser (0.3 to 3 GB of memory) is let go after 10 minutes without use, and at most two model sizes are kept loaded. The next transcription loads it again, which takes a few seconds.
- While the window is minimised, it stops asking the app for updates every second or two; it catches up as soon as you're back. Rendering and clipping carry on as before.
- Unused code removed.

### Fixed
- Deep Scan no longer finishes with "no quiet gems" when it couldn't read the video's sound: it stops with an error that says so, so Retry means something. A short unreadable stretch is skipped and mentioned in the log.
- Deep Scan on a video of zero length failed with the message 'gems'.
- Reading a clip's sound, and reading a video's details, can no longer wait forever on a damaged file or a disconnected drive.
- A render that fails because the layout's crop doesn't fit the video now says so in plain words (and what to do), instead of FFmpeg's error.
- A long live stream no longer keeps piling up cached file details.

## [4.1.6] - 2026-10-21

Faster start, less memory while idle. First checkpoint of the Clip Radar 5 work (audit and measurements).

### Fixed
- Opening Clip Radar no longer loads the speech recogniser, the downloader and the laughter detector just to show that they're installed. They load when they're first used. On a test machine the app answers about 130 ms sooner after opening and uses about 46 MB less memory while idle (see docs/audits/clip-radar-5-audit.md for how this was measured).
- The changelog's introduction is back at the top.

### Changed
- Every build reports its package size (biggest parts) and how long the packaged app takes to answer after starting, with its memory once idle, so later changes can be compared against real numbers.
- scripts/bench_startup.py measures the same startup numbers when running from source.

## [4.1.5] - 2026-10-20

Reliability fixes for stalled processing and failed clip renders.

### Fixed
- Clamp facecam crop geometry to valid, even-sized dimensions so tiny or edge-positioned crops cannot create invalid FFmpeg crop filters.
- Show progress during render preparation instead of remaining at 0% while captions and safety checks are prepared.
- Clear stale progress, ETA and error state when retrying a failed render while preserving its output name.
- Prevent the secondary render worker from looping when memory is low and no jobs are queued.

### Changed
- Add inactivity watchdogs for yt-dlp acquisition and FFmpeg rendering so stalled work fails with an actionable error instead of running forever.
- Add network socket timeouts and retry bounds to yt-dlp acquisition paths.
- Add crop-boundary regression coverage.

## [4.1.0] - 2026-10-14

All versions, and every version archived on GitHub.

### Added
- Settings › Updates › All versions: every version of Clip Radar (and VodClipper before it), newest first, with what changed in each. Install any version newer than yours from there. Going back to an older version isn't offered.
- Every version is on GitHub's Releases page with its source code attached, including VodClipper 1.0.0 to 1.3.0 from before releases were automated (marked as archives; no installer was published for them). New releases include their source code too.

## [4.0.0] - 2026-10-14

VodClipper is now Clip Radar.

### Changed
- New name everywhere: the app, its window, shortcuts, the installer (ClipRadar-Setup.exe), Settings › Apps, the help and the logs.
- Updating from VodClipper in the app just works: Clip Radar installs over it, the old program folder and shortcuts are removed, and on first start your settings and data move to %LOCALAPPDATA%\ClipRadar and your clips folder to Videos\Clip Radar. Exports, projects, sign-ins and settings carry over; saved paths are updated. Folders you chose yourself (Settings › Storage) stay where they are.
- If the old folders are in use at that moment, Clip Radar keeps using them and moves them next time it starts.
- Releases carry the installer under both names, so older VodClipper copies still find the update.

### Fixed
- Delete VODs tries busy files again after a moment instead of leaving them behind.

## [3.11.0] - 2026-10-14

Storage folders and resource controls.

### Added
- Settings › Storage › folders: choose where projects, downloaded videos, rendered clips, temporary files and logs go, each with the free space on its drive. Folders are checked before use (must be writable). New work goes to the new folder; nothing already saved is moved, and projects in a previous Projects folder still show up.
- A folder on a drive that isn't plugged in falls back to the default until it's back, and VodClipper says so.
- Clear temporary files: exact previews, framing data and finished live buffers. It never touches your videos, projects or clips. Old half-finished downloads are cleared too.
- Downloads stop before they start when the drive doesn't have room, and say how much is needed.
- A second render at once waits while memory is short. Settings › Performance shows free memory.

### Changed
- Upgrading changes nothing: every folder starts where it always was. Exact previews and framing data move to the temporary folder.
- The live recording buffer is kept with the temporary files and deleted when a stream ends (the moments are already cut out).
- Running out of memory says so in plain words.

## [3.10.0] - 2026-10-14

More sources: YouTube and X, several at once.

### Added
- YouTube: ordinary videos, Shorts and finished livestreams open in Past broadcast. A finished livestream brings its chat replay, so chat counts like it does for Twitch.
- YouTube livestreams in live clipping (a video link, youtube.com/live/…, or a channel's /live page). YouTube live chat isn't read, so they're clipped from what's on stream.
- X: posts with a video open in Past broadcast.
- VodClipper says what a link is before downloading (an ordinary video, a finished livestream, one that's live now or hasn't started) and explains in plain words when it can't be used: private, age-restricted, members-only, removed, not in your country, a post with no video, or the site limiting downloads.
- Start times in links (?t=1m30s) open the preview there. Each clip has a link back to that moment in the original (Clip tab, and Copy source link in Exports), for crediting the source. The header shows where the video came from.
- Home › Running now: every video being opened and every stream being clipped, with progress, and Open, Pause, Resume, Cancel or Stop for each. Open another video while one is still being opened.
- Clip up to three streams at once (Settings › Performance › Live streams at once; two by default). Downloads at once now goes up to three.

### Changed
- Each source gets its own folder by its ID (yt_…, x_…), so opening the same video again reuses the download.
- Opening the same link twice while it's still opening joins the first one instead of starting again.

## [3.9.0] - 2026-10-14

Framing: follow the streamer's face, find the facecam, and pick the layout for you.

### Added
- Follow the streamer's face (Layout tab, Whole frame layout): the crop moves with the face, smoothly, never faster than a slow pan, ignoring small wobbles and one-off misdetections. It holds still while the face is hidden for a moment and eases back to Focus if it's gone. When it isn't sure (face seen only now and then, or only a facecam overlay), the crop stays at Focus.
- Who to follow: automatic (biggest face), left-most or right-most person, or nearest to a focus point you set.
- Auto-frame: looks at the video and picks Cam + game with the facecam boxed (a face sitting still in a corner), Whole frame following the face (a person filling the frame), or the middle of the frame (no face).
- Cam only layout: the facecam filling the frame.
- Saved framings: keep a layout, facecam box, focus, zoom and follow settings and reuse them in one click.
- Captions in the automatic position move off the face. The preview follows the face the same way the render does.
- Faces are found by a small built-in detector (UltraFace RFB-320, MIT licence, about 1 MB) on the onnxruntime VodClipper already ships.

### Fixed
- Live: the "spotting" time could come out negative when a moment was seen forming before its peak arrived, and then went missing from the status line.

## [3.8.0] - 2026-10-13

Live clipping: more accurate, measured, and quicker to show what's coming.

### Added
- Lowest score for live clipping (on Home and in the live header): moments under it aren't clipped.
- A live status line: moments as they form, how late chat reacts, how long each step takes (recording, spotting, waiting for the reaction, cutting, captions and title, rendering) and how soon after it happens a clip is ready, and how many moments were passed on and why.
- The chat delay is worked out live from the stream and kept up to date (leave Chat delay empty), instead of a fixed 6 seconds.
- A replay tool (tests/live_replay.py) that runs a recorded stream through live detection against known moments and reports precision, recall, false positives, misses, boundaries and delay.

### Changed
- When chat disconnects or goes quiet, live clipping finds moments from the stream's sound until chat is back.
- A moment carried only by chat needs more people than usual reacting; one or two people spamming no longer make a clip. Repeated messages from one person count less and less.
- Two automatic moments less than 20 seconds apart are one moment, whatever the clip amount.
- When moments compete for the same stretch, or wait for captions or rendering, the strongest goes first (and a clip you asked for before all of them).
- Moving a clip's start or end to a pause no longer makes it shorter than the shortest clip length you set.

## [3.7.0] - 2026-10-13

Export formats, better captions and sound, honest time estimates, and performance settings.

### Added
- Export presets for TikTok, YouTube Shorts, Instagram Reels and X (Settings › Export format), each with its size, frame rate, quality, sound and longest clip. A clip longer than the platform takes gets a warning in Exports.
- Advanced export settings: resolution, frame rate, H.264, H.265 or VP9 (.webm), quality or bitrate, audio bitrate and file type, with Reset.
- Captions burned in, burned in plus a .srt file, or only a .srt file.
- File name templates with {title}, {streamer}, {number}, {date}, {platform}, {source}, {hashtags}, {project} and {preset}, with a live example.
- Sound: every clip evened out to the preset's loudness (−14 LUFS by default), optional background noise reduction and clearer voices.
- Caption words: replace words everywhere in captions, and a list of words for Whisper to expect. The streamer's name and the channel's emotes are passed along too.
- Caption Outline and Shadow sliders, and saved caption styles you can apply to any clip.
- Time left for each render and the whole queue (Exports, Home, status bar), based on how fast this PC rendered before.
- Settings › Performance: renders at once (1–3), downloads at once (1–2), and keep the PC responsive or finish faster.

### Changed
- Whisper no longer captions silence or music with invented lines ("thanks for watching").
- The same clip sent twice renders once.
- A render that comes out without sound is redone; an almost silent one gets a warning. Render steps show what's happening (getting captions ready, rendering, checking the file).
- Learned trims never make a clip shorter or longer than the clip lengths you set.

## [3.6.0] - 2026-10-13

Titles and captions: two titles for every clip, a separate description, optional hashtags.

### Added
- Two titles for every clip: A, the best line said or how chat reacted, and B, the same idea with the streamer's name worked in. Both show under the title box; click one to use it. Both are saved with the project.
- Set how the streamer's name is written (for example JasonTheWeen); title B and the descriptions update. Without a name, B is a second, different kind of title.
- Post captions are now a description of their own, not a copy of the title: what was said or how chat reacted, and whose stream.
- Up to five hashtags that fit the clip (the platform, the streamer, the mood), and a Hashtags switch to leave them off.
- Settings › Titles and captions: which title new clips use (A or B), and hashtags on or off.

### Changed
- Titles are tidied: no shouting in capitals, no stammered repeats, no trailing filler, one "!" at most, and long lines end on a word.
- Title templates only describe what the clip shows. Lines that claimed things the signals can't know ("Absolutely clutch", "The cleanest play of the stream") are gone.
- Generic tags (#fyp, #streamer) are no longer added.

## [3.5.0] - 2026-10-13

Better clip finding: more signals, scores you can read, settings for each kind of stream.

### Added
- A 0–100 score on every clip, with a breakdown of what made it (chat, volume, laughter, screams, emotes, chat surge, sudden reaction). Scores rank moments within a stream; they can't promise views.
- 7TV, BetterTTV and FrankerFaceZ emotes: the channel's emote names are downloaded (cached for a day), and waves of them from many chatters count as reactions. Without them, or offline, it carries on.
- Stream types: Gaming, IRL / Just Chatting, Reactions, Interviews, Podcasts and Any stream, each weighing the signals differently with its own clip lengths. Also "How many moments" (Fewer, Normal, More) and shortest/longest clip.
- The chat delay is worked out from the stream itself (leave Chat delay empty), instead of always 6 seconds.
- Detection settings in the Review header: change them, and Try first to see how many clips would be new, dropped or the same. Your kept, skipped and edited clips stay. Settings are saved with the VOD.
- After a skip you can say why (not interesting, bad cut, duplicate, just noise). Settings › Clip ranking shows how many suggestions you keep, why you skip, and how many moments you added by hand.
- New clips learn from your trims: after five, they start and end where you usually move them to (shown in the header; Forget undoes it).
- The editing timeline shows emote activity next to chat.

### Changed
- When chat reacts late, the clip starts before what it was reacting to.
- Two detections of the same moment become one clip instead of two near-identical ones.
- A sudden reaction right after a quiet stretch counts more; a short loud blip nobody reacted to counts less.
- Live clipping uses the stream type you choose and the channel's 7TV/BTTV/FFZ emotes too.

## [3.4.0] - 2026-10-13

Editing: a real timeline, frame-accurate trims, undo, and previews that match the export.

### Added
- An editing timeline under the player: the clip's stretch of the stream with its sound, captions, laughter/screams/cheers, chat activity and content check flags. Drag the clip's edges to trim (they snap to word edges; hold Alt to stop), click to jump, scroll to move, Ctrl+scroll or − Fit + to zoom. Times are stream times, to the hundredth of a second.
- Trim by a single frame (−1f/+1f), or press I and O to start or end the clip at the playhead.
- Restore original cut: back to the cut VodClipper found, any time.
- Undo and Redo (Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y) for trims, titles, post captions and caption edits.
- Frame back and forward (, and .), playback speed from 0.25× to 2× (< and >), jump to the clip's start or end ([ and ]), and a shortcut sheet (?).
- Square (1:1, 1080×1080) and wide (16:9, 1920×1080) clips next to vertical, in Layout › Shape. The preview shows exactly that shape, and the export matches it.
- Exact preview: renders a few seconds around the playhead through the same pipeline as the export (captions, title, censoring, shape), so you can check an edit without exporting the clip.

### Changed
- The shortest clip is now 1 second (was 3).
- Clip times in Review show hundredths of a second.

## [3.3.0] - 2026-10-13

Content Check review: decide what happens to each flag, and keep track of it.

### Added
- A Content check box in Review lists each flag with what was said and when; click the time to hear it.
- Fixes for a flag, each explained first with a Preview, and Undo afterwards: Mute it, Bleep it, End the clip before it, Start the clip after it, Hide it in the title or post caption. They apply when the clip renders; your video is never changed.
- Leave as is, Dismiss, Review and edit (jumps to the words in the Captions tab), Exclude from export (the clip stays but won't render until you include it again), and Remove from the project with Undo. A removed clip doesn't come back when you change the sliders.
- Statuses on every clip: Needs review, Reviewed, Corrected, Dismissed, Excluded from export, plus "changed since it was checked".
- A Flagged tab with a status filter and Leave all, Dismiss all and Exclude all (each asks first).
- History on each clip of what was decided, saved with the VOD.
- Settings: sensitivity (Everything, Skip mild, Only serious), check clips by themselves, show flags right away or collect them, what happens when you render a clip with unreviewed flags (Ask me first, Not until reviewed, Just render it), remember dismissed flags, and check again after edits.

### Changed
- A decision sticks to its flag: it comes back only when the words change, when you check again from scratch, or when you change what the check looks for.
- Flags you left as they are or dismissed aren't censored by automatic censoring.
- Rendering a clip with flags you haven't reviewed asks first by default. Settings › Content check can switch that off.
- Hands-free also holds back clips when rendering is set to "Not until reviewed".

## [3.2.0] - 2026-10-13

Nothing gets lost: your review, your clip numbers and your renders survive changing the sliders, closing the app and crashes.

### Added
- Your review of each VOD is saved as you go: kept and skipped clips, trims, titles, post captions and caption edits. Open the same VOD again and everything is where you left it, sliders included.
- Opening a VOD you've opened before is instant: the analysis is reused instead of listening to the whole stream again.
- Exports and the render queue are kept when you close VodClipper. Renders that hadn't finished start again by themselves next time.
- The Recent list on Home shows how far you got with each VOD.

### Changed
- Every clip has a number (#7) that stays with that moment for good. Moving the Clips, Length or Chat-vs-audio slider no longer renumbers clips, and a moment that comes back gets its old number.
- Clips you kept, skipped, trimmed, retitled or added yourself never drop out of the list when you ask for fewer clips.

### Fixed
- A kept clip could vanish from the list after lowering the Clips slider, and its number could then be given to a different moment that showed up as "Rendered".
- Retrying a failed render replaces its own file instead of leaving a "(2)" copy.
- Every finished file is checked before it counts as done; a broken one is rendered again automatically.
- VodClipper warns that the drive is full before a render starts, not halfway through.
- Temporary files left behind by a render that was cut off are cleaned up.

## [3.1.2] - 2026-10-13

### Fixed
- Exported clips are named after their title only (`core starts their taco bell mukbang.mp4`), without a number in front. A second clip with the same title gets " (2)" instead of overwriting.

## [3.1.1] - 2026-10-13

### Fixed
- Live mode no longer makes a "moment" out of the first minute of a stream, when chat is just saying hi.

## [3.1.0] - 2026-10-13

Smarter clip finding, and live recording without ads.

### Added
- **No ads in live clips.** VodClipper now reads the live stream itself and recognises the ad breaks Twitch stitches in (its `twitch-stitched-ad` markers). Ad breaks are left out of the recording, so they can never end up in a clip; the recording just pauses while an ad would play, and the live header shows how much was skipped.
- **Twitch sign-in** in Settings › Twitch sign-in, through Twitch's own login page in a small window or by pasting your token. With Turbo (or a sub to the channel) Twitch sends the ad-free stream, so recording never pauses. The sign-in also opens subscriber-only VODs, is stored encrypted, and is only sent to Twitch.
- **Laughter, scream and cheering detection.** A small sound detector (about 20 MB, bundled) listens to the whole stream. Moments where the streamer cracks up or screams rank higher even when chat is quiet, and loud music counts for less. It runs on the CPU and adds roughly a minute per hour of video; you can switch it off under Advanced.
- **Clips get their own length (15–90 s).** Each clip starts far enough back to show the setup and runs until the reaction dies down, instead of a fixed 30 seconds. Once a clip has been listened to, its start and end move to the nearest sentence boundary, so nothing starts or stops mid-sentence. Fixed lengths are still available.
- **Deep scan** (optional). Transcribes the whole stream and scores every line: laughter right after it, chat reacting, a sudden change in voice, and how punchline-like it is. Lines that land but that chat and volume missed show up as **quiet gems** in their own Gems tab. You can start reviewing straight away while it runs, and a VOD you deep scanned before opens with its gems ready.
- **Learns what you like, per streamer.** The clips you keep, skip and post teach the ranking. After about eight decisions, moments like the ones you keep move up the list, and hands-free live mode passes on the kind you always skip. See or reset it in Settings › Clip ranking.
- **How many clips are worth it.** The review header shows an estimate like "≈21 strong moments in this 7-hour stream". **Clips to suggest** is now Automatic by default and scales with the stream's length and how much happened.

### Changed
- **Cleaner chat signal.** Chat is now counted by chatters, not messages. Bots (Nightbot, StreamElements and friends), `!commands` and giveaway entries, sub and gift-sub notices, gift-bomb and raid floods, and copypasta no longer create fake moments.
- Live chat reads usernames and Twitch's sub and raid notices.
- Moment reasons mention laughter, screaming or cheering when that's what stood out.
- Re-ranking keeps clips that are still suggested exactly as they were (title, captions, cuts, kept or skipped).

### Fixed
- Live chat timing no longer drifts after the stream reconnects.
- A problem while cutting or adding a live moment no longer stops the recording.
- Rendering can't stall on a long ffmpeg error output.
- Unexpected server errors now come back as a message instead of a dropped request.
- Two live sessions started in the same minute no longer share a folder.

## [3.0.1] - 2026-10-12

### Fixed
- Updates keep working now that the GitHub repository has moved to allen1versonfr/vodclipper.
  - VodClipper points at the new address.
  - It follows GitHub's redirect for a moved repository, and keeps the access token for private ones.

## [3.0.0] - 2026-10-12

VodClipper 3. Live mode is now hands-free: start it, go watch the stream, and clips are titled, checked and rendered on their own while you watch.

### Added
- **Hands-free live mode** (the new default).
  - Every moment is titled, run through the content check, and rendered with your Text and Layout style without you touching anything.
  - Finished clips appear in Exports as the stream goes on.
  - **Review each clip** mode keeps the old way of working.
- **Wait for the stream to start.** Start VodClipper before you go live (or before the streamer you clip does) and recording begins by itself.
- **Keep watching after the stream ends.** VodClipper waits for the channel's next stream and starts again.
- **Clip hotkey, Ctrl+Alt+C.** Clips the last 30 seconds from any window while you watch or play, with a sound to confirm it. You can turn it off in Settings › Live clipping.
- **How many clips.** Choose Fewer, Normal, More or Lots, before or during a stream.
  - Each level sets how big a moment has to be and how far apart automatic clips are.
  - Past broadcasts can now suggest up to 100 clips.
- **Content check** (the "TOS scanner"), optional, in Settings › Content check.
  - Checks what each clip says, its title and its post caption for hate slurs, self-harm and harassment, personal info (phone numbers, emails and street addresses), violence, sexual terms, drugs, swearing and gambling, plus your own blocked words.
  - Flags show on each clip and in Exports.
- **Censoring.** Flagged words can be hidden in captions and titles ("f***"), muted, or bleeped in the audio. You choose which categories get censored.
- Hands-free holds back clips with serious flags so you can look at them first, instead of rendering them.
- Optionally send finished hands-free clips to your TikTok drafts automatically. You still finish each post in the TikTok app.
- **Failed renders explain themselves.**
  - Each failure says what went wrong in plain words (drive full, file open in another program, encoder or captions problem, damaged source…) and what to do about it, with the technical details underneath.
  - Retry from the clip, from Exports, or retry everything that failed at once.
- **Updates from a private repository.** Add a read-only GitHub token in Settings › Updates and the built-in updater can see private releases. The token is stored encrypted.

### Changed
- The live controls are now a **How many clips** menu and a **Hands-free** switch, replacing Sensitivity and Auto-render.
- The live status shows when it's waiting for the stream, and the Live button in the title bar takes you back to live clipping from anywhere.
- Lists in Review and Exports update in place, so thumbnails no longer flicker while a stream is running.

### Fixed
- Live sessions started from a stream link now use the channel name as their title.

## [2.2.0] - 2026-10-12

Editing tools inspired by CapCut.

### Added
- **Move the title and captions.** Drag either one on the phone preview to place it anywhere.
  - A guide shows when the text is centred, and the parts of the frame that TikTok's own buttons cover are marked.
  - **Reset positions** puts both back in their usual places.
- **Fix captions by hand.** The new **Captions** tab lists every caption line with its time.
  - Click a line to jump to it, then type to correct what Whisper heard, delete a line, or add your own caption at the playhead.
  - A line you edit stays together as one caption, and **Undo all edits** brings back the original transcript.
  - Rendered clips use your corrected captions.
- **Caption styles:** Pop, Highlight (a colour box behind the spoken word), Karaoke, Glow and Clean, plus an option to switch off the pop-in animation.
- **Title styles:** white box, dark box, colour box or outline, with a size slider.
- **Eight fonts for titles and captions:** Classic, Montserrat, Poppins, Inter, Anton, Bebas Neue, Bangers and Luckiest Guy. They're bundled with the app, so clips look the same on every PC.
- The self-test now renders a clip with hand-edited captions, a bundled font and moved text, and checks where the text lands.

### Changed
- The clip inspector is split into **Clip**, **Captions**, **Text** and **Layout** tabs.
- README badges no longer depend on GitHub's API, so they show up while the repository is private. CI checks that the version badge and changelog match `src/version.py`.
- Updated the GitHub Actions used by the build to their Node 24 versions.

### Fixed
- Clips whose title contains an apostrophe or quotes now render. The subtitles file now gets a plain internal name instead of one built from the title.
- When the repository can't be reached for updates, the message now explains why.

## [2.1.0] - 2026-10-12

### Added
- **Automatic updates.** VodClipper checks GitHub for new releases when it starts and every few hours after that.
  - When there's a new version, the status bar shows **Update to x.y.z** and Settings › Updates lists what's new.
  - **Update now** downloads the new installer, checks it against GitHub's checksum, closes VodClipper, upgrades it in place and opens it again. Your clips, projects, settings and TikTok sign-in are kept.
  - Portable copies link to the download page instead.
  - Automatic checks can be turned off in Settings › Updates.
- CI updates an installed copy of the app to a new release and checks that it reopens.

### Changed
- Settings has a separate Updates section, and About now holds only the uninstaller and the log location.

## [2.0.0] - 2026-10-12

The live update. VodClipper can now clip a channel while it's on air and post straight to TikTok. The whole interface has also been rebuilt.

### Added
- **Live mode.** Paste a channel name to record the stream in 10-second segments while it's on air.
  - Chat is read anonymously, and audio and chat are scored the same way as for VODs.
  - Every strong peak becomes a titled moment ready to review. There's no limit on the number of clips.
  - **Clip last 30s** grabs a moment by hand.
  - Optional auto-render renders every moment as soon as it's found.
  - A rolling 15-minute buffer keeps disk use flat during long streams.
- **TikTok posting**, using TikTok's official Content Posting API.
  - Sign in through the browser. Your tokens are encrypted with Windows DPAPI.
  - Post directly or send to TikTok drafts, with upload and processing status shown in the app.
  - The post form follows TikTok's content sharing guidelines: creator info, privacy with no default, interaction toggles, commercial content disclosure and the required declarations.
- **A new desktop interface.**
  - Sidebar with Home, Review, Exports and Settings, a docked hype timeline, a clip inspector and a status bar.
  - Neutral graphite palette with line icons, set in Inter (bundled, so it looks the same on every PC).
- **End-to-end tests.** They drive the real app against a simulated live stream, fake Twitch chat and a mock TikTok API, and run in CI against the built exe.
- Versioned releases. Each new version publishes the installer and a portable zip, with notes taken from this changelog.

### Changed
- Repository reorganised into `src/`, `scripts/`, `installer/`, `docs/` and `tests/`.

### Fixed
- Live: finished segments are now detected from the recording folder, not from ffmpeg's segment list, which the Windows build doesn't update while recording.
- Live: the recorder now reconnects if no new video arrives for 45 seconds, and it detects the end of the stream from the playlist.
- Moments from the same live session no longer get duplicate titles.
- Clips you kept no longer reappear after a slow queue refresh.
- Enter keeps the current clip even right after you've clicked a button.

## [1.3.0] - 2026-10-12

### Added
- **9:16 review queue.** Suggested clips play one at a time in a phone-sized preview, showing the layout, the title and word-by-word captions drawn live over the VOD.
  - Keep or skip with buttons or the keyboard, and move between clips TikTok-style with up and down.
- **Render queue.** Kept clips render in the background, and finished clips play back in the phone preview.
- **Auto titles.** A title is picked from the transcript and the chat reaction, either the best quote near the peak or a title that matches the mood.
  - You can cycle through alternatives.
  - Each export gets a post caption with hashtags, saved next to the video as a `.txt` file.
- Exported files are named after their titles.

### Changed
- The transcript is cached per VOD and reused for titles, the preview and rendering.

## [1.2.0] - 2026-10-12

### Added
- **Windows installer.** A per-user setup with Start menu and desktop shortcuts, listed in Settings › Apps.
- **Uninstaller.** It removes app data and asks before deleting your clips.
- **Storage panel** showing disk usage, with **Delete VODs**, **Delete all clips** and **Uninstall** buttons.
- CI installs and uninstalls the app on Windows on every build.

### Changed
- Caption models are now stored in VodClipper's own data folder, so uninstalling removes them too.

### Fixed
- The TwitchDownloader download no longer fails when GitHub's API rate limit is hit.

## [1.1.0] - 2026-10-12

### Added
- **Standalone Windows app.** VodClipper ships as an exe in its own window, so you don't need Python.
- Missing tools (FFmpeg, yt-dlp, TwitchDownloader) can be downloaded from inside the app.
- A built-in self-test (`VodClipper.exe --selftest`), run on every CI build.

### Fixed
- Captions work again with PyAV 19. Audio is now decoded with FFmpeg and passed to Whisper as samples.

## [1.0.0] - 2026-10-12

First release.

### Added
- Takes a Twitch VOD link or a local video and finds its best moments.
  - **Chat:** surges in messages and in reactions like KEKW, POG or "clip it".
  - **Audio:** spikes measured against the surrounding minutes, ignoring muted DMCA sections.
- Review and trim clips on a hype timeline, or add your own.
- Three vertical layouts: facecam above gameplay, full crop, and blurred fit.
- Word-by-word captions with Whisper.
- 1080×1920 MP4 export using your GPU's video encoder, with automatic fallback to the CPU.

[3.0.1]: https://github.com/allen1versonfr/vodclipper/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/allen1versonfr/vodclipper/compare/v2.2.0...v3.0.0
[2.2.0]: https://github.com/allen1versonfr/vodclipper/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/allen1versonfr/vodclipper/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/allen1versonfr/vodclipper/releases/tag/v2.0.0
[1.3.0]: https://github.com/allen1versonfr/vodclipper/commit/dcb1473
[1.2.0]: https://github.com/allen1versonfr/vodclipper/commit/76be94b
[1.1.0]: https://github.com/allen1versonfr/vodclipper/commit/bb53c2c
[1.0.0]: https://github.com/allen1versonfr/vodclipper/commit/b40b498
