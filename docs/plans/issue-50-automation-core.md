# Plan: one core for the automated features (issue #50)

Status: done (phases 1–6 merged; behaviour change shipped in 4.46.0). Written against Clip Radar 4.45.1
(`main` at the README screenshots merge). Line numbers are from that version and will drift; the function
names are what to search for. See [What was built](#what-was-built) at the end for where the work differed.

Read [`README.md`](README.md) in this folder first for how this plan and the one for #49 fit together.

---

## 1. Why

Everything Clip Radar does on its own is threaded by hand through the place it fires. There's no one
place that says "this happened, so do that, if it's allowed". Each automation keeps its own on/off
switch in a different file, starts its own thread, logs its own way, and marks its own state on the
clip or queue item.

That has three costs today:

1. **New automations are risky to add.** Auto-posting after a render, or the local LLM's titles
   (#49), would each need another hand-threaded callback in `server.py` (about 3,000 lines).
2. **The safety gate isn't shared.** The content check that holds a risky clip back is written inside
   the hands-free code path (`live_on_moment`'s `done()` callback). Discord sending after a render
   doesn't pass through it at all. Any new outward step has to remember to call it.
3. **There's no record of what automation did.** When a hands-free clip didn't render, the only trace
   is a `held` string on the clip and a line in `clipradar.log`. Bug reports like #42 had to be
   reconstructed from logs.

This is **not a speed-up**. The glue is function calls and a few threads. The time goes on downloads,
FFmpeg, Whisper and rendering, which #47 measures.

## 2. What exists today (the chain to move)

### 2.1 Hands-free live: moment found → titled → checked → queued

| Step | Where | What it does |
|---|---|---|
| Moment found | `live.LiveSession._...` calls `self.on_moment(self, {...})` (`src/live.py`, around line 1161) | Hands the cut moment (video, chat, analysis, clip, offset, `aired`, `lat`, `manual`) to the server |
| Registered | `server.live_on_moment` (`src/server.py:2656`) | Makes a one-clip project `projects[mid]`, runs `clip_dicts`, re-grades every moment of the session (`sess.grades`) |
| Titled | `server.start_listening(mid, [id], model, lang, on_done=done)` (`src/server.py:1209`) | Background job. Waits on `live_gate` (a `PriorityGate`, `src/server.py:2599`), runs Whisper (`ensure_words`), refines the cut (`engine.refine_with_words`), titles it (`_title_clip`, `src/server.py:559`), sets `listened` |
| Checked | inside `_title_clip` → `checkflow.check(d, cfg, has_audio)` when `cfg["auto"]` | Stores `d["safety"]` |
| Held or queued | `done()` closure inside `live_on_moment` | If `sess.extra["auto_render"]`: holds when `safety.load_config()` says `enabled` + `hold` and `d["safety"]["level"] == "high"`, else when `checkflow.blocks_render(d, cfg)` returns a reason; otherwise `queue_add(mid, c, sess.extra["opts"])` with `it["prio"]`, `it["auto"] = True`, `it["lat"]`, `it["aired"]`, and sets `d["auto_queued"]` |
| Latency | `done()` and `_live_rendered` (`src/server.py:2641`) | `sess.note_latency("title" / "render" / "total", ...)`, `d["lat"]`, `sess.last_lat` |

### 2.2 After a render

In `queue_worker` (`src/server.py:2445`), on success:

1. `play_sound("ready", it)` (`src/server.py:1586`)
2. `auto_discord(it)` (`src/server.py:2020`): its own thread, if the clip's campaign has `discord.auto` and a webhook
3. `_after_render(it)` (`src/server.py:2369`): `run_cleanup()` when `cleanup.load()["vods"]`
4. `_live_rendered(it)`: hands-free latency

### 2.3 Elsewhere

| Automation | Where | Switch |
|---|---|---|
| Keep watching | `_watch_for_next(sess)` (`src/server.py:2763`), a thread polling `sess.status` every second | `sess.extra["keep_watching"]` |
| Skip deletes a hands-free render | `record_feedback` → `cleanup.skipped_render(it)` (`src/server.py:1381`, `src/cleanup.py:169`) | `cleanup.load()["skipped"]` |
| Cleanup sweeps | `run_cleanup` (`src/server.py:2375`), on start and after renders | `cleanup.load()` (`temp`, `vods`, `skipped`) |
| Upload to TikTok | `routes.py` `/api/handoff` with `auto` → `studio_auto.upload` (user-pressed, not automatic today) | `studio_auto.load_settings()["press_post"]` |
| Updates | `updater` (user-pressed install) | Settings › Updates |

### 2.4 State flags this produces (all must keep working: the UI and saved files read them)

- On a clip dict `d`: `held` (string), `auto_queued` (bool), `lat` (dict), `listened`, `safety`, `state`.
- On a queue item `it`: `auto` (bool), `prio`, `lat`, `aired`, `discord` (`{at, attached, note, post_url}` or `{at, error}`), `handoff`.
- On a session: `sess.extra = {auto_render, opts, model, language, keep_watching, params, user_stopped, next}`.

The web UI reads `held`, `auto_queued` and `lat` (`web/src/features/review/live.ts`, the Running now
panel), and `discord` and `handoff` (`web/src/features/exports/postStatus.ts`). Their names and shapes
must not change in this work.

## 3. Goals and non-goals

**Goals**

1. One module, `src/autopilot.py`, that owns the automated chain: a few named events, the steps that
   listen to them, one shared outward gate, and one record of what happened.
2. Exactly today's behaviour after the move, proven by tests written **before** the move (section 6).
3. New automations, starting with #49's titles, plug in as a step instead of a hand-threaded callback.
4. A per-clip record a user can see and a bug report can include: "Moment 4: titled 3.1 s, held by the
   content check (slur), not rendered".

**Non-goals**

- No general plugin or rules engine, no user-editable rules, no config language.
- No change to how moments are found, titled, rendered or checked: only where the glue lives.
- No new threads model: the existing job threads and `queue_worker`s stay.
- Not the heavy-work scheduler. GPU turn-taking stays in `live_gate` and `engine._hw_slots`.

## 4. Design

### 4.1 Events

A small, closed set. Each is a plain function call into `autopilot`, made from the one place the thing
happens. No strings typed by hand at call sites: functions with arguments.

| Event | Fired from | Payload |
|---|---|---|
| `moment_found(sess, pid, d)` | end of `live_on_moment`, replacing the `start_listening(..., on_done=done)` call | session, the moment's project id, its clip dict |
| `clip_titled(pid, d)` | `start_listening`'s loop, after `_title_clip` and `d["listened"] = True` | project id, clip dict (words, title, safety on it) |
| `render_done(it)` | `queue_worker` success branch, replacing `play_sound`/`auto_discord`/`_after_render`/`_live_rendered` | the queue item |
| `render_failed(it, kind)` | `queue_worker` error branch | queue item, error kind |
| `clip_skipped(pid, cid, it_or_none)` | `record_feedback` when `action == "skip"` | ids, the clip's queue item if any |
| `session_ended(sess)` | `LiveSession` status change to `ended`/`stopped`/`error` (replaces `_watch_for_next`'s polling). `status` is assigned in about ten places in `src/live.py` (lines 243–635): add `LiveSession._set_status(s)` and route every assignment through it, so the event fires exactly once per end | session |
| `app_started()` | `server.start` (`src/server.py`, around line 2926), where `run_cleanup()` is called on start today | none |

`clip_titled` replaces the `on_done` callback for **every** titling, not just live, so #49 has one hook
for "a clip has words and a first title".

### 4.2 Steps

A step is a small function registered against one event, with a name, a switch, and whether it sends
anything off the PC.

```python
@dataclass(frozen=True)
class Step:
    name: str                       # "hands_free_render", "discord", "cleanup_vods", ...
    on: str                         # the event name
    enabled: Callable[[Ctx], bool]  # reads the switch where it lives today
    outward: bool                   # True: passes the gate (4.3) first
    run: Callable[[Ctx], Outcome]
    background: bool = False        # True: runs in a worker thread (Discord, cleanup), else inline
```

The switches **stay where they are** (`sess.extra`, campaign `discord.auto`, `cleanup.load()`,
`safety.load_config()`, `studio_auto.load_settings()`). `enabled` reads them. Moving the settings is a
UI and file-format change with no payoff; this plan doesn't do it.

Steps in this work, in event order:

| Event | Step | Today's code it replaces | outward |
|---|---|---|---|
| `moment_found` | `title_moment` | the `start_listening(mid, ...)` call | no |
| `clip_titled` | `hands_free_render` | the `done()` closure (hold rules, `blocks_render`, `queue_add`, `prio`, `auto`, `lat`, `auto_queued`) | no (the render stays on the PC) |
| `clip_titled` | `latency_title` | the latency part of `done()` | no |
| `render_done` | `ready_sound` | `play_sound("ready", it)` | no |
| `render_done` | `discord` | `auto_discord(it)` | **yes** |
| `render_done` | `cleanup_vods` | `_after_render(it)` | no |
| `render_done` | `latency_render` | `_live_rendered(it)` | no |
| `clip_skipped` | `delete_skipped_render` | `cleanup.skipped_render(it)` in `record_feedback` | no |
| `session_ended` | `keep_watching` | `_watch_for_next` thread | no |
| `app_started` | `cleanup_start` | the start-up `run_cleanup` call | no |

Order within one event is the registration order, which is the order of the table. That's the order the
code runs in today.

### 4.3 The outward gate

One function every outward step passes before it runs:

```python
def gate(ctx: Ctx) -> Optional[str]:
    """Why this clip may not leave the PC now (shown and recorded), or None."""
```

It applies, in order:

1. `safety.load_config()`: `enabled` and `hold` and the clip's `safety.level == "high"` → "Held: <label> (<text>)".
   This is the exact rule now inside `done()`, with the same message format.
2. `checkflow.blocks_render(d, cfg)` → its reason string.
3. The clip's campaign rules (`campaigns.length_warning`, required caption text) → a reason when they
   aren't met. **Today Discord sends regardless.** That's a behaviour change, so it goes in phase 4, behind
   its own test and changelog line, not in the pure move.

Phase 2 keeps `hands_free_render` calling the same two checks it calls today. They're not "outward",
because rendering stays on the PC, but they're the same rules. So it calls `gate()` too and records
the reason exactly as `d["held"]`. That keeps one implementation of the hold rule.

### 4.4 The record (what automation did)

Per clip, appended as steps run, kept on the clip dict as `d["auto_log"]` (and on the queue item as
`it["auto_log"]` for render-side steps):

```json
{"t": 1762390000.1, "event": "clip_titled", "step": "hands_free_render",
 "outcome": "held", "why": "Held for review: Hate slur (…)", "ms": 2}
```

- `outcome`: `ran`, `skipped` (switch off), `held` (gate said no), `failed` (exception, message in `why`).
- At most 30 entries per clip; oldest dropped.
- Saved with the project (`persist.save_project` already writes the clip dicts) and the queue
  (`save_queue` writes items). Old files without it load as before.
- `errorlog.record("autopilot:<step>", ...)` on `failed`, with the clip title and the step, as
  render failures do now.
- One line to `clipradar.log` per outcome other than `ran`/`skipped`.

API: `GET /api/autopilot/<pid>/<cid>` returns the clip's and its queue item's records, merged by time.
UI (phase 5): a small "What happened" list under the clip in Review (live moments) and in the Exports
panel. Text only, no new layout.

### 4.5 Threads and failure isolation

- `emit(event, ...)` runs inline steps in the caller's thread, in order. A step that raises is recorded
  `failed` and the next step still runs. **Nothing an automation step does may break the render loop
  or the titling job**, which is the case today only by accident (each callback has its own `try`).
- `background=True` steps run in one shared `ThreadPoolExecutor(max_workers=2, thread_name_prefix="autopilot")`,
  created lazily. Discord and cleanup are the background ones, as today: they each start a thread now.
- `emit` never holds `queue_lock`. Steps that touch the queue take it themselves, as `queue_add` does.
- On shutdown (`server.SHUTTING_DOWN[0]`), `emit` records `skipped` with "shutting down" and runs nothing.

### 4.6 Module layout

```
src/autopilot.py      events (emit), Step, the registry, gate(), the record, the executor
src/autopilot_steps.py the steps in 4.2, each a small function calling the existing code
```

`autopilot.py` imports nothing from `server`. `autopilot_steps.py` imports `server` lazily inside each
step (the same pattern as `import engine` inside functions across the codebase), so there's no import
cycle. `server.py` calls `autopilot.emit_*` and registers nothing itself. `autopilot_steps.register()`
runs once from `server.start` and from the test harness.

## 5. Phases

Each phase is one pull request, green on the full CI, released or not as the maintainer decides. The
order matters: the tests come first so the move can't change behaviour silently.

### Phase 1: pin today's behaviour (tests only, no product code)

New `tests/test_autopilot_pin.py`, in the CI unit-test list. It drives the real `server` module in
process, the way `tests/test_live.py` and `tests/live_replay.py` already do, with Whisper stubbed as
`scripts/readme_shots.py` stubs it (`server.start_listening` replaced, or `ensure_words` returning
fixed words).

Cases. Each one asserts the flags in 2.4 **and** the order of side effects (a list the stubs append to):

1. Hands-free on, clean clip → queued once, `it["auto"]`, `it["prio"] == quality` (1000 for manual),
   `d["auto_queued"]`, `d["lat"]["title"]` set; render done → sound, Discord (campaign auto), cleanup, latency, in that order.
2. Hands-free on, high-risk flag, `hold` on → `d["held"] == "Held for review: <label> (<text>)"`, nothing queued, a session log line.
3. Hands-free on, `hold` off, high-risk flag → queued.
4. Hands-free on, `export_flagged == "block"`, unreviewed flags → `d["held"] == "Held for review: flags not reviewed yet"`, not queued.
5. Hands-free off → titled, not queued, `lat["total"]` set at title time.
6. Manual (hotkey) moment → `prio == 1000`.
7. Render fails → no Discord, no cleanup, latency untouched.
8. Campaign `discord.auto` off → no send; webhook missing → no send.
9. Skip a hands-free render with `cleanup.skipped` on → file removed; off → kept; a clip you rendered yourself (`auto` false) → kept.
10. Keep watching: a session ending on its own starts the next one with the same params; `user_stopped` → doesn't.
11. Shutting down mid-render → no Discord, no cleanup.

The e2e test (`tests/e2e.py`, "content check holds a risky clip back", "auto-render renders moments")
already covers 1 and 2 end to end on Windows. Keep it as is.

**Done when** the new test passes against today's code, unchanged.

### Phase 2: the core, and the hands-free chain moved onto it

1. Add `src/autopilot.py` (events, `Step`, registry, `emit`, `gate`, the record, the executor).
2. Add `src/autopilot_steps.py` with `title_moment`, `hands_free_render`, `latency_title`.
3. `live_on_moment`: replace the `done()` closure and the `start_listening(..., on_done=done)` call with
   `autopilot.moment_found(sess, mid, d)`. `title_moment` calls `start_listening(mid, [d["id"]], model, lang)`
   **without** `on_done`.
4. `start_listening`: after `_title_clip` + `listened`, call `autopilot.clip_titled(pid, d)` for every
   clip. Keep the `on_done` parameter for one release. Only `live_on_moment` passes it today; the Review
   route (`routes.py`, `/api/listen`, around line 1119) doesn't. Delete it in phase 6.
5. `hands_free_render` reproduces `done()` exactly (same messages, same flags, same `queue_add` arguments).

**Done when** phase 1's test passes unchanged, the e2e passes on Windows, and `server.py` no longer
contains the hold rule.

### Phase 3: after-render steps

Move `play_sound("ready")`, `auto_discord`, `_after_render`, `_live_rendered` out of `queue_worker`'s
success branch into `render_done` steps. Add `render_failed` (no steps yet; it's for #49's and later
use, and the record). `queue_worker` keeps its retry and error classification exactly.

**Done when** phase 1 passes, and `queue_worker`'s success branch is `set_stage(...)`, `_attempt_end(...)`,
`save_queue_soon(0)`, `autopilot.render_done(it)`.

### Phase 4: the gate for everything outward, and the record shown

1. `discord` step marked `outward=True`: it now passes `gate()`. That's a **behaviour change**: a clip
   held by the content check, or one breaking its campaign's length rule, is no longer sent to Discord
   automatically. Its record says why. Changelog line under **Changed**, with a test.
2. `GET /api/autopilot/<pid>/<cid>`; `docs/api-routes.md` regenerated (`python scripts/api_routes.py`).
3. Review (live moment) and Exports show the record as a short list. Tests in `web/`.

### Phase 5: cleanup, skip and Keep watching

Move `clip_skipped`, `session_ended` (removing `_watch_for_next`'s polling thread: `LiveSession` calls
`autopilot.session_ended(self)` from where it sets `status`), and `app_started`.

### Phase 6: tidy

- Remove `start_listening`'s `on_done` parameter.
- `docs/api-routes.md` and the README's "Project layout" list `autopilot.py`.
- `server.py` should be about 150 lines shorter. Don't chase a number.

## 6. Testing strategy (all phases)

- **Pin first** (phase 1), then never edit the pin test's expectations in a "move" phase. If it needs
  changing, the phase isn't a pure move.
- **Unit**: `tests/test_autopilot.py` for the core alone: ordering, a failing step doesn't stop the
  next, background steps run off-thread, the record caps at 30, shutdown skips, the gate's reasons.
- **No sleeps in tests**: inject the executor (`autopilot.EXECUTOR = InlineExecutor()` in tests) so
  background steps run inline and deterministically.
- **CI**: add both new files to `.github/workflows/build.yml`'s unit list. The e2e and the Windows
  build run as they do.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Hands-free is latency-sensitive (`lat` is measured and shown) | `emit` is plain function calls; inline steps; phase 1 asserts the latency fields are set where they are today |
| A behaviour change hides inside a "move" | Phase 1 pins behaviour; only phase 4 changes behaviour, and says so |
| Import cycles (`server` ↔ steps) | Steps import `server` lazily; `autopilot.py` never imports `server` |
| Saved projects/queues from older versions | New fields only added (`auto_log`); nothing renamed or removed |
| Over-abstraction | Seven events, one dataclass, one gate. Anything more general is out of scope |

## 8. What #49 gets from this

`clip_titled` is the hook for "Smarter titles": a step registered after the built-in title is set and
before `hands_free_render`, so a hands-free clip renders with the polished title. Its outcome (polished,
kept, timed out) lands in the same record. See [`issue-49-local-llm.md`](issue-49-local-llm.md),
section 7.

## What was built

- **Phase 1 (#73):** as planned, 11 cases. Keep watching was pinned by calling `_watch_for_next` directly; the start-up cleanup wasn't pinned.
- **Phase 2 (#74):** steps register on `server` import, not in `server.start`; `latency_title` runs before `hands_free_render`; `moment_found` takes `aired`; `gate()` returns `Hold(rule, why)`; `Step` gained `concerns(ctx)`.
- **Phase 3 (#75):** `discord` stayed inline and not outward until phase 4; a step that raises after a render no longer marks the item failed, and shutdown skips the steps.
- **Phase 4 (#76, 4.46.0):** required caption text isn't gated (`apply_post` always adds it); the Discord switch moved into the step's `enabled()`; the API returns 404 when neither clip nor item exists; `test_campaigns` updated for the intended change.
- **Phase 5 (#77):** `session_ended` comes through a `LiveSession` `on_end` callback once the recording thread finishes; pin case 10 calls `autopilot.session_ended` instead of the removed `_watch_for_next` (expectations unchanged).
- **Phase 6 (#78):** `start_listening`'s `on_done` removed; grep found no other leftovers. README lists the two modules; `docs/api-routes.md` was already current.
