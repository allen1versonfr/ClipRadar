# Moving the UI to React

What the server already does for a React UI, and the contract the new UI has to keep so the
tests, the app window and updates keep working. Written alongside
[the readiness audit](audits/react-readiness-2026-10-22.md).

## Where things live

| Path | What |
|---|---|
| `web/` | The React app's source (Vite). Not created yet. |
| `src/web/` | Its build output (`npm run build`). Git-ignored; CI and `build_exe.bat` build it. |
| `src/ui/` | The original page. Stays shipped and is always served at `/legacy/`. |
| `src/ui/fonts/`, `src/ui/icon.*` | Shared: the React app loads caption fonts from `/ui/fonts/captions/…` instead of bundling a second copy. |

The server picks the UI at `/` on every request:

- `src/web/index.html` exists → the React app. Otherwise → the original page.
- `CLIPRADAR_UI=legacy` forces the original page; `CLIPRADAR_UI=web` shows an error instead of
  falling back (useful in CI so a broken build can't hide behind the old page).
- `/api/status` reports which one is served (`"ui": "web" | "legacy"`) and the API version (`"api": 1`).

With the React build present, the server answers:

- `/assets/*` from `src/web/assets/`, cached forever (`immutable`): Vite's file names are content hashes.
- Other files at the top of `src/web/` as they are (favicon, manifest, robots.txt).
- Any other path without a file extension (`/review`, `/settings/campaigns`) with `index.html`,
  so client-side routing (React Router's `BrowserRouter`) works, including a reload on a deep link.
- `/api/*`, `/files/*`, `/vod/*`, `/export/*`, `/wmimg/*`, `/ui/*`, `/legacy/*` exactly as before.

`.js`/`.mjs`/`.css`/fonts/`.wasm` are served with fixed content types. Windows' registry sometimes
maps `.js` to `text/plain`, and browsers refuse to run a module script with that type: without
the fix the React app would show a blank window on some PCs only.

## Setting up `web/`

```sh
npm create vite@latest web -- --template react-ts
```

`web/vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const app = 'http://127.0.0.1:7860'          // python src/app.py --browser (or CLIPRADAR_PORT)
const toApp = { target: app, changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: '../src/web', emptyOutDir: true },
  server: {
    port: 5173,
    // the app's server only answers requests for localhost / 127.0.0.1 (DNS-rebinding guard):
    // keep changeOrigin off so it sees Host: localhost:5173, which it accepts.
    proxy: {
      '/api': toApp, '/files': toApp, '/vod': toApp, '/export': toApp,
      '/wmimg': toApp, '/ui': toApp, '/legacy': toApp,
    },
  },
})
```

Run `python src/app.py --browser` and `npm run dev` in `web/`, then open http://localhost:5173.
POSTs from the dev server are allowed (`Origin: http://localhost:5173` is local).

Commit `web/package-lock.json`: CI runs `npm ci` and caches on it.

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
| `find-clips` | Home: Find clips (a primary button: the test checks it's the signature yellow `#ffd21f`) |
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
