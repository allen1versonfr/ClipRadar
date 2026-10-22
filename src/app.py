"""
Clip Radar desktop entry point (this is what ClipRadar.exe runs).

Opens Clip Radar in its own app window (Edge WebView2, built into Windows 10/11).
If that isn't available it falls back to your normal browser.

  python app.py              app window (or browser fallback)
  python app.py --browser    always use the browser
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

import paths

paths.ensure_dirs()
_fixed_paths = paths.fix_moved_paths()        # upgraded from VodClipper: saved paths follow the moved folders
paths.register_folders()

try:    # Settings › Storage › Logs (falls back to the default folder when that drive isn't there)
    import locations as _loc
    _lp = _loc.resolve("logs")["path"]
    if _lp:
        os.makedirs(_lp, exist_ok=True)
        paths.LOG_FILE = paths.Path(_lp) / "clipradar.log"
except Exception:
    pass

# A windowed .exe has no console: send output to a log file so errors can be reported.
if sys.stdout is None or sys.stderr is None or paths.FROZEN:
    try:
        if paths.LOG_FILE.exists() and paths.LOG_FILE.stat().st_size > 2_000_000:
            paths.LOG_FILE.replace(paths.LOG_FILE.with_suffix(".old.log"))
        _log = open(paths.LOG_FILE, "a", encoding="utf-8", buffering=1)
        _log.write(f"\n===== Clip Radar started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        if sys.stdout is None or paths.FROZEN:
            sys.stdout = _log
        if sys.stderr is None or paths.FROZEN:
            sys.stderr = _log
    except OSError:
        pass


class _Stamped:
    """Every line of the log starts with the time, so a slow or stuck step shows how long it took."""

    def __init__(self, stream):
        self._s, self._start = stream, True

    def write(self, text: str) -> int:
        if not text:
            return 0
        out = []
        for part in text.splitlines(keepends=True):
            if self._start:
                out.append(time.strftime("%H:%M:%S "))
            out.append(part)
            self._start = part.endswith(("\n", "\r"))
        self._s.write("".join(out))
        return len(text)

    def flush(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


if sys.stdout is not None:
    sys.stdout = _Stamped(sys.stdout)
if sys.stderr is not None:
    sys.stderr = _Stamped(sys.stderr)

for _old, _new in paths.MIGRATION.items():
    print(f"Moved {_old} to {_new}" if _new else f"Couldn't move {_old} yet (in use); using it for now", flush=True)
if _fixed_paths:
    print(f"Updated saved paths in {_fixed_paths} file(s)", flush=True)

import server  # noqa: E402  (after stdout is set up)

IDLE_MINUTES = 20   # browser mode only: quit when no tab has checked in for this long


def run_browser(url: str):
    server.MODE["window"] = False
    webbrowser.open(url)
    print(f"Running in browser mode at {url}", flush=True)
    while True:
        time.sleep(30)
        idle = time.time() - server.last_ping[0]
        if idle > IDLE_MINUTES * 60 and not server.busy():
            print("No browser tab open for a while, quitting.", flush=True)
            server.shutdown_all()


def run_window(url: str) -> bool:
    """Show the app in its own window. Returns False if no window could be made."""
    try:
        import webview
    except Exception as e:
        print(f"App window unavailable ({e}), using the browser.", flush=True)
        return False

    window = webview.create_window(
        "Clip Radar", url, width=1480, height=920, min_size=(1100, 700),
        background_color="#141518", text_select=True)

    def native_pick(kind: str) -> str:
        if kind == "folder":
            fd = getattr(webview, "FileDialog", None)
            res = window.create_file_dialog(fd.FOLDER if fd else webview.FOLDER_DIALOG)
            return (res[0] if isinstance(res, (list, tuple)) else str(res)) if res else ""
        types = (("Chat logs (*.json;*.gz)", "All files (*.*)") if kind == "chat" else
                 ("Images (*.png;*.jpg;*.jpeg;*.webp)", "All files (*.*)") if kind == "image" else
                 ("Videos (*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm;*.m4v;*.avi)", "All files (*.*)"))
        res = window.create_file_dialog(getattr(webview, "FileDialog", webview).OPEN
                                        if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG,
                                        allow_multiple=False, file_types=types)
        if not res:
            return ""
        return res[0] if isinstance(res, (list, tuple)) else str(res)

    server.picker_impl[0] = native_pick
    server.MODE["window"] = True

    def twitch_login(timeout: float) -> str:
        """Open twitch.tv's own login page in a small window; hand back the auth-token cookie once signed in."""
        closed = threading.Event()
        w = webview.create_window("Sign in to Twitch", "https://www.twitch.tv/login", width=520, height=780)
        w.events.closed += lambda *a: closed.set()
        token = ""
        end = time.time() + timeout
        while time.time() < end and not closed.is_set() and not token:
            time.sleep(1.0)
            try:
                for jar in w.get_cookies() or []:
                    for name, morsel in jar.items():
                        if name == "auth-token" and morsel.value:
                            token = morsel.value
            except Exception:
                pass
        if not closed.is_set():
            try:
                w.destroy()
            except Exception:
                pass
        return token

    import twitchauth
    if hasattr(window, "get_cookies"):
        twitchauth.login_impl[0] = twitch_login
    quitting = {"ok": False}

    def on_closing():
        if quitting["ok"] or not server.busy():
            return True
        # something is downloading/rendering: let the page ask the user
        threading.Thread(target=lambda: window.evaluate_js("window.askQuit && askQuit()"), daemon=True).start()
        return False

    def really_quit():
        quitting["ok"] = True
        try:
            window.destroy()
        except Exception:
            pass
        server.shutdown_all()

    window.events.closing += on_closing
    server.shutdown_hook[0] = really_quit

    try:
        kwargs = {"private_mode": False, "storage_path": str(paths.DATA_DIR / "webview")}
        if sys.platform == "win32":
            kwargs["gui"] = "edgechromium"
        try:
            webview.start(**kwargs)
        except TypeError:  # older pywebview without these options
            webview.start()
    except Exception as e:
        print(f"App window failed ({e}), using the browser.", flush=True)
        server.MODE["window"] = False
        server.picker_impl[0] = None
        server.shutdown_hook[0] = server.shutdown_all
        try:
            import twitchauth
            twitchauth.login_impl[0] = None
        except Exception:
            pass
        return False
    # window closed
    server.shutdown_all()
    return True


def selftest(out_json: str, media: str = "") -> int:
    """Checks the packaged app end to end. Used by the build; writes a JSON report."""
    import json
    import importlib
    import subprocess
    import traceback
    import engine

    checks = []

    def rec(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    for mod in ("numpy", "yt_dlp", "faster_whisper", "ctranslate2", "av", "webview", "tkinter", "certifi"):
        try:
            m = importlib.import_module(mod)
            rec(f"import {mod}", True, getattr(m, "__version__", ""))
        except Exception as e:
            rec(f"import {mod}", False, e)

    for tool, args in (("ffmpeg", ["-version"]), ("ffprobe", ["-version"]), ("yt-dlp", ["--version"]),
                       ("TwitchDownloaderCLI", ["--version"])):
        exe = engine.which(tool)
        if not exe:
            rec(f"tool {tool}", False, "not found")
            continue
        try:
            p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL, creationflags=engine.CREATE_NO_WINDOW)
            first = (p.stdout or p.stderr).strip().splitlines()[:1]
            rec(f"tool {tool}", True, f"{exe} | {first[0] if first else 'ran'}")
        except Exception as e:
            rec(f"tool {tool}", False, e)

    rec("ui bundled", (paths.RES_DIR / "ui" / "index.html").is_file(), paths.RES_DIR)
    try:
        rec("encoder", True, engine.video_encoder(engine.RenderOpts())[0])
    except Exception as e:
        rec("encoder", False, e)

    # the new 3.1 parts that don't need a video
    try:
        import chatsignal
        sc = chatsignal.ChatScorer()
        w = [sc.weight(1.0, "!enter", "a"), sc.weight(1.1, "Follow me on twitter", "nightbot"),
             sc.weight(1.2, "KEKW", "b"), sc.weight(1.5, "KEKW", "b"), sc.weight(1.6, "", "c", "subgift")]
        rec("chat cleaning", w[0] == 0 and w[1] == 0 and w[2] > 1 and w[3] < w[2] and w[4] == 0,
            f"weights {[round(x, 2) for x in w]}")
    except Exception:
        rec("chat cleaning", False, traceback.format_exc()[-300:])
    try:
        import learning
        f = {"chat": 1, "audio": 1, "laugh": 0, "scream": 0, "cheer": 0, "speech": 0, "length": 0.4, "clipreq": 0, "pos": 0.5}
        ok = learning._fit([{"f": dict(f, laugh=float(i % 2)), "y": float(i % 2), "w": 1.0} for i in range(20)])
        rec("learning model fits", ok and ok["w"][2] > 0, ok and f"laugh weight {ok['w'][2]:.2f}")
    except Exception:
        rec("learning model fits", False, traceback.format_exc()[-300:])

    try:
        import framing
        import numpy as _np
        if framing.available():
            f = _np.zeros((1, framing.IN_H, framing.IN_W, 3), dtype=_np.uint8) + 100
            framing.detect(f)      # loads and runs the face model
            rec("face finder runs", framing._sess() is not None, f"model {framing.model_path()}")
        else:
            rec("face finder runs", False, f"model missing (looked in {paths.RES_DIR / 'models' / 'face'})")
    except Exception:
        rec("face finder runs", False, traceback.format_exc()[-300:])

    if media and os.path.isfile(media):
        try:
            info = engine.probe(media)
            import audio_events
            if audio_events.available():
                ev = audio_events.scan_file(media, info["duration"])
                sp = float(max(ev["speech"][30:45].max(), ev["laugh"][30:45].max())) if len(ev["speech"]) > 45 else 0.0
                rec("laughter detector runs", len(ev["laugh"]) >= int(info["duration"]) and sp > 0.3,
                    f"voice heard {sp:.2f} at 0:30-0:45; laugh max {float(ev['laugh'].max()):.2f}, "
                    f"music max {float(ev['music'].max()):.2f}; model {audio_events.model_path()}")
            else:
                rec("laughter detector runs", False, f"model missing (looked in {paths.RES_DIR / 'models'})")
            # fake chat burst at 30s so the chat path is exercised too
            chat = [(t * 0.7, "hi") for t in range(80)] + [(33 + i * 0.05, "KEKW clip it") for i in range(120)]
            an = engine.analyze(media, sorted(chat))
            clips = engine.pick_clips(an, count=3, length=15, chat=chat)
            rec("analyze", bool(clips), f"{info['duration']:.0f}s video, top clip "
                f"{engine.fmt_ts(clips[0].start)}-{engine.fmt_ts(clips[0].end)} ({clips[0].reason})" if clips else "no clips")
            auto = engine.pick_clips(an, count=0, length=0, chat=chat)
            est = engine.estimate(an)
            rec("smart clip length", bool(auto) and all(engine.MIN_LEN - 0.5 <= c.end - c.start <= engine.MAX_LEN + 0.5
                                                        or c.end - c.start >= info["duration"] - 1 for c in auto),
                f"{[round(c.end - c.start, 1) for c in auto]} s; estimate {est}")
            heard, heard_words, rlog = [], [], []
            real = engine.transcribe

            def spy(*a, **k):
                w = real(*a, **k)
                heard.extend(x["word"] for x in w)
                heard_words.extend(w)
                return w
            engine.transcribe = spy
            out = os.path.join(os.path.dirname(out_json), "selftest_clip.mp4")
            start = 26.0
            engine.render_clip(media, start, start + 14, out,
                               engine.RenderOpts(layout="split", cam=(0.02, 0.03, 0.25, 0.25),
                                                 whisper_model="tiny", language="en"),
                               title="Self test", log=lambda m: (print("  " + m, flush=True), rlog.append(m)))
            oi = engine.probe(out)
            rec("render 1080x1920", (oi["width"], oi["height"]) == (1080, 1920) and oi["has_audio"],
                f"{oi['width']}x{oi['height']}, {oi['duration']:.1f}s, audio={oi['has_audio']}")
            rec("captions (Whisper)", len(heard) >= 3,
                (" ".join(heard)[:200] or "no words heard") + " || log: " + " / ".join(rlog)[-300:])
            import titles
            t = titles.generate(heard_words, [m for _, m in chat if _ > 30], peak_rel=6.0)
            # sentence-aligned cuts and the deep-scan scorer, on what Whisper heard
            absw = [dict(w, start=w["start"] + start, end=w["end"] + start) for w in heard_words]
            if absw:
                mid = absw[len(absw) // 2]
                a2, b2 = engine.refine_with_words(mid["start"] + 0.05, absw[-1]["end"] - 0.1, mid["start"], absw)
                import gems
                g, curve = gems.find(an, absw, [])
                rec("cuts land between sentences", a2 <= mid["start"] and b2 >= absw[-1]["end"] - 0.1,
                    f"{mid['start'] + 0.05:.2f}-{absw[-1]['end'] - 0.1:.2f} -> {a2:.2f}-{b2:.2f}; "
                    f"deep scan scored {int((curve > 0).sum())} s, {len(g)} quiet gems")
            rec("auto title", bool(t["title"]) and bool(t["quote"]),
                f"{t['title']} | vibe: {t['vibe'] or 'none'} | others: {' / '.join(t['titles'][1:4])}")
            # hand-edited captions, a bundled font, a highlight box and dragged positions
            fonts = sorted(p.name for p in engine.fonts_dir().glob("*.*") if p.suffix in (".ttf", ".otf"))
            rec("caption fonts bundled", len(fonts) >= 7, ", ".join(fonts))
            out2 = os.path.join(os.path.dirname(out_json), "selftest_styled.mp4")
            styled = engine.RenderOpts(layout="split", cam=(0.02, 0.03, 0.25, 0.25), caption_style="box",
                                       caption_font="montserrat", caption_color="#00FF00", caption_pos="custom",
                                       cap_x=0.35, cap_y=0.6, title_font="anton", title_style="dark",
                                       title_x=0.5, title_y=0.3)
            engine.render_clip(media, start, start + 3, out2, styled, title="Chat's \"best\" moment",
                               words=[{"start": 0.1, "end": 1.4, "word": "FIXED"}, {"start": 1.4, "end": 2.5, "word": "TEXT"}])
            # same clip without captions: whatever differs between the two frames is the captions
            out3 = os.path.join(os.path.dirname(out_json), "selftest_plain.mp4")
            plain = engine.RenderOpts(**{**styled.__dict__, "captions": False})
            engine.render_clip(media, start, start + 3, out3, plain, title="Chat's \"best\" moment", words=[])
            import numpy as np

            def frame(path):
                raw = subprocess.run([engine.ffmpeg(), "-v", "error", "-ss", "0.7", "-i", path, "-frames:v", "1",
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True,
                                     creationflags=engine.CREATE_NO_WINDOW).stdout
                return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(int)
            diff = np.abs(frame(out2) - frame(out3)).sum(axis=2) > 90
            ys, xs = np.nonzero(diff)
            where = f"{len(xs)} caption px" + (f" centred at {xs.mean() / 1080:.2f}, {ys.mean() / 1920:.2f}" if len(xs) else "")
            rec("styled captions render where they were placed", len(xs) > 3000 and abs(xs.mean() / 1080 - 0.35) < 0.08
                and abs(ys.mean() / 1920 - 0.6) < 0.05, where)
            # censoring: a bleep replaces the flagged word's audio
            import safety
            ws = [{"start": 0.2, "end": 0.6, "word": "okay"}, {"start": 1.0, "end": 1.5, "word": "fuck"},
                  {"start": 1.7, "end": 2.2, "word": "this"}]
            cfg = dict(safety.DEFAULTS, censor="bleep")
            res = safety.scan(ws, cfg=cfg)
            ws2, spans, mode = safety.censored(ws, res, cfg)
            out4 = os.path.join(os.path.dirname(out_json), "selftest_bleep.mp4")
            engine.render_clip(media, start, start + 3, out4, engine.RenderOpts(layout="split", hw_encode=False),
                               words=ws2, censor=spans, censor_mode=mode)
            pcm = np.frombuffer(subprocess.run([engine.ffmpeg(), "-v", "error", "-i", out4, "-ac", "1", "-ar", "16000",
                                                "-f", "f32le", "-"], capture_output=True, check=True,
                                               creationflags=engine.CREATE_NO_WINDOW).stdout, dtype=np.float32)

            def tone(a, b):
                seg = pcm[int(a * 16000):int(b * 16000)]
                sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
                f = np.fft.rfftfreq(len(seg), 1 / 16000)
                return float(sp[(f > 950) & (f < 1050)].sum() / max(sp.sum(), 1e-9))
            rec("content check bleeps flagged words", res["level"] == "low" and ws2[1]["word"] == "f***"
                and tone(1.05, 1.45) > 0.5 and tone(2.4, 2.9) < 0.3,
                f"flags {[f['cat'] for f in res['flags']]}, caption {ws2[1]['word']!r}, "
                f"1 kHz share {tone(1.05, 1.45):.2f} in the word vs {tone(2.4, 2.9):.2f} after")
        except Exception as e:
            rec("render pipeline", False, traceback.format_exc()[-400:])
    else:
        rec("render pipeline", False, f"test video missing: {media}")

    ok = all(c["ok"] for c in checks)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "version": server.VERSION, "checks": checks}, f, indent=1)
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        args = sys.argv[i + 1:] + ["", ""]
        os._exit(selftest(args[0] or str(paths.DATA_DIR / "selftest.json"), args[1]))
    srv, url = server.start()
    print(f"Clip Radar {server.VERSION} at {url}; clips folder {server.WORKROOT}", flush=True)
    if "--browser" in sys.argv or not run_window(url):
        run_browser(url)


if __name__ == "__main__":
    main()
