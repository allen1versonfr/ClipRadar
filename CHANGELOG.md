# Changelog

All notable changes to VodClipper are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

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

[2.2.0]: https://github.com/ali1qbal/vodclipper/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/ali1qbal/vodclipper/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ali1qbal/vodclipper/releases/tag/v2.0.0
[1.3.0]: https://github.com/ali1qbal/vodclipper/commit/dcb1473
[1.2.0]: https://github.com/ali1qbal/vodclipper/commit/76be94b
[1.1.0]: https://github.com/ali1qbal/vodclipper/commit/bb53c2c
[1.0.0]: https://github.com/ali1qbal/vodclipper/commit/b40b498
