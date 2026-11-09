---
name: speed-audit
description: Run Clip Radar's speed and efficiency audit on this PC (the owner's Windows machine), with real VODs, write up the results and open a PR. Use when asked to "run the speed audit", "do the efficiency audit", "do your thing" about speed or performance, or to measure where Clip Radar spends time on this machine.
---

# Speed audit on this PC

The goal is phase 1 of `docs/plans/efficiency-audit.md`. That means:

- real numbers from the owner's own PC (Windows, Ryzen 5 5600X, RX 7600 XT) against the budgets in the plan's §1;
- a ranked list of what to fix.

The cloud audit (`docs/audits/speed-audit-2026-11-08.md`) already covers what could be measured without this PC. Read
both before you start, and don't redo what they already settled.

**The owner wants this done start to finish without being asked anything.**
- Make sensible choices and say what you chose in the write-up.
- Stop only if something truly blocks the run, and if you can work around it, do that and note it instead.
- Don't change app code in this session.

## 1. Up to date

From the repository root, on `main`:

```
git checkout main
git pull
```

If the working tree has the owner's own uncommitted changes, leave them alone. Stash only if the pull needs it, and
pop afterwards.

## 2. Run it

`scripts\speed_audit.bat` does everything:

- Python 3.12 and the packages (in `.venv`);
- FFmpeg and the laughter detector, copied from the installed Clip Radar or downloaded;
- picking videos from the owner's Clip Radar projects: a long Twitch VOD with chat, a video of about 20 minutes, and a
  long video;
- three measured runs of each with Whisper `small`, and one run with `large-v3`;
- the timing lines the app already wrote in real use.

Run it in the background, because it takes about an hour or more:

```
scripts\speed_audit.bat /nopause
```

- Check its output every several minutes; don't sit in a sleep loop.
- The output says which step it's on.
- The results go to `audit_runs\<date>\`. Start with `SUMMARY.md` there.

**If it stops:**
- Read the message and the run's `app.log`.
- If it's the setup (a missing tool or package, a network hiccup), fix it and run again.
- If it's a bug in the app, note it as a finding (with the log lines) and run the rest with
  `--input` and `--chat` for the inputs that still work. `scripts\audit_speed.py --help` lists the options.
- If the owner's projects have no downloaded videos, the script falls back to a made-up test stream. That's much less
  useful. Say so plainly in the write-up, and suggest which kinds of video to download.

**If `SUMMARY.md` lists busy programs** (a game, OBS, the Clip Radar app), the numbers may be skewed. Say so in the
write-up rather than waiting.

## 3. Read the results

In `audit_runs\<date>\`:

- **`SUMMARY.md`:** the machine, what was set up, which videos were picked, what else was busy.
- **`whisper_small\report.md`:** for each input and run:
  - each step's time;
  - the app's own timing per step;
  - FFmpeg and Whisper launches per step, and how many of them read the source video;
  - CPU, RAM, GPU engine load and VRAM per step;
  - the slowest launches.
- **`whisper_large-v3\report.md`:** the same for one run with the bigger model.
- **`real_timings.md`:** medians and the slowest cases from the owner's real use, including hands-free live moments.
  This is the only real-world source for the live chain (the plan's §3.4).
- **Each run folder:**
  - `launches.csv`: every FFmpeg and Whisper run, with its step, seconds and the stretch of video it read;
  - `samples.csv`: CPU and memory once a second;
  - `gpu.csv`: the graphics card's 3D, compute, decode and encode load and its memory, once a second;
  - `profile.txt`: py-spy's collapsed stacks for the first run of each input, one stack per line with its sample
    count, so you can sort and grep where the Python side spent its time;
  - `app.log`.

## 4. Write it up

Create `docs/audits/efficiency-audit-<YYYY-MM-DD>.md`, in the style of the other audits in that folder (plain words,
tables, numbers with units, what was measured and what wasn't). It must have:

1. **The machine and inputs:**
   - CPU, GPU and driver, RAM, Windows version;
   - each video's length, source, and whether it has chat;
   - anything that was busy.
2. **The budget table from the plan's §1**, with a measured column and pass or fail for each row:
   - use medians of the three runs;
   - mark rows that couldn't be measured, and why (for example, live latency comes only from `real_timings.md`, if
     it has live moments).
3. **Where the time goes per step**, from the reports and the app's own timings.
4. **Findings, worst first.** Each finding needs:
   - the evidence: numbers, `launches.csv`, `profile.txt`;
   - the code responsible, as `file:line` (read the code; don't guess);
   - the fix, and how sure you are of it.

   Check each of the plan's §3 suspects and say what the numbers showed for it, especially:
   - Whisper's share of titles, and `small` against `large-v3`;
   - the time per title not inside Whisper;
   - how many times each clip's stretch of the source is decoded;
   - AMF encode time, and whether the GPU's 3D, encode or compute engine was the limit;
   - face finding's share of a render;
   - the sound pass and the laughter detector's share of opening;
   - idle CPU and launches.
5. **A ranked fix list:** time saved per week of the owner's real use first, then risk.
6. **An appendix** with `SUMMARY.md` and the `report.md` files copied in. Leave out the CSVs and the flame graph:
   `audit_runs\` is gitignored, and they're too big.

## 5. Finish

1. Create a branch, `audit/efficiency-<date>`, commit only the write-up, and push it.
2. Open a pull request into `main` that:
   - references issue #47;
   - summarises the pass/fail table and the top five findings.
3. Tell the owner, in a few lines:
   - the three biggest findings;
   - the PR link;
   - that the next step is fixing the top finding, as its own PR with before and after numbers from the same script
     (the plan's §5 rules).

   Offer to start on it.
