# Working on Clip Radar

- UI design rules are in `DESIGN.md`. In particular: **every setting whose effect isn't obvious gets a hover explanation**
  (`Seg` option hints, `<Help>`, `<Tip>` in `web/src/components/Tip.tsx`); add or update one whenever a setting is added
  or changed.
- Each update bumps `src/version.py`, the README badge and `CHANGELOG.md` (Keep a Changelog), with tests for what changed.
