# VodClipper: notes for Claude Code

Windows app: Python local HTTP server (`src/server.py`) and a WebView2 UI (`src/ui/`), packaged
with PyInstaller and Inno Setup. CI (`.github/workflows/build.yml`, windows-latest) builds the exe
and runs the unit tests, `--selftest`, `tests/e2e.py` and install/update/uninstall.

## How work is split
Planning happens in chat. Plans land in `docs/plans/`. Execute the plan you're pointed at. Don't
redesign it; if something in it is wrong, fix the smallest thing and note it in the commit message.

## Conventions
- Commit as `Ali <z6pmsjcz96@privaterelay.appleid.com>`.
- User-facing text is plain and short, with no jargon. Match the existing tone in `app.js` toasts.
- Write files atomically (`.tmp` + `os.replace`) for anything persisted.
- Keep the UI working with no network access, and degrade gracefully when a tool or model is missing.

## Tests you can run on Linux
```
python tests/test_safety.py && python tests/test_signals.py   # plus any new tests/test_*.py
python tests/e2e.py --app "python src/app.py" --ffmpeg ffmpeg  # needs ffmpeg
```
The full Windows suite only runs in CI. Open a PR (or push the branch) and read the run.

## Releasing
Bump `src/version.py`, add a `## [X.Y.Z] - date` section to CHANGELOG.md, and update the README
badge `badge/version-X.Y.Z-4c8dff`. CI fails if these disagree. Merging to main publishes the
release, and the in-app updater offers it.

## Don't
- Delete or replace the `models-v1` pre-release. Every build downloads the laughter detector from
  it (hash checked in `src/audio_events.py`).
- Use the Twitch Helix API or developer-app secrets. Rely on our own detection.
