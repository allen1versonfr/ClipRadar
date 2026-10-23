# Moving the UI to React

What the server already does for a React UI, and the contract the new UI has to keep so the
tests, the app window and updates keep working. Written alongside
[the readiness audit](audits/react-readiness-2026-10-22.md).

## Where things live

| Path | What |
|---|---|
| `web/` | The React app's source: Vite, React 19, TypeScript (strict), Zod. Unit tests with Vitest (`npm test`). |
| `src/web/` | Its build output (`npm run build`). Git-ignored; CI and `build_exe.bat` build it. |
| `src/ui/` | The original page. Stays shipped and is always served at `/legacy/`. |
| `src/ui/fonts/`, `src/ui/icon.*` | Shared: the React app loads Inter and caption fonts from `/ui/fonts/…` instead of bundling a second copy. |

### Which UI is where

Until the React app covers every screen, **the original page stays at `/`** and the React app is opt-in:

- `/next/` serves the React app whenever it's built (the original Exports screen links to it as "Try the new Exports").
- `CLIPRADAR_UI=web` serves it at `/` and refuses to fall back to the original page (useful in CI).
- `CLIPRADAR_UI=legacy` turns the React build off entirely, `/next/` included.
- When it covers everything, set `REACT_IS_DEFAULT = True` in `src/server.py` to make it the default at `/`.
- `/api/status` reports `"ui"` (what's at `/`: `"web"` or `"legacy"`), `"next"` (whether `/next/` works) and `"api": 1`.

Screens switch by `#hash` (`/next/#exports`), so the app needs no router and works the same at `/next/` and
at `/`, and a reload keeps the screen.

When the build is served, the server answers:

- `/assets/*` from `src/web/assets/`, cached forever (`immutable`): Vite's file names are content hashes.
- In web mode, other files at the top of `src/web/` as they are, and any other path without a file extension
  with `index.html`.
- `/api/*`, `/files/*`, `/vod/*`, `/export/*`, `/wmimg/*`, `/ui/*`, `/legacy/*` exactly as before.

`.js`/`.mjs`/`.css`/fonts/`.wasm` are served with fixed content types. Windows' registry sometimes
maps `.js` to `text/plain`, and browsers refuse to run a module script with that type: without
the fix the React app would show a blank window on some PCs only.

### Progress

| Screen | State |
|---|---|
| Shell (nav, status bar, toasts, `/api/ping`) | Ported |
| Exports | **Ported**: list with stages and honest progress, Retry / Retry failed / Cancel / Remove, attempt history, preview, Show in folder. TikTok Studio handoff, Discord and campaigns stay in the original page for now. |
| Home | **Ported** (4.15.0): Find clips with every option, Running now (pause, resume, cancel, open, hide), Recent (one click opens again), live clipping, the missing-tools banner. Choices are shared with the original page (same `vc_*` keys; the render defaults are checked against `app.js` by a test). |
| Review, Settings | Not yet. Opening a video or live session hands over to the original page's Review (`/legacy/#open=<project>`, `#live=<session>`, `#view=<screen>`), so the whole journey already works from the new UI. Port order: Settings → Review (canvas preview and editor last). |

### Working on it

```sh
cd web && npm ci            # once
python src/app.py --browser # the app's server, in another terminal
npm run dev                 # http://localhost:5173/#exports, hot reload, API proxied to the app
npm test                    # Vitest: API client, schemas, polling, the Exports screen
npm run build               # type-checks (tsc --noEmit), then writes src/web
python tests/test_web_exports.py   # the built Exports screen against real renders in a real browser
```

### Dependencies (and the ones left out on purpose)

- **react, react-dom** (19): the UI.
- **zod** (4): every answer from the server is checked against `web/src/api/schemas.ts` before a screen
  uses it. Shapes are "loose" (extra fields pass), so a newer server doesn't break an older screen,
  and a wrong shape shows a clear message instead of a broken screen.
- Dev only: **vite**, **@vitejs/plugin-react**, **typescript**, **vitest**, **jsdom**, **@testing-library/react**.
- **Not added:** a router (hash switching is enough), Zustand and TanStack Query (the server owns
  all state; `usePoll` refetches it and drops out-of-order answers; that's the whole data layer the
  ported screens need). Revisit Zustand when the Review editor's undo/redo state is ported.

Versions are pinned exactly in `web/package.json`; `web/package-lock.json` is committed and CI runs `npm ci`.

## The API

- Routes: [api-routes.md](api-routes.md) (generated: `python scripts/api_routes.py`).
- Every answer is JSON. Errors are `{"error": "…"}` with a 4xx/5xx status; the message is written for
  the user and can be shown as is. A 404 on `/api/project/{id}`, `/api/clips/{id}` etc. means the
  session expired (the app restarted): send the user home.
- POST bodies must be JSON objects with `Content-Type: application/json` (415 otherwise), under
  16 MB (413). A POST with no body needs no content type.
- Port the original `api()` helper as is (`src/ui/app.js`, top): it turns network failures and
  `{"error"}` into thrown `Error`s with user-facing messages.
- `python scripts/api_routes.py --ui` lists every API path the UIs build and flags any the server
  doesn't have. It scans `web/src` too, so a typo in a new fetch fails CI.

### Polling the original page does (keep the cadence, or move to one combined poll)

| What | Every | Only when |
|---|---|---|
| `/api/status` | 5 s | window visible |
| `/api/clips/{pid}` | 1.2 s | a VOD is open and visible |
| `/api/live/{id}` | 1.5 s | a live session is open and visible |
| `/api/queue` | 1.5 s | renders queued/running, Exports open, or live auto-render |
| `/api/job/{id}` | while a job runs | |
| `/api/ping` | 60 s | **always, even hidden**: browser mode quits after 20 min without it |

Catch up on `visibilitychange` (status, queue, live, clips) as the original does. In React, one
`usePoll(path, ms, enabled)` hook that pauses on `document.hidden` covers all of these.

### Saved UI preferences

The original page keeps these in `localStorage` with the prefix `vc_`. Read the same keys so
people don't lose their settings when the new UI ships:

`vc_cookies` `vc_cw` `vc_deepModel` `vc_guides` `vc_liveFreq` `vc_liveMinQ` `vc_liveMode`
`vc_liveSrc` `vc_maxh` `vc_muted` `vc_opts` `vc_src` `vc_useEmotes`

`vc_opts` holds the render options (layout, crop, captions, fonts, …) merged over `DEFAULTS` in
`app.js`; the server accepts them unchanged in `/api/queue/add`, `/api/preview_render` etc.

## The test contract

`tests/test_ui.py` touches the page **only** through these, so the same test runs against either
UI (`CLIPRADAR_UI=web python tests/test_ui.py`). CI runs it on the React build too, allowed to
fail for now (`continue-on-error` in `.github/workflows/build.yml`): remove that when the new UI
should ship.

`data-testid` attributes:

| testid | Element |
|---|---|
| `nav-home` `nav-review` `nav-exports` `nav-settings` | the four main views; clicking switches |
| `source-input` | Home: link or file box |
| `find-clips` | Home: Find clips (a primary button: the test checks it's the accent green `#2fe36f`, DESIGN.md) |
| `batch-bar` | Review: the bar with Keep / Skip many; hidden until there are clips |
| `batch-count` | its `<select>`: 3, 5, 10, 15, 20, 0 (all) |
| `batch-keep` | Keep and render the top N |
| `skip-score` | its `<select>`: 0 (all), 40, 50, 60, 75 |
| `batch-skip` | Skip clips |
| `clip-preview` | the 9:16 preview `<canvas>` |
| `confirm-yes` `confirm-no` | the confirm dialog's buttons |
| `toasts` | the toast stack; each toast's button is `toast-action` (Undo, Show, …) |

`window.ClipRadarTest` (plain JSON in and out):

| Member | Returns / does |
|---|---|
| `clips()` | `[{state, quality, gem}]` for the open VOD, in list order (`state`: `todo` `kept` `skipped`) |
| `setOption(key, value)` | sets one render option (the test turns `captions` off to skip Whisper) |
| `setTodoQualities([n, …])` | sets the score of each clip still to review (gems excluded), in order, and re-renders |
| `reloadExport()` | reloads export settings (watermarks) from the server and redraws |
| `hitBox(name)` | `{x, y, w, h}` where a draggable layer (`title`, `cap`, `wm0`…) was drawn, in canvas pixels, or `null` |
| `previewWidth()` | the preview canvas's own width, to scale `hitBox` to screen pixels |

Other tests (`tests/e2e.py`, every `tests/test_*.py`) use only the HTTP API and don't care which UI is served.

## Port, don't rewrite

The preview canvas has to match what FFmpeg renders. `drawTitle`, `drawCaption`, `drawWatermark`,
`drawGuides`, `FONTS` and the layout maths in `app.js` (player and format sections) should move into a
plain TypeScript module and be called from a `<canvas>` component's effect, not re-implemented in JSX.
Likewise the editor timeline (`drawEditor`, trims, undo/redo) and the heat strip. Keyboard shortcuts
(Enter keep, X skip, ↑/↓, Ctrl+Z) are on `document` in the original: keep one global handler that
ignores typing in inputs.
