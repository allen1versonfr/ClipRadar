"""
Listening: what's said in a project's video, heard with Whisper and kept (moved from server.py, hardening phase 5b;
no changes). Each stretch of the VOD is transcribed once and the words reused for titles, the caption preview and
the render; the deep scan hears the whole VOD and finds quiet moments worth a clip (gems).

  ensure_words(pr, a, b, model, lang, …)   the words of a stretch, heard now or from the transcript cache
  start_listening(pid, …)                  a job that hears the clips (titles, captions)
  start_deep_scan(pid, …), deep_status(pr) the whole VOD heard, and the gems in it

Everything shared (projects, jobs, the job helpers…) stays in server.py and is read here as S.<name> at call time
(server.py's docstring says why). A test that swaps a name defined here swaps listening.<name>.
"""

from __future__ import annotations

import sys
if "server" not in sys.modules:
    # imported before server: server imports this module itself (at its end), so server is imported first
    # and this copy steps aside (the import then gives back the copy server loaded in full)
    del sys.modules[__name__]
    import server  # noqa: E402,F401
import json
import os
import time
from typing import Optional
import engine
import analysis
import whisper
import ytextra
import exports
import gems
import persist
import timing

import autopilot  # noqa: E402
import server as S  # noqa: E402  shared state and helpers, read as S.<name> at call time
import live_glue  # noqa: E402


# ---- deep scan: transcribe the whole VOD, then find quiet gems

def _deep_model(choice: str) -> str:
    if choice and choice != "auto":
        return choice
    # small only where Whisper really runs on the GPU: a CUDA device without NVIDIA's libraries ran "small" on
    # the CPU, several times slower than "base" (4.36)
    import gpu
    return "small" if gpu.whisper_gpu_ok() or gpu.vulkan_usable() else "base"


def deep_status(pr: dict) -> dict:
    j = S.jobs.get(pr.get("deep_job", ""))
    st = {"state": "none", "progress": 0.0, "step": "", "model": pr.get("deep_model", ""), "error": "",
          "gems": len(pr.get("gems", [])), "can": whisper.whisper_available() and pr["info"].get("has_audio", False)}
    if pr.get("deep_done"):
        st["state"] = "done"
        st["progress"] = 1.0
    if j:
        st.update(progress=j["progress"], step=j["step"])
        if j["status"] == "running":
            st["state"] = "running"
        elif j["status"] == "error":
            st.update(state="error", error=j.get("error") or "")
        elif j["status"] == "cancelled" and not pr.get("deep_done"):
            st["state"] = "cancelled"
    return st


def _covered(pr: dict, model: str, lang: str) -> float:
    tx = pr.get("tx", {}).get(f"{model}|{lang}")
    if not tx:
        return 0.0
    return sum(b - a for a, b in tx["covered"]) / max(1.0, pr["analysis"].duration)


def _update_gems(pid: str, model: str, lang: str):
    pr = S.projects[pid]
    tx = pr.get("tx", {}).get(f"{model}|{lang}")
    if not tx:
        return
    words = tx["words"]
    taken = [(c["start"], c["end"]) for c in pr["clips"]]
    found, _curve = gems.find(pr["analysis"], words, taken, chat=pr["chat"], chat_delay=pr["chat_delay"])
    old = {g["id"]: g for g in pr.get("gems", [])}
    out, used = [], {c.get("title", "").lower() for c in pr["clips"]}
    fresh = []
    S._number(pr, found, "gem")
    gone = S._removed_nums(pr)
    found = [g for g in found if g.id not in gone]
    for g in found:
        o = old.pop(g.id, None)
        if o:
            out.append(o)
        else:
            fresh.append(g)
    # gems you already did something with stay, even if a later pass ranks them out
    out += [g for g in old.values() if persist.touched(g)]
    for d in S.clip_dicts(pid, fresh, kind="gem"):
        ws = [w for w in words if d["start"] - S.TX_MARGIN <= w["start"] <= d["end"] + S.TX_MARGIN]
        d["words"] = ws
        S._title_clip(pr, d, ws, used)
        d["listened"] = True
        d["kind"] = "gem"
        out.append(d)
    out.sort(key=lambda d: -d.get("score", 0))
    pr["gems"] = out
    S.save_project_soon(pid)


def start_deep_scan(pid: str, model: str, lang: str):
    pr = S.projects[pid]
    if not whisper.whisper_available():
        raise RuntimeError("Deep scan needs Whisper, which isn't available in this install.")
    if not pr["info"].get("has_audio"):
        raise RuntimeError("This video has no sound to transcribe.")
    old = S.jobs.get(pr.get("deep_job", ""))
    if old and old["status"] == "running":
        return old
    model = _deep_model(model)
    job = S.new_job("deepscan")
    pr.update(deep_job=job["id"], deep_model=model, deep_lang=lang, deep_done=False)
    dur = pr["analysis"].duration

    def go():
        check, log = S.job_check(job), S.job_logger(job)
        log(f"Deep scan with Whisper '{model}': transcribing the whole stream.")
        step_s = 60.0
        a, last_gems, t0 = 0.0, 0.0, time.time()
        stats, bad_run, bad_from = {}, 0, 0.0
        job["step"] = "Deep scan: getting Whisper ready"
        while a < dur:
            check()
            # titles for the suggested clips come first: let a running listening job finish
            while (S.jobs.get(pr.get("listen_job", "")) or {}).get("status") == "running":
                check()
                time.sleep(0.5)
            b = min(dur, a + step_s)
            before = stats.get("unreadable", 0)
            ensure_words(pr, a, b, model, lang, check, stats, task="scan")
            if stats.get("unreadable", 0) > before:
                bad_from = a if not bad_run else bad_from
                bad_run += 1
            else:
                bad_run = 0
            if bad_run >= 3:
                # not "done, no gems": say the sound couldn't be read, so Retry means something
                raise RuntimeError(f"Couldn't read the video's sound from {engine.fmt_ts(bad_from)}. "
                                   "The file may be damaged or still downloading.")
            job["progress"] = round(b / dur, 4)
            took = time.time() - t0
            left = took / max(b, 1) * (dur - b)
            job["step"] = f"Deep scan {int(b / dur * 100)}%" + (f", about {engine.fmt_ts(left)} left" if b > 120 else "")
            if b - last_gems >= 900 or b >= dur:
                _update_gems(pid, model, lang)           # gems show up while the scan goes on
                last_gems = b
            a = b
        pr["deep_done"] = True
        _save_transcripts(pr)
        if stats.get("unreadable"):
            log(f"Couldn't read the sound in {stats['unreadable']} short stretch(es); they were skipped.")
        n = len(pr.get("gems", []))
        log(f"Deep scan done: {n} quiet gems.")
        return {"gems": n}
    S.run_job(job, go)
    return job


def _tx_file(pr: dict) -> str:
    return os.path.join(pr["workdir"], "transcripts.json")


def _video_sig(pr: dict) -> str:
    try:
        st = os.stat(pr["video"])
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _load_transcripts(pr: dict):
    """Transcripts made before for this VOD (deep scans, captions) so nothing is transcribed twice."""
    try:
        with open(_tx_file(pr), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("video") != _video_sig(pr):
            return
        pr["tx"] = {k: {"words": v["words"], "covered": [tuple(x) for x in v["covered"]],
                        "rough": [tuple(x) for x in v.get("rough", [])]}
                    for k, v in data.get("tx", {}).items()}
    except (OSError, ValueError, KeyError, TypeError):
        pass


def _save_transcripts(pr: dict):
    if pr.get("live") or not pr.get("tx"):
        return
    tmp = _tx_file(pr) + ".tmp"
    try:
        with pr["tx_lock"]:
            data = {"video": _video_sig(pr), "tx": {k: {"words": v["words"], "covered": v["covered"],
                                                         "rough": v.get("rough", [])}
                                                     for k, v in pr["tx"].items()}}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _tx_file(pr))
    except OSError:
        pass


# ---- transcript cache: each stretch of the VOD is transcribed once, then reused
#      for titles, the live caption preview, and the final render

def _tx(pr: dict, model: str, lang: str) -> dict:
    return pr.setdefault("tx", {}).setdefault(f"{model}|{lang}", {"words": [], "covered": []})


def _vocab(pr: dict) -> str:
    """Names and words Whisper should expect for this stream: the streamer, the channel's emotes, your list."""
    es = getattr(pr.get("analysis"), "_emotes", None)
    return exports.prompt(S._streamer_name(pr), list(es.by_name) if es else None, exports.load().get("vocab"))


def _without(spans: list, cut: list) -> list:
    """spans with every stretch in cut taken out."""
    for ca, cb in cut:
        spans = [s for a, b in spans for s in ((a, min(b, ca)), (max(a, cb), b)) if s[1] > s[0]]
    return spans


def ensure_words(pr: dict, a: float, b: float, model: str, lang: str, check=lambda: None,
                 stats: Optional[dict] = None, task: str = "captions", timer: Optional[timing.Timer] = None) -> list:
    """Transcript words (absolute times) for [a, b] of the VOD, transcribing any missing parts.
    A stretch whose sound can't be read isn't marked as done (so it's tried again later) and is
    counted in stats["unreadable"]. task: "titles", "scan" or "captions" (gpu.choose). Words from the
    Vulkan engine are marked rough: good for a title, but a caption hears that stretch again on
    faster-whisper, or the words the card guessed for a title would end up burned into the clip."""
    dur = pr["analysis"].duration if "analysis" in pr else pr.get("duration") or b
    a, b = max(0.0, a), min(dur, b)
    import gpu
    pick = gpu.choose(task, model, lambda m: print(f"  {m}", flush=True))
    on_card = pick["engine"] == "vulkan"
    timer = timer or timing.Timer("words")
    heard = "Whisper (card)" if on_card or gpu.whisper_gpu_ok() else "Whisper (CPU)"
    # the lock covers the transcript, not the listening: a title being heard mustn't hold up a render's captions (measured,
    # 31 titles and a Deep scan: 35 to 62 s waiting for 15 to 52 s of Whisper)
    t0 = time.time()
    with pr["tx_lock"]:
        timer.add("waiting for other listening", time.time() - t0)
        tx = _tx(pr, model, lang)
        gaps, cur = [], a
        for ca, cb in sorted(tx["covered"] if on_card or task != "captions" else _without(tx["covered"], tx.get("rough", []))):
            if cb <= cur:
                continue
            if ca > cur:
                gaps.append((cur, min(ca, b)))
            cur = max(cur, cb)
            if cur >= b:
                break
        if cur < b:
            gaps.append((cur, b))
    for ga, gb in gaps:
        if gb - ga < 0.5:
            continue
        check()
        pad_a, pad_b = max(0.0, ga - 1.0), gb + 1.0   # a little overlap so edge words aren't cut
        t0 = time.time()
        audio = whisper.extract_audio(pr["video"], pad_a, pad_b - pad_a)
        timer.add("reading the sound", time.time() - t0)
        if audio is None:
            if stats is not None:
                stats["unreadable"] = stats.get("unreadable", 0) + 1
            continue
        new = []
        if audio.size >= 1600:
            t0 = time.time()
            found = whisper.transcribe(audio, model, lang, lambda m: None, prompt=_vocab(pr), pick=pick, check=check)
            timer.add(heard, time.time() - t0)
            new = [{"start": round(w["start"] + pad_a, 3), "end": round(w["end"] + pad_a, 3), "word": w["word"]} for w in found]
        with pr["tx_lock"]:
            # heard on the card, it gives way to any part a caption finished meanwhile
            for sa, sb in _without([(ga, gb)], _without(tx["covered"], tx.get("rough", []))) if on_card else [(ga, gb)]:
                tx["words"] = sorted([w for w in tx["words"] if not (sa <= (w["start"] + w["end"]) / 2 < sb)]
                                     + [w for w in new if sa <= (w["start"] + w["end"]) / 2 < sb], key=lambda w: w["start"])
                tx["covered"].append((sa, sb))
                tx["rough"] = _without(tx.get("rough", []), [(sa, sb)]) + ([(sa, sb)] if on_card else [])
            # merge intervals
            merged = []
            for ca, cb in sorted(tx["covered"]):
                if merged and ca <= merged[-1][1] + 0.01:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], cb))
                else:
                    merged.append((ca, cb))
            tx["covered"] = merged
    with pr["tx_lock"]:
        return [w for w in tx["words"] if a <= w["start"] < b]


def start_listening(pid: str, order: list, model: str, lang: str):
    """Background job: transcribe each suggested clip and give it a proper title."""
    pr = S.projects[pid]
    old = S.jobs.get(pr.get("listen_job", ""))
    if old and old["status"] == "running":
        old["cancel"] = True
    job = S.new_job("listen")
    pr["listen_job"] = job["id"]
    yt_words = bool((pr.get("yt") or {}).get("words"))
    can_listen = (whisper.whisper_available() and pr["info"]["has_audio"]) or yt_words

    def go():
        check = S.job_check(job)
        ver = pr.get("clips_ver", 0)
        ids = [i for i in order if any(c["id"] == i for c in pr["clips"])]
        ids += [c["id"] for c in pr["clips"] if c["id"] not in ids]
        todo = [i for i in ids if not next((c for c in pr["clips"] if c["id"] == i), {}).get("listened")]
        t_start = time.time()
        timer = timing.Timer("titles", pr.get("title") or "")
        if todo:
            how = "YouTube's captions" if yt_words else f"Whisper {model}" if can_listen else "chat only"
            print(f"  Titling {len(todo)} clips ({how})...", flush=True)
        for n, cid in enumerate(todo):
            check()
            if pr.get("clips_ver", 0) != ver:
                return
            d = next((c for c in pr["clips"] if c["id"] == cid), None)
            if not d or d.get("listened"):
                continue
            job["step"] = f"Listening to clip {n + 1} of {len(todo)}"
            words = []
            if can_listen:
                gated = bool(pr.get("live"))
                try:
                    if gated:
                        job["step"] = "Waiting for other moments to be listened to" if live_glue.live_gate.queued() or live_glue.live_gate.busy else job["step"]
                        t0 = time.time()
                        live_glue.live_gate.acquire(live_glue._live_prio(d), check)
                        timer.add("waiting for other moments", time.time() - t0)
                        job["step"] = f"Listening to clip {n + 1} of {len(todo)}"
                    try:
                        if yt_words:
                            # YouTube's own captions: titles and cut points right away, no Whisper
                            words = ytextra.words_in(pr["yt"], d["start"] - S.TX_MARGIN, d["end"] + S.TX_MARGIN)
                        else:
                            words = ensure_words(pr, d["start"] - S.TX_MARGIN, d["end"] + S.TX_MARGIN, model, lang, check, task="titles",
                                                 timer=timer)
                    finally:
                        if gated:
                            live_glue.live_gate.release()
                except engine.Cancelled:
                    raise
                except Exception as e:
                    S.job_logger(job)(f"Couldn't transcribe clip: {str(e)[:160]}")
            used = {c["title"].lower() for c in pr["clips"] if c is not d and c.get("listened")}
            sess = S.live_sessions.get(pr.get("live", ""))
            if sess:   # live moments are separate projects; don't repeat titles across the stream
                used |= {S.projects[m]["clips"][0]["title"].lower() for m in sess.moments
                         if m != pid and m in S.projects and S.projects[m]["clips"] and S.projects[m]["clips"][0].get("listened")}
            if words and not d.get("trimmed") and not d.get("manual") and not d.get("userTrim") \
                    and d.get("state") != "kept":
                # never start or stop mid-sentence
                fixed = (d.get("len_mode") or pr.get("length") or 0) > 0
                a2, b2 = analysis.refine_with_words(d["start"], d["end"], d.get("peak", d["start"]), words,
                                                  max_move=3.0 if fixed else 8.0)
                a2, b2 = max(0.0, a2), min(S._media_end(pr) or pr["analysis"].duration, b2)
                if b2 - a2 >= 3 and (abs(a2 - d["start"]) > 0.05 or abs(b2 - d["end"]) > 0.05):
                    d["start"], d["end"] = round(a2, 2), round(b2, 2)
                    d["orig"] = [d["start"], d["end"]]
                    if d.get("abs_start") is not None and d.get("at") is not None:
                        off = d["at"] - d.get("peak", 0)
                        d["abs_start"], d["abs_end"] = round(off + d["start"], 1), round(off + d["end"], 1)
            d["words"] = words
            t0 = time.time()
            S._title_clip(pr, d, words, used)
            timer.add("writing the title", time.time() - t0)
            d["listened"] = True
            autopilot.clip_titled(pid, d)
            job["progress"] = (n + 1) / max(1, len(todo))
            if not pr.get("live"):
                S.save_project_soon(pid, 3.0)
        if todo:
            print(f"  Titled {len(todo)} clips in {time.time() - t_start:.0f} s.", flush=True)
            if timer.steps:
                timer.done()
        if not pr.get("live"):
            _save_transcripts(pr)
        return {"ok": True}
    S.run_job(job, go)
    return job
