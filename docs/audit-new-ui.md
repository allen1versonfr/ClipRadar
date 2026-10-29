# New UI audit before it becomes the default (4.25.0)

Two questions, answered before the React UI replaced the original page at `/`:

1. **Is everything accounted for?** Can you do everything in the new UI that you could in the original page?
2. **Is everything accessible?** WCAG 2.2 A and AA, keyboard, and touch.

## 1. Coverage

### How it was checked

- Every server path each UI calls (`src/ui/app.js` and `index.html` against `web/src`, tests left out),
  so a feature that talks to the server can't be missing unnoticed.
- Every control in the original page (`index.html`: about 300 buttons, inputs, selects and links,
  screen by screen, plus the dialogs), each looked up in the new UI by what it does.
- Behaviour that isn't a control: the window's close button, the menu badges, the status bar, the
  update notice, links that still pointed at the original page.

### What was missing, and is now in the new UI

| Gap | Where | Now |
|---|---|---|
| Closing the window while something runs did nothing: the desktop window calls `window.askQuit()` and waits for the page to ask, and the new UI didn't define it | App | `window.askQuit` asks "Quit Clip Radar?"; Quit in the top bar too (`App.tsx`) |
| Quit | top bar | added |
| TikTok: Open TikTok Studio, Upload to TikTok (Clip Radar's own browser window), Press Post for me, the caption with its count, what happened last time | Exports | `features/exports/Posting.tsx` (`TikTokPanel`), `posting.ts` |
| Campaign submit: link to your post (saved as you type), Copy submission, Send to Discord, whether it was sent | Exports | `SubmitPanel` |
| The Posted column (Opened in Studio, In Studio, Posted, Upload stopped, Discord ✓) | Exports | added to the list |
| Copy caption, Copy source link, Remove in the clip's panel | Exports | added |
| The active campaign, with a way to change it | Exports | banner linking to Settings › Campaigns |
| A clip's own volume (Much quieter … Louder) | Review › Clip | added (`rv-gain`) |
| The Hashtags switch next to the post caption | Review › Clip | added |
| Badges on Review (clips to review) and Exports (renders running) | menu | added (`shell.ts`) |
| What's running (recording, renders) and the update notice | status bar | `components/StatusBar.tsx`, each a link to where it's shown |
| "Sign in to Twitch" on Home went to the original page's Settings | Home | `#settings/twitch` (Settings opens at a section: `#settings/<section>`) |
| "Edit in the current UI" (a stopgap while Review was being ported) | Review | removed: everything it opened is in the new Review |
| Live clipping running when the window opens: the original page opened it | App | opens `#review/live/<session>` |
| Keep watching: when the stream you're on ends and the channel's next one starts, the page follows it | Review (live) | follows it, and says the stream ended |

### Found on the way: a bug in both UIs

Running the TikTok upload journey twice in a row (once per UI) showed that **an upload after another
one could hang**: an upload only loaded TikTok Studio's upload page when Clip Radar's TikTok window wasn't
already on it. After an upload the window is still on that page, but in a used state (the last clip in
its editor, or an error), so the next upload waited 60 s for an upload box that wasn't there and failed
with "TikTok may have changed the page". Every upload now starts from a freshly loaded page
(`studio_auto.upload`). `tests/test_studio_handoff.py` runs the whole journey on each UI, the second
right after the first one's failed upload.

### Tests that now run on both UIs

- `test_ui.py`: the whole contract (CI: the new UI by default, the previous one with `CLIPRADAR_UI=legacy`).
- `test_live_switch.py`: the live session opens on start, a second stream stays open, Keep watching is followed.
- `test_studio_handoff.py`: Upload to TikTok with Press Post off and on, and a step that stops.

### Differences on purpose

- The keyboard help is a line of keys under the clip, not a dialog.
- The top bar's Live pill is in the status bar: "Recording <channel>" links to the session.
- `window.prompt` for the streamer's name is an inline box (Review › title B).

### Left out on purpose

- "Try the new Exports" in the original page: it pointed here.

## 2. Accessibility

### How it was checked (`tests/test_web_a11y.py`, in CI)

Against the real server with a real video open and a clip rendered:

- **axe-core 4.10** (WCAG 2.0, 2.1 and 2.2 A and AA rules) on Home, Review's list of open videos, Review
  on a video (Clip, Captions, Text, Layout and Flagged tabs), the detection settings dialog, a confirm
  dialog, Exports, and Settings with every section open.
- **The focus shows**: Tab through each screen; every control it reaches draws a ring.
- **Touch**: on a 420 px touch screen (coarse pointer), every control on Home, Review, Exports and
  Settings is at least 44 x 44 px (a checkbox counts with its label).

### Findings, all fixed

| Finding | Fix |
|---|---|
| axe-core: none on any screen or dialog | |
| Text boxes and selects showed focus only as a 1 px border colour | a 2 px ring (border and box-shadow) |
| On touch: small buttons were 44 px tall but narrower (Play, −1s, Zoom, ×) | 44 px wide too |
| On touch: links in sentences ("Sign in to Twitch") were 15 px tall | 44 px |
| On touch: the content check's category checkboxes were 13 px with no label around them | the cell around each takes the click, 44 px |
| On touch: checkbox labels in a row (Hashtags) were 19 px tall | 44 px |

`test_ui.py` (both UIs), the new-UI browser tests, the tests above and the Vitest tests pass with these changes.
