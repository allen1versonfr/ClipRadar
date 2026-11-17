# Plan: score how well Clip Radar picks moments

**Goal:** a number that says whether Clip Radar's top clips from a VOD are the moments people wanted, so any
change to ranking (`Analysis.combine`, `pick_clips`, the weights, a new signal) can be checked before it ships.

Line numbers are from Clip Radar 4.51.0 and will drift. The function names are what to search for.

## 1. Why

Titles have a blind test (`scripts/eval_titles.py`, 63% win in 4.48.0). Live mode has precision and recall
against hand-marked moments (`tests/live_replay.py`, `score()` near line 105). Finding moments in a VOD, which
is the main thing the app does, has no score at all. Ranking changes have shipped on "looks better on the
streams I tried". This plan adds the missing measure.

## 2. What exists today

- **Ranking.** `engine.Analysis.combine` (`src/engine.py:2144`) mixes chat, loudness, sound events, emotes,
  surges, reactions, YouTube's replay/comments/captions and viewers' clips into `an.score`, one value a second.
  `engine.pick_clips` (`:2777`) takes the best non-overlapping peaks and sets each clip's bounds
  (`smart_bounds`). `count=0` means "Automatic" (`estimate`, `standout_bar`).
- **Per-streamer learning.** `adjust=` in `pick_clips` comes from `src/learning.py`, built from what was kept,
  skipped and posted.
- **Viewers' clips.** `src/twitchclips.py`: `fetch(vod_id)` reads the clips viewers made of a Twitch VOD (start,
  end, views, title); `apply(an, d)` attaches them as the `viewers` part of the score (weight `w["viewers"]`,
  `GAIN = 3.0`).
- **A corpus builder.** `scripts/title_corpus.py` builds saved projects from public Twitch VODs (chat from GQL,
  a 20–40 minute stretch at 160p, `twitch_clips.json`, picked clips). It mixes viewers' clips into the ranking,
  and by default picks the stretch with the most viewer clips.
- **A/B between git refs.** `scripts/eval_titles.py --baseline REF` runs each side in its own process with that
  side's `src/` first on the path. Ranking A/B can use the same method.

## 3. Ground truth: the clips viewers made

On a Twitch VOD, the clips viewers made are a free, independent record of what people thought was worth
clipping. Use them as the answer key, and **switch them off as a ranking input while scoring** (weight
`viewers = 0`, or don't call `twitchclips.apply`). Otherwise the test reads its own answers.

**Moments from clips.** Clips of the same moment overlap. Group clips whose ranges overlap, or whose ends are
within 20 s of each other, into one *truth moment*. For each moment keep:
- its span (union of the clips),
- its *payoff*: the last 10 s of the most viewed clip (Twitch's button takes the 30 s before the press, so the
  reaction is at the end),
- `clippers` (distinct clip creators, or clips when the creator is missing) and `views` (sum).

**Which moments count.** A moment clipped by one person is noise as often as not. The main score uses moments
with `clippers >= 2` or `views >= 500`. Report the looser and stricter sets as well.

**Known bias:** viewers clip what is good *on Twitch*, which isn't always what does well on TikTok, and big
streams get far more clips than small ones. This is a test of "did it find the moments people noticed", not
"will it go viral". Say so in every report.

## 4. Metrics

For one VOD, with Clip Radar's clips in rank order (`pick_clips` with a fixed `count`, and a second run on
Automatic):

| Metric | Meaning |
|---|---|
| **Recall@N** (N = 5, 10, 20) | Share of truth moments whose payoff falls inside one of the top N clips. The headline number, at N = 10. |
| **Weighted recall@10** | The same, each moment weighted by `log2(1 + views)`. Big moments matter more. |
| **Precision@10** | Share of the top 10 clips that overlap *any* viewer clip, the one-clipper ones too. A low value can still be good clips nobody clipped, so this is reported but never gates. |
| **Payoff kept** | Of the matched moments, the share where the clip contains the whole payoff, not just part of the span. This checks `smart_bounds`. |
| **First hit rank** | The rank of the best (most viewed) truth moment in Clip Radar's list. |
| **Automatic count vs truth** | How many clips Automatic suggested against how many truth moments there are. |

Across the corpus, report the mean of each over VODs (each VOD counts once, however many clips it has), the
spread, and a per-VOD table.

## 5. The corpus

- **20 to 30 public Twitch VODs**, chosen for variety: just chatting, FPS, variety, IRL, events; small
  (under 2k viewers), medium and big streams; English and two or three other languages. A VOD needs at least
  8 truth moments (§3) to be included.
- **Long stretches.** Ranking is about choosing among many candidates, so use 60–120 minutes, or the whole VOD
  when it is under 3 h. Don't choose the stretch with the most clips (title_corpus's default). Choose it at
  random with a fixed seed, so the stretch isn't picked for being easy.
- **The manifest is committed; the data isn't.** `scripts/ranking_corpus.txt` lists `vod_id@start-end` with a
  short note per line. The downloaded data (chat, video, clips) lives outside the repo, as with title_corpus
  ("chat and sound are other people's, never committed").
- **Frozen.** Save `twitch_clips.json` once, at build time. Viewers keep making clips, so a later fetch would
  move the answer key.

## 6. The tool

`scripts/eval_ranking.py`, a developer tool that doesn't ship, next to `eval_titles.py`:

```
python scripts/eval_ranking.py build CORPUS --manifest scripts/ranking_corpus.txt     # once: download and freeze
python scripts/eval_ranking.py score CORPUS --out rank_out                            # this checkout
python scripts/eval_ranking.py score CORPUS --baseline main --out rank_out            # A/B against a ref
python scripts/eval_ranking.py score CORPUS --ablate                                  # each signal switched off in turn
```

- `build` reuses `title_corpus.py`'s functions (GQL chat paging, the section download, `twitchclips.fetch`)
  instead of copying them. Move the shared parts into an importable module if needed. It doesn't run Whisper:
  ranking doesn't need words. Deep scan / quiet gems are out of scope for v1.
- `score` runs `engine.analyze_cached` and `pick_clips` per project with viewers' clips off and **no learned
  adjust** (`adjust=None`), so the score doesn't depend on whose PC ran it. It writes `report.md` (headline
  table, per-VOD table, the biggest misses with times so you can go and watch them) and `report.json`.
- `--baseline REF` runs the other side in a subprocess with that ref's `src/` first on the path, the way
  `eval_titles.py` does. It prints the difference per metric and per VOD.
- `--ablate` runs once per score part (`chat`, `audio`, each event, `emotes`, `surge`, `react`, `agree`, `talk`
  dampening) with its weight set to 0. It shows which signals carry the ranking and which do nothing.
- The metric code goes in its own small module (`scripts/rankmetrics.py` or similar) so it can be unit tested
  without network or video.

## 7. Phases (one pull request each)

1. **Metrics and the tool, no network.** `rankmetrics` (grouping clips into moments, matching, every metric in
   §4) with `tests/test_rankmetrics.py` on hand-made clip lists: overlapping clips merge, a payoff just outside
   a clip is a miss, one moment matched by two clips counts once, an empty truth set, ties. `eval_ranking.py
   score` working on the existing `tests/assets` fixtures or a synthetic project. Add the unit test to CI.
2. **The corpus.** The manifest, and `build`. Twitch GQL and the VOD CDN may not be reachable from a cloud
   session. If not, write and unit-test `build` here, and have the owner run it on their PC. Record the build
   date and clip counts in the manifest.
3. **Baseline and ablation report.** Run `score` and `--ablate` on 4.51.0. Write
   `docs/audits/ranking-baseline-<date>.md`: the headline numbers, which signals matter, the 10 worst misses
   with a sentence each on why (watch them), and Automatic's count against truth. This report is the main
   deliverable. Don't change ranking in this phase.
4. **The rule for ranking changes.** Add a short section to `DESIGN.md` (or `docs/plans/README.md`): a PR that
   changes `combine`, `pick_clips`, `smart_bounds`, the weights or a signal includes `eval_ranking.py score
   --baseline main` output. It ships only if weighted recall@10 doesn't drop by more than 0.02 and no category
   of VOD drops by more than 0.05. Payoff kept must not drop.
5. **Optional, after the baseline:** fixes the baseline points at, each its own PR with its before/after
   numbers.

## 8. Risks

- **Overfitting to the corpus.** Tuning weights until the numbers go up will overfit 25 VODs. Hold out a third
  of the manifest (marked in the file) and only look at it when a change is otherwise ready.
- **Twitch's clip button bias.** The last 30 s before the press is the window, so "payoff = last 10 s" is a
  guess. Check it on 20 clips by hand in phase 3 and adjust.
- **Small streams have few clips.** The ≥8 truth moments rule keeps them out, so small streams are
  under-tested. Report that, and consider the owner's own kept/posted clips (`learning.json`) as a second
  answer key for small streams later.
- **Network access.** If GQL refuses or rate-limits, `build` must retry and resume, as title_corpus's `gql()`
  does.
