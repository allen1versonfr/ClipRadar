# Plan: an optional local LLM for titles, quiet gems and content-check context (issue #49)

Status: phase 1 shipped in 4.47.0. Phase 0 ran on 8 Nov 2026 and **failed the ship rule**: neither model's
titles beat the built-in ones (38% and 40% wins against a 60% bar), so phases 2–5 don't go ahead as planned (see
"Phase 0 results" at the end). Written against Clip Radar 4.45.1. Line numbers are from that version and will
drift; the function names are what to search for.

Read [`README.md`](README.md) in this folder first. This plan builds on the automation core from #50
([`issue-50-automation-core.md`](issue-50-automation-core.md)), which should land first, up to its phase 3.

---

## 1. The question this has to answer first

**Will a small local model make titles noticeably better than what Clip Radar writes now?** If not, none of
the rest is worth a 1–2.5 GB download. So phase 0 is an evaluation, and it decides whether phases 2–6
happen.

What the titles look like today: on 40 minutes of a real boxing stream (the README screenshots, NMS BOXING 5)
the six clips were titled "This had chat in tears", "Nobody was ready for this one", "This is too funny",
'Chat: "Nmsboxing"', "The timing on this" and "Chat lost it at this". They're safe and readable, but they're
templates that fit any stream (`src/titles.py`, `VIBES[...]["templates"]`). Chat was saying specific things,
like REF ×63 on clip #1, and the titles don't use any of it.

What the LLM is for, ranked by expected payoff:

1. **Titles and post captions.** It picks from and lightly edits the candidates `titles.py` already makes
   (pick-and-edit), and never writes freely.
2. **Quiet gems** (Deep scan): a second opinion on which transcribed lines actually land.
3. **Content check context**: it may only clear or downgrade a flag the rules raised, never raise one, and
   never touch the serious categories.

What it's **not** for: finding moments. Chat surges, loudness, laughter, Most replayed and viewers' clips
are measured directly. A model would do that worse and slower.

## 2. A step before any LLM: viewers' clip titles as title candidates

Since 4.45, `src/twitchclips.py` downloads the clips viewers made from a Twitch VOD, and their titles. On the
boxing VOD those were "ninja", "AURA", "scripted??", "JASON EATS SHIT" and "TKO". They were written by people
who watched the moment. They're used for ranking only today.

Do this first, as its own small pull request, because it's cheap and it moves the evaluation baseline up:

- `twitchclips.titles_near(d, start, end) -> list[str]`: titles of viewer clips overlapping the clip by at
  least half of the viewer clip, most viewed first. Drop titles that are only the streamer's name, the
  stream's title, or under 3 characters.
- `server._title_clip` (`src/server.py:559`): pass them to `titles.generate(...)` as a new `viewer_titles`
  argument. `generate` puts the top one after a strong quote and before the chat title (`lead = ...`,
  around `titles.py:696`), runs it through `polish()`, and keeps it out of `used` collisions like the others.
- `safety.mask_text` applies as for every title.
- Tests: `tests/test_titles.py` (a viewer title wins over templates, loses to a strong quote, is masked,
  and isn't reused across clips) and `tests/test_twitchclips.py` (overlap rules).

The LLM's evaluation (phase 0) then compares against **this** baseline, not today's.

## 3. Goals and non-goals

**Goals**

- Optional, off by default. Without it, results are exactly as today. With it on, any failure or timeout
  falls back to today's result without a word.
- Fully set up by the app: one download in Settings, with progress, checksums and Remove. Nothing for the
  user to install, configure or keep running.
- Tied to Clip Radar's lifetime. It never runs when Clip Radar is closed, crashes or is ended from Task
  Manager.
- Takes turns with Whisper and renders on the graphics card. It never competes with them for memory (#42).

**Non-goals**

- No cloud models, no API keys.
- No chat or assistant UI.
- No moment finding.
- No free writing: every output is a choice among, or a light edit of, things the app already has.

## 4. Runtime: llama.cpp's `llama-server`, run and owned by Clip Radar

### 4.1 Why llama.cpp (not CTranslate2, not Ollama)

- ggml-org publishes ready Windows builds of llama.cpp for **CPU (AVX2)**, **CUDA (NVIDIA)** and
  **Vulkan (AMD, Intel, NVIDIA)**. No build workflow is needed, unlike `whisper-vulkan.yml`.
- `llama-server` serves HTTP on localhost and supports **JSON-schema-constrained output**, so the model can
  only answer in the shape Clip Radar parses.
- GGUF covers every current small instruct model, with quantisations already made.
- CTranslate2 (already shipped for faster-whisper) supports far fewer recent small instruct models, and
  has no constrained output.
- Ollama would be a second app for the user to install and run, which is ruled out by the goals.

### 4.2 What gets downloaded (pin everything)

Into `paths.DATA_DIR / "llm"` (next to `tools`; `whispercpp.folder()` is the pattern). Each file is fetched
to `<name>.part`, checked against a SHA-256 **pinned in the code**, then renamed (`whispercpp._download`,
`src/whispercpp.py:114`, does exactly this; reuse it, moved to a shared `downloads.py` if both need it).

| Asset | Source | Size | Pick it when |
|---|---|---|---|
| `llama-server.exe` + DLLs, CPU build | llama.cpp GitHub release `llama-<tag>-bin-win-cpu-x64.zip` | ~30 MB | no usable GPU |
| Vulkan build | `llama-<tag>-bin-win-vulkan-x64.zip` | ~40 MB | AMD/Intel card, or NVIDIA without CUDA libs |
| CUDA build | `llama-<tag>-bin-win-cuda-12.x-x64.zip` (+ its `cudart` zip) | ~150 MB + ~400 MB | NVIDIA, **only if** GPU support for Whisper (`gpu.whisper_gpu_ok()`) isn't already providing the CUDA runtime; otherwise prefer Vulkan, which needs no extra 400 MB |
| Model, standard | an Apache-2.0 4B instruct GGUF, Q4_K_M (candidate: Qwen3-4B-Instruct-2507) | ~2.5 GB | ≥ 6 GB VRAM, or ≥ 16 GB RAM on CPU |
| Model, small | an Apache-2.0 ~1.7B instruct GGUF, Q4_K_M (candidate: Qwen3-1.7B) | ~1.1 GB | everything else |

Rules for pinning (do this in phase 2 and record it in the code next to the constants):

- Pin a llama.cpp release tag (`bNNNN`) and the SHA-256 of each zip, the same way `whispercpp.BIN_SHA256` is pinned.
- Pin the model to a Hugging Face **commit**, not a branch (`whispercpp.MODELS_AT` resolves a fixed revision), and its file SHA-256.
- **Licence check before choosing the model**: prefer Apache-2.0 or MIT. Gemma's terms add use
  restrictions; Llama's add attribution and naming rules. Record the licence in `docs/` like
  `src/models/face/LICENSE-ultraface.txt`.
- Re-evaluate the model choice at implementation time: small models improve every few months. The
  evaluation in phase 0 picks the model; this table only gives the starting candidates.
- Unzip only `llama-server.exe` and the DLLs it loads. Verify the zip's SHA, then the extracted
  `llama-server.exe` runs `--version` before the install counts as done.

Disk: refuse to start the download without 1.2 × the total free (`whispercpp._room` is the pattern).

### 4.3 Choosing the build and the model (pure function, unit-tested)

```python
def choose(card: dict, ram_gb: float, cuda_ok: bool, want: str = "auto") -> dict:
    """{"build": "cpu"|"vulkan"|"cuda", "model": "standard"|"small", "gpu_layers": int, "why": str}"""
```

Inputs come from `gpu.card()` (`vendor`, `vram_mb`), `gpu.whisper_gpu_ok()`, and the total RAM. Rules:

- No card or `vram_mb < 3000` → `cpu`, `small` if RAM < 16 GB else `standard`, `gpu_layers = 0`.
- NVIDIA with `cuda_ok` → `cuda`, `standard` if `vram_mb ≥ 6000` else `small`, all layers.
- Else a Vulkan-capable card (`gpu.vulkan_usable()`) → `vulkan`, the same size rule.
- `want` in Settings can force `cpu` or force `small`.

### 4.4 The process: started on demand, adopted, stopped when idle

New module `src/llm.py`. One process at a time, owned by a small state machine:

```
stopped --(first request)--> starting --(GET /health ok)--> ready --(idle IDLE_S)--> stopped
                                  \--(fails/timeout)--> broken --(RETRY_AFTER)--> stopped
```

- **Start**: `subprocess.Popen([exe, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", "4096",
  "-ngl", str(gpu_layers), "--parallel", "1", ...], creationflags=CREATE_NO_WINDOW, stdout/stderr to
  data/llm/server.log)`. The port is a free one picked by binding to port 0 first. Check every flag against
  the pinned release's `--help` in phase 2; flag names change between llama.cpp releases.
- **Tie it to Clip Radar**: `procjob.adopt(p)` straight after `Popen`. That puts it in the job object
  Windows closes when Clip Radar's process ends, however it ends, the same as FFmpeg, yt-dlp and
  TwitchDownloader (`src/procjob.py`). Add it to `tests/test_child_cleanup.py`, which already checks
  this on Windows CI.
- **Ready**: poll `GET /health` every 250 ms for up to 60 s (model load is 2–10 s from SSD, longer
  from HDD). On timeout, `kill_tree`, mark `broken`.
- **Idle stop**: `IDLE_S = 180`. A timer checks the last request time; when idle, terminate the process
  (`p.terminate()`, then `kill_tree` after 5 s). This frees 1.5–3.5 GB. `engine.release_whisper` and
  `WHISPER_IDLE` do the same for Whisper models.
- **Shutdown**: `server.shutdown_all` calls `llm.stop()` first.
- **Broken**: two failures in a row (start, crash, or a request error) turn it off for `RETRY_AFTER = 600` s,
  as `whispercpp` does with `STRIKES`/`RETRY_AFTER`. Everything falls back meanwhile. Settings says why.

### 4.5 Taking turns on the graphics card

When the build is `cuda` or `vulkan`, a request holds **the same lock Whisper holds** while it runs, so
the two never sit on the card at once. That lock is `engine._whisper_lock`; GPU Whisper on AMD/Intel also
serialises on `whispercpp._lock`, so take both. A title request is short (≤ 2 s on a GPU). Whisper waiting
that long is acceptable, and is what #42 asked for.

- Live moments already queue for Whisper through `live_gate` (`PriorityGate`, `src/server.py:2599`), in
  priority order. The LLM step runs inside the same titling job, after Whisper, so it inherits that order.
- Renders on NVENC/AMF/QSV use the card's encoder, not its compute, and are bounded by `engine._hw_slots`.
  Don't gate on renders; measure in phase 6 whether it matters.
- The CPU build takes no lock. It has its own `--threads` limit: half the cores, at least 2, so
  hands-free live recording keeps room.

### 4.6 Talking to it

`llm.ask(prompt: str, schema: dict, max_tokens: int, timeout: float) -> Optional[dict]`

- POST to the server's chat endpoint with the JSON schema as the response format (or `/completion` with
  `json_schema`; use whichever the pinned release supports, and test it in phase 2).
- `temperature 0.3`, `top_p 0.9`, `seed` fixed per clip (its id), so a retitle gives the same answer.
- Returns the parsed object, or `None` on timeout, a non-200, unparsable output, or a schema mismatch.
  The caller **always** has a fallback.
- Logs one line per call: the task, the milliseconds, and the outcome. It never logs the prompt (it holds
  chat messages and usernames).

## 5. Feature 1: smarter titles (pick and edit)

### 5.1 Input (built from what `_title_clip` already has)

From `titles.generate(...)` (`src/titles.py`, around line 652) and `_title_clip` (`src/server.py:559`):

- `candidates`: `info["titles"]` (up to 8: quotes, the chat title, topic and mood templates), plus viewer
  clip titles (section 2), numbered 1..N.
- `quote`, `vibe`, `topic` (`info["quote"]`, `info["vibe"]`, `info["topic"]`).
- `transcript`: the clip's words (`rel` in `_title_clip`), joined, at most 600 characters around the peak.
- `chat`: the 15 most-agreed chat phrases in the clip's window (from `_chat_near`, deduplicated by
  chatter, the way `chattitles.find` counts). Messages only, never usernames.
- `streamer`: `_streamer_name(pr)`. `game`: `engine.game_at(...)`.

### 5.2 Output schema (constrained)

```json
{"type": "object", "additionalProperties": false,
 "required": ["pick", "title", "caption"],
 "properties": {
   "pick": {"type": "integer", "minimum": 0, "maximum": 20},
   "title": {"type": "string", "maxLength": 70},
   "caption": {"type": "string", "maxLength": 180}}}
```

`pick` is the candidate number it started from (0 = none fitted). `title` is that candidate, lightly
edited, or a short new line made **only** from words in the transcript, chat or candidates.

### 5.3 Prompt (fixed, versioned)

Kept in `src/llm_prompts.py` with a `PROMPT_VERSION`. The version goes into the record and into the cache
key, so a prompt change invalidates old answers. Plain instructions, no persona:

> You title short vertical clips from a livestream. Here is what was said, what chat said, and numbered
> title ideas. Pick the idea that best says what happens in this clip, and improve its wording if you
> can. Use only facts from the transcript or chat. Don't invent names, outcomes or numbers. 70 characters
> at most, no hashtags, no emoji, no quotes around it. Also write one short post caption that adds
> something the title doesn't. Answer only as JSON.

Then the inputs as labelled blocks. Keep the whole prompt under ~700 tokens; with a 4B model on a CPU,
prompt processing dominates the time.

### 5.4 Checks on the answer (in code, never trusted to the model)

Reject and fall back if any fails:

1. Not empty, ≤ 70 characters, not all caps (unless the source line was), no URL, no `@`, no hashtag.
2. **No invented proper nouns**: every capitalised word not at the start of the title, and every number,
   must appear (case-insensitively) in the transcript, chat, candidates, streamer name or game.
3. `titles.garbled(title)` is false; `titles.polish(title)` is applied.
4. Not already used by another clip in the project (`used`, as `_title_clip` tracks).
5. `safety.mask_text(title, cfg)` is applied, and `checkflow.check` runs on the final title as it does
   today. If the LLM's title raises a flag the built-in title didn't, keep the built-in one.

### 5.5 Where it lands

- New clip fields: `title_ai` (the accepted title), `title_ai_pick`, `post_ai`, `ai` (`{"v": PROMPT_VERSION,
  "model": "<file>", "ms": int, "outcome": "used"|"kept"|"timeout"|"rejected:<rule>"}`).
- If accepted and the user hasn't set a title (`user_title`/`userTitle`): `d["title"] = title_ai`, the
  built-in title stays first in `d["titles"]` after it, so the **T** shortcut (another title) gets it back
  in one press. `d["post"]` uses `post_ai` + the hashtags (`titles.compose`).
- **Through #50**: a `clip_titled` step named `smarter_titles`, registered **before** `hands_free_render`,
  so a hands-free clip renders with the polished title. Live budget: a 6 s timeout; past it, the built-in
  title is kept and the record says `timeout`.
- **Without #50** (if it hasn't landed): call it at the end of `_title_clip`. That's acceptable for the
  first version, but it's exactly the hand-threading #50 removes.
- Retitle (Review's title idea button): unchanged. It cycles `d["titles"]`, which includes the AI title.

### 5.6 Settings

Settings › Titles and captions › **Smarter titles (local AI)**:
`Off` (default) / `On`, then a status line ("Ready, small model on the CPU" / "Not downloaded" / "Stopped
after errors, tries again in 9 minutes"), Download/Remove, and the size. Stored in `titles.json` next to
`pick`/`hashtags`/`chat_titles` (`server.title_prefs`, `src/server.py:533`) as `"ai": bool`.

## 6. Features 2 and 3

Only if phase 0 shows feature 1 is worth it. Same runtime, same checks philosophy.

### 6.1 Quiet gems, second opinion (`src/gems.py`)

- After `score_sentences`, take the top `min(40, 3 × count)` sentences by the existing score. For each,
  send the sentence, the 2 before it and the 2 after it, with a schema of `{"lands": 0..3, "why": enum["joke",
  "story payoff", "reaction", "quote", "none"]}`. Batch 5 sentences per request to share the prompt.
- New score = existing score + `0.6 × lands`. Then the existing selection runs unchanged. It can only
  re-order or drop what the rules already scored, and never adds a sentence the rules didn't see.
- Budget: Deep scan is minutes already. Cap at 40 sentences (≈ 8 requests, under a minute on a GPU),
  and skip on CPU-only PCs unless the user turns it on explicitly.
- Record `gem["ai"] = {"lands": n, "why": ...}` and show "AI: joke" next to the reason.

### 6.2 Content check context (`src/safety.py`, `src/checkflow.py`)

- **Only** for flags in `violence`, `drugs`, `sexual`, `profanity`, `gambling` (severity ≤ 2). **Never**
  `slurs`, `harassment`, `personal`, `custom` (severity 3). Those stay exactly as the rules say.
- Input: the flagged term, ±12 words around it, the game if known. Output schema:
  `{"context": enum["game or media talk", "quote or joke", "real"], "sure": bool}`.
- If `sure` and not `real`, the flag's status becomes `context_cleared`. It's still listed under the
  clip, with "Cleared by context: game talk", one click restores it, and `checkflow.blocks_render` treats it
  like a dismissed flag. It never changes `hold` decisions for severity 3, so hands-free safety is untouched.
- 4.30.0 needed lists (`SAFE_PHRASES`) to stop flagging "Suicide Squad" and "counter terrorists". This is
  the general version of that list. Keep the list too; it's free and certain.

## 7. Phases

Each phase is one pull request with CI green. A phase may stop the plan (phase 0 especially).

### Phase 0: evaluation (no product code, a script)

`scripts/eval_titles.py`, run by a developer, not shipped:

1. Input: a folder of saved projects (`project.json` + `transcripts.json` from real VODs, opened in
   Clip Radar), at least 5 streams of different kinds (gaming, Just Chatting, IRL/event, a YouTube video)
   and ≥ 60 clips.
2. For each clip: today's title (with section 2 done), and the LLM title from 2 candidate models on the
   developer's PC (llama-server run by hand, the same prompt and checks as section 5).
3. Writes a blind HTML page: for each clip, the transcript excerpt, the top chat phrases, and the two
   titles in random order. A rater picks the better one or "same".
4. **Ship rule**: build phases 2–5 only if the LLM title wins ≥ 60 % of the non-"same" comparisons across
   two raters, and its rejection rate (section 5.4) is ≤ 25 %. Record the result in this file.

Also measure per-clip time on the target hardware (an 8-core CPU without GPU, and a 6–8 GB GPU), and
keep the prompt within ~700 tokens to hit the budgets in section 8.

### Phase 1: viewer clip titles (section 2)

Ship it whatever phase 0 decides.

### Phase 2: the runtime

`src/llm.py`: `choose`, install/remove, start/adopt/health/idle-stop/broken, `ask`. Endpoints mirroring
GPU support (`routes.py`, `/api/gpu*`):

- `GET /api/llm` → `{available, installed, build, model, size_mb, state, why, settings}`
- `POST /api/llm/install` → `{job}` (a job with progress, like `/api/gpu/install`)
- `POST /api/llm/remove`
- `POST /api/llm/settings` → `{ai_titles, ai_gems, ai_context, force}`

Settings UI card (section 5.6) with an "Are you sure?" before downloading (the size), as GPU support asks.

Tests: section 9.

### Phase 3: smarter titles (feature 1)

Section 5, wired as a #50 step. Off by default.

### Phase 4: quiet gems (6.1)

### Phase 5: content check context (6.2)

### Phase 6: measure and tune

- Timing lines in `timing.Timer` for "AI title" per clip; check the live latency (`lat`) isn't worse by
  more than the 6 s budget at the 90th percentile in a replayed session (`tests/live_replay.py`).
- Watch `errors.jsonl` for `llm:` entries from real users before turning anything on by default.

## 8. Budgets (acceptance numbers)

| | GPU (6–8 GB) | CPU only (8 cores) |
|---|---|---|
| First request after idle (model load) | ≤ 6 s | ≤ 12 s |
| One title | ≤ 2 s | ≤ 8 s (small model) |
| Live title timeout | 6 s | 6 s (built-in title kept past it) |
| Memory while loaded | ≤ VRAM − 1 GB | ≤ 3.5 GB RAM |
| Memory after idle | 0 (process gone) | 0 |

If CPU-only titles can't meet 8 s with the small model, the setting says so ("slow on this PC") and stays
off for live sessions there.

## 9. Testing

- **A stand-in server**, `tests/fake_llama.py`, an HTTP server like `tests/fake_studio.py`. It serves
  `/health` and the chat endpoint, with modes `ok`, `slow` (sleeps past the timeout), `garbage` (not JSON),
  `invented` (a title with a name not in the inputs), `flagged` (a title with a slur), `crash` (closes the
  connection), and `hang_on_start` (never healthy).
- `tests/test_llm.py` (CI unit list):
  - `choose` for every card, RAM and CUDA combination
  - every rule in 5.4
  - the fallback for every bad mode
  - idle stop, with an injected clock
  - broken after two failures, and back after `RETRY_AFTER`
  - prompts that never contain usernames
  - the cache key changing with `PROMPT_VERSION`
  - download: checksum mismatch refused, `.part` removed, disk-space refusal; against a stand-in HTTP host, as `tests/test_gpu.py` does for PyPI
- `tests/test_child_cleanup.py` (Windows CI): a `llama-server` stand-in launched through `llm.start` dies
  when the app process is killed.
- `tests/test_titles.py`: with `llm.ask` stubbed, a polished title is used, a rejected one falls back, a
  user title is never replaced, and **T** gets the built-in title back.
- `web/`: the Settings card's states (not downloaded, downloading, ready, broken), the confirm before
  downloading, and the toggle.
- **No real model in CI.** Real-model checks happen in phase 0 and phase 6, by hand, and their results are
  written down in this file.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Titles not actually better | Phase 0 decides before any product work; off by default afterwards |
| Made-up facts | Pick-and-edit, the constrained schema, the proper-noun and number check, fallback |
| GPU contention with Whisper (#42) | Shares Whisper's lock (4.5); CPU build takes no GPU at all |
| Slow on CPU-only PCs | Small model, a tight prompt, a live timeout, "slow on this PC" turns it off for live |
| Leftover process | `procjob.adopt` and idle stop; tested on Windows CI |
| Download size | Asked before downloading; Remove frees it all; never bundled in the installer |
| Licences | Apache-2.0/MIT model; licence file shipped in docs |
| llama.cpp flag or API changes | Pinned release; flags checked against it; stand-in server tests the client side |
| Privacy | Nothing leaves the PC; prompts never logged; no usernames in prompts |

## Phase 0 results

**Verdict: don't build phases 2–5.** Neither model wins 60% of the non-"same" comparisons (small 38%,
standard 40%). Both stay under the 25% rejection limit, so the problem is title quality, not reliability.

### Run of 8 Nov 2026

**Setup**

- PC: AMD Ryzen 5 5600X (6 cores, 12 threads), 15.9 GB RAM, AMD Radeon RX 7600 XT with 16 GB VRAM
  (driver 32.0.32015.7029). This is the "6–8 GB GPU" target in section 8 (with more VRAM). The 8-core
  CPU-only target wasn't measured.
- llama.cpp **b12194** (commit `e9a2a44fe`), `llama-b12194-bin-win-vulkan-x64.zip`, SHA-256
  `1ee4a0776d0edce325ea499e2fcaa3651d031f2970c772c42cbf8f0bfa9fca1a`. Run with `-c 4096 --jinja -ngl 99`.
  - GitHub's "latest" release is now `v0.9.0`, which has release notes but no binaries. The Windows zips
    ship only with the `bNNNN` builds, which are now marked pre-release. Phase 2 has to pin a `bNNNN` tag
    and can't use `releases/latest`.
  - `response_format` with `{"type": "json_schema", ...}` was accepted. `eval_titles.py` needed no change.
- Models (both Apache-2.0):

  | | Repo | File | Revision | SHA-256 |
  |---|---|---|---|---|
  | small | `Qwen/Qwen3-1.7B-GGUF` | `Qwen3-1.7B-Q8_0.gguf` (1.75 GB) | `90862c4b9d2787eaed51d12237eafdfe7c5f6077` | `061b54da…6590cb1a` |
  | standard | `unsloth/Qwen3-4B-Instruct-2507-GGUF` | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2.38 GB) | `a06e946bb6b655725eafa393f4a9745d460374c9` | `3605803b…4c67e597` |

- Inputs: 26 saved projects with 584 clips (470 with words). They cover Twitch IRL (Japan, Korea), Just
  Chatting, QSMP/League, a Fortnite stream, YouTube videos and VOD uploads, and single X clips.
  - NMS BOXING 5 wasn't among them.
  - Most VOD projects no longer had `chat.json`, so most prompts had no chat phrases.
  - Live recordings (`live_*`) have no `project.json` and were skipped.
- Prompt size: median 333 tokens and largest 663 (`usage.prompt_tokens`), within the ~700 budget.

**Time per title** (Vulkan, all layers on the GPU)

| | First request after loading | Median after that | 90th percentile after that | Slowest |
|---|---|---|---|---|
| small | 893 ms | 478 ms | 579 ms | 1,834 ms |
| standard | 1,319 ms | 752 ms | 961 ms | 7,512 ms |

Both are well within the 2 s GPU budget in section 8. The standard model had one 7.5 s outlier, which would
have hit the 6 s live timeout.

**Rejections** (section 5.4, out of 584 clips each)

| | Rejected | `used` | `garbled` | `flagged` |
|---|---|---|---|---|
| small | 113 (19.3%) | 109 | 4 | 0 |
| standard | 99 (17.0%) | 94 | 4 | 1 |

- Almost every rejection is `used`: the model picked a candidate that the built-in titler had already given
  to another clip of the same stream. There were no `invented`, `caps`, `link`, `mention`, `hashtag`,
  `emoji` or `too_long` rejections.
- Most accepted answers were the built-in title itself: 441 for small and 426 for standard, out of 584. The
  script counts those as "same" without showing them. About 75% of the time, the model adds nothing.

**Blind ratings** (89 pairs: 30 small, 59 standard)

| | Wins | Losses | Same | Win rate (non-"same") | Ship rule |
|---|---|---|---|---|---|
| small | 14 | 23 | 23 | **37.8%** | no |
| standard | 37 | 55 | 26 | **40.2%** | no |

- **The two raters weren't people.** They were two separate Claude subagents, run by the owner's choice
  instead of two human raters. Each one saw only what `rate.html` shows (the transcript excerpt, chat and
  the two titles in random order) and never saw `key.json`, `results.json` or the run log. They agreed on
  83 of 89 pairs.
- Both raters preferred the built-in title more often than the model's, for both models. So the result isn't
  close: a human re-rating would have to reverse it, not just tip it over 60%.

**What this means**

- As specified (pick and edit from `titles.py`'s candidates), a local model mostly returns the candidate
  the app would have used anyway. When it changes something, raters prefer the change less than half the
  time. That isn't worth a 1.8–2.4 GB download plus a runtime to maintain.
- Phase 1 (viewers' clip titles) stays. It has already moved the baseline up.
- If this is ever revisited, the inputs are the likely limit, more than the model: most of these projects
  had no chat. A rerun on projects that keep their `chat.json`, with human raters, would be the fair second
  try before any product work.

### What's built (with 4.47.0)

- `src/llm_prompts.py`: the prompt (5.3, `PROMPT_VERSION = 1`), the schema (5.2), the inputs (5.1: numbered
  candidates, a 600-character transcript excerpt centred on the peak, the 15 most-agreed chat phrases with
  @mentions, links and chatters' names taken out) and every check in 5.4 (`check()` returns `used` or
  `rejected:<rule>`, with rules `shape`, `empty`, `too_long`, `caps`, `link`, `mention`, `hashtag`, `emoji`,
  `invented`, `garbled`, `used`, `flagged`). Unit-tested in `tests/test_llm_prompts.py` (CI), no model.
- A maximal prompt (12 candidates of 55 characters, a full transcript excerpt, 15 long chat phrases) is
  2,835 characters, about **709 tokens** by the 4-characters-a-token estimate. Real clips are smaller (the
  smoke test's largest was ~120 tokens for the user part). Count real tokens with `usage.prompt_tokens`,
  which `results.json` records.
- `scripts/eval_titles.py`: titles every clip of a folder of saved projects the way the app does
  (`server._title_clip`, so with viewers' clip titles), asks each `llama-server` given by `--url`, applies
  the checks, and writes `rate.html` (blind A/B, saves each rater's answers as JSON), `key.json` and
  `results.json` (outcome, rejection rule, milliseconds, prompt tokens per clip and model). `--score
  key.json rater1.json rater2.json` gives each model's win rate, rejection rate and the ship rule's verdict.
  Pairs where the model's title is the built-in one are counted as "same" and not shown to raters.
- Checked end to end only against a stand-in server written for the purpose (not committed), on a project
  built from the real viewer clips of VOD 2910903624 and made-up words and chat: the script ran, viewer
  titles reached the candidates ("TKO", "Tbvnks wylin"), invented names were rejected, the page and the
  score worked. Those answers were canned, so they say nothing about title quality.

### Why it wasn't run here (7 Nov 2026, a cloud session)

- llama.cpp: GitHub's release API and release pages were refused by the session's network policy (403).
  Cloning the source worked, but building and running it was refused by the session's permission rules.
- Models: Hugging Face was reachable. `Qwen/Qwen3-1.7B-GGUF` (Apache-2.0, revision
  `90862c4b9d2787eaed51d12237eafdfe7c5f6077`) only has `Qwen3-1.7B-Q8_0.gguf` (~1.8 GB), not a Q4_K_M.
  `Qwen/Qwen3-4B-Instruct-2507-GGUF` doesn't exist under Qwen's account (the API answered 401).
- Inputs: Twitch's clip list for VOD 2910903624 (jasontheween, NMS BOXING 5) was readable: 14 viewer clips,
  from "ninja" (62k views) and "AURA" (27.5k) to "TKO", "tko" and "?". Of their titles, only "?" fails
  `titles_near`'s filters (under 3 characters). The video itself (`usher.ttvnw.net`) was refused, so there was no audio and no
  transcript to build real projects from. The machine had 4 cores and no GPU, so even with a model its
  timings wouldn't have matched either target PC in section 8.

### To run it on a PC

1. Open at least 5 VODs of different kinds in Clip Radar 4.47+ (gaming, Just Chatting, IRL or an event, a
   YouTube video, and NMS BOXING 5), with 60 clips or more between them, and let it listen to the clips
   (Review), so each clip has its words. Close Clip Radar. The projects are under `%APPDATA%\ClipRadar\work`
   (or wherever Settings › Storage points).
2. Download llama.cpp's latest Windows release from https://github.com/ggml-org/llama.cpp/releases
   (`llama-bNNNN-bin-win-cpu-x64.zip`, or `-vulkan-x64` / `-cuda-12.x-x64` for the GPU run). Note the tag.
3. Download two models from Hugging Face: a small one (`Qwen/Qwen3-1.7B-GGUF`, `Qwen3-1.7B-Q8_0.gguf`, or a
   Q4_K_M quantisation from a well-known publisher) and a ~4B instruct one (for example a Q4_K_M of
   `Qwen/Qwen3-4B-Instruct-2507`). Check each licence (Apache-2.0 or MIT).
4. Start one server per model:
   `llama-server -m Qwen3-1.7B-Q8_0.gguf --port 8081 -c 4096 --jinja` and the same with the 4B on 8082.
   Add `-ngl 99` for the GPU run. `--jinja` (the default in recent releases) makes the server honour
   `chat_template_kwargs`, which turns Qwen3's thinking off.
5. `python scripts/eval_titles.py "%APPDATA%\ClipRadar\work" --url small=http://127.0.0.1:8081 --url
   std=http://127.0.0.1:8082 --out eval_out`. If the server rejects `response_format` with `json_schema`,
   note the release tag and the error here; older releases want `{"type": "json_object", "schema": ...}`.
6. Two people rate `eval_out/rate.html` separately and save their answers. Then
   `python scripts/eval_titles.py --score eval_out/key.json ratings_a.json ratings_b.json`.
7. Write here: the llama.cpp tag, each model's file and revision, the PC (CPU, GPU, RAM), the median and
   90th-percentile milliseconds per title (first request after loading separately), the rejection rate and
   its rules, and each model's win rate. Phases 2–5 go ahead only if a model wins at least 60% of the
   non-"same" comparisons and has at most 25% rejected.
