# Titles v2: better built-in titles and post captions, no model

Issue #49 tested whether a local model writes better titles than Clip Radar. It didn't: it won 38–40% against
the built-in titles, and about 75% of the time it just returned the built-in title. This plan does the other
thing: make the built-in titles and captions better, and measure it the same blind way.

Read [`README.md`](README.md) in this folder first.

## 1. How it's measured

`scripts/eval_titles.py PROJECTS --baseline main --out eval_v2` makes every clip's title and post caption twice,
once with `src/` from a git ref (unpacked with `git archive`) and once with this checkout's, each side in its own
process, the way the app does it (`server._title_clip`, with the channel's emotes, viewers' clip titles and the
default Settings › Titles and captions). It writes the same `rate.html` / `key.json` / `results.json` as phase 0
of #49. Titles and post captions are separate items. Pairs that came out the same aren't shown and count as
"same". `--score key.json rater1.json rater2.json` gives the new side's win rate for titles and for captions.

**The ship rule:** each of titles and captions ships on its own if the new side wins at least 60% of the
non-"same" comparisons, across two independent raters. As in phase 0, the raters are two Claude subagents that see
only `rate.html`'s content (the clip's words around the peak, the most-agreed chat, the two options), never the
key or the code.

## 2. The corpus

`scripts/title_corpus.py OUT --vod ID[@start-end]` builds saved projects from public Twitch VODs: chat from
Twitch's GQL (stepping `video.comments(contentOffsetSeconds: N)`; paging by cursor is refused to anonymous
clients), the stretch's 160p video with yt-dlp `--download-sections`, viewers' clips with `twitchclips.fetch`,
clips found by `engine.analyze_cached` + `engine.pick_clips` (12 per VOD), and each clip's words from
faster-whisper `small` on the CPU (`engine._hear`). Media and chat stay outside the repository.

Built on 8 Nov 2026 in a cloud session (video, chat and viewer clips were all reachable):

| VOD | Streamer | Kind | Stretch | Chat messages | Viewer clips in it |
|---|---|---|---|---|---|
| 2910903624 NMS BOXING 5 | jasontheween | Event (boxing) | 7200–9600 s (40 min) | 22,283 | 8 |
| 2910392998 Japan night life | KaiCenat | IRL | 517–2317 s | 17,183 | 3 |
| 2921768812 Losing my mind | caseoh_ | Gaming (Minecraft, "Just Chatting") | 8870–10670 s | 12,849 | 12 |
| 2919608075 Rating your setups | Jynxzi | Gaming (Rainbow Six Siege) | 9761–11561 s | 3,239 | 0 |
| 2920357942 anotha one | tarik | Gaming (VALORANT) | 735–2535 s | 1,780 | 1 |
| 2921381312 Files and trials | HasanAbi | Just Chatting (politics) | 17502–19302 s | 11,728 | 4 |
| 2921461169 Stunt actor class | ExtraEmily | IRL | 5206–7006 s | 8,200 | 4 |

84 clips, 81 with words, 22 with a viewer's clip title.

## 3. What's wrong with the baseline (4.47.1), seen in this corpus

Written before any change. Of 84 titles, **75 are a line said on stream, 7 are mood templates, 2 are viewers'
clip titles and none is chat's phrase**. Of 84 captions, **81 are mood templates** ("Chat could not stop laughing
at this one · jasontheween's stream 😂") and 3 a quote.

1. **Any line said near the peak beats everything else.** The bar for a quote (score 2.5) lets through flat
   commentary and fragments: "Sarah slowing down the pace a little bit", "No, the lady, you know what I mean?",
   "Feeling like wait till", "Then he'll put the lights", "Tell them after this game after", "I think everyone can
   believe in what", "In the House", "Chat blue bio chat", "Keep going out chat".
2. **Two sentences run together.** Whisper often leaves out the full stop but starts the next sentence with a
   capital, and the title takes both halves: "Kiss your lips No!", "Jynxzi, Jynxzi Oh my god", "I'm flanking a Oh
   my", "Why I oughta Oh", "He's already fluent in Korean Holy f***", "I think there's something wrong wait What is
   this?" (9 titles).
3. **Whisper's spacing shows**: "Did y 'all see that?", "1 ,000 yen".
4. **Good viewer titles lose to those lines** (20 clips): "KAI GOT SMACKED", "Jacob jumps and twists from the top
   of the building", "Emily runs away from an exploding car", "how tf you make crack?", "theres a villager",
   "ninja", "AURA", "TKO", "scripted??", "Take that Russel". **Generic viewer titles win** when there's no line:
   "Yup", and as candidates "lol", "LMFAOOOOO", "GGS", "OMFG", "LMAOOO".
5. **Chat's phrase is never used here.** It needs two words or more and 12% of everyone who chatted, which a big
   chat almost never reaches, and a single word never counts. Chat was saying specific things: "Baking soda" ×29,
   "WW DIANA" ×51, "POLLO" ×12, "Pig dawg" ×17, "LIBBERS" ×14, "lets do stuff" ×15, "What a deal" ×12, "Why
   bebe" ×8, "DEAL" ×10. When it is used, it can be the wrong thing: on the README's run of this boxing VOD it was
   'Chat: "Nmsboxing"', a channel emote that's also the event's name (and hashtag).
6. **What chat named isn't used.** Counted once per chatter, chat named people and things: diana (83 chatters on
   one clip), water/coach/round, villager/nether, alex, pig, slap, mexico, new york. `topic()` only takes a word
   chat used with "the/his/that" twice, so it found 5 topics in 84 clips, and two were poor ("top", "team").
7. **Captions say nothing specific.** A mood line plus whose stream, whatever happened, even when the clip has a
   quote, a viewer's title or a chat phrase that the title didn't use.
8. Smaller: Twitch sub emotes ("caseohDailydoodlestwerk", "jynxziBadAim") count as words; "NMS Boxing" gets
   #gaming.

## 4. What changed (4.48.0)

`src/titles.py`:

- **Order** (`generate`): a line said on stream scoring `QUOTE_STRONG` (6.0) or more, then a viewer's clip
  title, then chat's phrase, then a line scoring `QUOTE_FAIR` (5.5), then one word chat kept typing, then other
  lines (`QUOTE_OK`, 2.5, as before), then the templates. Before: any line of 2.5 came first. The strong lines in
  the tests ("No way he actually did that!") score 6.2–6.7; on the corpus, 7 of 84 best lines do.
- **Viewer titles that only react** (`says_nothing`) are skipped: "Yup", "LMFAOOOOO", "GGs……", "OMFG".
- **Sentences run together** (`_new_sentence`, `SENTENCE_WORDS`): a capitalised everyday word after a word with
  no punctuation starts a new phrase. Names and "I" don't.
- **Scoring** (`_score_phrase`): a line ending on a preposition, article or "you're" (`TRAIL_PREP`) loses 1; a
  line that's mostly one word repeated loses 2.5 or more.
- **Spacing** (`polish`): "y 'all" → "y'all", "1 ,000" → "1,000".
- **Captions** (`caption`, `QUESTIONS`): lead with the first of a line said scoring 5.5+, a viewer's title,
  chat's phrase, a line said scoring 4+ with a specific word in it (`CAPTION_QUOTE`, `_has_specific`), none of
  them the title in other words or already used by another clip of the stream; else a mood line. Then a question
  (the least used for the mood), then "(name on Twitch)". `compose` still puts the hashtags last, and
  `studio_auto.fit_caption` still fits it to TikTok's limit. With no mood, the next line said is used, as in 4.41.

`src/chattitles.py`:

- Hashtags, Twitch's own emotes (`TWITCH_EMOTES`), channels' sub emotes (`_SUB_EMOTE`: "caseohHamster"), a
  phrase of one word repeated, a phrase ending on an emote, and the stream's title, game and streamer squeezed
  together (`avoid`, passed by `server._chat_title`) are never a title.
- The 12% share counts chatters who typed a word that isn't an emote or filler, not everyone in the window.
- **Shouts**: with no phrase, a word typed in a message of up to three words by `SHOUT_PEOPLE` (6) chatters and
  `SHOUT_SHARE` (10%) of those talking, which isn't an everyday reaction (`SHOUT_STOP`), a name, a swear or an
  emote. Shown as the form most of them typed ('Chat: "Call it"', 'Chat kept saying "Diana"'), cased the way chat
  writes it.

Unchanged: masking and the content check (`server._title_clip` still masks every title, option and caption),
no repeats (`used`), Settings › Titles and captions (A/B, hashtags, chat titles off also turns off viewer titles
and shouts), the hashtags, and the templates, which never claim an outcome.

## 5. Results

Round 1, rated by two Claude subagents that read only `rate.html` (122 items: 38 titles and 84 captions differed;
46 titles were the same and not shown):

| | New wins | Baseline wins | Same | Win rate | Rater A | Rater B | Ships |
|---|---|---|---|---|---|---|---|
| Titles | 47 | 28 | 1 | **62.7%** | 63.2% (24–14) | 62.2% (23–14) | Yes |
| Post captions | 143 | 25 | 0 | **85.1%** | 85.7% (72–12) | 84.5% (71–13) | Yes |

Both passed the first time, so there was no second round. The code that was rated is the code that shipped: the
corpus's titles and captions were made again after the last change (a test-driven fix to the shout filter) and
came out identical.

Before → after, titles (both raters preferred the new one):

| Stream | 4.47.1 | 4.48.0 |
|---|---|---|
| KaiCenat | Three, two, one Cut it! | Kai got smacked |
| HasanAbi | Oh Baking soda baking soda take baking | How tf you make crack? |
| ExtraEmily | I think there's something wrong wait What is this? | Emily runs away from an exploding car |
| ExtraEmily | Facing the ground or facing the sky? | Jacob jumps and twists from the top of the building |
| jasontheween | Did y 'all see that? | TKO |
| jasontheween | This is too funny | Chat: "Call it" |
| jasontheween | I'm wondering about that, she's very calm | Chat: "Next round" |
| KaiCenat | Tobayu, Luis Yanda Luis Yanda | Chat kept saying "Mexico" |
| Jynxzi | On roof, on roof | Chat: "Bad aim" |
| HasanAbi | Football to win elections oh my god | Lets do stuff! |
| tarik | He's already fluent in Korean Holy f*** | Oh my god, he's already fluent in Korean |

Captions:

| 4.47.1 | 4.48.0 |
|---|---|
| This one never gets old · KaiCenat's stream 😂 | "Jesus make this s*** out of me bro!" 😂 Rate this from 1 to 10 (KaiCenat on Twitch) |
| Nobody was ready for this, live on HasanAbi's stream 😂 | Chat: "Baking soda" 😂 Who needs to see this? (HasanAbi on Twitch) |
| Chat could not stop laughing at this one, live on Jynxzi's stream 😂 | "Bro, this isn't Fortnite, ass wipe!" 😂 Who laughed first? (Jynxzi on Twitch) |
| The whole chat lost it · caseoh_'s stream 😂 | Chat: "Pig dawg" 😂 Did you laugh too? (caseoh_ on Twitch) |
| I can't stop replaying this, live on tarik's stream 😂 | "What does that mean, double major?" 😂 Who needs to see this? (tarik on Twitch) |

(Hashtags, the same on both sides, left out.)

### What lost, and what that says

- **Terse viewer titles without context**: "Trader", "AURA", "Scripted?", "No ender pearls", "There's a villager"
  lost to the line said (both raters). The raters see the words and chat, not the video; someone who watched
  may judge these differently, but a one-word viewer title is a weaker pick than a long one.
- **One-word shouts the page's chat sample doesn't show**: 'Chat kept saying "Jacob"', '"Diana"', '"Russel"'
  (in a caption). The shout is counted per chatter across every form ("DIANA", "W DIANA", "go diana"), while the
  page lists the 8 most repeated exact messages, so a rater couldn't see the support. Both raters called these
  "names that appear nowhere in the chat".
- **Fragments still get through** on both sides: "Especially here oh my god I", "Pick somebody who your state".
  Whisper's words on loud streams are the limit; better phrase scoring would help both titles and captions.
- **Captions with a question** won even when the lead was the same mood line; both raters said so. Some of the
  caption margin is the question, not the specific lead.

Next steps, if titles get another pass: require more words of a viewer title when it would beat a fair line,
and rank a shout below a fair line unless its exact form is among chat's top messages.

## 6. Running it again

```
python scripts/title_corpus.py ../title-corpus --vod 2910903624@7200-9600 --vod 2921768812 ...
python scripts/eval_titles.py ../title-corpus --baseline main --out ../eval_v2
# two raters (people, or subagents given only rate.html) save their answers, then:
python scripts/eval_titles.py --score ../eval_v2/key.json ratings_A.json ratings_B.json
```

Twitch removes VODs after a while, so the VODs above may not be there later; any 4–6 public VODs of different
kinds with busy chat will do.
