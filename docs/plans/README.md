# Plans

Detailed implementation plans for larger work, written for the developer who builds it. Each plan says
what exists today (with file and function names), the design, the phases (one pull request each), the
tests, and the risks.

| Plan | Issue | Status |
|---|---|---|
| [One core for the automated features](issue-50-automation-core.md) | #50 | Planned |
| [Optional local LLM: titles, quiet gems, content-check context](issue-49-local-llm.md) | #49 | Planned |

## Order

1. **#50, phases 1–3.** This pins today's hands-free behaviour with tests, then moves the chain (title →
   check → render → Discord → cleanup) into `src/autopilot.py`. It gives #49 one hook (`clip_titled`)
   instead of another hand-threaded callback.
2. **#49, phase 0 and phase 1.** Phase 0 checks with a script whether a local model writes clearly better
   titles at all. Phase 1 adds viewers' clip titles as title candidates (no model needed) and ships either way.
3. **#49, phases 2–3**, only if phase 0 says yes. This is the runtime (`src/llm.py`) and smarter titles, as a
   `clip_titled` step.
4. **#50, phases 4–6** and **#49, phases 4–6** in either order.

Line numbers in the plans are from Clip Radar 4.45.1 and will drift. The function names are what to search for.
