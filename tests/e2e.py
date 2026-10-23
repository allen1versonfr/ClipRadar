"""
End-to-end test of a running Clip Radar: VOD and live clipping, rendering, and the TikTok Studio handoff.

It starts the app itself (the built .exe or `python src/app.py`), a simulated
Twitch stream (ffmpeg writing a live HLS playlist in real time), a fake Twitch
chat server, and mock GitHub and Twitch APIs, then drives the app over its HTTP API
exactly like the UI does.

  python tests/e2e.py --app "dist/ClipRadar/ClipRadar.exe" --ffmpeg tools/ffmpeg.exe --out report.json
  python tests/e2e.py --app "python src/app.py" --ffmpeg ffmpeg --out report.json
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _s in (sys.stdout, sys.stderr):      # Windows consoles default to cp1252; app text has arrows and quotes
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import fake_irc      # noqa: E402
import fake_twitch   # noqa: E402
import mock_github   # noqa: E402
import mock_twitch_api  # noqa: E402

PORT, IRC_PORT, HLS_PORT, GH_PORT, TW_PORT = 7870, 9461, 9481, 9491, 9501
B = f"http://127.0.0.1:{PORT}"
checks: list = []
diag: dict = {}


def rec(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
    print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def get(path):
    with urllib.request.urlopen(B + path, timeout=30) as r:
        return json.load(r)


def post(path, body=None):
    req = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read() or b"{}")}


def wait(fn, timeout, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def wait_job(jid, timeout=300):
    return wait(lambda: (lambda j: j if j["status"] != "running" else None)(get(f"/api/job/{jid}")), timeout)


def make_stream_source(ffmpeg, path, seconds=210, burst=70):
    subprocess.run([ffmpeg, "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-filter_complex",
                    f"[0:v]drawbox=x=20:y=20:w=320:h=180:color=orange@1:t=fill[v];"
                    f"[2:a]volume='if(between(t,{burst},{burst + 8}),0.8,0)':eval=frame[s];"
                    f"[1:a][s]amix=inputs=2:normalize=0[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
                    "-c:a", "aac", path], check=True)


def make_vod(a, tmp) -> tuple:
    """A 150 s 'VOD' with a loud moment at 60 s (and speech at 95 s when given), plus a chat replay
    full of the things that used to fool the ranking: a giveaway, a gift-sub bomb, bots and a raid."""
    import json as _json
    import random
    vod = os.path.join(tmp, "e2e_vod.mp4")
    dur = 150
    cmd = [a.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={dur}",
           "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:duration={dur}:seed=7",
           "-f", "lavfi", "-i", f"sine=frequency=500:duration={dur}"]
    fc = "[2:a]volume='if(between(t,60,66),0.8,0)':eval=frame[s]"
    mix, n = "[1:a][s]", 2
    if a.speech and os.path.isfile(a.speech):
        cmd += ["-i", a.speech]
        fc += ";[3:a]adelay=95000|95000,volume=3[sp]"
        mix, n = mix + "[sp]", 3
    fc += f";{mix}amix=inputs={n}:normalize=0:duration=first[a]"
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
            "-g", "60", "-c:a", "aac", vod]
    subprocess.run(cmd, check=True)
    rnd = random.Random(7)
    com = []

    def add(t, user, body, notice=None):
        com.append({"content_offset_seconds": round(t, 2), "commenter": {"name": user, "display_name": user},
                    "message": {"body": body, "user_notice_params": {"msg-id": notice}, "bits_spent": 0}})
    users = [f"fan{i}" for i in range(400)]
    for t in range(dur):
        for _ in range(rnd.choice([0, 1, 1, 2])):
            add(t + rnd.random(), rnd.choice(users), rnd.choice(["hi", "lol", "nice", "gg", "what game", "ok"]))
    for t in range(0, dur, 10):
        add(t + 0.5, "nightbot", "Follow on twitter KEKW KEKW")
    for k in range(100):                                       # giveaway
        add(20 + rnd.random() * 8, users[k], "!enter")
    for k in range(25):                                        # gift-sub bomb
        add(35 + k * 0.2, "biggifter", "", "subgift")
    for k in range(40):
        add(36 + rnd.random() * 6, users[200 + k % 8], "POGGERS POGGERS")
    for k in range(90):                                        # the real reaction to the loud moment
        add(66 + rnd.random() * 8, users[k], rnd.choice(["KEKW", "OMEGALUL", "clip it", "LETSGO", "no way"]))
    add(119, "streamelements", "bigstreamer is raiding with 900 viewers")
    for k in range(90):                                        # raid
        add(120 + rnd.random() * 8, f"raider{k}", "bigstreamerRaid RAID FROM THE BEST COMMUNITY bigstreamerRaid")
    com.sort(key=lambda c: c["content_offset_seconds"])
    chat = os.path.join(tmp, "e2e_vod.chat.json")
    with open(chat, "w", encoding="utf-8") as f:
        _json.dump({"streamer": {"name": "e2estreamer", "id": 1}, "comments": com}, f)
    return vod, chat, dur


def vod_checks(a, tmp):
    vod, chat, dur = make_vod(a, tmp)
    speech = bool(a.speech and os.path.isfile(a.speech))
    j = post("/api/analyze", {"source": vod, "chat_file": chat, "count": 0, "length": 0, "events": True,
                              "deep_scan": speech, "deep_model": a.model, "language": "en"})
    r = wait_job(j.get("job", ""), 300) if j.get("job") else None
    ok = r and r["status"] == "done"
    rec("VOD analysis", ok, r and (r.get("error") or " / ".join(r["log"][-4:])))
    if not ok:
        return None
    pv = r["result"]
    pid = pv["pid"]
    cs = pv.get("chat_stats") or {}
    rec("chat cleaning: bots, commands, notices and raids", cs.get("ignored", 0) >= 130 and cs.get("raids", 0) >= 1
        and cs.get("chatters", 0) >= 150, cs)
    clips = pv["clips"]
    peaks = [c["peak"] for c in clips]
    rec("the real reaction ranks first, not the giveaway, gift bomb or raid",
        # chat is shifted back 6 s: giveaway 14-22, gift bomb 29-36, raid 114-122
        clips and 54 <= clips[0]["peak"] <= 76 and not any(12 <= p <= 24 or 28 <= p <= 38 or 112 <= p <= 126 for p in peaks),
        [(c["peak"], c["score"], c["reason"]) for c in clips])
    lens = [round(c["end"] - c["start"], 1) for c in clips]
    rec("clips get their own length (15-90 s)", clips and all(14.5 <= x <= 90.5 for x in lens), lens)
    est = pv.get("estimate") or {}
    rec("expected clip estimate", est.get("strong", 0) >= 1 and est.get("suggest", 0) >= 5, est)
    rec("streamer recognised from the chat replay", pv.get("streamer") == "e2estreamer", pv.get("streamer"))
    # 3.5: detection
    rec("chat delay automatic (short test VOD: falls back to 6 s and says so)", (pv.get("detect") or {}).get("chat_delay") == "auto" and 4 <= pv.get("chat_delay", 0) <= 9
        and "chat delay" in pv.get("detect_text", ""),
        (pv.get("chat_delay"), pv.get("detect_text")))
    rec("7TV, BTTV and FFZ emotes loaded for the channel", pv.get("emotes_known", 0) >= 5, pv.get("emotes_known"))
    rec("clips get a 0-100 score with the signals behind it", clips and all(1 <= c.get("quality", 0) <= 100 and c.get("parts") for c in clips)
        and "chat" in clips[0]["parts"], [(c.get("quality"), c.get("parts")) for c in clips[:2]])
    # 3.6: titles and captions
    c0 = clips[0] if clips else {}
    rec("clips get title A and title B with the streamer's name", c0.get("title_a") and "e2estreamer" in (c0.get("title_b") or "").lower()
        and c0.get("title_b_named"), (c0.get("title_a"), c0.get("title_b")))
    rec("a description apart from the title, and a few relevant hashtags", c0.get("description") and c0.get("description") != c0.get("title")
        and 2 <= len(c0.get("hashtags") or []) <= 5 and "#e2estreamer" in c0.get("hashtags", []), (c0.get("description"), c0.get("hashtags")))
    post("/api/titles", {"hashtags": False})
    sn = post("/api/streamer", {"pid": pid, "name": "E2E Streamer"})
    s0 = next((x for x in sn.get("clips", []) if x["id"] == c0.get("id")), {})
    rec("setting the streamer's name rewrites title B; hashtags can be switched off",
        "E2E Streamer" in (s0.get("title_b") or "") and "#" not in (s0.get("post") or "#"), (s0.get("title_b"), s0.get("post")))
    post("/api/titles", {"hashtags": True})
    # stable ids when re-ranking
    again = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60})
    rec("re-ranking keeps clips (and their ids)", sorted(c["id"] for c in again.get("clips", [])) == sorted(c["id"] for c in clips),
        [c["id"] for c in again.get("clips", [])])
    add = post("/api/add_clip", {"pid": pid, "t": 57, "length": 0})
    rec("a clip made by hand also gets a smart length", 14.5 <= add.get("end", 0) - add.get("start", 0) <= 90.5,
        f"{add.get('start')}-{add.get('end')}")
    # learning: keep the loud ones, skip the quiet ones
    made = [post("/api/add_clip", {"pid": pid, "t": t, "length": 20}) for t in (10, 30, 45, 85, 100, 110, 140)]
    for c in clips + [add]:
        post("/api/feedback", {"pid": pid, "clip": c["id"], "action": "keep"})
    for c in made:
        post("/api/feedback", {"pid": pid, "clip": c.get("id"), "action": "skip"})
    lr = get("/api/learning")
    prof = next((p for p in lr.get("profiles", []) if p["streamer"] == "e2estreamer"), {})
    rec("learns from keeps and skips", prof.get("decisions", 0) >= 8 and prof.get("kept", 0) >= 2, prof)
    tuned = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60})
    rec("ranking tuned for this streamer", (tuned.get("learning") or {}).get("active"), tuned.get("learning"))
    cmp = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 45, "detect": {"preset": "podcasts"}, "dry_run": True})
    rec("trying other detection settings compares without changing anything", "added" in cmp and "dropped" in cmp
        and (get(f"/api/clips/{pid}") and post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60}).get("detect", {}).get("preset") == "any"),
        {k: cmp.get(k) for k in ("count", "same", "added", "dropped")})
    pod = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 45, "detect": {"preset": "podcasts"}})
    auto_lens = [t["end"] - t["start"] for t in cmp.get("top", [])]
    rec("a stream-type preset brings its own clip lengths", (pod.get("detect") or {}).get("min_len") == 30
        and (pod.get("detect") or {}).get("max_len") == 150 and auto_lens and all(29.5 <= x <= 150.5 for x in auto_lens),
        (pod.get("detect_text"), [round(x, 1) for x in auto_lens]))
    post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60, "detect": {"preset": "any"}})
    post("/api/feedback", {"pid": pid, "clip": made[0].get("id"), "action": "reason", "reason": "noise"})
    prof2 = next((p for p in get("/api/learning").get("profiles", []) if p["streamer"] == "e2estreamer"), {})
    rec("skip reasons and acceptance are tracked", prof2.get("reasons") and prof2["reasons"][0]["key"] == "noise"
        and prof2.get("accept") is not None and prof2.get("added", 0) >= 7, prof2)
    post("/api/learning/forget", {"streamer": "e2estreamer"})
    rec("forget what was learned", not any(p["streamer"] == "e2estreamer" for p in get("/api/learning").get("profiles", [])))
    if speech:
        done = wait(lambda: (lambda d: d if d["state"] in ("done", "error", "cancelled") else None)(
            get(f"/api/clips/{pid}")["deep"]), 400, 3)
        work = os.path.join(tmp, "work")
        txs = [os.path.join(rt, f) for rt, _, fs in os.walk(work) for f in fs if f == "transcripts.json"]
        words = 0
        if txs:
            import json as _json
            d = _json.load(open(txs[0], encoding="utf-8"))
            words = sum(len(v["words"]) for v in d.get("tx", {}).values())
        rec("deep scan transcribes the whole VOD", done and done["state"] == "done" and words >= 5,
            f"{done and done.get('state')} {done and done.get('error', '')}; {words} words saved; "
            f"{len(get(f'/api/clips/{pid}').get('gems', []))} quiet gems")
    return {"pid": pid, "vod": vod, "chat": chat, "clips": clips}


def _item(iid):
    return next((i for i in get("/api/queue")["items"] if i["id"] == iid), None)


def _wait_item(iid, timeout, states=("done", "error")):
    return wait(lambda: (lambda q: q if q and q["status"] in states else None)(_item(iid)), timeout, 0.5)


def persistence_checks(ctx, restart):
    """3.2: clip numbers stay with their moments, and the review, Exports and unfinished renders
    survive closing the app (here: killing it mid-render)."""
    pid = ctx["pid"]
    opts = {"layout": "blur", "captions": False, "hw_encode": False}
    auto = get(f"/api/clips/{pid}")["clips"]
    if len(auto) < 3:
        rec("persistence: enough clips to test with", False, len(auto))
        return
    k, s, x = auto[0], auto[1], auto[2]
    post("/api/clip_state", {"pid": pid, "id": k["id"], "state": "kept", "title": "Kept forever", "userTitle": True})
    post("/api/clip_state", {"pid": pid, "id": s["id"], "state": "skipped"})
    few = {c["id"]: c for c in post("/api/repick", {"pid": pid, "count": 1, "length": 0, "chat_weight": 60}).get("clips", [])}
    rec("kept and skipped clips stay when you ask for fewer",
        k["id"] in few and s["id"] in few and few[k["id"]].get("title") == "Kept forever" and few[k["id"]].get("state") == "kept",
        sorted(few))
    full = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60}).get("clips", [])
    before = {round(c["peak"]): c["id"] for c in auto}
    after = {round(c["peak"]): c["id"] for c in full}
    same = [p for p in before if p in after]
    rec("clip numbers stay with their moments", same and all(before[p] == after[p] for p in same),
        [(p, before[p], after.get(p)) for p in before])
    ids = [c["id"] for c in full]
    rec("no two clips share a number", len(ids) == len(set(ids)), ids)

    it1 = post("/api/queue/add", {"pid": pid, "clip": dict(k, title="Kept forever", rank=k["id"], words=None), "opts": opts})
    d1 = _wait_item(it1.get("id"), 240)
    rec("kept clip renders", d1 and d1["status"] == "done" and d1.get("name") == "Kept forever.mp4",
        d1 and (d1.get("name") or d1.get("error")))
    post("/api/test/break_next_output")
    it2 = post("/api/queue/add", {"pid": pid, "clip": dict(s, title="Second try", rank=s["id"], words=None), "opts": opts})
    d2 = _wait_item(it2.get("id"), 240)
    rec("a broken render is caught and redone", d2 and d2["status"] == "done" and d2.get("name") == "Second try.mp4",
        d2 and (d2.get("name") or d2.get("error")))
    # a long render, closed half-way through
    long_clip = dict(x, start=0, end=140, title="Interrupted", rank=x["id"], words=None)
    it3 = post("/api/queue/add", {"pid": pid, "clip": long_clip, "opts": opts})
    mid = wait(lambda: (lambda q: q if q and q["status"] == "rendering" and q["progress"] >= 0.35 else None)(
        _item(it3.get("id"))), 180, 0.2)
    rec("render in progress before closing", mid, mid and mid.get("progress"))
    up = restart()
    rec("app starts again", up)
    if not up:
        return
    items = {i["id"]: i for i in get("/api/queue")["items"]}
    e1 = items.get(it1.get("id"))
    served = False
    if e1 and e1.get("url"):
        try:
            served = urllib.request.urlopen(B + e1["url"], timeout=20).status == 200
        except Exception:
            served = False
    rec("Exports survive a restart", e1 and e1["status"] == "done" and served and it2.get("id") in items,
        e1 and f"{e1['status']} served={served}")
    d3 = _wait_item(it3.get("id"), 400)
    rec("an unfinished render finishes after a restart", d3 and d3["status"] == "done" and d3.get("name") == "Interrupted.mp4",
        d3 and (d3.get("name") or d3.get("error")) or (items.get(it3.get("id")) or {}).get("status"))
    folder = (d1 or {}).get("folder") or ""
    left = [f for f in os.listdir(folder) if f.startswith("vc_")] if os.path.isdir(folder) else []
    files = sorted(f for f in os.listdir(folder) if f.endswith(".mp4")) if os.path.isdir(folder) else []
    rec("no temp or duplicate files left behind", not left and files == ["Interrupted.mp4", "Kept forever.mp4", "Second try.mp4"],
        f"{left} {files}")
    # open the same VOD again
    j = post("/api/analyze", {"source": ctx["vod"], "chat_file": ctx["chat"], "count": 5, "length": 0, "events": True})
    r = wait_job(j.get("job", ""), 300) if j.get("job") else None
    pv = (r or {}).get("result") or {}
    got = {c["id"]: c for c in pv.get("clips", [])}
    rec("reopening a VOD picks up where you left off",
        pv.get("restored") and pv.get("pid") == pid and got.get(k["id"], {}).get("state") == "kept"
        and got.get(k["id"], {}).get("title") == "Kept forever" and got.get(s["id"], {}).get("state") == "skipped"
        and sorted(got) == sorted(c["id"] for c in full),
        f"restored={pv.get('restored')} review={pv.get('review')} ids={sorted(got)}")
    rec("reopening reuses the analysis", r and any("analysis from last time" in ln for ln in r.get("log", [])),
        r and r.get("log", [])[-3:])
    rec("kept clip still shows as rendered", any(i.get("pid") == pid and i.get("clip_id") == k["id"] and i["status"] == "done"
                                                 for i in get("/api/queue")["items"]))


def review_checks(ctx):
    """3.3: the Content Check review workflow, through the same API the Review screen uses."""
    pid = ctx["pid"]
    clips = [c for c in get(f"/api/clips/{pid}")["clips"] if c.get("state") != "kept"]
    if len(clips) < 2:
        rec("review: enough clips to test with", False, len(clips))
        return
    a, b = clips[0], clips[1]
    t0 = a["start"]
    ws = [{"start": t0 + 2 + i * 0.4, "end": t0 + 2.35 + i * 0.4, "word": w} for i, w in enumerate("what the fuck was that".split())]
    r = post("/api/check", {"pid": pid, "id": a["id"], "wordsEdited": True, "words_edit": ws, "start": a["start"], "end": a["end"],
                            "action": ""})
    sf = (r.get("clips") or [{}])[0].get("safety") or {}
    swear = next((f for f in sf.get("flags", []) if f["source"] == "speech"), None)
    rec("a flag needs review, with its time and possible fixes", sf.get("status") == "needs_review" and swear
        and abs(swear["start"] - 2.8) < 0.1 and "bleep" in swear["fixes"], swear and (swear["start"], swear["fixes"]))
    if not swear:
        return
    pv = post("/api/check", {"pid": pid, "id": a["id"], "action": "fix", "args": {"fp": swear["fp"], "kind": "mute", "preview": True}})
    still = get(f"/api/clips/{pid}")
    still_a = next(c for c in still["clips"] if c["id"] == a["id"])
    rec("previewing a fix doesn't apply it", pv.get("fix", {}).get("kind") == "mute" and not (still_a.get("check") or {}).get("fixes"))
    r = post("/api/check", {"pid": pid, "id": a["id"], "action": "fix", "args": {"fp": swear["fp"], "kind": "mute"}})
    v = (r.get("clips") or [{}])[0]
    rec("fixing a flag marks it corrected (original kept)", v.get("safety", {}).get("status") == "corrected"
        and any(f["status"] == "corrected" and f["text"] == swear["text"] for f in v["safety"]["flags"]), v.get("safety", {}).get("status"))
    # it renders muted
    it = post("/api/queue/add", {"pid": pid, "clip": dict(a, title="Muted bit", words=None),
                                 "opts": {"layout": "blur", "captions": False, "hw_encode": False}})
    done = _wait_item(it.get("id"), 240)
    quiet = None
    if done and done["status"] == "done":
        try:
            out = subprocess.run([ARGS.ffmpeg, "-v", "info", "-ss", "2.9", "-t", "0.2", "-i", done["folder"] + os.sep + done["name"],
                                  "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=60).stderr
            mx = float(out.split("max_volume:")[1].split("dB")[0])
            quiet = mx < -50
        except Exception as e:
            quiet = f"couldn't measure: {e}"
    rec("the fix is in the rendered file (muted)", done and done["status"] == "done" and done.get("censored", 0) >= 1 and quiet is True,
        done and f"{done.get('name') or done.get('error')} censored={done.get('censored')} quiet={quiet}")
    # E: excluded clips don't render; including again works
    post("/api/check", {"pid": pid, "id": b["id"], "action": "exclude", "args": {"on": True}})
    blocked = post("/api/queue/add", {"pid": pid, "clip": b, "opts": {}})
    rec("an excluded clip won't render", blocked.get("_status") == 400 and "excluded" in blocked.get("error", ""), blocked.get("error"))
    post("/api/check", {"pid": pid, "id": b["id"], "action": "exclude", "args": {"on": False}})
    # "not until reviewed"
    tb = b["start"]
    post("/api/check", {"pid": pid, "id": b["id"], "wordsEdited": True, "start": b["start"], "end": b["end"], "action": "",
                        "words_edit": [{"start": tb + 3, "end": tb + 3.4, "word": "shit"}]})
    post("/api/safety", {"export_flagged": "block"})
    blocked = post("/api/queue/add", {"pid": pid, "clip": b, "opts": {}})
    left = post("/api/check", {"pid": pid, "id": b["id"], "action": "leave"})
    st = ((left.get("clips") or [{}])[0].get("safety") or {}).get("status")
    allowed = post("/api/queue/add", {"pid": pid, "clip": dict(b, title="Left alone"), "opts": {"layout": "blur", "captions": False, "hw_encode": False}})
    post("/api/safety", {"export_flagged": "ask"})
    rec("'not until reviewed' blocks until you decide", blocked.get("_status") == 400 and st == "reviewed" and allowed.get("id"),
        f"{blocked.get('error')} / {st} / {allowed.get('error') or 'queued'}")
    if allowed.get("id"):
        _wait_item(allowed["id"], 240)
    # F: removed clips stay removed
    c3 = next((c for c in get(f"/api/clips/{pid}")["clips"] if c["id"] not in (a["id"], b["id"]) and c.get("state") != "kept"), None)
    if c3:
        post("/api/clip_remove", {"pid": pid, "id": c3["id"]})
        again = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60})
        gone = c3["id"] not in [c["id"] for c in again.get("clips", [])]
        back = post("/api/clip_restore", {"pid": pid, "id": c3["id"]})
        rec("a removed clip stays out after re-ranking, and can be put back", gone and (back.get("clip") or {}).get("id") == c3["id"]
            and c3["id"] in [c["id"] for c in get(f"/api/clips/{pid}")["clips"]], f"gone={gone} back={bool(back.get('clip'))}")
    time.sleep(1.5)
    work = os.path.dirname(done["folder"]) if done and done.get("folder") else ""
    saved = {}
    try:
        saved = json.load(open(os.path.join(work, "project.json"), encoding="utf-8"))
    except Exception:
        pass
    sa = next((c for c in saved.get("clips", []) if c["id"] == a["id"]), {})
    rec("review decisions are saved with the VOD", (sa.get("check") or {}).get("fixes") and any(
        l["action"] == "fix" for l in (sa.get("check") or {}).get("log", [])), (sa.get("check") or {}).get("log"))


def _probe_size(path):
    out = subprocess.run([ARGS.ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in os.path.basename(ARGS.ffmpeg) else "ffprobe",
                          "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", path], capture_output=True, text=True, timeout=60).stdout
    d = json.loads(out or "{}")
    st = (d.get("streams") or [{}])[0]
    return st.get("width"), st.get("height"), float((d.get("format") or {}).get("duration") or 0)


def export_checks(ctx):
    """3.7: platform presets, other formats, .srt files, file name templates, duplicate protection,
    length warnings, time estimates and performance settings."""
    pid = ctx["pid"]
    before = get("/api/export")["prefs"]
    clips = get(f"/api/clips/{pid}")["clips"]
    c = next((x for x in clips if x.get("state") != "kept" and not (x.get("check") or {}).get("excluded")), clips[0])
    base = dict(c, start=c["start"], end=c["start"] + 4, words=None)
    opts = {"layout": "blur", "aspect": "9:16", "captions": False, "hw_encode": False}
    try:
        v = post("/api/export", {"preset": "x", "template": "{streamer} - {number} - {title}", "subtitles": "burn_srt",
                                 "sample": {"title": "Big moment", "streamer": "Sam", "number": 3}})
        rec("export settings save and preview the file name", v["prefs"]["preset"] == "x" and v["preview_name"] == "Sam - 03 - Big moment.mp4"
            and "720×1280" in v["describe"], (v.get("preview_name"), v.get("describe")))
        t0 = c["start"]                              # caption words use the video's own times
        w = [{"start": t0 + 1.0, "end": t0 + 1.4, "word": "hello"}, {"start": t0 + 1.5, "end": t0 + 1.9, "word": "there"}]
        body = {"pid": pid, "clip": dict(base, title="X size", rank=c["id"], words=w), "opts": dict(opts, captions=True)}
        it = post("/api/queue/add", body)
        again = post("/api/queue/add", body)
        rec("the same clip sent twice renders once", it.get("id") and again.get("id") == it.get("id"), (it.get("id"), again.get("id")))
        q = get("/api/queue")
        rec("the queue reports its time left and renders at once", "eta" in q and q.get("workers") == 1, {k: q.get(k) for k in ("eta", "workers")})
        d = _wait_item(it.get("id"), 240)
        got = _probe_size(os.path.join(d["folder"], d["name"])) if d and d["status"] == "done" else None
        rec("X preset renders at 720x1280", got and got[:2] == (720, 1280), got or (d and d.get("error")))
        rec("file name follows the template", d and d.get("name", "").endswith(f"{int(c['id']):02d} - X size.mp4"), d and d.get("name"))
        post("/api/hashtags", {"file": {"enabled": True, "general": ["#TwitchClips", "#Gaming"]}})
        # on Windows' real file system: Unicode, a tag already in the title, and characters Windows forbids
        tg = post("/api/queue/add", {"pid": pid, "clip": dict(base, title="Tagged é 名前 #Gaming: why?", rank=c["id"] + 1000, words=None,
                                                             start=base["start"] + 1), "opts": opts})
        dt = _wait_item(tg.get("id"), 240)
        tname = (dt or {}).get("name", "")
        rec("hashtags are in the rendered file's name", dt and dt["status"] == "done" and " #TwitchClips" in tname
            and "Tagged é 名前" in tname and tname.count("#Gaming") == 1 and not any(ch in tname for ch in ':?')
            and os.path.isfile(os.path.join(dt["folder"], tname)),
            {"name": ascii(tname), "status": (dt or {}).get("status"), "error": (dt or {}).get("error"),
             "on disk": [ascii(f) for f in (os.listdir(dt["folder"]) if dt and os.path.isdir(dt.get("folder", "")) else [])
                         if "Tagged" in f]})
        post("/api/hashtags", {"file": {"enabled": False}})
        srt = os.path.join(d["folder"], os.path.splitext(d["name"])[0] + ".srt") if d and d.get("name") else ""
        txt = open(srt, encoding="utf-8").read() if srt and os.path.isfile(srt) else ""
        rec("captions also saved as .srt", "hello there" in txt.lower() and "-->" in txt and d.get("srt"), txt[:80] or srt)
        rec("a finished render shows no time left", d and d.get("eta") == 0, d and d.get("eta"))
        long_ = post("/api/queue/add", {"pid": pid, "clip": dict(base, end=c["start"] + 150, title="Too long for X"), "opts": opts})
        rec("a clip longer than the platform allows is flagged", "2:20" in (long_.get("warning") or ""), long_.get("warning"))
        if long_.get("id"):
            post("/api/queue/remove", {"id": long_["id"]})
        post("/api/export", {"preset": "tiktok", "custom": {"container": "webm"}, "subtitles": "srt", "template": "{title}"})
        it = post("/api/queue/add", {"pid": pid, "clip": dict(base, title="Web format", words=w), "opts": dict(opts, captions=True)})
        d = _wait_item(it.get("id"), 300)
        fmt = ""
        if d and d["status"] == "done":
            fmt = subprocess.run([ARGS.ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in os.path.basename(ARGS.ffmpeg) else "ffprobe",
                                  "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                                  os.path.join(d["folder"], d["name"])], capture_output=True, text=True, timeout=60).stdout.split()
        rec("WebM export: VP9 and Opus", d and d.get("name") == "Web format.webm" and "vp9" in fmt and "opus" in fmt,
            (d and (d.get("name") or d.get("error")), fmt))
        rec("captions as .srt only: a file, nothing burned in", d and d.get("srt")
            and os.path.isfile(os.path.join(d["folder"], "Web format.srt")), d and d.get("srt"))
        p = post("/api/export", {"renders": 2, "priority": "fast"})
        q = get("/api/queue")
        perf = get("/api/perf")
        rec("performance: two renders at once", p["prefs"]["renders"] == 2 and q.get("workers") == 2 and perf.get("priority") == "fast"
            and perf.get("speeds"), (q.get("workers"), perf))
    finally:
        post("/api/export", dict(before, renders=1))


def _face_video(a, tmp, name, overlay, scale):
    """A made-up stream with a face in it (tests/assets/face.jpg) over moving test colours."""
    path = os.path.join(tmp, name)
    subprocess.run([a.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=40",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=40", "-loop", "1", "-i", os.path.join(HERE, "assets", "face.jpg"),
                    "-filter_complex", f"[2]scale={scale}[f];[0][f]overlay={overlay}[v]", "-map", "[v]", "-map", "1:a",
                    "-t", "40", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
                   check=True, timeout=180)
    return path


def multi_source_checks(a, tmp):
    """3.10: two videos opened at once, their own folders and numbers, pause and resume."""
    v1 = _face_video(a, tmp, "src_one.mp4", "x=100:y=100", "240:300")
    v2 = _face_video(a, tmp, "src_two.mp4", "x=900:y=300", "240:300")
    j1 = post("/api/analyze", {"source": v1, "count": 3, "length": 10, "events": False})
    j2 = post("/api/analyze", {"source": v2, "count": 3, "length": 10, "events": False})
    again = post("/api/analyze", {"source": v1, "count": 3, "length": 10, "events": False})
    r1, r2 = wait_job(j1.get("job", ""), 300), wait_job(j2.get("job", ""), 300)
    ok = r1 and r2 and r1["status"] == r2["status"] == "done"
    rec("two videos open at the same time, each in its own project", ok and r1["result"]["pid"] != r2["result"]["pid"]
        and bool(again.get("already")) and again.get("job") == j1.get("job"),
        (r1 and r1["status"], r2 and r2["status"], again))
    dash = get("/api/sources")
    done = [x for x in dash["items"] if x["type"] == "vod" and x["status"] == "done" and x["pid"] in
            ((r1 or {}).get("result", {}).get("pid"), (r2 or {}).get("result", {}).get("pid"))]
    rec("Running now lists each source", len(done) == 2 and all(x["label"] == "Video file" for x in done), [(x["title"], x["status"]) for x in dash["items"]])
    if ok:
        pv = get(f"/api/project/{r1['result']['pid']}")
        rec("a project can be reopened from Running now", pv.get("pid") == r1["result"]["pid"] and pv.get("origin", {}).get("platform") == "local",
            pv.get("origin"))
    # pause and resume
    long_v = os.path.join(tmp, "src_long.mp4")
    subprocess.run([a.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=900", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:amplitude=0.05:duration=900", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", long_v], check=True, timeout=600)
    j = post("/api/analyze", {"source": long_v, "count": 3, "length": 10, "events": True})
    time.sleep(0.5)
    post("/api/sources/pause", {"job": j["job"]})
    pj = wait_job(j["job"], 120)
    if pj and pj["status"] == "done":
        rec("pause and resume", True, "finished before it could be paused")
    else:
        rs = post("/api/sources/resume", {"job": j["job"]})
        rj = wait_job(rs.get("job", ""), 600) if rs.get("job") else None
        rec("pause and resume", pj and pj["status"] == "paused" and rj and rj["status"] == "done",
            (pj and pj["status"], rj and (rj["status"], rj.get("error"))))


def storage_checks(ctx, tmp):
    """3.11: folders for each kind of file, checked before use, with temporary files safe to clear."""
    lv = get("/api/locations")
    rec("storage lists each folder with free space", [x["key"] for x in lv["locations"]] == ["projects", "downloads", "clips", "temp", "logs"]
        and all(x["free"] for x in lv["locations"]), [(x["key"], x["where"]) for x in lv["locations"]])
    bad = post("/api/locations", {"clips": "not/a/full/path"})
    rec("a folder that can't be used is refused", "clips" in (bad.get("errors") or {}), bad.get("errors"))
    clips_dir = os.path.join(tmp, "my clips")
    ok = post("/api/locations", {"clips": clips_dir})
    rec("rendered clips can go to a folder of your choice", not ok.get("errors") and any(x["key"] == "clips" and x["path"] == clips_dir
                                                                                        for x in ok["locations"]), ok.get("errors"))
    clips = get(f"/api/clips/{ctx['pid']}")["clips"]
    c = next((x for x in clips if not (x.get("check") or {}).get("excluded")), clips[0])
    it = post("/api/queue/add", {"pid": ctx["pid"], "clip": dict(c, end=c["start"] + 4, title="Elsewhere", words=None),
                                 "opts": {"layout": "blur", "captions": False, "hw_encode": False}})
    d = _wait_item(it.get("id"), 240)
    rec("the clip lands in that folder", d and d["status"] == "done" and os.path.normcase(d["folder"]).startswith(os.path.normcase(clips_dir))
        and os.path.isfile(os.path.join(d["folder"], d["name"])), d and (d.get("folder"), d.get("error")))
    st = get("/api/storage")
    rec("storage counts clips in the new folder", st["clips"]["count"] >= 1, st["clips"])
    cc = post("/api/storage/clear_cache", {})
    rec("temporary files can be cleared", "freed" in cc and not cc.get("failed"), cc.get("freed"))
    back = post("/api/locations", {"clips": ""})
    rec("back to the default folder", any(x["key"] == "clips" and not x["set"] for x in back["locations"]), back.get("errors"))


def framing_checks(a, tmp):
    """3.9: auto-framing, following a face, facecam only, and a facecam found in the corner."""
    irl = _face_video(a, tmp, "irl_face.mp4", "x='200+min(t,20)*55':y=200:enable='not(between(t,28,29.5))'", "480:600")
    cam = _face_video(a, tmp, "cam_face.mp4", "x=W-w-40:y=40", "240:300")
    pids = {}
    for name, v in (("irl", irl), ("cam", cam)):
        j = post("/api/analyze", {"source": v, "count": 0, "length": 0, "events": False})
        r = wait_job(j.get("job", ""), 300) if j.get("job") else None
        pids[name] = r and r["status"] == "done" and r["result"]["pid"]
    if not all(pids.values()):
        rec("framing: test videos open", False, pids)
        return
    s1 = get(f"/api/framing/{pids['irl']}")
    rec("auto-frame: a person filling the frame is followed", s1.get("layout") == "full" and s1.get("track") == "auto", s1)
    s2 = get(f"/api/framing/{pids['cam']}")
    box = s2.get("cam") or [0, 0, 0, 0]
    real = (1640 / 1920, 40 / 1080, 240 / 1920, 300 / 1080)
    inside = box[0] <= real[0] + 0.01 and box[1] <= real[1] + 0.01 and box[0] + box[2] >= real[0] + real[2] - 0.01 \
        and box[1] + box[3] >= real[1] + real[3] - 0.01 and box[2] < 0.35
    rec("auto-frame: a facecam in the corner gives Cam + game, boxed around the camera", s2.get("layout") == "split" and inside, s2)
    opts = {"layout": "full", "aspect": "9:16", "captions": False, "hw_encode": False, "track": "auto"}
    tr = post("/api/framing/track", {"pid": pids["irl"], "start": 2, "end": 30, "opts": opts})
    xs = [x for _, x in tr.get("path", [])]
    rec("follow face: the crop moves with the person", xs and xs[-1] - xs[0] > 0.3 and tr.get("coverage", 0) > 0.8,
        {k: tr.get(k) for k in ("reason", "coverage", "confidence")} | {"from": xs[:1], "to": xs[-1:]})
    clip = {"id": 1, "start": 2, "end": 30, "title": "Following"}
    it = post("/api/queue/add", {"pid": pids["irl"], "clip": clip, "opts": opts})
    d = _wait_item(it.get("id"), 300)
    got = _probe_size(os.path.join(d["folder"], d["name"])) if d and d["status"] == "done" else None
    rec("a followed clip renders", got and got[:2] == (1080, 1920) and (d.get("framing") or {}).get("followed"),
        (got, d and (d.get("framing") or d.get("error"))))
    it = post("/api/queue/add", {"pid": pids["cam"], "clip": dict(clip, title="Cam only", end=8),
                                 "opts": {"layout": "cam", "cam": box, "aspect": "9:16", "captions": False, "hw_encode": False}})
    d = _wait_item(it.get("id"), 300)
    got = _probe_size(os.path.join(d["folder"], d["name"])) if d and d["status"] == "done" else None
    rec("facecam only renders the camera full frame", got and got[:2] == (1080, 1920), got or (d and d.get("error")))
    off = post("/api/framing/track", {"pid": pids["irl"], "start": 2, "end": 30, "opts": dict(opts, layout="blur")})
    rec("follow face only applies to the whole-frame layout", off.get("path") == [] and "Whole frame" in off.get("reason", ""), off)


def editing_checks(ctx):
    """3.4: timeline data, the original cut, square and wide renders, and the exact preview."""
    pid = ctx["pid"]
    clips = get(f"/api/clips/{pid}")["clips"]
    c = next((x for x in clips if x.get("state") != "kept" and not (x.get("check") or {}).get("excluded")), clips[0])
    rec("clips know their original cut and frame rate", all(x.get("orig") and len(x["orig"]) == 2 for x in clips)
        and 20 <= (c.get("fps") or 0) <= 61, (c.get("orig"), c.get("fps")))
    st = get(f"/api/strip/{pid}?a=40&b=80")
    rec("timeline data: waveform, laughter and chat for the stretch shown", abs(len(st.get("wave", [])) - 400) <= 2
        and st.get("hz") == 10 and len(st.get("chat", [])) >= 39
        and ("laugh" in st.get("events", {}) or not get("/api/status").get("laughter")),
        f"wave={len(st.get('wave', []))} chat={len(st.get('chat', []))} events={list(st.get('events', {}))}")
    base = dict(c, start=c["start"], end=c["start"] + 5, words=None)
    for aspect, size in (("1:1", (1080, 1080)), ("16:9", (1920, 1080))):
        it = post("/api/queue/add", {"pid": pid, "clip": dict(base, title=f"Shape {aspect.replace(':', 'x')}"),
                                     "opts": {"layout": "split", "aspect": aspect, "captions": False, "hw_encode": False}})
        d = _wait_item(it.get("id"), 240)
        got = _probe_size(os.path.join(d["folder"], d["name"])) if d and d["status"] == "done" else None
        rec(f"{aspect} clips render at {size[0]}x{size[1]}", got and got[:2] == size and abs(got[2] - 5) < 0.6,
            got or (d and d.get("error")))
    j = post("/api/preview_render", {"pid": pid, "clip": {"id": c["id"], "start": c["start"], "end": c["end"], "title": "Exact"},
                                     "opts": {"layout": "blur", "aspect": "1:1", "captions": False, "hw_encode": False},
                                     "at": c["start"] + 2})
    r = wait_job(j.get("job", ""), 240) if j.get("job") else None
    ok = r and r["status"] == "done"
    got = None
    if ok:
        rel = r["result"]["url"].split("/files/" + pid + "/", 1)[1]
        path = next((os.path.join(rt, f) for rt, _, fs in os.walk(os.path.dirname(ctx["vod"])) for f in fs
                     if os.path.join(rt, f).replace(os.sep, "/").endswith(rel)), None)
        got = _probe_size(path) if path else None
        try:
            served = urllib.request.urlopen(B + r["result"]["url"], timeout=20).status == 200
        except Exception:
            served = False
    rec("exact preview renders a few seconds like the export", ok and got and got[:2] == (1080, 1080) and 4 <= got[2] <= 6.6 and served,
        got or (r and r.get("error")))
    time.sleep(1.5)
    saved = {}
    for rt, _, fs in os.walk(os.path.dirname(ctx["vod"])):
        if "project.json" in fs:
            try:
                saved = json.load(open(os.path.join(rt, "project.json"), encoding="utf-8"))
            except Exception:
                pass
    rec("the original cut is saved with the VOD", all(x.get("orig") for x in saved.get("clips", [])) and saved.get("clips"))


ARGS = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out", default="e2e_report.json")
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--speech", default="", help="a WAV of someone talking, for the VOD and deep scan checks")
    a = ap.parse_args()
    global ARGS
    ARGS = a

    tmp = tempfile.mkdtemp(prefix="vc_e2e_")
    work, data, hls = os.path.join(tmp, "work"), os.path.join(tmp, "data"), os.path.join(tmp, "hls")
    for d in (work, data, hls):
        os.makedirs(d)
    fake_irc.start_in_thread(IRC_PORT, [72])
    mock_github.start(GH_PORT, "v99.0.0")
    mock_twitch_api.start(TW_PORT)

    env = dict(os.environ, CLIPRADAR_WORK=work, CLIPRADAR_DATA=data, CLIPRADAR_PORT=str(PORT),
               CLIPRADAR_IRC=f"127.0.0.1:{IRC_PORT}:plain",
               CLIPRADAR_NO_BROWSER="1",
               CLIPRADAR_UPDATE_API=f"http://127.0.0.1:{GH_PORT}/repos/allen1versonfr/vodclipper/releases/latest",
               CLIPRADAR_TWITCH_ID=f"http://127.0.0.1:{TW_PORT}", CLIPRADAR_TWITCH_GQL=f"http://127.0.0.1:{TW_PORT}/gql",
               CLIPRADAR_EMOTE_API=f"http://127.0.0.1:{TW_PORT}",
               CLIPRADAR_TEST="1", CLIPRADAR_WAIT_POLL="3")
    with open(os.path.join(data, "tiktok.dat"), "wb") as f:     # left by 4.3's TikTok API sign-in
        f.write(b"plain:e30=")
    cmd = shlex.split(a.app, posix=(os.name != "nt")) + ["--browser"]

    def start_app():
        kw = {} if os.name == "nt" else {"start_new_session": True}
        out = open(os.environ["VC_E2E_LOG"], "a") if os.environ.get("VC_E2E_LOG") else subprocess.DEVNULL
        return subprocess.Popen(cmd, env=env, stdout=out, stderr=subprocess.STDOUT if out is not subprocess.DEVNULL else subprocess.DEVNULL, **kw)

    def kill_app(p):
        """Hard kill, like a crash or power cut: no chance to save on the way out."""
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                p.kill()
        try:
            p.wait(timeout=20)
        except Exception:
            pass

    proc = [start_app()]

    def restart():
        kill_app(proc[0])
        time.sleep(1.5)
        proc[0] = start_app()
        return wait(lambda: _try(lambda: get("/api/status")), 90)

    producer = None
    try:
        st = wait(lambda: _try(lambda: get("/api/status")), 90)
        rec("app starts", st, f"version {st and st.get('version')}")
        if not st:
            return finish(a.out)
        # file-name hashtags are on by default; the name checks below are about titles and
        # templates, so they run without tags (export_checks turns them on for its own check)
        _try(lambda: post("/api/hashtags", {"file": {"enabled": False}}))

        # ------------------------------------------------------------ VOD: chat cleaning, smart cuts, deep scan, learning
        ctx = vod_checks(a, tmp)
        if ctx:
            persistence_checks(ctx, restart)
            review_checks(ctx)
            editing_checks(ctx)
            export_checks(ctx)
            framing_checks(a, tmp)
            multi_source_checks(a, tmp)

        # ------------------------------------------------------------ live mode
        src = os.path.join(tmp, "stream_src.mp4")
        make_stream_source(a.ffmpeg, src)
        # a pretend Twitch stream over HTTP, with an ad break stitched in at 100 s
        tw = fake_twitch.FakeTwitch(HLS_PORT, a.ffmpeg, src, hls, ad_at=100, ad_len=20)
        stream_url = tw.url
        opts = {"layout": "split", "cam": [0.015, 0.028, 0.25, 0.25], "captions": True,
                "whisper_model": a.model, "hw_encode": True}
        bad = post("/api/live/start", {"source": "not a channel!!"})
        rec("live rejects a bad channel", bad.get("_status") == 400, bad.get("error"))
        # hands-free, started before the stream is up: it should wait for it
        v = post("/api/live/start", {"source": stream_url, "chat_channel": "testchan", "length": 20, "warmup": 30,
                                     "buffer_minutes": 1, "hands_free": True, "wait_for_live": True,
                                     "frequency": "normal", "opts": opts, "model": a.model})
        sid = v.get("id")
        rec("live session starts", sid, v.get("status"))
        waiting = wait(lambda: get(f"/api/live/{sid}")["status"] == "waiting", 20)
        rec("waits for an offline stream to start", waiting, get(f"/api/live/{sid}")["status"])
        twice = post("/api/live/start", {"source": stream_url})
        rec("the same stream can't be clipped twice", twice.get("_status") == 409 and "already" in twice.get("error", ""), twice.get("error"))
        rec("busy while live", get("/api/status")["running"])
        tw.start()
        ok_rec = wait(lambda: get(f"/api/live/{sid}")["status"] == "recording", 60)
        lv = get(f"/api/live/{sid}")
        segdir0 = lv.get("seg_dir") or os.path.join(lv["dir"], "segments")
        rec("recording", ok_rec, f"status={lv['status']} recorded={lv['recorded']} files={sorted(os.listdir(segdir0))[:6] if os.path.isdir(segdir0) else 'none'}")
        if not ok_rec:
            diag["live log (early)"] = lv.get("log", [])
            lst = [f for f in os.listdir(segdir0) if f.endswith(".csv")] if os.path.isdir(segdir0) else []
            if lst:
                diag["segment list"] = open(os.path.join(segdir0, lst[0]), encoding="utf-8", errors="replace").read()[-600:]
        rec("chat connected", wait(lambda: get(f"/api/live/{sid}")["chat_connected"], 30))
        # a second stream at the same time: a YouTube livestream (resolved to the same test stream)
        # its chat as yt-dlp records a live chat (offset next to the action, real timestamps)
        ytchat = os.path.join(tmp, "yt_live_chat.jsonl")
        with open(ytchat, "w", encoding="utf-8") as f:
            for k in range(240):
                f.write(json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
                    "liveChatTextMessageRenderer": {"message": {"runs": [{"text": "this is so funny "},
                                                                         {"emoji": {"emojiId": "😂", "isCustomEmoji": False}}]},
                                                    "authorName": {"simpleText": f"ytfan{k % 40}"}}}}}]},
                    "videoOffsetTimeMsec": str(k * 500), "isLive": True}, ensure_ascii=False) + "\n")
        post("/api/test/yt_hls", {"url": stream_url, "chat": ytchat})
        yv = post("/api/live/start", {"source": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "length": 20, "warmup": 30,
                                      "buffer_minutes": 1, "frequency": "normal", "opts": opts, "model": a.model})
        ysid = yv.get("id")
        yrec = ysid and wait(lambda: get(f"/api/live/{ysid}")["status"] == "recording", 60)
        yl = get(f"/api/live/{ysid}") if ysid else {}
        rec("a YouTube livestream records alongside the Twitch one", yrec and yl.get("title") == "testchannel (live)"
            and len(get("/api/status").get("lives", [])) == 2,
            {k: yl.get(k) for k in ("status", "title", "chat_state", "error")} if ysid else yv.get("error"))
        ychat = ysid and wait(lambda: (lambda v: v if v.get("chat_total", 0) >= 10 and v.get("chat_state") == "ok" else None)(
            get(f"/api/live/{ysid}")), 30)
        yl = get(f"/api/live/{ysid}") if ysid else {}
        rec("a YouTube livestream's chat is read", bool(ychat),
            {k: yl.get(k) for k in ("chat_state", "chat_total", "chat_connected")})
        third = post("/api/live/start", {"source": "twitch.tv/someoneelse"})
        rec("live streams at once is limited (Settings › Performance)", third.get("_status") == 409 and "at once" in third.get("error", ""),
            third.get("error"))
        dash = get("/api/sources")
        rec("Running now lists both streams", sum(1 for x in dash["items"] if x["type"] == "live" and x["status"] == "recording") == 2,
            [(x["type"], x["status"], x["title"]) for x in dash["items"]])
        if ysid:
            post(f"/api/live/{ysid}/stop")
            rec("stopping one stream leaves the other recording", wait(lambda: get(f"/api/live/{ysid}")["status"] == "stopped", 30)
                and get(f"/api/live/{sid}")["status"] == "recording", (get(f"/api/live/{ysid}")["status"], get(f"/api/live/{sid}")["status"]))
        first = wait(lambda: (lambda c: c if c else None)(get(f"/api/live/{sid}")["clips"]), 150, 2)
        rec("found a moment automatically", first and all(c["at"] >= 30 for c in first if not c.get("manual")),
            first and f"{first[0]['reason']} at {first[0]['at']}s")
        clipped = post(f"/api/live/{sid}/clip", {"seconds": 20})
        rec("clip-that request accepted", clipped.get("pending_manual", 0) >= 1 or len(clipped.get("clips", [])) >= 2,
            clipped.get("error", ""))
        two = wait(lambda: (lambda c: c if len(c) >= 2 else None)(get(f"/api/live/{sid}")["clips"]), 60, 2)
        rec("manual clip created", two and any(c.get("manual") for c in two))
        fr = post(f"/api/live/{sid}/settings", {"frequency": "lots"})
        rec("clip amount can be changed while live", fr.get("frequency") == "lots", fr.get("frequency"))
        post(f"/api/live/{sid}/settings", {"frequency": "normal"})
        hk = get("/api/status").get("hotkey", {})
        if hk.get("supported"):
            rec("clip hotkey is registered", hk.get("registered"), hk)
            before = len(get(f"/api/live/{sid}")["clips"])
            post("/api/test/hotkey")
            got = wait(lambda: len(get(f"/api/live/{sid}")["clips"]) > before, 60, 2)
            rec("clip hotkey clips the stream", got and get(f"/api/live/{sid}")["hotkey_clips"] >= 1,
                f"{len(get(f'/api/live/{sid}')['clips'])} moments")
        titled = wait(lambda: (lambda c: c if c and all(x["listened"] for x in c) else None)(
            get(f"/api/live/{sid}")["clips"]), 240, 2)
        rec("moments get titles", titled, titled and " | ".join(f"{c['title']} ({c.get('vibe') or '-'})" for c in titled))
        lvn = get(f"/api/live/{sid}")
        auto_c = [c for c in (titled or []) if not c.get("manual")]
        rec("live latency measured per stage", {"ingest", "detect", "context", "cut", "title"} <= set(lvn.get("latency") or {})
            and auto_c and {"detect", "cut", "title"} <= set(auto_c[0].get("lat") or {}),
            (lvn.get("latency"), auto_c and auto_c[0].get("lat")))
        rec("live status: chat health and delay", lvn.get("chat_state") == "ok" and lvn.get("delay_auto") and lvn.get("chat_delay") > 0,
            {k: lvn.get(k) for k in ("chat_state", "chat_delay", "delay_sure", "delay_auto")})
        mq = post(f"/api/live/{sid}/settings", {"min_quality": 60})
        rec("lowest score can be changed while live", mq.get("min_quality") == 60, mq.get("min_quality"))
        post(f"/api/live/{sid}/settings", {"min_quality": 0})
        # content check: a word on your blocked list makes hands-free hold the clip back
        cfg = post("/api/safety", {"custom": ["twitch"], "hold": True})
        held_clip = None
        if get(f"/api/live/{sid}")["status"] == "recording":
            n0 = len(get(f"/api/live/{sid}")["clips"])
            post(f"/api/live/{sid}/clip", {"seconds": 15})
            held_clip = wait(lambda: (lambda c: c[-1] if len(c) > n0 and c[-1]["listened"] else None)(
                get(f"/api/live/{sid}")["clips"]), 120, 2)
            queued = held_clip and any(i["pid"] == held_clip["pid"] for i in get("/api/queue")["items"])
            rec("content check holds a risky clip back", held_clip and held_clip.get("held") and not queued,
                held_clip and f"{held_clip.get('held')} | flags: {[f['label'] for f in (held_clip.get('safety') or {}).get('flags', [])]}")
        else:
            rec("content check holds a risky clip back", False, "stream ended before the test clip")
        post("/api/safety", {"custom": []})
        rendered = wait(lambda: (lambda q: q if q and all(i["status"] in ("done", "error") for i in q) else None)(
            [i for i in get("/api/queue")["items"] if i["pid"] in [c["pid"] for c in (titled or [])]]), 400, 3)
        okr = rendered and all(i["status"] == "done" for i in rendered) and len(rendered) >= 1
        import re as _re
        rec("file names are just the title", okr and all(not _re.match(r"^\d+ - ", i.get("name", "")) for i in rendered),
            rendered and [i.get("name") for i in rendered])
        rec("auto-render renders moments", okr,
            rendered and "; ".join(f"{i['status']} {i.get('name') or i.get('error')}" for i in rendered))
        sess = get(f"/api/live/{sid}")
        diag["live log"] = sess.get("log", [])
        segdir = sess.get("seg_dir") or os.path.join(sess["dir"], "segments")
        on_disk = len([f for f in os.listdir(segdir) if f.endswith(".ts")]) if os.path.isdir(segdir) else 0
        rec("rolling buffer limits disk use", sess["recorded"] < 100 or on_disk * 10 < sess["recorded"],
            f"{on_disk} segments on disk for {sess['recorded']:.0f}s recorded")
        wait(lambda: tw.done, 260, 2)
        time.sleep(4)
        tw.finish()
        ended = wait(lambda: get(f"/api/live/{sid}")["status"] in ("ended", "stopped"), 60)
        rec("notices the stream ended", ended, get(f"/api/live/{sid}")["status"])
        sess = get(f"/api/live/{sid}")
        rec("ad break left out of the recording", sess.get("ad_breaks", 0) >= 1 and sess.get("ads_skipped", 0) >= 15
            and not tw.ad_fetches() and abs(sess["recorded"] - tw.content_seconds) < 15,
            f"ads skipped {sess.get('ads_skipped')}s in {sess.get('ad_breaks')} break(s); ad files fetched "
            f"{len(tw.ad_fetches())}; recorded {sess['recorded']:.0f}s of {tw.content_seconds:.0f}s content; mode {sess.get('mode')}")
        rec("live chat ignores bots, commands and sub notices", sess.get("chat_ignored", 0) >= 3,
            f"{sess.get('chat_ignored')} ignored, {sess.get('chatters')} chatters")
        lens = [c["end"] - c["start"] for c in sess["clips"] if not c.get("manual")]
        rec("live moments get their own length", lens and all(9 <= x <= 95 for x in lens), [round(x, 1) for x in lens])
        rec("not busy after the stream ends", wait(lambda: not get("/api/status")["running"], 60))
        if ctx:
            storage_checks(ctx, tmp)

        # ------------------------------------------------------------ content check, failures and retry
        sc = post("/api/safety/scan", {"words": [{"start": 0, "end": 0.3, "word": "what"}, {"start": 0.4, "end": 0.8, "word": "the"},
                                                 {"start": 0.9, "end": 1.3, "word": "fuuuck"}], "title": "Call 555 123 4567"})
        rec("content check finds swearing and personal info", sorted({f["cat"] for f in sc.get("flags", [])}) == ["personal", "profanity"]
            and sc.get("level") == "high", [(f["label"], f["text"]) for f in sc.get("flags", [])])
        base = held_clip or (titled or [None])[0]
        if base:
            post("/api/test/fail_next_render")
            c = dict(base, rank=90, title="Retry me", words=None)
            it = post("/api/queue/add", {"pid": base["pid"], "clip": c, "opts": opts})
            failed = wait(lambda: (lambda q: q if q and q["status"] == "error" else None)(
                next((i for i in get("/api/queue")["items"] if i["id"] == it.get("id")), None)), 60, 1)
            rec("a failed render says why", failed and "drive is full" in failed["error"] and failed.get("detail"),
                failed and f"{failed['error']} || {failed.get('detail', '')[:80]}")
            rt = post("/api/queue/retry", {"id": it.get("id")})
            again = wait(lambda: (lambda q: q if q and q["status"] in ("done", "error") else None)(
                next((i for i in get("/api/queue")["items"] if i["id"] == it.get("id")), None)), 240, 2)
            rec("retry renders it, under the same name", rt.get("retried") == 1 and again and again["status"] == "done"
                and again.get("name") == "Retry me.mp4",
                again and (again.get("name") or again.get("error")))
            post("/api/safety", {"censor": "bleep"})
            s0 = base["start"]
            c = dict(base, rank=91, title="Bleep test", words=[{"start": s0 + 1.0, "end": s0 + 1.4, "word": "fuck", "g": "1"},
                                                              {"start": s0 + 1.4, "end": s0 + 1.8, "word": "yeah", "g": "1"}])
            it = post("/api/queue/add", {"pid": base["pid"], "clip": c, "opts": opts})
            bl = wait(lambda: (lambda q: q if q and q["status"] in ("done", "error") else None)(
                next((i for i in get("/api/queue")["items"] if i["id"] == it.get("id")), None)), 240, 2)
            rec("censoring bleeps flagged words", bl and bl["status"] == "done" and bl.get("censored") == 1
                and any(f["cat"] == "profanity" for f in (bl.get("safety") or {}).get("flags", [])),
                bl and f"{bl['status']} censored={bl.get('censored')} {bl.get('error', '')}")
            post("/api/safety", {"censor": "off"})

        # ------------------------------------------------------------ TikTok Studio handoff
        rec("the old TikTok API sign-in is removed on upgrade", not os.path.exists(os.path.join(data, "tiktok.dat")))
        gone = post("/api/tiktok/post", {"item": "x", "mode": "direct"})
        rec("posting through TikTok's API is gone", gone.get("_status") == 404, gone)
        done_items = [i for i in get("/api/queue")["items"] if i["status"] == "done"]
        if done_items:
            item = done_items[0]
            h = post("/api/handoff", {"item": item["id"]})
            rec("TikTok Studio handoff: caption ready and the upload page", h.get("url", "").startswith("https://www.tiktok.com/tiktokstudio")
                and h.get("caption") and h.get("file", "").endswith(item["name"]), {k: h.get(k) for k in ("url", "caption", "file", "_status")})
            q = next(i for i in get("/api/queue")["items"] if i["id"] == item["id"])
            rec("the clip says Studio was opened, never that it was posted", (q.get("handoff") or {}).get("at")
                and not q.get("tiktok"), {k: q.get(k) for k in ("handoff", "tiktok")})
        else:
            rec("TikTok Studio handoff: caption ready and the upload page", False, "no rendered clip")
        not_done = post("/api/handoff", {"item": "nope"})
        rec("handing off a clip that isn't rendered says so", not_done.get("_status") == 400, not_done)

        # ------------------------------------------------------------ Twitch sign-in (ad-free live recording)
        tws = get("/api/twitch")
        rec("Twitch starts signed out", not tws.get("signed_in"), tws)
        bad = post("/api/twitch/token", {"token": "short"})
        rec("Twitch rejects something that isn't a token", bad.get("_status") == 400, bad.get("error"))
        wrong = post("/api/twitch/token", {"token": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"})
        rec("Twitch rejects a token Twitch doesn't accept", wrong.get("_status") == 400 and "expired" in wrong.get("error", ""),
            wrong.get("error"))
        ok_tw = post("/api/twitch/token", {"token": "auth-token=" + mock_twitch_api.GOOD})
        rec("Twitch sign-in with a token", ok_tw.get("signed_in") and ok_tw.get("login") == "e2e_viewer" and ok_tw.get("turbo") is True,
            ok_tw)
        tstore = os.path.join(data, "twitch.dat")
        thead = open(tstore, "rb").read(6) if os.path.exists(tstore) else b""
        rec("Twitch sign-in stored encrypted" if os.name == "nt" else "Twitch sign-in stored",
            thead == b"dpapi:" if os.name == "nt" else thead, thead.decode(errors="replace"))
        out_tw = post("/api/twitch/logout")
        rec("Twitch sign-out", not out_tw.get("signed_in") and not os.path.exists(tstore), out_tw)

        # ------------------------------------------------------------ storage
        st = get("/api/storage")
        d = post("/api/delete", {"what": "vods"})
        rec("delete VODs clears live recordings", d.get("freed", 0) > 0 and get("/api/storage")["vods"]["bytes"] == 0,
            f"freed {d.get('freed', 0) // 1000} KB of {st['vods']['bytes'] // 1000} KB; couldn't delete: {d.get('failed')}")
        d = post("/api/delete", {"what": "clips"})
        rec("delete all clips", d.get("deleted", 0) >= 1 and get("/api/storage")["clips"]["count"] == 0, d)
        # ------------------------------------------------------------ updates
        u = post("/api/update/check")
        rec("update check finds a newer release", u.get("available") and u.get("latest") == "99.0.0" and u.get("state") == "idle",
            {k: u.get(k) for k in ("state", "available", "latest", "current", "error", "can_install")})
        rec("update notes are readable", "Something new" in u.get("notes", "") and "**" not in u.get("notes", "")
            and "Download" not in u.get("notes", ""), u.get("notes", "")[:120])
        rec("automatic update checks run", mock_github.STATE["hits"] >= 2, f"{mock_github.STATE['hits']} checks")
        vv = get("/api/update/versions?refresh=1")
        rec("All versions lists every release, newest first", [x["version"] for x in vv.get("versions", [])][:2] == ["99.0.0", "98.5.0"]
            and any(x["archive"] for x in vv["versions"]) and not any(x["version"] == "models-v1" for x in vv["versions"])
            and not any(x["installable"] for x in vv["versions"] if not x["newer"]), [(x["version"], x["newer"], x["archive"]) for x in vv.get("versions", [])])
        old = post("/api/update/install", {"version": "1.0.0"})
        rec("an older version can't be installed", old.get("_status") == 400 and "newer" in old.get("error", ""), old.get("error"))
        i = post("/api/update/install")
        rec("a copy without Setup won't try to install", i.get("_status") == 400 and "installed" in i.get("error", ""), i.get("error"))
        a1 = post("/api/update/settings", {"auto": False})
        a2 = get("/api/update")
        post("/api/update/settings", {"auto": True})
        rec("automatic checks can be turned off", a1.get("auto") is False and a2.get("auto") is False, a2.get("auto"))
        mock_github.STATE["tag"] = "v1.0.0"
        u = post("/api/update/check")
        rec("older release isn't offered", u.get("available") is False and u.get("state") == "idle", u.get("latest"))
        mock_github.STATE["tag"] = "nightly"
        u = post("/api/update/check")
        rec("odd release tag is reported, not crashed on", u.get("state") == "error" and u.get("available") is False, u.get("error"))
        mock_github.STATE.update(tag="v99.0.0", token="github_pat_" + "e2e" * 10)
        u = post("/api/update/check")
        rec("private repository: explains the token", u.get("state") == "error" and "private" in u.get("error", ""), u.get("error"))
        bad_tok = post("/api/update/token", {"token": "hello"})
        rec("rejects something that isn't a token", bad_tok.get("_status") == 400, bad_tok.get("error"))
        u = post("/api/update/token", {"token": "github_pat_" + "e2e" * 10})
        rec("private repository: token finds the update", u.get("available") and u.get("token") and u.get("latest") == "99.0.0",
            {k: u.get(k) for k in ("state", "available", "latest", "token_hint", "error")})
        mock_github.STATE["moved"] = True
        u = post("/api/update/check")
        rec("renamed private repository: still finds the update", u.get("available") and not u.get("error"),
            {k: u.get(k) for k in ("state", "available", "latest", "error")})
        mock_github.STATE["moved"] = False
        post("/api/update/token", {"token": ""})
        mock_github.STATE["token"] = ""

        page = urllib.request.urlopen(B + "/", timeout=20).read()
        rec("UI loads", b"<html" in page.lower(), f"{len(page)} bytes")
    except Exception:
        rec("test crashed", False, traceback.format_exc()[-400:])
    finally:
        try:
            post("/api/quit")
        except Exception:
            pass
        time.sleep(3)
        for p in (producer, proc[0]):
            if p and p.poll() is None:
                p.kill()
        if keep_tmp:
            print("kept", tmp, flush=True)
            return finish(a.out)
        try:
            diag["app log"] = open(os.path.join(data, "clipradar.log"), encoding="utf-8", errors="replace").read().splitlines()[-40:]
        except OSError:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    return finish(a.out)


keep_tmp = bool(os.environ.get("VC_E2E_KEEP"))


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


def finish(out):
    ok = all(c["ok"] for c in checks) and bool(checks)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "checks": checks, "diag": diag}, f, indent=1)
    print("PASSED" if ok else "FAILED", f"{sum(c['ok'] for c in checks)}/{len(checks)}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
