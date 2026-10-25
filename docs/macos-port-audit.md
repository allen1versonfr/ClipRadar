# macOS port audit

## Repository baseline

- Audited source: public `main`, tag `v4.18.2`, commit `e8d42c1` (2026-10-24).
- Baseline checkout was clean. Work is isolated on `port/macos`.
- The repository has no `AGENTS.md` files.
- The runtime host is Apple Silicon (`arm64`, macOS 15.6, Python 3.12.5).
- The upstream CI runs Windows only. At initial inspection, `python3 tests/test_safety.py` passed. The system Python lacked NumPy, pytest and Ruff; system PATH also lacked FFmpeg, FFprobe and Node/npm. The bundled runtimes later allowed baseline `test_locations.py` to pass and the React test suite to pass (54 tests). Baseline `test_signals.py` stopped at missing NumPy with system Python; with bundled Python the port's new Keychain dependency first needed a mock for test isolation.

## Architecture and known platform boundaries

Clip Radar is a Python process (`src/app.py`) that starts a threaded local HTTP API/render queue (`src/server.py`, `src/routes.py`) and serves the React UI built from `web/` (with `src/ui/` as fallback). Video download, analysis, framing and rendering are in `src/engine.py`; live recording, chat processing, titles, captions, safety checks, review history and exports remain in Python. The desktop window uses pywebview. The Windows release workflow builds a PyInstaller bundle and Inno Setup installer; the macOS workflow builds an arm64 app bundle and release archives after Windows checks pass.

Platform-specific code is isolated in app packaging and in `paths.py`, `secret.py`, `hotkey.py`, `tools_setup.py`, `updater.py`, `studio_auto.py`, and selected process-management/render helpers. FFmpeg filter construction is largely platform-neutral but includes Windows-specific text/path accommodations. `TwitchDownloaderCLI` is treated as an optional fallback; yt-dlp's Python module is already an alternate download path.

## Feature compatibility matrix (initial audit)

| Feature | Initial status | Evidence / work needed |
|---|---|---|
| Python analysis, ranking, learning, safety, title and hashtag logic | Reusable without platform changes | Python modules have no required Win32 imports in their core algorithms; verify full suite on macOS. |
| Local API, React UI, review and render queue | Reusable with configuration changes | Server uses standard HTTP/threading; app lifecycle, packaging and native window need macOS verification. |
| FFmpeg render pipeline, captions, layouts, watermark and audio | Real media matrix passes on Apple Silicon | Signed FFmpeg 9.0.2 build includes VideoToolbox, x264, subtitles and HTTPS. Portrait/landscape, split/blur, rotated, HEVC, VP9, variable-rate, audio/no-audio and malformed-input cases passed. Hardware VideoToolbox is detected but its smoke test fails on this host with `Cannot create compression session: -12903`; software x264 fallback remains functional. |
| Paths, logs, cache, projects and exports | macOS defaults implemented; needs app verification | Frozen builds use `~/Library/Application Support/ClipRadar` and `~/Movies/Clip Radar`; source mode stays repository-local. |
| Memory-aware rendering | macOS memory reporting implemented; render throttle uses shared 1 GB reserve | `locations.memory()` reads `hw.memsize` and reclaimable/free pages from `vm_stat`; the queue uses the same low-memory gate as Windows. The display path and 8 GB cap have a fixture test, but full M1 pressure profiling remains outstanding. |
| Credentials | Keychain adapter implemented; real Keychain still needs verification | DPAPI remains Windows-only and unchanged. macOS stores random Keychain references in credential files and fails closed if keyring selects another backend. Automated tests use an in-memory Keychain backend. |
| Tool discovery/provisioning | macOS discovery implemented; provisioning partly verified | Searches app-data tools and common Homebrew/MacPorts directories, ignores `.exe` on macOS, downloads official yt-dlp and architecture-matched TwitchDownloaderCLI, and can install FFmpeg through Homebrew already present on the system. No Homebrew is installed automatically. |
| Desktop window, native dialogs, lifecycle and Twitch login | Packaged app window launches on this Apple Silicon host; interactions need further verification | Opening the built `.app` displayed the Clip Radar window. pywebview exposes native file/folder pickers; Twitch login cookie retrieval and clean quit during active jobs still need end-to-end verification. |
| Global shortcut | Implemented for macOS with explicit permission guidance | `hotkey.py` listens for Ctrl+Option+C using `pynput`; macOS Accessibility permission is required. This permission path is automated in CI only through a stub listener, not verified against a fresh TCC prompt. |
| TikTok Studio handoff/Finder reveal | Platform browser discovery and Finder reveal are implemented | `studio_auto.py` discovers installed Edge, Chrome, Brave or Chromium application executables on macOS; the existing browser automation remains the same. TikTok can change its page and requires real-account verification. `studio.reveal` uses `open -R`. |
| In-app updates and installer | macOS arm64 ZIP/DMG packaging and update replacement implemented | The updater selects `ClipRadar-macOS-arm64.zip`, validates and stages the app bundle, waits for shutdown, swaps it with rollback on failure, then reopens it. The macOS workflow builds after Windows release checks and attaches the ZIP plus DMG when available. Real GitHub release execution and update replacement on a second installed app version remain to be verified. |
| Twitch VOD/chat/live and external services | Reusable with configuration changes | HTTP, yt-dlp Python fallback and FFmpeg pipeline exist; TwitchDownloader binary, sign-in WebView cookies and live cleanup need macOS tests. |
| Metal/Core ML acceleration | Deferred until baseline exists | No measured workload currently justifies a separate GPU implementation. First compare native FFmpeg VideoToolbox encoders and profile actual tasks. |

## Milestones and acceptance

1. **Audit and baseline:** this document plus recorded environment/test state; no large refactor.
2. **Cross-platform foundation:** native app-data/media paths, executable discovery/provisioning, secure Keychain abstraction, and tests that leave Windows behavior intact.
3. **Launchable desktop:** WKWebView startup/shutdown, local API communication, native dialogs and login window verified on macOS.
4. **Video vertical slice:** real local video -> analysis -> review -> vertical render -> output validation -> Finder reveal, with VideoToolbox and software paths compared.
5. **Twitch:** VOD, auth, chat, live and cleanup verified against fixtures/services where possible.
6. **Parity/stability:** feature audit and queue/resource failure testing.
7. **Apple Silicon optimization:** measured baseline and before/after comparisons; no unsupported acceleration claims.
8. **Packaging:** reproducible arm64 `.app` and DMG install/start/use/quit checks; signing/notarization reported only if actually completed.

## Current implementation evidence (2026-10-24)

- `tests/test_macos_foundation.py`, `test_safety.py`, `test_signals.py`, `test_locations.py`, `test_persist.py` and `test_startup.py` pass using the project virtual environment or bundled Python runtime.
- React tests pass (54 tests). `tests/test_media_matrix.py` passes its full quick matrix (41 seconds) with the signed FFmpeg 9.0.2/FFprobe pair.
- `src/app.py` starts its local API on `127.0.0.1:7860`. In a later check, opening the built arm64 `.app` presented the Clip Radar window. Earlier source-mode pywebview attempts aborted in Cocoa initialization; the packaged UI launch is now verified, but the full native feature flow still needs end-to-end checks.
- `scripts/build_macos.sh` builds an arm64 PyInstaller `.app`, verifies/downloads the bundled 19 MB AudioSet model, and attempts to create a compressed DMG. The `.app` build completed (324 MB) and its packaged self-check loaded Python dependencies, models, UI assets, FFmpeg/FFprobe and the x264 fallback; it analyzed a generated 65-second speech clip and produced a real 1080×1920 output. Its full caption/title self-check did not pass: Whisper produced no transcript for the synthesized test speech. DMG creation could not be tested in this environment: `hdiutil create` failed with `Device not configured`. The built app uses ad-hoc signing and is not notarized.
- The updater and global hotkey now have macOS implementations, but a second-version replacement and the actual Accessibility permission prompt need end-to-end validation. Full Twitch service integration, M1 8 GB profiling, and signing/notarization remain outstanding; do not describe this port as complete macOS parity.
