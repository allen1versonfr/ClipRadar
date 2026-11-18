# Plans

Detailed implementation plans for larger work, written for the developer who builds it. Each plan says
what exists today (with file and function names), the design, the phases (one pull request each), the
tests, and the risks.

| Plan | Issue | Status |
|---|---|---|
| [One core for the automated features](issue-50-automation-core.md) | #50 | Done |
| [Optional local LLM: titles, quiet gems, content-check context](issue-49-local-llm.md) | #49 | Closed: the model won 38–40% against the built-in titles |
| [Titles v2: better built-in titles and post captions, no model](titles-v2.md) | — | Done in 4.48.0 (titles 63%, captions 85% in a blind test) |
| [Efficiency and speed audit](efficiency-audit.md) | #47 | Planned |
| [Score how well Clip Radar picks moments](ranking-eval.md) | — | Phases 1–4 done: baseline in [docs/audits/ranking-baseline-2026-11-18.md](../audits/ranking-baseline-2026-11-18.md), rule below |
| [Harden what's there before adding more](hardening.md) | — | Planned |

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

## Changing how moments are ranked

From [ranking-eval.md](ranking-eval.md), phase 4. A pull request that changes how moments are found or cut
has to show it doesn't make that worse. That covers `engine.Analysis.combine`, `engine.pick_clips`,
`engine.smart_bounds`, `estimate` and `standout_bar`, the presets' weights in `detect.PRESETS`, or a signal
(`detect.enrich`, `audio_events`, `chatsignal`, emotes).

Run this on a corpus built from `scripts/ranking_corpus.txt`:

```
python scripts/eval_ranking.py score CORPUS --baseline main --out rank_out
```

Put the report's "Against `main`" table in the pull request. The report says whether the change passes.
It ships only if, against `main`:

- **weighted recall@10** doesn't drop by more than **0.02**,
- **no category's** weighted recall@10 drops by more than **0.05**. Only categories with at least 3 scored VODs
  are judged: with one or two, one moment changes the number that much. Today that leaves chatting, variety and IRL.
- **payoff kept** doesn't drop by more than **0.02**. One moment on one VOD moves it about 0.01, so "must not
  drop at all" would fail on noise.

The thresholds are `RULE_*` in `scripts/rankmetrics.py`; change them there and here together.

Also:

- **Look at the held-out VODs only when the change is otherwise ready.** Run `score --holdout --baseline main`
  once, and put that result in the pull request too, passing or not. If a change passes on the main VODs but not
  on the held-out ones, it was tuned to the main VODs.
- **Gains count only if they're bigger than the noise.** Across 19 VODs the spread is about ±0.2 per VOD, so
  read a change of a few hundredths in one metric as no change, not as a win.
- **The viewers' clips signal (`twitchclips`) is switched off while scoring**: it's the answer key. A change to it
  can't be scored this way. Show instead where the clips land against the VOD's own sound (as in the baseline's
  "Where a clip is in its VOD"), and that `score --baseline main` is unchanged.
- **This measures the moments viewers clipped on Twitch.** It isn't a measure of what does well on TikTok. A change
  made for TikTok reasons that costs recall can still be the right call, but the pull request says so and gives the
  numbers.
