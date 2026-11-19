"""
Export VOD (vodexport.py): the copyright scan's music stretches (and how much of each is speech), the
export's settings, the flagged words queue, many muted spans as a commands file (engine.mute_commands), a
video opened only to export (a file on this PC, a VOD downloaded before), and, where FFmpeg is installed: a
whole video exported with music and flagged words muted, captions burned in and saved as a subtitle file;
nothing listened to with captions off; the picture copied when nothing is drawn on it; a music stretch sent
through the music remover with the rest of the sound untouched; and the exact preview. Whisper and the music
remover's model are stood in for.

  python tests/test_vodexport.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

import engine  # noqa: E402
import locations  # noqa: E402
import whisper  # noqa: E402
import server  # noqa: E402
import listening  # noqa: E402
import vodexport  # noqa: E402
import voicekeep  # noqa: E402
import songerase  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# ---- music stretches
m = np.zeros(120, dtype=np.float32)
m[10:40] = 0.9          # a song
m[44:60] = 0.8          # the same song after a 4 s break: one stretch
m[80:83] = 0.95         # a 3 s sound alert: not listed
sp = vodexport.music_spans(m, 120)
check("a song is one stretch, across a short break", len(sp) == 1, sp)
check("padded a second each side", sp and sp[0]["start"] == 9.0 and sp[0]["end"] == 61.0, sp)
check("with how sure it is", sp and 0.6 < sp[0]["confidence"] <= 0.9, sp)
check("silence has none", vodexport.music_spans(np.zeros(50), 50) == [])
check("nothing heard has none", vodexport.music_spans(None, 0) == [])
m2 = np.zeros(30, dtype=np.float32)
m2[20:] = 1.0
check("music to the end stops at the end", vodexport.music_spans(m2, 30)[0]["end"] == 30, vodexport.music_spans(m2, 30))

sp2 = vodexport.music_spans(np.r_[np.zeros(5), np.ones(20), np.zeros(5)], 30,
                           speech=np.r_[np.zeros(5), np.ones(10), np.zeros(15)])
check("a stretch says how much of it is speech", sp2 and sp2[0]["speech"] == 0.5, sp2)
check("no speech heard is none", sp[0]["speech"] == 0.0, sp)
for conf, spk in ((0.9, 0.0), (0.9, 0.8), (0.5, 0.3), (0.3, 1.0)):
    for can in (True, False):
        act = vodexport.suggest_action(conf, spk, can)
        check(f"a stretch starts as one of the choices ({conf}, {spk}, remover {can})", act in vodexport.MUSIC_ACTIONS, act)
sa = vodexport.suggest_action
check("talking over music: the music is removed", sa(0.9, 0.7, True) == "voice")
check("no talking: muted (the same result, much quicker)", sa(0.9, 0.0, True) == "mute")
check("no talking, with the song eraser: erased, so the game's sound stays (5.0)", sa(0.9, 0.0, True, True) == "voice")
check("no talking, the song eraser, a weak detection: still kept", sa(0.4, 0.0, True, True) == "keep")
check("no talking and a weak detection: kept", sa(0.4, 0.0, True) == "keep")
check("without the remover, a stretch with lots of talking isn't silenced", sa(0.9, 0.8, False) == "keep")
check("without the remover, a little talking is muted", sa(0.9, 0.3, False) == "mute")
voicekeep.session_impl[0] = None
shown =vodexport._scan_view({"spans": [{"start": 0, "end": 9, "confidence": 0.9, "speech": 0.9}], "at": 1})
check("without the music remover a stretch never starts as Remove music", shown["spans"][0]["suggest"] != "voice", shown)

# ---- settings
o = vodexport.clean_options({"captions": "nope", "words": "bleep", "size": 720, "quality": "high",
                             "music": [[5, 9], ["x", 1], [50, 900, "voice"], [3, 3.05], [20, 30, "nonsense"]],
                             "word_spans": [[12, 12.4], [200, 201], ["a", "b"]],
                             "style": {"caption_size": 80, "caption_style": "box", "layout": "cam", "words_per_line": 4}}, 100)
check("an unknown captions choice is burned in", o["captions"] == "burn")
check("music spans are kept inside the video with what to do, bad ones dropped",
      o["music"] == [(5.0, 9.0, "mute"), (20.0, 30.0, "mute"), (50.0, 100.0, "voice")], o["music"])
check("flagged word spans are kept inside the video", o["word_spans"] == [(12.0, 12.4)], o["word_spans"])
check("size and quality are read", o["size"] == "720" and o["quality"] == "high" and o["words"] == "bleep", o)
check("no size asked is the video's own", vodexport.clean_options({}, 100)["size"] == "source")
check("the caption style is taken, and only caption settings", o["style"]["caption_style"] == "box" and "layout" not in o["style"], o["style"])
ro = vodexport.render_opts(server, o, {"width": 1920, "height": 1080})
check("a 16:9 video is shown whole in a wide frame", ro.layout == "full" and ro.aspect == "16:9" and ro.track == "off")
check("captions look the way the screen chose", ro.caption_style == "box" and ro.caption_size == 80 and ro.words_per_line == 4, ro)
check("a 4:3 video sits on a blurred background", vodexport.render_opts(server, o, {"width": 1440, "height": 1080}).layout == "blur")
check("the stream keeps its own volume", ro.loudness == 0.0)
src = vodexport.clean_options({"captions": "off"}, 100)
check("the video's own size: a 720p video stays 720p", abs(vodexport.render_opts(server, src, {"width": 1280, "height": 720}).scale - 0.6667) < 0.01)
check("the picture is copied when nothing is drawn on it", vodexport.can_copy(src, {"vcodec": "h264"}))
check("not with captions burned in", not vodexport.can_copy(vodexport.clean_options({"captions": "burn"}, 100), {"vcodec": "h264"}))
check("not at another size", not vodexport.can_copy(vodexport.clean_options({"captions": "off", "size": "720"}, 100), {"vcodec": "h264"}))
check("not a kind of video an .mp4 can't hold", not vodexport.can_copy(src, {"vcodec": "vp9"}))

# ---- many spans as commands
cmds = engine.mute_commands([(1, 2, "mute"), (1.5, 3, "bleep"), (5, 6, "mute")])
lines = cmds.strip().splitlines()
check("overlapping spans are joined, a bleep winning", len(lines) == 2 and lines[0].startswith("1.000-3.000")
      and "volume@bp volume 0.6" in lines[0], cmds)
check("a mute only silences", "volume@bp" not in lines[1] and "volume@cm volume 0" in lines[1], cmds)
g, c = engine.mute_graph([(1, 2, "mute")], 10, "anull", "1:a:0")
check("a few spans sit in the graph, reading the sound it's told to", g.startswith("[1:a:0]volume=0") and c == "" and g.endswith("[a]"), g)

# ---- the flagged words queue
words = [{"start": 1.0, "end": 1.3, "word": "this"}, {"start": 1.4, "end": 1.8, "word": "fucking"},
         {"start": 1.9, "end": 2.2, "word": "game"}]
fl = vodexport.find_flags(words)
check("a swear word is listed with where it is", len(fl) == 1 and fl[0]["start"] == 1.4 and fl[0]["end"] == 1.8, fl)
check("masked, with the words around it", fl and "fucking" not in fl[0]["context"] and "this" in fl[0]["context"]
      and "game" in fl[0]["context"], fl)
two = vodexport.find_flags(words + [{"start": 2.3, "end": 2.6, "word": "shit"}])
check("a flagged word next to another is masked in both lines", len(two) == 2 and
      all("fucking" not in f["context"] and "shit" not in f["context"] for f in two), two)
check("nothing flagged is an empty queue", vodexport.find_flags([{"start": 0, "end": 1, "word": "hello"}]) == [])
masked = vodexport.mask_spoken(words, [(1.35, 1.85)])
check("a muted word is masked in the captions, the rest isn't", masked[1]["word"] != "fucking" and masked[0]["word"] == "this", masked)

# ---- the music remover's plumbing, with a stand-in for the model


class Same:
    """What goes in comes out: the voice is everything."""
    dim_f, dim_t = 3072, 256

    def run(self, x):
        return x


class Nothing(Same):
    """No voice at all: whatever goes through the model comes out silent."""
    calls = 0

    def run(self, x):
        Nothing.calls += 1
        return np.zeros_like(x)


voicekeep.session_impl[0] = Same()
sep = voicekeep.Separator()
rng = np.random.default_rng(1)
ctx = (rng.standard_normal((2, sep.chunk)) * 0.1).astype(np.float32)
# below the model's top frequency (3072 of 3841 bins): what's above it is dropped by design
spec = voicekeep.stft(ctx)
spec[:, 3072:] = 0
ctx = voicekeep.istft(spec, sep.chunk)
got = sep.block(ctx) / voicekeep.GAIN
want = ctx[:, sep.trim:sep.trim + sep.gen]
check("sound through the transform and back is the same sound", float(np.abs(got - want).max()) < 2e-3, float(np.abs(got - want).max()))
m = voicekeep.blend(1000, 500, [(1200, 1400)], 50)
check("the voice fades in and out at a stretch's edges", m[0] == 0 and m[225] == 0.5 and m[300] == 1 and m[-1] == 0, (m[225], m[300]))
check("a block no stretch touches is left alone", voicekeep.blend(0, 500, [(1200, 1400)], 50) is None)


# ---- the song eraser (5.0): a stand-in model that says how much of each moment is music
class Stems:
    """music: the share of the sound that's music (the rest is split between dialogue and effects)."""
    calls = 0

    def __init__(self, music=0.0):
        self.music = music

    def run(self, spec):
        Stems.calls += 1
        re, im = spec[0, 0], spec[0, 1]
        k = [(1 - self.music) / 2, self.music, (1 - self.music) / 2]
        return np.stack([x for s in k for x in (re * s, im * s)])[None].astype(np.float32)


songerase.session_impl[0] = Stems(0.0)
ctx = (rng.standard_normal((2, songerase.SEG)) * 0.1).astype(np.float32)
got = songerase.erase_block(ctx)
check("song eraser: sound with no music in it comes through as it was", float(np.abs(got - ctx[:, songerase.CTX:songerase.CTX + songerase.GEN]).max()) < 2e-3,
      float(np.abs(got - ctx[:, songerase.CTX:songerase.CTX + songerase.GEN]).max()))
st = np.r_[ctx[:1], ctx[:1] * 0.2]                         # quieter on the right: stereo is kept, not made mono
got = songerase.erase_block(st)
check("and stereo stays stereo", float(np.abs(got[1] - st[1, songerase.CTX:songerase.CTX + songerase.GEN]).max()) < 2e-3)
songerase.session_impl[0] = Stems(1.0)
check("sound that's all song is erased", float(np.abs(songerase.erase_block(ctx)).max()) < 1e-4)
songerase.session_impl[0] = Stems(0.5)
left = lambda k: float(np.sqrt((songerase.erase_block(ctx, k) ** 2).mean()) / np.sqrt((ctx ** 2).mean()))  # noqa: E731
check("where the model is unsure, strength pushes the song down harder than the model's own share",
      left(songerase.STRENGTH) < left(1.0) - 0.05, (left(songerase.STRENGTH), left(1.0)))
m = songerase.keep_mask(np.ones((6, 3, 2), dtype=np.float32), strength=1.0)
check("the mask keeps dialogue and effects, as the model shares them out", abs(float(m.mean()) - 2 / 3) < 1e-5, float(m.mean()))
songerase.session_impl[0] = None

# ---- the copyright scan from what opening the video heard
PID = "vx1"
work = tempfile.mkdtemp(prefix="vc_vx_")
video = os.path.join(work, "vod.mp4")
an = engine.Analysis(duration=40.0, chat_z=np.zeros(41), audio_z=np.zeros(41), has_chat=False, has_audio=True,
                     events={"laugh": np.zeros(41), "music": np.r_[np.zeros(10), np.ones(15), np.zeros(16)],
                             "speech": np.r_[np.zeros(10), np.ones(15), np.zeros(16)]})
server.projects[PID] = {"workdir": work, "video": video, "title": "My stream", "analysis": an,
                        "info": {"has_audio": True, "duration": 40.0}, "tx_lock": threading.Lock()}
open(video, "wb").close()
res = vodexport.scan_copyright(server.projects[PID])
check("the scan uses what opening the video heard", res["how"].startswith("heard when") and len(res["spans"]) == 1, res)
check("and how much of the music has speech over it", res["spans"][0]["speech"] == 1.0, res)
server.projects[PID].pop("copyright")
check("and is remembered for this video", (vodexport.load_scan(server.projects[PID]) or {}).get("spans") == res["spans"])
v = vodexport.view(server)
mine = [x for x in v["videos"] if x["pid"] == PID]
check("the screen lists the open video with its scan", len(mine) == 1 and [s_["start"] for s_ in mine[0]["scan"]["spans"]]
      == [s_["start"] for s_ in res["spans"]] and "suggest" in mine[0]["scan"]["spans"][0], v)


# ---- the music as you left it on the screen (5.0): Segments uses it too
pr = server.projects[PID]
held, voicekeep.session_impl[0] = voicekeep.session_impl[0], None       # no remover here: Erase song falls back to muting
sug = vodexport.suggested(pr)[0]
want_ = [(s_["start"], s_["end"], s_["suggest"]) for s_ in vodexport._scan_view(vodexport.load_scan(pr))["spans"] if s_["suggest"] != "keep"]
check("with nothing changed, Segments uses what the scan suggests", sug == want_, (sug, want_))
vodexport.save_music(pr, {"at": res["at"], "songs": [{"start": 9.5, "end": 26, "act": "mute"}, {"start": 30, "end": 35, "act": "voice", "mine": True},
                                                    {"start": 36, "end": 36.2, "act": "mute"}, {"start": "x", "end": 3}]})
pr.pop("music_mine")
got = vodexport.load_music(pr)
check("what you set is kept with the video (a too-short or broken stretch isn't)", got and [s_["start"] for s_ in got["songs"]] == [9.5, 30], got)
check("and Segments uses it instead of the scan's suggestions", vodexport.suggested(pr)[0] == [(9.5, 26, "mute"), (30, 35, "mute")],
      vodexport.suggested(pr)[0])          # no eraser here, so Erase song falls back to muting
pr["copyright"] = dict(pr["copyright"], at=res["at"] + 10)
check("after a new scan, only the music you marked yourself is added to its suggestions",
      (30, 35, "mute") in vodexport.suggested(pr)[0] and (9.5, 26, "mute") not in vodexport.suggested(pr)[0], vodexport.suggested(pr)[0])
check("the screen gets it back", [x for x in vodexport.view(server)["videos"] if x["pid"] == PID][0]["music"]["songs"][1]["mine"])
pr.pop("music_mine")
os.remove(vodexport._music_file(pr))
pr["music_mine"] = None
pr["copyright"] = dict(pr["copyright"], at=res["at"])
voicekeep.session_impl[0] = held


def wait(job, s=180):
    end = time.time() + s
    while job["status"] == "running" and time.time() < end:
        time.sleep(0.1)
    return job


if shutil.which("ffmpeg"):
    os.remove(video)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", video], check=True)
    server.projects[PID]["info"] = engine.probe(video)
    said = [{"start": 30.0 + k * 0.3, "end": 30.25 + k * 0.3, "word": "fucking" if k == 2 else "word"} for k in range(6)]
    listened = []

    def fake_words(pr, a, b, *x, **k):
        listened.append((a, b))
        return [w for w in said if a <= w["start"] < b]
    listening.ensure_words = fake_words
    whisper.whisper_available = lambda: True
    listening._save_transcripts = lambda pr: None

    def rms(path, at, ln=0.25):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at}", "-t", f"{ln}", "-i", path, "-ac", "1",
                              "-f", "f32le", "-"], capture_output=True).stdout
        x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
        return float(np.sqrt((x ** 2).mean())) if x.size else -1.0

    # ---- the flagged words check, then an export with what it found
    job = wait(vodexport.start_words(server, PID, {"whisper_model": "small"}))
    fl = vodexport.load_flags(server.projects[PID]) or {}
    check("the check hears the video and lists the flagged word", job["status"] == "done" and len(fl.get("flags", [])) == 1
          and abs(fl["flags"][0]["start"] - 30.6) < 0.01, (job.get("error"), fl))
    check("and isn't stale", fl.get("stale") is False, fl)
    server.projects[PID].pop("vod_flags")
    check("it's remembered for this video", len((vodexport.load_flags(server.projects[PID]) or {}).get("flags", [])) == 1)
    flag = fl["flags"][0]
    # 30 short music spans: more than SPANS_INLINE, so they go to the commands file
    music = [[1.0 + i * 0.8, 1.4 + i * 0.8] for i in range(30)]
    job = vodexport.start_export(server, PID, {"captions": "both", "words": "mute", "music": music, "size": "480",
                                               "word_spans": [[flag["start"] - 0.05, flag["end"] + 0.05]],
                                               "quality": "fast", "hw_encode": False})
    wait(job)
    r = job.get("result") or {}
    check("the whole video exports", job["status"] == "done", (job.get("error"), job["log"][-5:]))
    if job["status"] == "done":
        info = engine.probe(r["path"])
        check("saved in the Exported VODs folder, apart from the clips (5.0)",
              os.path.normcase(r["folder"]) == os.path.normcase(locations.vods_dir()), r["folder"])
        check("all of it, in a wide frame", abs(info["duration"] - 40) < 0.6 and info["width"] / info["height"] > 1.7, info)
        check("at the size asked", info["height"] == 480, info)
        check("with a subtitle file next to it", r["srt"] and os.path.isfile(os.path.join(r["folder"], r["srt"])), r)
        srt = open(os.path.join(r["folder"], r["srt"]), encoding="utf-8").read()
        check("the muted word is masked in the captions", "fucking" not in srt and "word" in srt, srt[:300])
        check("music and the flagged word counted", r["muted_music"] == 30 and r["muted_words"] == 1 and not r["copied"], r)
        check("music stretches are silent", rms(r["path"], 1.05) < 0.005 and rms(r["path"], 9.05) < 0.005)
        check("between them the sound plays", rms(r["path"], 1.5, 0.2) > 0.05, rms(r["path"], 1.5, 0.2))
        check("the flagged word is silent", rms(r["path"], 30.62, 0.15) < 0.005, rms(r["path"], 30.62, 0.15))
        check("the rest plays", rms(r["path"], 36) > 0.05, rms(r["path"], 36))
        left = [f for f in os.listdir(r["folder"]) if f.startswith("vc_")]
        check("no working files left behind", not left, left)

    # ---- captions off: nothing is listened to, and the picture is copied
    del listened[:]
    job = wait(vodexport.start_export(server, PID, {"captions": "off", "music": [[5, 9, "mute"]],
                                                    "word_spans": [[flag["start"] - 0.05, flag["end"] + 0.05]]}))
    r = job.get("result") or {}
    check("with captions off the export runs", job["status"] == "done", (job.get("error"), job["log"][-5:]))
    check("and nothing is listened to", listened == [], listened)
    if job["status"] == "done":
        info = engine.probe(r["path"])
        check("the picture was copied, not encoded again", r["copied"] and info["width"] == 640 and info["height"] == 360
              and info["vcodec"] == "h264", (r, info))

        def frames(path):
            return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                   "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
        check("every frame of it", frames(video) == frames(r["path"]) and frames(video) != "", (frames(video), frames(r["path"])))
        check("all of it", abs(info["duration"] - 40) < 0.6, info)
        check("the music stretch is silent, the rest plays", rms(r["path"], 6) < 0.005 and rms(r["path"], 12) > 0.05,
              (rms(r["path"], 6), rms(r["path"], 12)))
        check("the flagged word ticked in the queue is still muted", rms(r["path"], 30.62, 0.15) < 0.005)
        check("no captions, no subtitle file", r["srt"] == "" and not r["captions"], r)
        check("no working files left behind after a copy", not [f for f in os.listdir(r["folder"]) if f.startswith("vc_")])

    # ---- a music stretch through the music remover: only that stretch goes through the model
    Nothing.calls = 0
    voicekeep.session_impl[0] = Nothing()
    job = wait(vodexport.start_export(server, PID, {"captions": "off", "music": [[10, 20, "voice"]]}))
    r = job.get("result") or {}
    check("a stretch set to Remove music exports", job["status"] == "done" and r.get("voice_music") == 1, (job.get("error"), r))
    if job["status"] == "done":
        check("that stretch went through the model", rms(r["path"], 14) < 0.005, rms(r["path"], 14))
        check("the sound before and after it didn't", rms(r["path"], 5) > 0.05 and rms(r["path"], 28) > 0.05,
              (rms(r["path"], 5), rms(r["path"], 28)))
        check("only the blocks the stretch touches were run", 1 <= Nothing.calls <= 3, Nothing.calls)
        info = engine.probe(r["path"])
        check("the whole video is still there", abs(info["duration"] - 40) < 0.6, info)
        tmp = vodexport.locations.temp_dir(os.path.basename(work), "vodexport")
        check("the working sound file is gone", not os.path.isdir(tmp) or not os.listdir(tmp))
    voicekeep.session_impl[0] = None
    job = wait(vodexport.start_export(server, PID, {"captions": "off", "music": [[10, 20, "voice"]]}))
    check("without a remover, Erase song says what to do", job["status"] == "error"
          and "song eraser" in (job.get("error") or ""), job.get("error"))

    # ---- a song through the song eraser: used ahead of the older remover, only that stretch
    Stems.calls = 0
    songerase.session_impl[0] = Stems(1.0)
    voicekeep.session_impl[0] = Nothing()
    Nothing.calls = 0
    job = wait(vodexport.start_export(server, PID, {"captions": "off", "music": [[10, 20, "voice"]]}))
    r = job.get("result") or {}
    check("a stretch set to Erase song exports", job["status"] == "done" and r.get("voice_music") == 1, (job.get("error"), r))
    if job["status"] == "done":
        check("the song eraser erased it", rms(r["path"], 14) < 0.005, rms(r["path"], 14))
        check("the sound around it is untouched", rms(r["path"], 5) > 0.05 and rms(r["path"], 28) > 0.05)
        check("only the blocks the stretch touches went through it", 1 <= Stems.calls <= 3, Stems.calls)
        check("the older remover wasn't used", Nothing.calls == 0, Nothing.calls)
    songerase.session_impl[0] = None
    voicekeep.session_impl[0] = None

    # ---- the exact preview: a few seconds made like the export
    voicekeep.session_impl[0] = Nothing()
    job = wait(vodexport.start_preview(server, PID, {"captions": "burn", "music": [[10, 20, "voice"]], "at": 16}))
    r = job.get("result") or {}
    check("the exact preview renders", job["status"] == "done" and r.get("url", "").startswith(f"/files/{PID}/preview/vod_"),
          (job.get("error"), r))
    if job["status"] == "done":
        pv = os.path.join(vodexport.locations.temp_dir(os.path.basename(work), "preview"), r["url"].rsplit("/", 1)[1])
        info = engine.probe(pv)
        check("ten seconds from where you are", abs(info["duration"] - 10) < 0.6 and r["start"] == 16, (info, r))
        check("with the music removed in it and the rest playing", rms(pv, 1) < 0.005 and rms(pv, 7) > 0.05, (rms(pv, 1), rms(pv, 7)))
    voicekeep.session_impl[0] = None

    # ---- a video opened only to export: a file anywhere on this PC, then listed as one opened before
    other = os.path.join(tempfile.mkdtemp(prefix="vc_drive_"), "Friday stream.mp4")
    shutil.copy(video, other)
    pid2 = vodexport.open_video(server, path=other)
    pr2 = server.projects[pid2]
    check("a file on this PC opens without being scored for clips", pr2.get("light") and "analysis" not in pr2
          and pr2["title"] == "Friday stream" and abs(pr2["duration"] - 40) < 0.6, {k: pr2[k] for k in ("title", "duration")})
    check("opening it again is the same video", vodexport.open_video(server, path=other) == pid2)
    v = vodexport.view(server)
    check("it's listed on the screen", pid2 in [x["pid"] for x in v["videos"]] and
          next(x for x in v["videos"] if x["pid"] == pid2)["light"], [x["pid"] for x in v["videos"]])
    job = wait(vodexport.start_export(server, pid2, {"captions": "off"}))
    check("and exports", job["status"] == "done" and os.path.isfile((job.get("result") or {}).get("path", "")), job.get("error"))
    server.projects.pop(pid2)
    lib = vodexport.library(server)
    check("closed, it's offered again from this PC", [x for x in lib if x["pid"] == pid2 and x["where"] == "file"
                                                      and x["title"] == "Friday stream"], lib)
    check("and opens by its id", vodexport.open_video(server, pid=pid2) == pid2 and pid2 in server.projects)
    for bad, why in ((os.path.join(work, "nope.mp4"), "isn't there"), (os.path.join(work, "copyright.json"), "isn't a video")):
        try:
            vodexport.open_video(server, path=bad)
            check(f"a bad file is refused ({why})", False)
        except RuntimeError as e:
            check(f"a bad file is refused ({why})", why in str(e), str(e))

    # ---- YouTube Studio: nothing to upload before an export, the file shown and the page opened after
    os.environ["CLIPRADAR_NO_BROWSER"] = "1"
    try:
        vodexport.to_youtube(server, pid2, {})
        check("no export yet: nothing to upload", False)
    except RuntimeError as e:
        check("no export yet: nothing to upload", "Export the video first" in str(e), str(e))
    del vodexport.studio.opened[:]
    got = vodexport.to_youtube(server, PID, {})
    check("after an export, the file is shown and YouTube's upload page opened", got.get("opened")
          and ("url", vodexport.YT_UPLOAD_URL) in vodexport.studio.opened
          and any(k == "reveal" for k, _ in vodexport.studio.opened), vodexport.studio.opened)
    check("a title YouTube takes", vodexport.studio_auto.youtube_title("  My <best>\n stream " + "x" * 200) ==
          ("My best stream " + "x" * 200)[:100].rstrip())
else:
    print("[skip] no ffmpeg here: the export isn't rendered")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
