"""
Phase 0 of issue #49 (docs/plans/issue-49-local-llm.md, section 7): does a small local model write clearly
better titles than Clip Radar does without one? A developer tool, not shipped.

For every clip of every saved project in a folder, it makes today's title (with viewers' clip titles, 4.47)
the way the app does, then asks one or more llama-server instances for theirs with the prompt and the checks
in src/llm_prompts.py. It writes:

  <out>/rate.html     a blind A/B page: per clip, what was said, what chat said, and two titles in random
                      order. A rater picks the better one or "Same", then saves their answers as a .json file.
  <out>/key.json      which title is which (keep it away from raters)
  <out>/results.json  every clip's titles, each model's outcome (used / rejected:<rule>), and timings

Run llama-server by hand first, one per model, for example (llama.cpp's release zip for your PC):

  llama-server -m Qwen3-1.7B-Q8_0.gguf --port 8081 -c 4096 --jinja
  llama-server -m Qwen3-4B-Q4_K_M.gguf --port 8082 -c 4096 --jinja

then:

  python scripts/eval_titles.py PROJECTS --url small=http://127.0.0.1:8081 --url std=http://127.0.0.1:8082
  python scripts/eval_titles.py --score eval_out/key.json ratings_rater1.json ratings_rater2.json

PROJECTS is a folder of Clip Radar project folders (each with project.json, meta.json, and chat.json /
transcripts.json / twitch_clips.json when it has them), e.g. %APPDATA%/ClipRadar/work. Open each VOD in Clip
Radar and let it listen to the clips (or Deep scan) first, so the clips have their words.

The ship rule (section 7): build phases 2-5 only if the model's title wins at least 60% of the comparisons
that aren't "Same", across two raters, and at most 25% of its answers are rejected by the checks.

Built-in against built-in, no model (titles-v2, docs/plans/titles-v2.md): a git ref's titles and post captions
against this checkout's, each side in its own process with that side's src/ first on the path. Titles and
captions are separate items on the page, and each has its own verdict (60%, two raters):

  python scripts/eval_titles.py PROJECTS --baseline main --out eval_v2
  python scripts/eval_titles.py --score eval_v2/key.json ratings_a.json ratings_b.json

scripts/title_corpus.py builds PROJECTS from public Twitch VODs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("CLIPRADAR_DATA", tempfile.mkdtemp(prefix="eval_titles_"))   # the user's settings untouched
sys.path.insert(0, os.path.join(ROOT, "src"))

WIN_RATE = 0.60
MAX_REJECTED = 0.25


# ---------------------------------------------------------------------------------------------- talking to it
def ask(url: str, body: dict, timeout: float = 120.0) -> tuple:
    """(parsed answer or None, milliseconds, extra: usage/timings/error)."""
    t0 = time.perf_counter()
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    extra: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, int((time.perf_counter() - t0) * 1000), {"error": str(e)[:200]}
    ms = int((time.perf_counter() - t0) * 1000)
    extra["usage"] = data.get("usage") or {}
    extra["timings"] = data.get("timings") or {}
    try:
        text = data["choices"][0]["message"]["content"] or ""
        text = text.split("</think>")[-1].strip()      # a thinking model that thought anyway
        return json.loads(text), ms, extra
    except (KeyError, IndexError, TypeError, ValueError) as e:
        extra["error"] = f"unparsable: {str(e)[:80]}"
        return None, ms, extra


def model_name(url: str) -> str:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        return os.path.basename(str(d["data"][0]["id"]))
    except Exception:
        return ""


# ---------------------------------------------------------------------------------------------- the projects
def _read(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _tx_words(wd: str) -> list:
    tx = (_read(os.path.join(wd, "transcripts.json"), {}) or {}).get("tx") or {}
    words = [w for v in tx.values() for w in (v.get("words") or [])]
    return sorted(words, key=lambda w: w["start"])


def projects(root: str) -> list:
    out = []
    for name in sorted(os.listdir(root)):
        wd = os.path.join(root, name)
        p = _read(os.path.join(wd, "project.json"))
        if isinstance(p, dict) and isinstance(p.get("clips"), list) and p["clips"]:
            out.append((wd, p))
    return out


def clips_of(wd: str, p: dict, emotes_on: bool = False) -> list:
    """Today's titles for a project's clips, made as server._title_clip makes them, with what a prompt needs."""
    import engine
    import llm_prompts as lp
    import server
    import titles
    meta = engine._meta(wd)
    info: dict = {}
    chat = engine.load_chat(os.path.join(wd, "chat.json"), info) if os.path.exists(os.path.join(wd, "chat.json")) else []
    pr = {"workdir": wd, "chat": chat, "chat_delay": float(meta.get("chat_delay") or 6.0), "info": {"has_audio": True},
          "streamer": p.get("streamer") or meta.get("streamer") or info.get("streamer", ""),
          "streamer_display": meta.get("streamer_display", ""), "title": p.get("title") or meta.get("title", ""),
          "source": p.get("source", "")}
    if emotes_on:
        # the channel's emotes, as the app's Analysis carries them (chattitles leaves single emotes out with them)
        import types
        import emotes
        pr["analysis"] = types.SimpleNamespace(_emotes=emotes.for_channel(meta.get("streamer_id", ""), pr["streamer"]))
    tx = _tx_words(wd)
    seen: list = []
    real_generate = titles.generate

    def spy(*a, **k):
        r = real_generate(*a, **k)
        seen.append((a, k, r))
        return r
    titles.generate = spy
    out, used = [], set()
    try:
        for i, c in enumerate(p["clips"], 1):
            d = {k: c[k] for k in ("start", "end", "peak") if k in c}
            if "start" not in d or "end" not in d:
                continue
            words = c.get("words") or [w for w in tx if d["start"] - 0.2 <= w["start"] <= d["end"]]
            seen.clear()
            server._title_clip(pr, d, words, used)
            args, kw, gen = seen[-1]
            rel = args[0]
            near = server._chat_near(pr, d["start"], d["end"])
            vt = kw.get("viewer_titles") or []
            ctx = {"candidates": lp.candidates(gen, vt), "transcript": lp.excerpt(rel, kw.get("peak_rel")),
                   "chat": lp.top_chat(near), "quote": gen["quote"], "vibe": gen["vibe"], "topic": gen["topic"],
                   "streamer": server._streamer_name(pr), "game": engine.game_at(meta, float(d.get("peak", d["start"])))}
            out.append({"id": f"{os.path.basename(wd)}#{i}", "project": p.get("title") or os.path.basename(wd),
                        "n": i, "start": d["start"], "end": d["end"], "builtin": d["title"],
                        "builtin_raw": gen["title"], "viewer_titles": vt, "ctx": ctx, "post": d.get("post", ""),
                        "description": d.get("description", ""), "title_a": d.get("title_a", ""),
                        "title_b": d.get("title_b", ""), "titles": d.get("titles", []),
                        "game": ctx["game"], "stream_title": pr["title"],
                        "seed": int(d["start"] * 1000) ^ i})
    finally:
        titles.generate = real_generate
    return out


# ---------------------------------------------------------------------------------------------- running it
def run(args) -> int:
    import llm_prompts as lp
    import safety
    models = []
    for spec in args.url:
        name, _, url = spec.rpartition("=")
        models.append({"name": name or f"m{len(models) + 1}", "url": url})
    for m in models:
        m["file"] = model_name(m["url"])
        print(f"{m['name']}: {m['url']} ({m['file'] or 'model name unknown'})", flush=True)
    cfg = safety.load_config()
    allc = []
    for wd, p in projects(args.projects):
        cl = clips_of(wd, p)
        print(f"{p.get('title') or os.path.basename(wd)}: {len(cl)} clips", flush=True)
        # each model tracks the titles it gave earlier clips of the stream, as the app's `used` would
        for m in models:
            used: set = set()
            for c in cl:
                body = lp.request(c["ctx"], seed=c["seed"])
                ans, ms, extra = ask(m["url"], body, args.timeout)
                chk = lp.check(ans, c["ctx"], used, c["builtin_raw"], cfg) if ans is not None else \
                    {"title": "", "caption": "", "pick": 0, "outcome": "rejected:" + ("timeout" if "timed out" in
                                                                                        extra.get("error", "") else "no_answer")}
                final = chk["title"] or c["builtin"]
                used.add(final.lower())
                c.setdefault("models", {})[m["name"]] = {
                    "answer": ans, "title": chk["title"], "caption": chk["caption"], "pick": chk["pick"],
                    "outcome": chk["outcome"], "ms": ms, "prompt_tokens": (extra.get("usage") or {}).get("prompt_tokens"),
                    "timings": extra.get("timings"), "error": extra.get("error", "")}
                print(f"  #{c['n']:>3} {m['name']}: {ms:>6} ms  {chk['outcome']:<18} {chk['title'] or '-'}  "
                      f"(built-in: {c['builtin']})", flush=True)
        allc += cl
    os.makedirs(args.out, exist_ok=True)
    summary = {}
    for m in models:
        rs = [c["models"][m["name"]] for c in allc]
        ms = sorted(r["ms"] for r in rs)
        summary[m["name"]] = {
            "url": m["url"], "file": m["file"], "clips": len(rs),
            "rejected": sum(r["outcome"] != "used" for r in rs),
            "rejected_by": {o: sum(r["outcome"] == o for r in rs) for o in sorted({r["outcome"] for r in rs}) if o != "used"},
            "same_as_builtin": sum(r["outcome"] == "used" and r["title"].lower() == c["builtin"].lower()
                                   for r, c in zip(rs, allc)),
            "ms_median": ms[len(ms) // 2] if ms else None, "ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
            "prompt_tokens_max": max((r["prompt_tokens"] or 0 for r in rs), default=0)}
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"prompt_version": lp.PROMPT_VERSION, "summary": summary, "clips": allc}, f, indent=1)
    write_page(allc, models, args.out, random.Random(args.seed))
    print(json.dumps(summary, indent=1))
    print(f"Wrote {args.out}/rate.html, key.json and results.json")
    return 0


def write_page(allc: list, models: list, out: str, rnd: random.Random):
    """One item per clip and model whose title was used and differs from the built-in one, shuffled."""
    items, key = [], {}
    for c in allc:
        for m in models:
            r = c["models"][m["name"]]
            if r["outcome"] != "used" or r["title"].lower() == c["builtin"].lower():
                continue
            iid = f"i{len(items) + 1}"
            pair = [("builtin", c["builtin"]), (m["name"], r["title"])]
            rnd.shuffle(pair)
            key[iid] = {"clip": c["id"], "x": pair[0][0], "y": pair[1][0]}
            items.append({"id": iid, "project": c["project"], "n": c["n"], "transcript": c["ctx"]["transcript"],
                          "chat": c["ctx"]["chat"][:8], "x": pair[0][1], "y": pair[1][1]})
    rnd.shuffle(items)
    with open(os.path.join(out, "key.json"), "w", encoding="utf-8") as f:
        json.dump({"models": [m["name"] for m in models], "items": key,
                   "auto_same": {m["name"]: sum(1 for c in allc if c["models"][m["name"]]["outcome"] == "used"
                                               and c["models"][m["name"]]["title"].lower() == c["builtin"].lower())
                                 for m in models}}, f, indent=1)
    rows = []
    for it in items:
        e = html.escape
        chat = "".join(f"<li>{e(x)}</li>" for x in it["chat"]) or "<li><i>no chat</i></li>"
        rows.append(f"""<section data-id="{it['id']}"><h2>{e(it['project'])} · clip {it['n']}</h2>
<p class="tx">{e(it['transcript']) or '<i>nothing said</i>'}</p><ul class="chat">{chat}</ul>
<div class="pick"><label><input type="radio" name="{it['id']}" value="x"> {e(it['x'])}</label>
<label><input type="radio" name="{it['id']}" value="y"> {e(it['y'])}</label>
<label class="same"><input type="radio" name="{it['id']}" value="same"> Same</label></div></section>""")
    _page(rows, len(items), out, "Which title is better for this clip?",
          "The two titles are in random order. Pick the one you'd rather post, or Same.")


# ---------------------------------------------------------------------------------------------- built-in vs built-in
# titles-v2 (docs/plans/titles-v2.md): today's built-in titles and captions (a git ref) against this checkout's,
# rated blind the same way. Each side runs in its own process with that side's src/ first on the path.
def _ref_src(ref: str) -> str:
    """src/ of a git ref, unpacked into a temporary folder."""
    import io
    import subprocess
    import tarfile
    out = tempfile.mkdtemp(prefix="eval_base_")
    tar = subprocess.run(["git", "-C", ROOT, "archive", ref, "src"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(out, filter="data")
    return os.path.join(out, "src")


def dump(projects_dir: str, out_json: str) -> int:
    """--dump: every clip's title and caption made by whichever src/ is first on the path."""
    allc = []
    for wd, p in projects(projects_dir):
        cl = clips_of(wd, p, emotes_on=True)
        allc += cl
        print(f"  {p.get('title') or os.path.basename(wd)}: {len(cl)} clips", flush=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(allc, f, indent=1)
    return 0


def _side(src: str, projects_dir: str, out_json: str):
    import subprocess
    env = dict(os.environ, CLIPRADAR_DATA=tempfile.mkdtemp(prefix="eval_titles_"))
    subprocess.run([sys.executable, os.path.abspath(__file__), projects_dir, "--dump", out_json, "--src", src],
                   check=True, env=env)


def compare(args) -> int:
    os.makedirs(args.out, exist_ok=True)
    base_json, new_json = os.path.join(args.out, "baseline.json"), os.path.join(args.out, "new.json")
    print(f"baseline: {args.baseline}", flush=True)
    _side(_ref_src(args.baseline), args.projects, base_json)
    print("new: this checkout", flush=True)
    _side(os.path.join(ROOT, "src"), args.projects, new_json)
    base = {c["id"]: c for c in _read(base_json, [])}
    new = {c["id"]: c for c in _read(new_json, [])}
    pairs = []
    for cid, b in base.items():
        n = new.get(cid)
        if n:
            pairs.append({"id": cid, "project": b["project"], "n": b["n"], "game": b.get("game", ""),
                          "stream_title": b.get("stream_title", ""), "ctx": b["ctx"],
                          "viewer_titles": b.get("viewer_titles", []),
                          "title": {"baseline": b["builtin"], "new": n["builtin"]},
                          "caption": {"baseline": b.get("post", ""), "new": n.get("post", "")}})
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"baseline": args.baseline, "clips": pairs}, f, indent=1)
    key = write_pair_page(pairs, args.out, random.Random(args.seed))
    for kind in ("title", "caption"):
        print(f"{kind}s: {sum(1 for k in key['items'].values() if k['kind'] == kind)} differ, "
              f"{key['auto_same'][kind]} the same")
    print(f"Wrote {args.out}/rate.html, key.json and results.json")
    return 0


def write_pair_page(pairs: list, out: str, rnd: random.Random) -> dict:
    """One item per clip and kind (title, post caption) where the two sides differ, shuffled."""
    items, key, same = [], {}, {"title": 0, "caption": 0}
    for c in pairs:
        for kind in ("title", "caption"):
            b, n = c[kind]["baseline"], c[kind]["new"]
            if b.strip().lower() == n.strip().lower():
                same[kind] += 1
                continue
            iid = f"i{len(items) + 1}"
            pair = [("baseline", b), ("new", n)]
            rnd.shuffle(pair)
            key[iid] = {"clip": c["id"], "kind": kind, "x": pair[0][0], "y": pair[1][0]}
            items.append({"id": iid, "kind": kind, "project": c["project"], "n": c["n"], "game": c["game"],
                          "transcript": c["ctx"]["transcript"], "chat": c["ctx"]["chat"][:8],
                          "x": pair[0][1], "y": pair[1][1]})
    rnd.shuffle(items)
    key_out = {"models": ["new"], "baseline": "baseline", "items": key, "auto_same": same}
    with open(os.path.join(out, "key.json"), "w", encoding="utf-8") as f:
        json.dump(key_out, f, indent=1)
    e = html.escape
    rows = []
    for it in items:
        chat = "".join(f"<li>{e(x)}</li>" for x in it["chat"]) or "<li><i>no chat</i></li>"
        ask = "Which title is better for this clip?" if it["kind"] == "title" else \
            "Which post caption is better for this clip?"
        opt = (lambda t: e(t).replace("\n", "<br>"))
        rows.append(f"""<section data-id="{it['id']}" data-kind="{it['kind']}"><h2>{e(it['project'])} · clip {it['n']}
{(' · ' + e(it['game'])) if it['game'] else ''}</h2><p class="q">{ask}</p>
<p class="tx">{e(it['transcript']) or '<i>nothing said</i>'}</p><ul class="chat">{chat}</ul>
<div class="pick"><label><input type="radio" name="{it['id']}" value="x"> <span>{opt(it['x'])}</span></label>
<label><input type="radio" name="{it['id']}" value="y"> <span>{opt(it['y'])}</span></label>
<label class="same"><input type="radio" name="{it['id']}" value="same"> Same</label></div></section>""")
    _page(rows, len(items), out, "Titles and captions test",
          "Each item asks about a clip's <b>title</b> (the text on the video) or its <b>post caption</b> (the text "
          "posted with it, hashtags at the end). The two options are in random order. Pick the one you'd rather "
          "post, or Same.")
    return key_out


def _page(rows: list, count: int, out: str, title: str, intro: str):
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--bg:#fff;--fg:#1b1b1b;--mute:#666;--line:#ddd;--acc:#1f7a4d}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181a;--fg:#e8e8e8;--mute:#9a9a9a;--line:#333;--acc:#4fd18b}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:16px}}
section{{border-top:1px solid var(--line);padding:12px 0}} h2{{font-size:14px;color:var(--mute);margin:0 0 6px}}
.q{{font-weight:600;margin:0 0 4px}} .tx{{margin:0 0 6px}} .chat{{color:var(--mute);font-size:13px;margin:0 0 8px;padding-left:18px}}
.pick label{{display:block;padding:6px 8px;border:1px solid var(--line);border-radius:6px;margin:4px 0;cursor:pointer}}
.pick label:has(input:checked){{border-color:var(--acc)}} .same{{color:var(--mute)}}
header{{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
</style></head><body><header><b>{title}</b>
<input id="rater" placeholder="Your name" aria-label="Your name"><button id="save">Save answers</button><span id="n"></span></header>
<p>{count} items. {intro}</p>
{''.join(rows)}
<script>
const K='title-test';let st={{}};try{{st=JSON.parse(localStorage.getItem(K)||'{{}}')}}catch(e){{}}
const n=()=>document.getElementById('n').textContent=Object.keys(st.c||{{}}).length+' / {count} done';
document.querySelectorAll('input[type=radio]').forEach(r=>{{if((st.c||{{}})[r.name]===r.value)r.checked=true;
r.addEventListener('change',()=>{{st.c=st.c||{{}};st.c[r.name]=r.value;try{{localStorage.setItem(K,JSON.stringify(st))}}catch(e){{}};n()}})}});
const rt=document.getElementById('rater');rt.value=st.rater||'';rt.addEventListener('input',()=>{{st.rater=rt.value;try{{localStorage.setItem(K,JSON.stringify(st))}}catch(e){{}}}});
document.getElementById('save').onclick=()=>{{const b=new Blob([JSON.stringify({{rater:rt.value,choices:st.c||{{}}}},null,1)],{{type:'application/json'}});
const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='ratings_'+(rt.value||'rater')+'.json';a.click()}};n();
</script></body></html>"""
    with open(os.path.join(out, "rate.html"), "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------------------------------------------------------- the verdict
def score(key_path: str, ratings: list, results_path: str = "") -> dict:
    key = _read(key_path, {})
    if key.get("baseline"):
        return score_pairs(key, ratings)
    res = _read(results_path or os.path.join(os.path.dirname(key_path), "results.json"), {}) or {}
    out = {}
    for m in key.get("models", []):
        win = lose = same = 0
        for rp in ratings:
            ch = (_read(rp, {}) or {}).get("choices") or {}
            for iid, k in key["items"].items():
                if m not in (k["x"], k["y"]) or iid not in ch:
                    continue
                if ch[iid] == "same":
                    same += 1
                elif k[ch[iid]] == m:
                    win += 1
                else:
                    lose += 1
        s = (res.get("summary") or {}).get(m) or {}
        rej = s.get("rejected", 0) / s["clips"] if s.get("clips") else None
        rate = win / (win + lose) if win + lose else None
        out[m] = {"wins": win, "losses": lose, "same": same, "auto_same": key.get("auto_same", {}).get(m, 0),
                  "win_rate": rate, "rejected_rate": rej, "ms_median": s.get("ms_median"), "ms_p90": s.get("ms_p90"),
                  "ship": bool(rate is not None and rate >= WIN_RATE and rej is not None and rej <= MAX_REJECTED
                               and len(ratings) >= 2)}
    return out


def score_pairs(key: dict, ratings: list) -> dict:
    """--baseline mode: the new side's wins over the baseline, titles and captions apart; each ships on its own."""
    out = {}
    for kind in ("title", "caption"):
        win = lose = same = 0
        per = []
        for rp in ratings:
            ch = (_read(rp, {}) or {}).get("choices") or {}
            w = lo = sa = 0
            for iid, k in key["items"].items():
                if k.get("kind") != kind or iid not in ch:
                    continue
                if ch[iid] == "same":
                    sa += 1
                elif k.get(ch[iid]) == "new":
                    w += 1
                else:
                    lo += 1
            per.append({"rater": os.path.basename(rp), "wins": w, "losses": lo, "same": sa,
                        "win_rate": w / (w + lo) if w + lo else None})
            win, lose, same = win + w, lose + lo, same + sa
        rate = win / (win + lose) if win + lose else None
        out[kind] = {"wins": win, "losses": lose, "same": same, "auto_same": key.get("auto_same", {}).get(kind, 0),
                     "win_rate": rate, "raters": per,
                     "ship": bool(rate is not None and rate >= WIN_RATE and len(ratings) >= 2)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="?", help="a folder of Clip Radar project folders")
    ap.add_argument("--url", action="append", default=[], help="name=http://127.0.0.1:8081, once per model")
    ap.add_argument("--out", default="eval_out")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per answer (the app's live budget is 6)")
    ap.add_argument("--seed", type=int, default=49, help="shuffles the page")
    ap.add_argument("--score", nargs="+", metavar="FILE", help="key.json then each rater's ratings .json")
    ap.add_argument("--baseline", metavar="REF", help="compare a git ref's built-in titles and captions with this "
                                                       "checkout's, no model (titles-v2)")
    ap.add_argument("--dump", metavar="JSON", help=argparse.SUPPRESS)
    ap.add_argument("--src", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.src:
        sys.path.insert(0, os.path.abspath(a.src))
    if a.dump:
        return dump(a.projects, a.dump)
    if a.score:
        v = score(a.score[0], a.score[1:])
        print(json.dumps(v, indent=1))
        if len(a.score) < 3:
            print("The ship rule needs two raters.")
        return 0
    if a.baseline and a.projects:
        return compare(a)
    if not a.projects or not a.url:
        ap.error("give a projects folder and at least one --url, or --baseline REF (or --score)")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
