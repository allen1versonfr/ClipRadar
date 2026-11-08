---
name: speed-audit
description: Run Clip Radar's speed and efficiency audit on this PC (the owner's Windows machine), with real VODs, and write up the results. Use when asked to "run the speed audit", "do the efficiency audit", "do your thing" about speed or performance, or to measure where Clip Radar spends time on this machine.
---

# Speed audit on this PC

The goal is phase 1 of `docs/plans/efficiency-audit.md`: real numbers from the owner's own PC (Ryzen 5 5600X,
RX 7600 XT, Windows) against the budgets in the plan's §1, and a ranked list of what to fix. The cloud audit
(`docs/audits/speed-audit-2026-11-08.md`) covers what could be measured without this PC. Read both first; don't
redo what they already settled.

The owner wants this to run without being asked questions. Make sensible choices, say what you chose in the write-up,
and only stop to ask if something truly blocks the run.

## 1. Set up (from the repository root)

1. **Python 3.12 environment.** Use `.venv` if it exists, otherwise create it with `py -3.12 -m venv .venv`. Then:

   ```
   .venv\Scripts\python -m pip install -r requirements.txt psutil py-spy onnxruntime
   ```

2. **FFmpeg.** Running from source, the app looks in the repo's `tools\` folder (gitignored), then Clip Radar's data
   folder, then PATH. Check with:

   ```
   .venv\Scripts\python -c "import sys; sys.path.insert(0, 'src'); import engine; print(engine.which('ffmpeg'), engine.which('ffprobe'))"
   ```

   If either is missing, copy `ffmpeg.exe`, `ffprobe.exe` and the FFmpeg DLLs (`av*.dll`, `sw*.dll`, `postproc*.dll`)
   from the installed app's tools folder (`%LOCALAPPDATA%\Programs\Clip Radar\`, look for the folder holding
   `ffmpeg.exe`) into `tools\`. Use the installed app's FFmpeg, not another build: it's the pinned one the app ships.

3. **The laughter detector** (`src\models\audio_events.onnx`, gitignored, about 20 MB). Without it the sound pass
   skips laughter, screams and cheering, and runs faster than it does for the owner. If it's missing:
   - find it in the installed app: `Get-ChildItem "$env:LOCALAPPDATA\Programs\Clip Radar" -Recurse -Filter audio_events.onnx`;
   - otherwise download it from the repository's `models-v1` pre-release;
   - copy it into `src\models\`.

   The face model is already in the repository.

## 2. Find the inputs

Use videos already on this PC. Don't download anything unless there's nothing usable.

- Clip Radar's projects: `%USERPROFILE%\Videos\Clip Radar\projects` by default. Settings › Storage can move it; the
  current place is in the JSON settings in `%LOCALAPPDATA%\ClipRadar`. Each project folder has its video (`vod.mp4`
  or `vod.*`) and, for Twitch, `chat.json`.
- Pick up to three, matching the plan's inputs as closely as you can:
  - **A:** the longest Twitch VOD that has `chat.json`, ideally 3 hours or more;
  - **B:** a YouTube video of about 20 minutes;
  - **C:** a long YouTube video or a second long VOD.
- Use the files where they are; the script never writes next to them.

## 3. Close other heavy programs, then run

Check nothing heavy is running (a game, OBS, a render in the Clip Radar app): Task Manager, or
`Get-Process | Sort-Object CPU -Descending | Select -First 10`. If something is, say so in the write-up rather than
waiting for it.

```
.venv\Scripts\python scripts\audit_speed.py --input A.mp4 --chat A\chat.json --input B.mp4 --chat "" --input C.mp4 --chat "" --model small --clips 3 --runs 3 --pyspy --out audit_runs\small
```

Then one run of A with the larger model, to see what it costs:

```
.venv\Scripts\python scripts\audit_speed.py --input A.mp4 --chat A\chat.json --model large-v3 --clips 3 --out audit_runs\large-v3
```

- Expect about an hour in all for a 3-hour VOD. Run it in the background and check on it; don't sit in a sleep loop.
- If a run stops early, read its `app.log` in the run folder, fix the cause if it's the setup (a missing tool, a
  model), and run again. If it's a bug in the app, note it and carry on with the other inputs.
- `audit_runs\` is scratch output. Don't commit it.

## 4. Real-world numbers already on this PC

The app writes a `Timing, ...` line for every opened video, title job and render, including hands-free live moments.
Collect the ones from real use:

```
Select-String -Path "$env:LOCALAPPDATA\ClipRadar\clipradar*.log" -Pattern "Timing,"
```

Live moments are the only real-world source for the live chain (the plan's §3.4); the script doesn't run a live session.
Summarise them: medians per step, and the slowest few.

## 5. Write it up

Create `docs/audits/efficiency-audit-<YYYY-MM-DD>.md`, following the style of the other audits in that folder
(plain words, tables, numbers with units, what was measured and what wasn't). It must have:

1. **The machine and inputs:** CPU, GPU and driver, RAM, Windows version, and each input's length and source.
2. **The budget table from the plan's §1**, with a measured column and pass or fail for each row. Use medians of the
   three runs. Mark any row that couldn't be measured, and why.
3. **Where the time goes per step**, from `report.md` and the app's Timing lines.
4. **Findings, worst first.** Each finding needs:
   - the evidence: numbers, the launch log, the py-spy flame graph;
   - the code responsible (`file:line`);
   - the fix and how sure you are of it.

   Check the plan's §3 suspects explicitly, especially:
   - Whisper's share of titles, `small` against `large-v3`;
   - repeated decodes per clip;
   - AMF encode time, and whether the 3D or encode engine was the limit;
   - the "time not inside Whisper" per title;
   - idle CPU and wake-ups.
5. **A ranked fix list:** time saved per week of the owner's real use, then risk.

Copy the run's `report.md` files into an appendix at the end of the write-up.

## 6. Finish

- Create a branch, `audit/efficiency-<date>`, commit only the write-up, push it, and open a pull request that
  references issue #47 and summarises the top five findings.
- Don't change app code in this session. The fixes come next, each as its own PR with before and after numbers from
  this same script (the plan's §5 rules). Offer to start on the top one.
