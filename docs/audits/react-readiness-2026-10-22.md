# Clip Radar React-readiness audit — 2026-10-22

## Scope

Goal: get the app ready for the UI to be rebuilt in React, without changing what users see today.
Reviewed how the local server serves the UI and answers the API, how the current page (`src/ui`)
talks to it, what the tests depend on, and how the UI is built, packaged and updated.

Branch `prep/react-ui`, from `main` at `b094f4b` (4.11.1). Checked on Linux with Python 3.13,
FFmpeg and Playwright Chromium: all 27 unit suites, the new contract test and the UI smoke test pass
before and after. **Not run here:** the Windows build, the installer, `tests/e2e.py` (needs the
packaged app and network tools), and anything needing Node: the sandbox had no npm/PyPI access, so
no React project was created and the CI Node steps are untested until the first push with `web/`.

The contract the React UI has to keep is in [react-migration.md](../react-migration.md).

## Current state in numbers

| | |
|---|---|
| `src/ui/app.js` | 3,575 lines, ~230 top-level functions, ~40 module-level mutable variables |
| DOM coupling | ~720 `$()`/`$$()` lookups, 68 `innerHTML` writes, markup strings built in JS |
| `src/ui/index.html` | 882 lines, all four views, 25 SVG icons as `<symbol>`s |
| HTTP routes | 97 (GET and POST) in one `Handler` if-chain in `server.py`, plus one prefix sub-router (`/api/campaigns/*`) |
| Pollers | 5 `setInterval`s (status 5 s, clips 1.2 s, live 1.5 s, queue 1.5 s, ping 60 s) |
| UI prefs | 13 `localStorage` keys (`vc_*`) |

What's already in good shape for a React port:
- The API is clean JSON with user-facing `{"error"}` messages; no endpoint returns HTML.
- Every native need (file pickers, Twitch sign-in window, opening folders/links) goes through the
  server, so the UI has no pywebview-specific code to port.
- `tests/e2e.py` and all unit tests drive the HTTP API only, so they're unaffected by the UI swap.
- Secrets stay server-side: Twitch and GitHub tokens are never returned (only `signed_in` / a 4-character hint).
- The installer deletes `{app}\_internal` before copying, so a UI build can't linger from an older install.

## Findings and fixes

### RR-01 — High: on some Windows PCs a React build would load as a blank window — fixed

`_send_file` takes content types from `mimetypes`, which on Windows reads the registry. Some
machines map `.js` to `text/plain` (a known Python-on-Windows issue). The current page uses a classic
`<script>`, which browsers run anyway, but Vite emits `<script type="module">`, and browsers refuse
module scripts that aren't served as JavaScript. Result: a blank window on those PCs only, invisible in CI.

**Fix:** `server.py` pins types for `.js .mjs .css .json .map .svg .woff .woff2 .ttf .otf .wasm .webp`,
and text types get `charset=utf-8`. Tested: bundles come back `text/javascript`.

### RR-02 — High: the server could only serve the one hand-written page — fixed

`/` always returned `src/ui/index.html`, nothing served `/assets/`, there was no fallback for
client-side routes, and every UI file was `no-cache`.

**Fix:**
- `src/web/` (the build output of a future `web/` Vite project) is served at `/` when it exists.
  `CLIPRADAR_UI=legacy|web` overrides; `/legacy/` always serves the original page as an escape hatch.
- `/assets/*` is cached `immutable` (hashed names); `index.html` is `no-cache` so updates show up.
- Extension-less paths (`/review`, `/settings/campaigns`) return `index.html` for client routing;
  missing files are real 404s; API/media prefixes are never captured by the fallback.
- Traversal out of the build folder is refused (tested with encoded `../`).

### RR-03 — Medium: the UI smoke test was welded to `app.js` internals — fixed

`tests/test_ui.py` read and wrote `app.js` globals (`W.clips`, `opts`, `hit.wm0`, `phone.width`,
`renderRows()`, `loadExport()`, `isGem()`) and element ids. A React port has none of these, so the
regression test would have had to be rewritten at the same time as the UI it protects.

**Fix:** the test now touches the page only through `data-testid` attributes and a small
`window.ClipRadarTest` hook object (JSON in and out). The current page provides both; the React app
must too (contract in the migration doc). The same test runs against either UI via `CLIPRADAR_UI`.

**Also fixed while porting it:** the "skip under a score" check assigned scores by a clip's index
in the whole list, so whether any clip landed under the threshold depended on where kept clips sat.
It now sets scores on the clips still to review directly, starting low, and asserts there is
something to skip before checking that it was skipped.

### RR-04 — Medium: no inventory or contract for the API — fixed

97 routes in an if-chain, with no list of them and no check that the paths the UI builds exist.
During a rewrite, a mistyped path would only show up as a runtime error in one screen.

**Fix:**
- `scripts/api_routes.py` reads the routes from `server.py` (no imports, runs anywhere) and finds
  every `/api/…` path built in `src/ui` and `web/src`, including `'a' + id` and `${}` templates.
- `docs/api-routes.md` is generated from it.
- `tests/test_api_contract.py` (added to CI): every UI path resolves; every GET answers JSON with
  `no-store` and never a 5xx (also with a non-existent id → JSON 404); unknown routes are JSON 404s;
  status reports `api` and `ui`; plus the security and static-serving checks below. Confirmed it fails
  against the old `server.py`.

### RR-05 — Medium: POST bodies were unbounded and not type-checked — fixed

(Prior audit F-05.) `_body()` read whatever `Content-Length` said and parsed any content type.

**Fix:** bodies over 16 MB get a 413 without being read; a non-empty body that isn't
`application/json` gets a 415 (cross-site pages can't send that type without a CORS preflight this
server never answers, so this backs up the Origin check when a browser omits Origin). After a rejected
body the connection is closed, so unread bytes can't be parsed as the next keep-alive request.
Every existing caller (the page, e2e, tests, bench scripts) already sends JSON, and no request carries
file data (uploads are by path), so nothing legitimate is affected. Verified: Vite's dev origin
(`localhost:5173`) is accepted; `Origin: null` and other hosts are refused.

### RR-06 — Low: the UI couldn't tell which API or UI it was talking to — fixed

`/api/status` now includes `"api": 1` (bump when a response shape changes incompatibly) and
`"ui": "web" | "legacy"`.

### RR-07 — Medium: nothing would build or package a React UI — fixed (untested until `web/` exists)

- CI: if `web/package.json` exists, set up Node 22 (npm cache on the lockfile), `npm ci`,
  `npm run build`, fail if `src/web/index.html` wasn't produced; PyInstaller adds `src/web` as `web`
  only when built. A second UI smoke run with `CLIPRADAR_UI=web` is `continue-on-error` until the
  new UI is meant to ship.
- `scripts/build_exe.bat` does the same locally (with a clear message if Node is missing).
- `.gitignore`: `src/web/`, `web/node_modules/`.

Until `web/` exists, all of these are no-ops and the build is identical to today's.

## Not changed (recommendations)

- **N-1 Port, don't redesign, the preview renderer.** `drawTitle`/`drawCaption`/`drawWatermark`/`FONTS`
  and the layout maths must keep matching FFmpeg's output. Move them into a plain TS module and call
  them from a canvas component; same for the editor timeline and heat strip.
- **N-2 Port order.** Shell + status + toasts/dialog → Home → Exports (simple lists) → Settings
  (many small forms) → Review (canvas, timeline, editor) last. Ship behind `CLIPRADAR_UI=web` until
  the smoke test passes on it, then drop `continue-on-error`.
- **N-3 Polling.** Five pollers work and don't need to change for parity. A single `usePoll` hook that
  pauses when hidden reproduces them; a combined `/api/poll` or SSE stream is a later optimisation.
  `/api/ping` must keep running while hidden (browser mode quits after 20 idle minutes).
- **N-4 `/api/job/{id}` returns the raw job dict**, including internal fields (`cancel`, `pause`,
  `body`). Don't build on those; read `status/step/progress/log/result/error`. A public projection
  can come later (the original page would need checking against it).
- **N-5 `server.py` is 3.5k lines.** Splitting route handling from queue/render orchestration (prior
  audit) is safer now that the contract test pins the routes, but doing it during the UI rewrite would
  move both halves at once. Do it before or after, not during.
- **N-6 FYI:** the updater, model download, installer URLs and CI mocks point at
  `allen1versonfr/vodclipper`, while this clone's remote is `ali1qbal/vodclipper`. If that's an
  account rename, GitHub's redirect keeps updates working (the updater follows same-host redirects);
  worth updating `updater.REPO` in a release so updates don't depend on the redirect. Left as is.

## Files changed

- `src/server.py`: UI selection, React build serving, pinned MIME types, body limit and JSON-only POSTs, `api`/`ui` in status.
- `src/ui/index.html`, `src/ui/app.js`: `data-testid`s and `window.ClipRadarTest` (no visible change).
- `tests/test_ui.py`: uses only the test contract; deterministic skip-by-score check.
- `tests/test_retry.py`: waits for the "ready" sound instead of racing it (failed 1 run in 5 here; the item shows done a moment before the sound is logged).
- `tests/test_api_contract.py`, `scripts/api_routes.py`, `docs/api-routes.md`, `docs/react-migration.md`: new.
- `.github/workflows/build.yml`, `scripts/build_exe.bat`, `.gitignore`: React build hooks.
