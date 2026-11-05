# VOD download audit (2026-11-05, Clip Radar 4.45.0)

Scope: everything between pasting a link and having a video on disk. That is `engine.acquire`, `_download_video`,
`_ytdlp_cli`, `_ytdlp_module`, the TwitchDownloader fallback, the sound-only download that's listened to ahead
(`_early_sound`, `_hand_over`), chat (`_download_chat`, `_youtube_chat`), the space check, the stall watchdog and
reusing a VOD downloaded before (`_video_in`, `project_video`).

## How a download ends up on disk

| Source | Writes | Becomes `vod.<ext>` |
|---|---|---|
| Twitch, yt-dlp | `vod.mp4.part` + `.part-FragN`, then **renamed to `vod.mp4` (still MPEG-TS)**, then FFmpeg writes `vod.temp.mp4` and swaps it in (FixupM3u8) | at the rename, *before* the join |
| YouTube, yt-dlp | `vod.f<v>.mp4` + `vod.f<a>.webm/m4a`, then FFmpeg Merger writes `vod.temp.mp4`, then renames it | at the rename, after the join |
| Twitch, TwitchDownloader | its FFmpeg writes `-o` in place | as soon as it starts writing |

`_video_in()` takes any `vod.<video ext>` over 1 KB as finished. Two of the three rows put a file there before
it is finished.

## Fixed in 4.45.0

1. **A Twitch VOD stopped while it was being joined was reused broken (the bug).** The join is the slowest step
   of a long VOD: about 6 of 18 minutes on a 9-hour one. Pausing, cancelling, a stall, closing Clip Radar or a
   crash during it left `vod.mp4` as raw MPEG-TS. Every later open said "VOD already downloaded, reusing it":
   the preview wouldn't play it, seeking and duration were off, and clips could come out blank.
   Now `_reusable()` checks a reused VOD with FFprobe. MPEG-TS in an `.mp4` is joined there and then (the same
   stream copy yt-dlp makes, cancellable, ended with the app), and the half-written `vod.temp.*` is removed.
2. **TwitchDownloader's half-written `vod.mp4` was reused as finished.** It now writes `vod.tdl-part.mp4`, which
   is renamed only when it exits cleanly. A file from an older version that FFprobe can't read (no index) is
   removed, so it downloads again instead of failing at every later step. With no FFprobe nothing is deleted.
3. **TwitchDownloader had no stall check.** A stuck one waited for ever. It now has the same rule as yt-dlp:
   no output and no file growth for 3 minutes stops it with everything it started (`_watch_stall`).
4. **A stuck sound-only download held the whole job.** After the video finished, `acquire` waited for the sound
   with no limit. It now waits 3 more minutes at most (`SOUND_GRACE_S`), then stops it and listens to the video,
   which is what already happened when that download failed.
5. **Resuming a big download on a nearly full drive was refused.** The space check asked for the whole VOD
   twice even when most of it was already on disk. What's on disk is counted now: room for the rest, plus the
   join's copy.
6. **Twitch chat download:** TwitchDownloader wasn't tied to the app (it ran on for up to an hour after a crash)
   and wrote `chat.json` in place. It is adopted into the job object now, and writes `chat.part.json`, which
   is renamed when complete.
7. **Cleanup** (Settings › Storage, old temporary files) also removes a stopped join's `vod.temp.*` and
   TwitchDownloader's unfinished file after a day, like yt-dlp's `.part` files.
8. **Download errors sent people to controls that no longer exist.** "Home › Advanced › Sign in with" and
   "More options" went with the previous UI (4.43). The messages now point to Settings › Downloads and tools ›
   Use cookies from, Settings › Twitch, and Update downloader.
9. **A video only on offer in low resolution downloaded without a word.** YouTube sometimes offers only 360p
   to a client it doesn't trust. The whole video then downloaded and was ranked and rendered before the blur
   showed. The link check already lists every format, so a warning with what to do is logged up front
   (`low_quality`). The download goes on, since some videos really are 360p.

Tests: `tests/test_download.py`, with the low-resolution warning, a real MPEG-TS joined, an unreadable MP4 removed, nothing deleted without
FFprobe, the resumed space check, and a stand-in TwitchDownloader that fails, finishes and hangs.

## Checked, no change needed

- **Stall watchdog for yt-dlp** (`_ytdlp_cli`): it counts file growth across `vod.*`, so a long join isn't a
  stall. It kills the whole tree. A download killed right as it finished isn't reported as failed.
- **Cookies:** the Twitch sign-in cookie file is never handed to background work that outlives `acquire`.
  The early sound is waited for before returning, and chat uses no cookies.
- **`_Soon`, one per key:** a resumed job picks up its running chat download instead of writing the same file
  twice.
- **Handing YouTube's sound part over** (`_hand_over`): it never overwrites a part yt-dlp started, and it
  copies through a temp file and renames.
- **`+faststart` off for both joins:** checked against a real merge in the tests.

## Left as they are (noted for later)

- **Python-module fallback** (`_ytdlp_module`): it has no stall check, since a thread can't be killed. It only
  runs when `yt-dlp.exe` is missing or failed. If it became a common path, run it in a subprocess.
- **After a yt-dlp.exe stall**, the module fallback tries the same download again, then TwitchDownloader. A
  dead connection can take three 3-minute stalls to give up. That's acceptable for now: the second try often
  gets a fresh link.
- **YouTube chat replay** (`_youtube_chat`) still uses `subprocess.run` with a one-hour timeout and isn't
  adopted into the job object.
