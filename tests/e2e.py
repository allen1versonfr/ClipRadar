"""
End-to-end test of a running VodClipper: live clipping and TikTok posting.

It starts the app itself (the built .exe or `python app.py`), a simulated
Twitch stream (ffmpeg writing a live HLS playlist in real time), a fake Twitch
chat server, and a mock TikTok API, then drives the app over its HTTP API
exactly like the UI does.

  python tests/e2e.py --app "dist/VodClipper/VodClipper.exe" --ffmpeg tools/ffmpeg.exe --out report.json
  python tests/e2e.py --app "python app.py" --ffmpeg ffmpeg --out report.json
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
import mock_tiktok   # noqa: E402

PORT, IRC_PORT, TT_PORT = 7870, 9461, 9471
B = f"http://127.0.0.1:{PORT}"
checks: list = []


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


def make_stream_source(ffmpeg, path, seconds=150, burst=70):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out", default="e2e_report.json")
    ap.add_argument("--model", default="tiny")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="vc_e2e_")
    work, data, hls = os.path.join(tmp, "work"), os.path.join(tmp, "data"), os.path.join(tmp, "hls")
    for d in (work, data, hls):
        os.makedirs(d)
    fake_irc.start_in_thread(IRC_PORT, [72])
    mock_tiktok.start(TT_PORT)

    env = dict(os.environ, VODCLIPPER_WORK=work, VODCLIPPER_DATA=data, VODCLIPPER_PORT=str(PORT),
               VODCLIPPER_IRC=f"127.0.0.1:{IRC_PORT}:plain",
               VODCLIPPER_TIKTOK_API=f"http://127.0.0.1:{TT_PORT}",
               VODCLIPPER_TIKTOK_AUTH=f"http://127.0.0.1:{TT_PORT}/v2/auth/authorize/")
    cmd = shlex.split(a.app, posix=(os.name != "nt")) + ["--browser"]
    app = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    producer = None
    try:
        st = wait(lambda: _try(lambda: get("/api/status")), 90)
        rec("app starts", st, f"version {st and st.get('version')}")
        if not st:
            return finish(a.out)

        # ------------------------------------------------------------ live mode
        src = os.path.join(tmp, "stream_src.mp4")
        make_stream_source(a.ffmpeg, src)
        playlist = os.path.join(hls, "live.m3u8")
        producer = subprocess.Popen([a.ffmpeg, "-v", "error", "-re", "-i", src, "-c", "copy", "-f", "hls",
                                     "-hls_time", "2", "-hls_list_size", "8",
                                     "-hls_flags", "delete_segments+omit_endlist", playlist])
        wait(lambda: os.path.exists(playlist), 20, 0.5)
        time.sleep(3)
        opts = {"layout": "split", "cam": [0.015, 0.028, 0.25, 0.25], "captions": True,
                "whisper_model": a.model, "hw_encode": True}
        bad = post("/api/live/start", {"source": "not a channel!!"})
        rec("live rejects a bad channel", bad.get("_status") == 400, bad.get("error"))
        v = post("/api/live/start", {"source": playlist, "chat_channel": "testchan", "length": 20, "warmup": 30,
                                     "buffer_minutes": 1, "auto_render": True, "opts": opts, "model": a.model})
        sid = v.get("id")
        rec("live session starts", sid, v.get("status"))
        twice = post("/api/live/start", {"source": playlist})
        rec("only one live session at a time", twice.get("_status") == 409, twice.get("error"))
        rec("busy while live", get("/api/status")["running"])
        rec("recording", wait(lambda: get(f"/api/live/{sid}")["status"] == "recording", 60))
        rec("chat connected", wait(lambda: get(f"/api/live/{sid}")["chat_connected"], 30))
        first = wait(lambda: (lambda c: c if c else None)(get(f"/api/live/{sid}")["clips"]), 150, 2)
        rec("found a moment automatically", first, first and f"{first[0]['reason']} at {first[0]['at']}s")
        clipped = post(f"/api/live/{sid}/clip", {"seconds": 20})
        rec("clip-that request accepted", clipped.get("pending_manual", 0) >= 1 or len(clipped.get("clips", [])) >= 2,
            clipped.get("error", ""))
        two = wait(lambda: (lambda c: c if len(c) >= 2 else None)(get(f"/api/live/{sid}")["clips"]), 60, 2)
        rec("manual clip created", two and any(c.get("manual") for c in two))
        titled = wait(lambda: (lambda c: c if c and all(x["listened"] for x in c) else None)(
            get(f"/api/live/{sid}")["clips"]), 240, 2)
        rec("moments get titles", titled, titled and " | ".join(f"{c['title']} ({c.get('vibe') or '-'})" for c in titled))
        rendered = wait(lambda: (lambda q: q if q and all(i["status"] in ("done", "error") for i in q) else None)(
            [i for i in get("/api/queue")["items"] if i["pid"] in [c["pid"] for c in (titled or [])]]), 400, 3)
        okr = rendered and all(i["status"] == "done" for i in rendered) and len(rendered) >= 1
        rec("auto-render renders moments", okr,
            rendered and "; ".join(f"{i['status']} {i.get('name') or i.get('error')}" for i in rendered))
        sess = get(f"/api/live/{sid}")
        segdir = os.path.join(sess["dir"], "segments")
        on_disk = len([f for f in os.listdir(segdir) if f.endswith(".ts")]) if os.path.isdir(segdir) else 0
        rec("rolling buffer limits disk use", sess["recorded"] < 100 or on_disk * 10 < sess["recorded"],
            f"{on_disk} segments on disk for {sess['recorded']:.0f}s recorded")
        producer.wait(timeout=200)
        with open(playlist, "a") as f:
            f.write("#EXT-X-ENDLIST\n")
        ended = wait(lambda: get(f"/api/live/{sid}")["status"] in ("ended", "stopped"), 60)
        rec("notices the stream ended", ended, get(f"/api/live/{sid}")["status"])
        rec("not busy after the stream ends", wait(lambda: not get("/api/status")["running"], 60))

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
        else:
            rec("direct post publishes", False, "no rendered clip to post")
        post("/api/tiktok/disconnect")
        rec("TikTok disconnects", not get("/api/tiktok")["connected"])

        # ------------------------------------------------------------ storage
        st = get("/api/storage")
        d = post("/api/delete", {"what": "vods"})
        rec("delete VODs clears live recordings", d.get("freed", 0) > 0 and get("/api/storage")["vods"]["bytes"] == 0,
            f"freed {d.get('freed', 0) // 1000} KB of {st['vods']['bytes'] // 1000} KB")
        d = post("/api/delete", {"what": "clips"})
        rec("delete all clips", d.get("deleted", 0) >= 1 and get("/api/storage")["clips"]["count"] == 0, d)
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
        shutil.rmtree(tmp, ignore_errors=True)
    return finish(a.out)


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


def finish(out):
    ok = all(c["ok"] for c in checks) and bool(checks)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "checks": checks}, f, indent=1)
    print("PASSED" if ok else "FAILED", f"{sum(c['ok'] for c in checks)}/{len(checks)}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
