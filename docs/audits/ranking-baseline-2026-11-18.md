# Ranking baseline, 2026-11-18

How well Clip Radar 4.52.1 picks a Twitch VOD's moments, scored against the clips viewers made of it. This is phase 3 of
`docs/plans/ranking-eval.md`: the numbers every later ranking change is compared with. Nothing in ranking was changed.

**What this measures:** whether Clip Radar found the moments viewers noticed and clipped on Twitch. It isn't a measure of
what will do well on TikTok, and big streams get far more clips than small ones.

## How it was run

```
python scripts/eval_ranking.py build CORPUS --write-manifest      # 2026-11-17/18, scripts/ranking_corpus.txt
python scripts/eval_ranking.py score CORPUS --ablate --out rank_out
```

- **Code:** `src/` identical to `main` at 4.52.1 (merge 3dd8220). The score ran on the phase 2 branch, whose `src/` differs
  from main only in the version number.
- **Settings:** the app's defaults. That's the "Any stream" preset, Normal sensitivity, 15–120 s clips, and the chat delay
  worked out per VOD. The laughter detector was on for every VOD. Nothing was learned from kept clips (`adjust=None`), and
  the viewers' clips were not part of the ranking.
- **Corpus:** 20 VODs (the 9 held-out ones weren't looked at). 19 were scored, with 306 truth moments between them: a
  truth moment is two or more people clipping it, or 500+ views. tarik's VALORANT VOD has 7 truth moments, so it isn't
  scored, and **no shooter VOD is in the scored set**. The 19 are 11 chatting, 4 variety, 3 IRL and 1 event. Languages
  are English, German, Spanish, Portuguese and French; sizes run from small (under 2,000 watching) to big.
- **Clips:** each VOD's whole clip list, every clip in the stretch with who made it, frozen at build time.
- **Run time:** 55 s on a 4-core cloud machine, once the analyses were cached.

## Headline

| Metric | Mean | sd | Min | Max |
|---|---|---|---|---|
| **Recall@10** | **0.26** | ±0.21 | 0.00 | 0.88 |
| Recall@10 at best (10 clips, 10 different moments) | 0.74 | ±0.26 | 0.27 | 1.00 |
| **Weighted recall@10** (big moments count more) | **0.27** | ±0.22 | 0.00 | 0.90 |
| Recall@5 | 0.15 | ±0.13 | 0.00 | 0.40 |
| Recall@20 | 0.38 | ±0.23 | 0.08 | 1.00 |
| Precision@10 (top 10 clips that overlap anything viewers clipped) | 0.70 | ±0.17 | 0.40 | 0.90 |
| **Payoff kept** (a clip that found a moment has its last 10 s) | **0.72** | ±0.24 | 0.33 | 1.00 |
| First hit rank (where the most viewed moment came, when it came at all) | 9.0 | ±4.9 | 1 | 17 |
| Automatic: clips suggested | 25.3 | ±7.9 | 7 | 38 |
| Automatic: recall | 0.40 | ±0.23 | 0.05 | 1.00 |

In plain words:
- **Of the moments viewers clipped, a quarter are in Clip Radar's top 10.** With 16 truth moments on an average VOD, the
  top 10 could have held about three quarters of them.
- **What it does pick is mostly right.** 7 of its top 10 overlap something viewers clipped. The low recall comes from
  what it misses, not from picking junk.
- **The VOD's most viewed moment isn't in the top 20 on 12 of the 19 VODs.** On the other 7 it came 9th on average.
  This is the weakest result.
- **Automatic suggests too many.** It suggested 25 clips on average where there were 16 truth moments, and those 25
  found 40% of them. It suggested more than there were on 15 of 19 VODs, and as many as 38 for 11.

### By category

| Category | VODs | Recall@10 | Weighted recall@10 | Payoff kept |
|---|---|---|---|---|
| chatting | 11 | 0.21 | 0.20 | 0.72 |
| variety (games) | 4 | 0.53 | 0.56 | 0.68 |
| IRL | 3 | 0.06 | 0.10 | 0.67 |
| events | 1 | 0.36 | 0.42 | 1.00 |

Games do far better than talking. On the GTA RP and Rocket League VODs, the moments viewers clip are the ones the
streamer and chat react to. IRL is the worst. Both agaamuza VODs (Germany, outdoors) and the 36-hour sleep stream have
plenty of loud, chatty stretches that nobody clipped. One event VOD and no shooters isn't enough to say anything about
those.

## Which signals carry it

Each part of the score set to 0 in turn, over the same 19 VODs. A negative change means the part helps.

| Part | Weighted recall@10 | Change | Recall@10 | Change | Payoff kept | Change |
|---|---|---|---|---|---|---|
| chat | 0.17 | **−0.103** | 0.16 | **−0.102** | 0.51 | **−0.214** |
| surge (chat speeding up) | 0.24 | −0.030 | 0.24 | −0.016 | 0.68 | −0.041 |
| emotes | 0.25 | −0.021 | 0.24 | −0.024 | 0.69 | −0.031 |
| laugh | 0.26 | −0.018 | 0.24 | −0.024 | 0.67 | −0.049 |
| agree (chat and sound together) | 0.26 | −0.012 | 0.25 | −0.008 | 0.70 | −0.021 |
| cheer | 0.27 | −0.007 | 0.26 | −0.004 | 0.72 | −0.005 |
| scream | 0.27 | −0.006 | 0.25 | −0.005 | 0.69 | −0.030 |
| react (after a quiet moment) | 0.27 | −0.003 | 0.26 | −0.003 | 0.65 | −0.078 |
| talk (chat that's just talking, played down) | 0.27 | +0.002 | 0.26 | −0.003 | 0.73 | +0.006 |
| audio (loudness) | 0.29 | **+0.016** | 0.27 | +0.007 | 0.63 | −0.093 |

- **Chat carries the ranking.** Without it, recall@10 drops by 0.10 (40% of it), and payoff kept by 0.21.
- **Loudness doesn't help find moments: without it, recall goes slightly up.** It does help the bounds: payoff kept
  drops 0.09 without it, and the same holds for react. Loudness is worth keeping for where clips start and end, but not
  for which moments are picked.
- Laughter, emotes, chat speeding up and agreement each add a little (0.01–0.03). Cheering and screaming barely
  register.
- **Talk dampening does nothing measurable here.** It was added in 4.32 to stop chat answering a question from counting
  as a moment. No change either way.

These are differences of 0.01–0.03 on 19 VODs, where the VOD-to-VOD spread is ±0.21. Only chat (and loudness's effect
on payoff kept) is clearly more than noise.

## Why it misses

Comparing the 61 truth moments the top 10 found with the 245 it missed, at each moment's best second:

| | Found | Missed |
|---|---|---|
| Score, against the 10th clip's | 1.12 | 0.35 |
| Chat part of the score (median) | 3.23 | 0.92 |
| Loudness part (median) | 0.39 | 0.19 |

Most missed moments aren't near misses. They score about a third of what the 10th clip does, and chat hardly reacted to
them. The moments viewers clip and the moments chat reacts to are different sets. Viewers clip lines in a conversation,
a play the casters call, an event's crowd and songs, none of which make chat explode.

One mechanism seen in the worst miss was checked across the corpus and is small. In `combine`, a below-average loudness
(negative z) subtracts from a chat spike. Of the 75 missed moments where chat did react strongly, 31 were pulled down
this way, but only 4 would have reached the top 10 without it.

### The 10 worst misses

The most viewed truth moments the top 10 didn't find, at most 2 per VOD. **None of these were watched.** Each sentence
comes from the data:
- what the streamer said over the viewers' clip (Whisper "small", through the app's own `engine._hear`),
- what chat said around it,
- what the score was there.

Times are in the VOD, at the start of the payoff.

1. **stableronaldo, [1:11:08](https://www.twitch.tv/videos/2922292514?t=1h10m48s), "WE DID IT", 78,962 views, 27 people.
   Not in the top 20.** Chat's biggest reaction (WEDIDIT, OMG, OOOO), but Whisper heard nothing and the sound was well
   below normal (loudness part −1.2). The score was 2.9 against the 10th clip's 4.0. A chat explosion over a quiet
   stream, possibly something on screen, or a stretch Twitch muted. It's the one case above where the loudness pull
   decided it.
2. **plaqueboymax, [0:48:41](https://www.twitch.tv/videos/2926394800?t=0h48m21s), untitled (the stream's own title),
   6,532 views, 10 people.** Chat laughed (LOL, LMAOOO, KC3), but at little more than its usual rate (3.3 a second
   against 2.6). Whisper heard a conversation ("bring your A game"). The score was 1.4 against 7.2: a funny line, not a
   reaction.
3. **realkatieb, [0:10:39](https://www.twitch.tv/videos/2929376496?t=0h10m19s), "katieb buckets", 3,647 views.** Chat was
   busy (9.2 a second against 5.2: KATIE, W, BUCKETS), but the score was 2.5 against 6.2. Whisper heard an event host
   (Jordan brand, TwitchCon). A shot made at a live event, where chat's W spam is short and the sound is a PA system.
4. **peereira7, [1:17:49](https://www.twitch.tv/videos/2921218131?t=1h17m29s), "Se china y se le cae la baba",
   3,224 views.** GTA San Andreas RP dialogue. Chat at its usual rate, no laughter worth the name, score 1.1 against 5.7.
   A gag in the role-play that nobody reacted to out loud.
5. **realkatieb, [1:00:36](https://www.twitch.tv/videos/2929376496?t=1h0m16s), "HOLY AURA", 1,682 views.** A crowd
   chanting "One more! One more!" at the event. Cheering 1.0 and screaming 0.7 were detected, but the score was 1.4
   against 6.2. The event's crowd isn't the streamer reacting, and that's what the score is built for.
6. **plaqueboymax, [1:32:39](https://www.twitch.tv/videos/2926394800?t=1h32m19s), untitled, 1,537 views.** Laughter (3.4),
   chat speeding up (1.8) and a reaction after quiet (1.4). Score 5.5 against 7.2. A real moment that came just under, on
   a VOD with many strong laughs.
7. **blastpremier, [1:45:11](https://www.twitch.tv/videos/2927424042?t=1h44m51s), "xertioN 4K demolishes the force buy",
   1,424 views, 16 people.** Caster play-by-play. Chat went to 2.5 a second against 1.1 (ace, gg), but the score was 1.5
   against 7.4. An esports highlight lives in the gameplay and the casters' voices, which the score doesn't look at.
8. **stableronaldo, [1:34:01](https://www.twitch.tv/videos/2922292514?t=1h33m41s), "LOL", 1,312 views, 12 people.
   Came 13th.** Screaming (2.3) and chat laughing hard (LOL, LMFAO, 11 a second). Score 3.6 against 4.0: a near miss.
9. **blastpremier, [0:37:15](https://www.twitch.tv/videos/2927424042?t=0h36m55s), "crowd", 1,211 views.** The analyst
   desk talking, with chat at its usual rate. Score 1.2 against 7.4. Whatever the crowd did is under the casters'
   voices.
10. **bisteconee, [0:22:53](https://www.twitch.tv/videos/2927116113?t=0h22m33s), "música", 717 views, 10 people.** The
    streamer saying he might end the stream, chat saying good night and DinoDance. Score 1.0 against 5.1. A song moment.
    The score plays down loudness while music plays and ignores vibing emotes, both on purpose. So by design it doesn't
    pick moments like this, and viewers do.

## Automatic against the truth

| | Mean | Range |
|---|---|---|
| Clips Automatic suggests | 25.3 | 7–38 |
| Truth moments | 16.1 | 8–37 |
| Truth moments Automatic finds | 40% | 5%–100% |

Automatic suggested more clips than there were truth moments on 15 of 19 VODs, and fewer on 4. It doesn't follow the
truth count. The VODs with the most truth moments (plaqueboymax 37, bisteconee 35, agaamuza 34) got 33, 34 and 33
suggestions. But itskreas, jinnytty and coachlim, with 8 each, got 29, 30 and 31, and mirssadvm with 11 got 38.

## Per VOD

| VOD | Category | Truth | R@5 | R@10 (best possible) | R@20 | wR@10 | P@10 | Payoff kept | First hit | Automatic / truth |
|---|---|---|---|---|---|---|---|---|---|---|
| agaamuza_2924165363_0 | irl | 22 | 0.05 | 0.09 (0.45) | 0.18 | 0.19 | 0.40 | 1.00 | 9 | 7 / 22 |
| agaamuza_2929771098_0 | irl | 34 | 0.06 | 0.09 (0.29) | 0.15 | 0.10 | 0.40 | 0.33 | – | 33 / 34 |
| bisteconee_2927116113_0 | chatting | 35 (+1 at edges) | 0.03 | 0.06 (0.29) | 0.17 | 0.03 | 0.80 | 1.00 | – | 34 / 35 |
| blastpremier_2927424042_1426 | events | 11 | 0.27 | 0.36 (0.91) | 0.45 | 0.42 | 0.70 | 1.00 | – | 14 / 11 |
| coachlim_2921308640_1011 | chatting | 8 (+1 at edges) | 0.25 | 0.25 (1.00) | 0.38 | 0.15 | 0.80 | 0.50 | – | 31 / 8 |
| coachlim_2929595961_523 | irl | 12 | 0.00 | 0.00 (0.83) | 0.08 | 0.00 | 0.60 | – | – | 26 / 12 |
| hasanabi_2927850434_3248 | chatting | 12 | 0.08 | 0.25 (0.83) | 0.58 | 0.19 | 0.40 | 1.00 | 17 | 30 / 12 |
| hasanabi_2930725553_1668 | chatting | 10 | 0.30 | 0.30 (1.00) | 0.40 | 0.46 | 0.70 | 1.00 | 1 | 14 / 10 |
| indoorscream_2930661265_5185 | variety | 8 | 0.38 | 0.88 (1.00) | 1.00 | 0.90 | 0.80 | 0.71 | 6 | 26 / 8 |
| itskreas_2928001302_0 | chatting | 8 (+1 at edges) | 0.25 | 0.38 (1.00) | 0.38 | 0.32 | 0.50 | 0.33 | – | 29 / 8 |
| jinnytty_2920994237_7715 | chatting | 8 (+1 at edges) | 0.25 | 0.25 (1.00) | 0.25 | 0.27 | 0.90 | 1.00 | – | 30 / 8 |
| mirssadvm_2928013414_0 | chatting | 11 | 0.00 | 0.00 (0.91) | 0.18 | 0.00 | 0.90 | – | – | 38 / 11 |
| paache__2926795779_1427 | variety | 13 | 0.08 | 0.38 (0.77) | 0.77 | 0.37 | 0.80 | 0.60 | 14 | 21 / 13 |
| peereira7_2921218131_2561 | variety | 15 | 0.13 | 0.27 (0.67) | 0.47 | 0.30 | 0.70 | 0.75 | – | 17 / 15 |
| plaqueboymax_2922596094_0 | chatting | 20 (+2 at edges) | 0.00 | 0.10 (0.50) | 0.25 | 0.15 | 0.90 | 0.50 | – | 29 / 20 |
| plaqueboymax_2926394800_0 | chatting | 37 (+2 at edges) | 0.05 | 0.11 (0.27) | 0.16 | 0.13 | 0.80 | 0.75 | – | 33 / 37 |
| realkatieb_2929376496_0 | chatting | 13 | 0.15 | 0.31 (0.77) | 0.38 | 0.32 | 0.70 | 0.75 | 9 | 21 / 13 |
| stableronaldo_2922292514_0 | chatting | 19 | 0.16 | 0.26 (0.53) | 0.42 | 0.22 | 0.60 | 0.40 | – | 26 / 19 |
| ster_2930726095_8806 | variety | 10 (+1 at edges) | 0.40 | 0.60 (1.00) | 0.60 | 0.66 | 0.90 | 0.67 | 7 | 21 / 10 |
| tarik_2930747792_7407 (not scored: too few moments) | fps | 7 | 0.43 | 0.43 (1.00) | 0.43 | 0.33 | 0.60 | 0.00 | – | 18 / 7 |


## Checks on the method

**Where a clip is in its VOD.** Twitch's `videoOffsetSeconds` for a clip is where it **ends**, not where it starts. The
sound of 22 clips from 5 channels was matched against their VODs, and every one started `durationSeconds` before the
offset (within 2 s; one within 6 s). Before this was found, the answer key was a clip-length late, and the first two
VODs scored recall@10 0.08 and precision 0.45. `build` now places each clip at `[offset − duration, offset]`.
**The app's `twitchclips.parse` still takes the offset as the start.** So the viewers' clips signal it adds to ranking
(4.45), and the clip titles it offers as title ideas (4.47), are one clip-length late. Fixing it changes ranking, so it
isn't part of this audit.

**The payoff: the last 10 s of the most viewed clip.** The plan asks for this to be checked on 20 clips by hand. That
wasn't possible here, since nobody watched them. Instead, chat was used as a clock: when chat reacted most, minus its
delay. Over 269 truth moments, the moment chat reacted to fell a median of 11 s before the clip's end:
- 22% in the last 10 s,
- 51% earlier,
- 27% after the end. That last share is partly the edge of the window searched.

Chat's timing is too rough to settle it either way. The headline doesn't depend much on it: with the payoff as the last
20 s, recall@10 is 0.31; with the last 30 s, 0.32. **Payoff kept does depend on it:** 0.72 → 0.49 → 0.36. Until 20
clips are checked by watching, read payoff kept as a rough figure.

**Where the clips come from.** The app reads a channel's 100 most viewed clips per period, shared between its VODs
(`twitchclips.fetch`). Read that way, 1 of 382 surveyed VODs had 8 truth moments in a stretch. `build` reads each VOD's
own list from both ends instead. That's every clip of a VOD with up to ~200, and the stretch is picked only where every
clip is known. The same thin list is all the app's viewers' clips signal has to work with.

## Limits

- **No shooters and one event in the scored set.** Shooter VODs rarely have 8 moments two people clipped. 8 of the first
  30 VODs picked were subscriber-only, or had parts muted for music (their stretches come down empty), which also keeps
  out music streams. The scored set is mostly English (11 of 19) and chatting (11 of 19).
- **Big streams count the same as small ones**, but a big stream's truth moments are better attested: more people
  clipped each one.
- **The held-out third (9 VODs) wasn't looked at.** One of them (peereira7 2930895806) didn't build, because its stretch
  of a 25-hour VOD took longer than the download limit. A cache warm-up over every built VOD printed one combined line;
  nothing per VOD from it was read.
- **Viewers clip what's good on Twitch**, a funny line in a conversation for example. Clip Radar ranks reactions.
  Some of this gap may be the right call for TikTok. This baseline can't tell.

## What this points at (phase 5, not done here)

Each item would be its own pull request with `eval_ranking.py score --baseline main` numbers, under phase 4's rule:

1. **`twitchclips.parse` places clips one clip-length late.** A plain bug in the viewers' clips signal. The fix is
   `start = offset − duration`.
2. **Automatic suggests about 25 clips whatever the VOD.** The fixed count of 20 found 0.38 of moments, Automatic's ~25
   found 0.40. A tighter standout bar could keep most of that with fewer clips.
3. **The most viewed moment is missing from the top 20 on 12 of 19 VODs.** The moments people clip most often have a
   modest chat reaction and a long, building setup. Signals that see a sustained build, not just a spike, are worth
   trying.
4. **Loudness picks no moments here.** Its weight in choosing which moments (as opposed to their bounds) could go down,
   and a quiet stretch shouldn't subtract from a chat spike. That alone is worth 4 of 245 misses, so it's small.
