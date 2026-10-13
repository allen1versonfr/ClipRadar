# Changelog

All notable changes to VodClipper are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

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
