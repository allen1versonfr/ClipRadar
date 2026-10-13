"""
End-to-end test of a running VodClipper: live clipping and TikTok posting.

It starts the app itself (the built .exe or `python src/app.py`), a simulated
Twitch stream (ffmpeg writing a live HLS playlist in real time), a fake Twitch
chat server, and a mock TikTok API, then drives the app over its HTTP API
exactly like the UI does.

  python tests/e2e.py --app "dist/VodClipper/VodClipper.exe" --ffmpeg tools/ffmpeg.exe --out report.json
  python tests/e2e.py --app "python src/app.py" --ffmpeg ffmpeg --out report.json
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
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
import fake_irc      # noqa: E402
import fake_twitch   # noqa: E402
import mock_tiktok   # noqa: E402
import mock_github   # noqa: E402
import mock_twitch_api  # noqa: E402

PORT, IRC_PORT, TT_PORT, HLS_PORT, GH_PORT, TW_PORT = 7870, 9461, 9471, 9481, 9491, 9501
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
           "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:duration={dur}",
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
        return
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
    # stable ids when re-ranking
    again = post("/api/repick", {"pid": pid, "count": 0, "length": 0, "chat_weight": 60})
    rec("re-ranking keeps clips (and their ids)", sorted(c["id"] for c in again.get("clips", [])) == sorted(c["id"] for c in clips),
        [c["id"] for c in again.get("clips", [])])
    add = post("/api/add_clip", {"pid": pid, "t": 62, "length": 0})
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out", default="e2e_report.json")
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--speech", default="", help="a WAV of someone talking, for the VOD and deep scan checks")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="vc_e2e_")
    work, data, hls = os.path.join(tmp, "work"), os.path.join(tmp, "data"), os.path.join(tmp, "hls")
    for d in (work, data, hls):
        os.makedirs(d)
    fake_irc.start_in_thread(IRC_PORT, [72])
    mock_tiktok.start(TT_PORT)
    mock_github.start(GH_PORT, "v99.0.0")
    mock_twitch_api.start(TW_PORT)

    env = dict(os.environ, VODCLIPPER_WORK=work, VODCLIPPER_DATA=data, VODCLIPPER_PORT=str(PORT),
               VODCLIPPER_IRC=f"127.0.0.1:{IRC_PORT}:plain",
               VODCLIPPER_TIKTOK_API=f"http://127.0.0.1:{TT_PORT}",
               VODCLIPPER_TIKTOK_AUTH=f"http://127.0.0.1:{TT_PORT}/v2/auth/authorize/",
               VODCLIPPER_UPDATE_API=f"http://127.0.0.1:{GH_PORT}/repos/allen1versonfr/vodclipper/releases/latest",
               VODCLIPPER_TWITCH_ID=f"http://127.0.0.1:{TW_PORT}", VODCLIPPER_TWITCH_GQL=f"http://127.0.0.1:{TW_PORT}/gql",
               VODCLIPPER_TEST="1", VODCLIPPER_WAIT_POLL="3")
    cmd = shlex.split(a.app, posix=(os.name != "nt")) + ["--browser"]
    app = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    producer = None
    try:
        st = wait(lambda: _try(lambda: get("/api/status")), 90)
        rec("app starts", st, f"version {st and st.get('version')}")
        if not st:
            return finish(a.out)

        # ------------------------------------------------------------ VOD: chat cleaning, smart cuts, deep scan, learning
        vod_checks(a, tmp)

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
        rec("only one live session at a time", twice.get("_status") == 409, twice.get("error"))
        rec("busy while live", get("/api/status")["running"])
        tw.start()
        ok_rec = wait(lambda: get(f"/api/live/{sid}")["status"] == "recording", 60)
        lv = get(f"/api/live/{sid}")
        segdir0 = os.path.join(lv["dir"], "segments")
        rec("recording", ok_rec, f"status={lv['status']} recorded={lv['recorded']} files={sorted(os.listdir(segdir0))[:6] if os.path.isdir(segdir0) else 'none'}")
        if not ok_rec:
            diag["live log (early)"] = lv.get("log", [])
            lst = [f for f in os.listdir(segdir0) if f.endswith(".csv")] if os.path.isdir(segdir0) else []
            if lst:
                diag["segment list"] = open(os.path.join(segdir0, lst[0]), encoding="utf-8", errors="replace").read()[-600:]
        rec("chat connected", wait(lambda: get(f"/api/live/{sid}")["chat_connected"], 30))
        first = wait(lambda: (lambda c: c if c else None)(get(f"/api/live/{sid}")["clips"]), 150, 2)
        rec("found a moment automatically", first, first and f"{first[0]['reason']} at {first[0]['at']}s")
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
        rec("auto-render renders moments", okr,
            rendered and "; ".join(f"{i['status']} {i.get('name') or i.get('error')}" for i in rendered))
        sess = get(f"/api/live/{sid}")
        diag["live log"] = sess.get("log", [])
        segdir = os.path.join(sess["dir"], "segments")
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
            rec("retry renders it", rt.get("retried") == 1 and again and again["status"] == "done",
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

        # ------------------------------------------------------------ TikTok
        rec("TikTok starts disconnected", not get("/api/tiktok")["connected"])
        post("/api/tiktok/config", {"client_key": "ck_e2e", "client_secret": "cs_e2e"})
        url = post("/api/tiktok/connect", {"open": False}).get("url", "")
        rec("TikTok sign-in link", "code_challenge=" in url and "%2Fcallback%2F" in url, url[:120])

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a_, **k):
                return None
        loc = ""
        try:
            urllib.request.build_opener(NoRedirect).open(url, timeout=20)
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
        page = urllib.request.urlopen(loc, timeout=30).read().decode() if loc else ""
        rec("TikTok sign-in completes", "TikTok connected" in page, loc[:80])
        ts = get("/api/tiktok")
        rec("TikTok account connected", ts["connected"] and ts["user"].get("nickname") == "Mock Creator", ts["user"])
        store = os.path.join(data, "tiktok.dat")
        head = open(store, "rb").read(6) if os.path.exists(store) else b""
        rec("tokens stored encrypted" if os.name == "nt" else "tokens stored", head == b"dpapi:" if os.name == "nt" else head,
            head.decode(errors="replace"))
        ci = post("/api/tiktok/creator")
        rec("creator info", "privacy_level_options" in ci, ci.get("privacy_level_options"))
        done_items = [i for i in get("/api/queue")["items"] if i["status"] == "done"]
        if done_items:
            item = done_items[0]["id"]
            j = post("/api/tiktok/post", {"item": item, "mode": "direct", "privacy": "SELF_ONLY",
                                          "title": "e2e test #fyp", "allow_comment": True})
            r = wait_job(j["job"], 120)
            rec("direct post publishes", r and r["status"] == "done" and r["result"]["status"] == "PUBLISH_COMPLETE",
                r and (r.get("error") or r["result"]))
            q = next(i for i in get("/api/queue")["items"] if i["id"] == item)
            rec("clip shows as posted", (q.get("tiktok") or {}).get("state") == "done", q.get("tiktok"))
            j = post("/api/tiktok/post", {"item": item, "mode": "draft"})
            r = wait_job(j["job"], 120)
            rec("send to drafts", r and r["status"] == "done" and r["result"]["status"] == "SEND_TO_USER_INBOX",
                r and (r.get("error") or r["result"]))
            mock_tiktok.STATE["unaudited"] = True
            j = post("/api/tiktok/post", {"item": item, "mode": "direct", "privacy": "PUBLIC_TO_EVERYONE"})
            r = wait_job(j["job"], 60)
            rec("unaudited public post gives a clear message", r and r["status"] == "error" and "Only me" in r["error"],
                r and r.get("error"))
            mock_tiktok.STATE["unaudited"] = False
            if base:
                it = post("/api/queue/add", {"pid": base["pid"], "clip": dict(base, rank=92, title="Draft me"),
                                             "opts": opts, "auto_draft": True})
                dr = wait(lambda: (lambda q: q if q and (q.get("tiktok") or {}).get("state") in ("done", "error") else None)(
                    next((i for i in get("/api/queue")["items"] if i["id"] == it.get("id")), None)), 300, 2)
                rec("hands-free sends finished clips to drafts", dr and dr["tiktok"]["state"] == "done"
                    and dr["tiktok"]["mode"] == "draft", dr and dr.get("tiktok"))
        else:
            rec("direct post publishes", False, "no rendered clip to post")
        post("/api/tiktok/disconnect")
        rec("TikTok disconnects", not get("/api/tiktok")["connected"])

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
            f"freed {d.get('freed', 0) // 1000} KB of {st['vods']['bytes'] // 1000} KB")
        d = post("/api/delete", {"what": "clips"})
        rec("delete all clips", d.get("deleted", 0) >= 1 and get("/api/storage")["clips"]["count"] == 0, d)
        # ------------------------------------------------------------ updates
        u = post("/api/update/check")
        rec("update check finds a newer release", u.get("available") and u.get("latest") == "99.0.0" and u.get("state") == "idle",
            {k: u.get(k) for k in ("state", "available", "latest", "current", "error", "can_install")})
        rec("update notes are readable", "Something new" in u.get("notes", "") and "**" not in u.get("notes", "")
            and "Download" not in u.get("notes", ""), u.get("notes", "")[:120])
        rec("automatic update checks run", mock_github.STATE["hits"] >= 2, f"{mock_github.STATE['hits']} checks")
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
        for p in (producer, app):
            if p and p.poll() is None:
                p.kill()
        if keep_tmp:
            print("kept", tmp, flush=True)
            return finish(a.out)
        try:
            diag["app log"] = open(os.path.join(data, "vodclipper.log"), encoding="utf-8", errors="replace").read().splitlines()[-40:]
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
