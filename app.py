"""
VodClipper desktop entry point (this is what VodClipper.exe runs).

Opens VodClipper in its own app window (Edge WebView2, built into Windows 10/11).
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

# A windowed .exe has no console: send output to a log file so errors can be reported.
if sys.stdout is None or sys.stderr is None or paths.FROZEN:
    try:
        if paths.LOG_FILE.exists() and paths.LOG_FILE.stat().st_size > 2_000_000:
            paths.LOG_FILE.replace(paths.LOG_FILE.with_suffix(".old.log"))
        _log = open(paths.LOG_FILE, "a", encoding="utf-8", buffering=1)
        _log.write(f"\n===== VodClipper started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        if sys.stdout is None or paths.FROZEN:
            sys.stdout = _log
        if sys.stderr is None or paths.FROZEN:
            sys.stderr = _log
    except OSError:
        pass

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
        "VodClipper", url, width=1440, height=920, min_size=(900, 620),
        background_color="#141A2E", text_select=True)

    def native_pick(kind: str) -> str:
        types = (("Chat logs (*.json;*.gz)", "All files (*.*)") if kind == "chat" else
                 ("Videos (*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm;*.m4v;*.avi)", "All files (*.*)"))
        res = window.create_file_dialog(getattr(webview, "FileDialog", webview).OPEN
                                        if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG,
                                        allow_multiple=False, file_types=types)
        if not res:
            return ""
        return res[0] if isinstance(res, (list, tuple)) else str(res)

    server.picker_impl[0] = native_pick
    server.MODE["window"] = True
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
            p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL, creationflags=engine.CREATE_NO_WINDOW)
            first = (p.stdout or p.stderr).strip().splitlines()[:1]
            rec(f"tool {tool}", True, f"{exe} | {first[0] if first else 'ran'}")
        except Exception as e:
            rec(f"tool {tool}", False, e)

    rec("ui bundled", (paths.RES_DIR / "ui" / "index.html").is_file(), paths.RES_DIR)
    try:
        rec("encoder", True, engine.video_encoder(engine.RenderOpts())[0])
    except Exception as e:
        rec("encoder", False, e)

    if media and os.path.isfile(media):
        try:
            info = engine.probe(media)
            # fake chat burst at 30s so the chat path is exercised too
            chat = [(t * 0.7, "hi") for t in range(80)] + [(33 + i * 0.05, "KEKW clip it") for i in range(120)]
            an = engine.analyze(media, sorted(chat))
            clips = engine.pick_clips(an, count=3, length=15, chat=chat)
            rec("analyze", bool(clips), f"{info['duration']:.0f}s video, top clip "
                f"{engine.fmt_ts(clips[0].start)}-{engine.fmt_ts(clips[0].end)} ({clips[0].reason})" if clips else "no clips")
            heard = []
            real = engine.transcribe

            def spy(*a, **k):
                w = real(*a, **k)
                heard.extend(x["word"] for x in w)
                return w
            engine.transcribe = spy
            out = os.path.join(os.path.dirname(out_json), "selftest_clip.mp4")
            start = 26.0
            engine.render_clip(media, start, start + 14, out,
                               engine.RenderOpts(layout="split", cam=(0.02, 0.03, 0.25, 0.25),
                                                 whisper_model="tiny", language="en"),
                               title="Self test", log=lambda m: print("  " + m, flush=True))
            oi = engine.probe(out)
            rec("render 1080x1920", (oi["width"], oi["height"]) == (1080, 1920) and oi["has_audio"],
                f"{oi['width']}x{oi['height']}, {oi['duration']:.1f}s, audio={oi['has_audio']}")
            rec("captions (Whisper)", len(heard) >= 3, " ".join(heard)[:300] or "no words heard")
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
    print(f"VodClipper {server.VERSION} at {url}; clips folder {server.WORKROOT}", flush=True)
    if "--browser" in sys.argv or not run_window(url):
        run_browser(url)


if __name__ == "__main__":
    main()
