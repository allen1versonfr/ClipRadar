# Clip Radar design direction

Set by the product owner on 2026-10-23 (answers to four questions). Applies to the new UI (`web/`) and,
for the accent colour, to the current UI (`src/ui/`) too. Values marked *chosen by the agent* were
picked to carry out the owner's answers and can be changed here.

## Identity

- **Personality: energetic creator app.** Punchy and confident: bigger type, more contrast, feels fast.
  It is still a tool you work in for hours, so the energy comes from type, contrast and one strong
  accent, not from decoration.
- **Theme: dark only.** Footage reads best on dark, like a video editor. No light theme.
- **Motion: subtle.** Short transitions when screens and panels change. Nothing moves on its own except
  progress. Everything respects "reduce motion" in Windows.

## Colour

- **One accent: electric green.** It replaces the old signature yellow in both UIs. Used only for the
  main action on a screen, the current selection, and progress, never as decoration.
  - `--accent: #2fe36f` (*chosen by the agent*: a saturated green that reads as "go" and holds 10:1+
    contrast on the graphite surfaces)
  - `--accent-2: #22c75d` (hover / pressed)
  - `--accent-soft: rgba(47, 227, 111, .12)` (selected rows, active tab backgrounds)
  - `--on-accent: #06140b` (text on green)
- **Greys: graphite, unchanged** (`--bg0 #0e0f11` to `--bg4 #2a2b31`, lines `#26272c` / `#33353c`).
- **Text:** `--tx0 #e9eaed`; `--tx1 #b4b7bf` (raised from `#a3a6ae` for the "more contrast" answer,
  *chosen by the agent*); `--tx2 #90939c`, the dimmest grey that still meets WCAG AA (4.5:1) on every
  surface, `--bg0` to `--bg4` (the old `#6d7079` was 3.5:1 on panels, below the minimum).
- **Measured contrast:** accent on surfaces 10.1-11.3:1; text on green 11.1:1 (8.4:1 on hover);
  `--tx1` 8.6-9.1:1; `--tx2` 4.6:1 at worst.
- **Status colours keep their meaning:** danger `#f05a5e`, warning `#e3a33b`, live recording `#f2484d`.
  Success / ready uses the accent itself: green already means done, so a second green would only blur
  the two.

## Type

- **Inter**, as now (already bundled in `src/ui/fonts/`; chosen for legibility of numbers and small
  labels in a dense tool).
- New UI scale (*chosen by the agent* for "bigger type"): body 14px; screen titles 22px / 700 with
  -0.01em tracking; section titles 15px / 650; primary buttons 600 weight. The current UI keeps its
  sizes until each screen is ported.

## Dials

`Dial: ENERGY 2 / RHYTHM 2 / MOTION 2`

- ENERGY 2: confident, high contrast, one loud accent, but a work tool, not a showcase page.
- RHYTHM 2: screens share one structure (list + detail, cards for inputs) with deliberate breaks where
  the content needs them (the clip preview, the timeline).
- MOTION 2: 120-180 ms transitions on screen and panel changes; progress animates; no ambient motion.

## Not changed without asking

- The app icon / logo (`src/ui/icon.svg`).
- Caption colours offered for the clips themselves (`CAP_TEMPLATES`, `SWATCHES` in `src/ui/app.js`):
  those style the video, not the app.
