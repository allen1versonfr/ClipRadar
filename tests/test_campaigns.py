"""
Unit tests for working with clipping campaigns and clipping Discords: watermark layers (kept within
range, older settings carried over, images only from Clip Radar's own folder), watermark and
campaign files you can share and import (images inside, webhook only when you choose), a campaign's
post rules and length limits, its own watermark on the clips queued for it, and sending finished
clips to its Discord channel (by hand and as soon as they render).

  python tests/test_campaigns.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.environ["CLIPRADAR_DATA"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_WORK"] = tempfile.mkdtemp(prefix="vc_test_")
os.environ["CLIPRADAR_UPDATE_API"] = "http://127.0.0.1:9/none"
os.environ["CLIPRADAR_NO_BROWSER"] = "1"
os.environ["CLIPRADAR_FAKE_DISCORD"] = "1"
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import campaigns  # noqa: E402
import discordhook  # noqa: E402
import exports  # noqa: E402
import server  # noqa: E402
import watermarks as WM  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


# a 1x1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
TMP = tempfile.mkdtemp(prefix="vc_cp_")
logo = os.path.join(TMP, "logo.png")
with open(logo, "wb") as f:
    f.write(PNG)
HOOK = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEFG-123"

# ---- watermark layers
w = exports.clean(dict(exports.load(), watermark={"on": True, "text": "x" * 100, "pos": "zz", "opacity": 500, "size": 1}))["watermark"]
m = w["marks"][0] if w["marks"] else {}
check("settings from before 4.11 become one text layer", len(w["marks"]) == 1 and m["kind"] == "text", w)
check("kept within range", len(m.get("text", "")) == 60 and m.get("pos") == "tl" and m.get("opacity") == 100 and m.get("size") == 20, m)
img = WM.store_file(logo)
check("an image is kept as Clip Radar's own copy", os.path.dirname(img) == WM.image_dir() and os.path.isfile(img), img)
check("the same image twice is one file", WM.store_file(logo) == img)
w = WM.clean({"on": True, "marks": [
    {"kind": "image", "image": logo},
    {"kind": "image", "image": img, "pos": "free", "x": 3, "y": -1, "rotation": 999, "color": "red"},
    {"kind": "text", "text": "  "},
    {"kind": "text", "text": "@me", "font": "nope", "color": "00ff00"}]})
check("an image outside Clip Radar's folder isn't used (it couldn't be served or exported safely)",
      all(x["image"] != logo for x in w["marks"]), w["marks"])
check("empty layers are dropped", len(w["marks"]) == 2, w["marks"])
fr = w["marks"][0]
check("a free layer stays inside the frame and turns at most half a turn", (fr["x"], fr["y"], fr["rotation"]) == (1.0, 0.0, 180), fr)
check("colours and fonts are checked", w["marks"][1]["color"] == "#00FF00" and w["marks"][1]["font"] == "classic", w["marks"][1])
check("at most 6 layers", len(WM.clean_marks([{"kind": "text", "text": str(i)} for i in range(10)])) == 6)
try:
    WM.store_image(b"GIF89a....")
    check("anything but PNG, JPG or WebP is refused", False)
except ValueError:
    check("anything but PNG, JPG or WebP is refused", True)
check("off: no watermark in the render options", server._wm_opts({"on": False, "marks": [{"kind": "text", "text": "hi"}]}) == {})
check("on: the render gets the layers", server._wm_opts({"on": True, "marks": [{"kind": "text", "text": "hi", "pos": "br"}]})["wm_marks"][0]["pos"] == "br")
check("clips queued by older versions still get their mark", server._wm_opts({"on": True, "text": "hi", "pos": "br"})["wm_marks"][0]["text"] == "hi")

# ---- watermark files
pack = WM.make_pack("Kai clipping", [{"kind": "image", "image": img, "pos": "br"}, {"kind": "text", "text": "@kai"}])
check("a shared watermark carries its image inside", len(pack["images"]) == 1 and pack["marks"][0]["image"] in pack["images"], pack["marks"])
os.remove(img)
back = WM.read_pack(json.loads(json.dumps(pack)))
check("importing it restores the layers and the image", len(back["marks"]) == 2 and os.path.isfile(back["marks"][0]["image"]), back)
img = back["marks"][0]["image"]
bad = dict(pack, images={k: base64.b64encode(b"<script>").decode() for k in pack["images"]})
check("a layer whose image isn't really an image is left out", len(WM.read_pack(bad)["marks"]) == 1)
for junk, why in ((b"not json", "isn't JSON"), (json.dumps({"clipradar": "other"}).encode(), "another kind of file")):
    try:
        campaigns.import_any(junk)
        check(f"a file that {why} is refused", False)
    except ValueError as e:
        check(f"a file that {why} is refused, in plain words", "Clip Radar" in str(e), str(e))

# ---- campaigns
c = campaigns.clean_one({"name": "Kai Cenat clipping", "caption": "@kaicenat · Code KAI", "tags": "#ad, kaiclips #AD",
                         "min_len": 70, "max_len": 20, "discord": {"webhook": "https://evil.example/api/webhooks/1/x"}})
check("hashtags are cleaned and de-duplicated", c["tags"] == ["#ad", "#kaiclips"], c["tags"])
check("a minimum above the maximum is swapped", (c["min_len"], c["max_len"]) == (20, 70), (c["min_len"], c["max_len"]))
check("only real Discord webhooks are kept", c["discord"]["webhook"] == "", c["discord"])
check("discord.com, ptb and discordapp.com webhooks are recognised", all(discordhook.valid_webhook(u) for u in (
    HOOK, HOOK.replace("discord.com", "ptb.discord.com"), HOOK.replace("discord.com", "discordapp.com"), HOOK.replace("/api/", "/api/v10/"))))
post = campaigns.apply_post("Chat lost it #twitch #AD", c, "t")
check("the required text and hashtags are added once", post == "Chat lost it #twitch #AD\n@kaicenat · Code KAI #kaiclips", post)
check("adding them again changes nothing", campaigns.apply_post(post, c) == post)
check("at the start when the campaign says so",
      campaigns.apply_post("Hi", dict(c, caption_where="start")).startswith("@kaicenat · Code KAI\nHi"))
check("a clip with no post text gets its title", campaigns.apply_post("", c, "Big moment").startswith("Big moment\n"))
check("no campaign: the post is left alone", campaigns.apply_post("Hi", None) == "Hi")
check("too short a clip gets a warning", "at least 20 s" in campaigns.length_warning(c, 12))
check("too long a clip too", "at most 70 s" in campaigns.length_warning(c, 90))
check("a clip in range doesn't", campaigns.length_warning(c, 45) == "")
msg = campaigns.message(dict(c, discord={"message": "**{title}**\n{post_url}\n{campaign} {nope}"}), {"title": "Big moment"})
check("the Discord message fills in what it knows and drops empty lines", msg == "**Big moment**\nKai Cenat clipping {nope}", msg)

camp = campaigns.save(dict(c, discord={"webhook": HOOK, "auto": True, "file": True},
                           watermark={"use": True, "marks": [{"kind": "image", "image": img, "pos": "free", "x": .8, "y": .2}]}))
cid = camp["list"][0]["id"]
share = campaigns.make_pack(campaigns.get(cid))
check("a shared campaign leaves the webhook out", share["campaign"]["discord"]["webhook"] == "", share["campaign"]["discord"])
check("unless you choose to share it", campaigns.make_pack(campaigns.get(cid), with_webhook=True)["campaign"]["discord"]["webhook"] == HOOK)
check("and carries its watermark image inside", len(share["images"]) == 1)
r = campaigns.import_any(json.dumps(share).encode())
check("importing it adds a second campaign under a new name and id",
      r["kind"] == "campaign" and r["campaign"]["id"] != cid and r["campaign"]["name"] == "Kai Cenat clipping (2)", r["campaign"]["name"])
check("with its watermark", len(r["campaign"]["watermark"]["marks"]) == 1 and os.path.isfile(r["campaign"]["watermark"]["marks"][0]["image"]))
campaigns.delete(r["campaign"]["id"])

# ---- through the app: share files, import, queue for a campaign, send to Discord
OUT = tempfile.mkdtemp(prefix="vc_out_")


def fake_render(it):
    it.setdefault("folder", OUT)
    it["name"] = f"{it['title']}.mp4"
    it["path"] = os.path.join(OUT, it["name"])
    with open(it["path"], "wb") as f:
        f.write(b"video")
    it["post"] = campaigns.apply_post(it.get("post", ""), it.get("campaign"), it["title"])


server._render_item = fake_render
srv, url = server.start(7989)


def api(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


def wait_status(iid, want, timeout=15):
    end, it = time.time() + timeout, None
    while time.time() < end:
        it = next((x for x in api("/api/queue")["items"] if x["id"] == iid), None)
        if it and it["status"] in want:
            return it
        time.sleep(0.1)
    return it


r = api("/api/watermark/image", {"path": logo})
check("choosing an image for a layer keeps a copy and gives its preview address",
      r.get("url", "").startswith("/wmimg/") and os.path.isfile(r["image"]), r)
got = urllib.request.urlopen(url + r["url"], timeout=10).read()
check("the preview can show it", got == PNG)
try:
    urllib.request.urlopen(url + "/wmimg/..%2Fexport.json", timeout=10)
    check("only watermark images are served", False)
except urllib.error.HTTPError as e:
    check("only watermark images are served", e.code == 404, e.code)
r = api("/api/watermark/share", {"name": "Kai: clips?", "marks": [{"kind": "image", "image": img}]})
check("sharing a watermark saves one file in the Shared folder", os.path.isfile(r.get("path", "")) and r["name"] == "Kai clips - watermark.json", r)
r = api("/api/import", {"path": r["path"]})
check("importing that file saves it as a watermark you can pick", r.get("kind") == "watermark" and r["marks"]
      and any(p["name"] == "Kai: clips?" for p in r["export"]["prefs"]["wm_presets"]), r)
r = api("/api/import", {"path": logo})
check("importing a plain logo makes it an image layer", r.get("kind") == "image" and os.path.isfile(r["image"]), r)
r = api("/api/import", {"url": "http://example.com/x.json"})
check("links must be https", r.get("status") == 400 and "https" in r.get("error", ""), r)

r = api("/api/campaigns/test", {"webhook": "https://example.com/hook"})
check("testing something that isn't a webhook says so", r.get("status") == 400 and "webhook" in r.get("error", ""), r)
r = api("/api/campaigns/test", {"webhook": HOOK})
check("testing a webhook sends a message", r.get("ok") and discordhook.SENT[-1][0] == HOOK, discordhook.SENT[-1:])
r = api("/api/campaigns/active", {"id": cid})
check("a campaign can be made active", r.get("active") == cid, r)
r = api("/api/campaigns/share", {"id": cid})
check("sharing a campaign saves its file", os.path.isfile(r.get("path", "")) and r["name"].endswith(" - campaign.json"), r)

exports.save({"watermark": {"on": True, "marks": [{"kind": "text", "text": "@mine"}]}})
folder = tempfile.mkdtemp(prefix="vc_w_")
server.projects["p1"] = {"title": "Friday stream", "video": "", "workdir": folder, "live": "", "clips": []}
sent0 = len(discordhook.SENT)
it = server.queue_add("p1", {"start": 0.0, "end": 12.0, "title": "Too short", "post": "lol"}, {})
check("a clip queued while a campaign is active is made for it", (it.get("campaign") or {}).get("id") == cid, it.get("campaign"))
check("with the campaign's watermark instead of yours", it["export"]["watermark"]["marks"][0]["kind"] == "image", it["export"]["watermark"])
check("and a warning when it's shorter than the campaign wants", "at least 20 s" in (it.get("warning") or ""), it.get("warning"))
done = wait_status(it["id"], ("done",))
check("it renders with the campaign's caption rules",
      done and "@kaicenat · Code KAI" in done.get("post", "") and "#ad" in done["post"], done and done.get("post"))
end = time.time() + 5
while len(discordhook.SENT) == sent0 and time.time() < end:
    time.sleep(0.1)
check("and goes to the campaign's Discord as soon as it renders, with the video", len(discordhook.SENT) == sent0 + 1
      and discordhook.SENT[-1][2] == "Too short.mp4", discordhook.SENT[sent0:])
r = api("/api/queue/post_url", {"item": it["id"], "url": "not a link"})
check("a post link must be a link", r.get("status") == 400, r)
r = api("/api/discord/send", {"item": it["id"], "post_url": "https://www.tiktok.com/@me/video/1"})
check("sending again with the post link sends the link, not the video",
      r.get("ok") and not r["attached"] and "https://www.tiktok.com/@me/video/1" in discordhook.SENT[-1][1]["content"], discordhook.SENT[-1:])
check("Exports shows it was sent", r.get("item", {}).get("discord", {}).get("post_url") is True, r.get("item", {}).get("discord"))
check("mentions in a message never ping anyone", discordhook.SENT[-1][1]["allowed_mentions"] == {"parse": []})

api("/api/campaigns/active", {"id": ""})
it2 = server.queue_add("p1", {"start": 30.0, "end": 45.0, "title": "Own clip"}, {})
check("with no campaign active, clips use your own watermark and rules", "campaign" not in it2
      and it2["export"]["watermark"]["marks"][0]["text"] == "@mine", it2["export"]["watermark"])
done = wait_status(it2["id"], ("done",))
r = api("/api/discord/send", {"item": it2["id"]})
check("and there's no Discord to send them to", r.get("status") == 400, r)

server.SHUTTING_DOWN[0] = True
srv.shutdown()
print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
