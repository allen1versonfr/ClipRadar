"""
Series (series.py, 4.51): who's in a clip, a series suggested when a name keeps coming back (and nothing done until
you say so), the next part numbered, the title and caption named the way Settings › Series says, the end card, and
the queue and Review's control on the real server functions.

  python tests/test_series.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

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

import series  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def W(text):
    return [{"start": i * 0.4, "end": i * 0.4 + 0.3, "word": w} for i, w in enumerate(text.split())]


# ---- who's in a clip
said = W("Wait, did Emily just take my car? Emily, give it back. Chat, Monday is wild. Okay. Jason here.")
chat = [(1.0, "emily is crazy", "a"), (1.1, "EMILY LMAO", "b"), (1.2, "kekw", "c")]
ppl = series.people(said, chat, "Jason")
check("a name said and typed is someone in the clip", ppl[:1] == ["Emily"], ppl)
check("not the streamer, a day, chat or a word starting a sentence", not {"Jason", "Monday", "Chat", "Okay", "Wait"} & set(ppl), ppl)
check("a name said once and typed by nobody isn't", series.people(W("so then Marlon left"), [], "Jason") == [])

# ---- suggestions: nothing until a name comes back, and off when switched off
check("one clip with Emily: no new series yet", series.suggest("Jason", ["Emily"], "p|1") is None)
series.seen("Jason", ["Emily"], "p|1")
sg = series.suggest("Jason", ["Emily"], "p|2")
check("the second: a new series suggested", sg and sg["new"] and sg["name"] == "Jason × Emily" and sg["part"] == 1, sg)
series.save_settings({"suggest": False})
check("Suggest series off: nothing suggested", series.suggest("Jason", ["Emily"], "p|2") is None)
series.save_settings({"suggest": True})

# ---- a series, its parts
s = series.create("", "Jason", ["Emily"])
check("a new series is named after who's in it", s["name"] == "Jason × Emily", s)
a = series.add(s["id"], "p|1", "Emily steals his car")
b = series.add(s["id"], "p|2", "Carne asada")
check("parts are numbered in order", (a["part"], b["part"]) == (1, 2), (a, b))
check("adding a clip again keeps its part", series.add(s["id"], "p|1")["part"] == 1)
sg = series.suggest("Jason", ["Emily"], "p|3")
check("a clip with Emily: the series suggested as the next part", sg and not sg["new"] and sg["id"] == s["id"] and sg["part"] == 3, sg)
check("a clip already in it: no suggestion", series.suggest("Jason", ["Emily"], "p|2") is None)
check("another streamer's clip with an Emily: not this series", (series.suggest("Kai", ["Emily"], "k|1") or {}).get("id") != s["id"])
series.remove(s["id"], "p|1")
check("taking one out keeps the others' numbers", series.add(s["id"], "p|9")["part"] == 3)

# ---- naming
ser = {"id": s["id"], "name": "Jason × Emily", "part": 6}
t, post, card = series.apply("Emily steals his car", "Who's right here? 😳\n\n#jason", ser)
check("before the title by default", t == "Jason × Emily Pt. 6: Emily steals his car", t)
check("the caption starts with the part and gets the series' tag", post.startswith("Jason × Emily Pt. 6\n") and post.endswith("#jasonxemily"), post)
check("no end card unless turned on", card == "")
check("applying twice doesn't add it twice", series.apply(t, post, ser)[:2] == (t, post))
series.save_settings({"naming": "after", "end_card": True})
t2, _, card = series.apply("Emily steals his car", "", ser)
check("after the title", t2 == "Emily steals his car | Jason × Emily Pt. 6", t2)
check("end card names the next part", card == "Follow for Pt. 7", card)
series.save_settings({"naming": "caption", "caption_line": False, "hashtag": False, "end_card": False})
check("caption only, and both caption extras off: nothing changes",
      series.apply("Emily steals his car", "x", ser) == ("Emily steals his car", "x", ""))
check("a clip in no series is unchanged", series.apply("T", "P", None) == ("T", "P", ""))
mk = series.end_card_mark("Follow for Pt. 7", 30.0)
check("the end card shows over the last seconds", mk["start"] == 27.5 and mk["kind"] == "text", mk)
series.save_settings({"naming": "before", "caption_line": True, "hashtag": True})

# ---- remembered
series.reset_cache()
check("series and settings are saved", any(x["id"] == s["id"] for x in series.all_series()) and series.settings()["naming"] == "before")

# ---- the server: Review's control, and the queue
import server  # noqa: E402

pr = {"workdir": os.path.join(os.environ["CLIPRADAR_WORK"], "vod1"), "video": "", "title": "Stream", "streamer": "Jason",
      "info": {"has_audio": True, "duration": 600}, "tx_lock": threading.Lock(), "source": "x",
      "clips": [{"id": 1, "start": 10.0, "end": 40.0, "title": "Emily steals his car", "post": "Who's right here?",
                 "people": ["Emily"]},
                {"id": 2, "start": 60.0, "end": 90.0, "title": "Other", "post": ""}]}
server.projects["sp1"] = pr
server.save_project_soon = lambda *a, **k: None
d = server.series_clip("sp1", 1, "new", name="")
check("Review: a new series from a clip", d["series"]["part"] == 1 and d["series"]["name"] == "Jason × Emily"
      and d["series"]["id"] != s["id"], d.get("series"))
d2 = server.series_clip("sp1", 2, "add", d["series"]["id"])
check("Review: another clip as the next part", d2["series"]["part"] == 2, d2.get("series"))
server.series_clip("sp1", 2, "remove")
check("Review: and out again", "series" not in pr["clips"][1])
series.save_settings({"end_card": True})
it = server.queue_add("sp1", dict(pr["clips"][0]), {"layout": "full"})
check("the queued clip carries the part in its title and caption",
      it["title"] == "Jason × Emily Pt. 1: Emily steals his car" and it["post"].startswith("Jason × Emily Pt. 1\n"), (it["title"], it["post"]))
check("and its end card", it.get("end_card") == "Follow for Pt. 2", it.get("end_card"))
with server.queue_lock:
    server.queue[:] = []

# ---- when a clip is titled: who's in it, and a suggestion (never put in a series by itself unless that's on)
server._streamer_name = lambda pr: "Jason"
d3 = {"id": 7, "start": 100.0, "end": 130.0, "title": "x"}
pr["clips"].append(d3)
sid1 = pr["clips"][0]["series"]["id"]
server._series_hint(pr, d3, said, chat)
check("titling a clip notes who's in it", d3.get("people", [])[:1] == ["Emily"], d3.get("people"))
check("and suggests the series they're in, without adding it", d3.get("series_hint", {}).get("id") in (s["id"], sid1)
      and "series" not in d3, d3.get("series_hint"))
series.save_settings({"auto_add": True})
d4 = {"id": 8, "start": 140.0, "end": 160.0, "title": "y"}
pr["clips"].append(d4)
server._series_hint(pr, d4, said, chat)
check("Add by itself on: it joins as the next part", (d4.get("series") or {}).get("part", 0) >= 2 and "series_hint" not in d4, d4)
series.save_settings({"auto_add": False})

print(f"\n{len(failures)} failed" if failures else "\nPASSED")
sys.exit(1 if failures else 0)
