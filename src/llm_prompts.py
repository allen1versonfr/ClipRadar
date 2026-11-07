"""
The prompt and the answer checks for local-AI titles (issue #49, docs/plans/issue-49-local-llm.md, sections 5.1
to 5.4). Kept apart from any model so phase 0 (scripts/eval_titles.py) and a later src/llm.py use the same
words and the same rules. Nothing in the app calls this yet.

The model only picks one of the numbered title ideas Clip Radar already made and may lightly edit it. It
never writes freely: every answer goes through check(), in code, and anything that fails falls back to the
built-in title. Prompts hold chat messages but never who wrote them.
"""

from __future__ import annotations

import re
from typing import Optional

PROMPT_VERSION = 1
MAX_TITLE = 70
MAX_CAPTION = 180
MAX_CANDIDATES = 12
MAX_CHAT = 15
MAX_TRANSCRIPT = 600
MAX_CHAT_CHARS = 60

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["pick", "title", "caption"],
          "properties": {
              "pick": {"type": "integer", "minimum": 0, "maximum": 20},
              "title": {"type": "string", "maxLength": MAX_TITLE},
              "caption": {"type": "string", "maxLength": MAX_CAPTION}}}

INSTRUCTIONS = (
    "You title short vertical clips from a livestream. Here is what was said, what chat said, and numbered "
    "title ideas. Pick the idea that best says what happens in this clip, and improve its wording if you "
    "can. Use only facts from the transcript or chat. Don't invent names, outcomes or numbers. 70 characters "
    "at most, no hashtags, no emoji, no quotes around it. Also write one short post caption that adds "
    "something the title doesn't. Answer only as JSON: {\"pick\": idea number or 0, \"title\": ..., "
    "\"caption\": ...}.")

_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|tv|gg|net|org|io|ly)\b", re.I)
_MENTION = re.compile(r"@\w+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
_WORDS = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_PRONOUN_I = {"i", "i'm", "i'll", "i've", "i'd"}


def _text(m) -> tuple:
    """(text, user) from a chat message: a text, (text, user), or (seconds, text, user, ...)."""
    if isinstance(m, str):
        return m, ""
    m = tuple(m)
    if len(m) >= 3 and isinstance(m[0], (int, float)):
        return m[1], m[2]
    if len(m) == 2:
        return m[0], m[1]
    return (m[1] if len(m) > 1 else m[0]), ""


def top_chat(msgs: list, n: int = MAX_CHAT) -> list:
    """The n phrases most chatters agreed on in the clip's window, messages only. Each chatter counts once
    per phrase (as chattitles.find counts). @mentions, links and any word that is a chatter's name are taken
    out, so no username reaches the prompt."""
    pairs = [_text(m) for m in msgs or []]
    names = {str(u).lower() for _, u in pairs if u}
    people: dict = {}
    first: dict = {}
    shown: dict = {}
    for i, (text, user) in enumerate(pairs):
        if not isinstance(text, str) or _URL.search(text):
            continue
        t = _MENTION.sub("", text)
        t = " ".join(w for w in t.split() if w.lower().strip(",.!?:;") not in names)
        t = " ".join(t.split())[:MAX_CHAT_CHARS].strip()
        key = t.lower()
        if len(key) < 2:
            continue
        people.setdefault(key, set()).add(str(user).lower() if user else f"#{i}")
        first.setdefault(key, i)
        shown.setdefault(key, t)
    order = sorted(people, key=lambda k: (-len(people[k]), first[k]))
    return [f"{shown[k]} (x{len(people[k])})" if len(people[k]) > 1 else shown[k] for k in order[:n]]


def excerpt(words: list, peak_rel: Optional[float] = None, limit: int = MAX_TRANSCRIPT) -> str:
    """The clip's words joined, at most limit characters, centred on the peak when there's more."""
    toks = [str(w.get("word", "")).strip() for w in words or []]
    toks = [t for t in toks if t]
    text = " ".join(toks)
    if len(text) <= limit:
        return text
    if peak_rel is None:
        k = len(toks) // 2
    else:
        k = min(range(len(words)), key=lambda i: abs(float(words[i].get("start", 0)) - peak_rel))
        k = min(k, len(toks) - 1)
    a = b = k
    size = len(toks[k])
    while True:
        grew = False
        if a > 0 and size + len(toks[a - 1]) + 1 <= limit:
            a -= 1
            size += len(toks[a]) + 1
            grew = True
        if b < len(toks) - 1 and size + len(toks[b + 1]) + 1 <= limit:
            b += 1
            size += len(toks[b]) + 1
            grew = True
        if not grew:
            break
    return " ".join(toks[a:b + 1])


def candidates(info: dict, viewer_titles: Optional[list] = None) -> list:
    """The numbered ideas: titles.generate's titles, then viewers' clip titles not among them."""
    out, seen = [], set()
    for t in list(info.get("titles") or []) + list(viewer_titles or []):
        t = " ".join(str(t).split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_CANDIDATES]


def build(ctx: dict) -> str:
    """The user turn: the inputs as labelled blocks. ctx keys: candidates, transcript, chat, quote, vibe,
    topic, streamer, game (all optional but candidates)."""
    lines = []
    if ctx.get("streamer"):
        lines.append(f"Streamer: {ctx['streamer']}")
    if ctx.get("game"):
        lines.append(f"Game or category: {ctx['game']}")
    if ctx.get("vibe"):
        lines.append(f"Chat's mood: {ctx['vibe']}")
    if ctx.get("topic"):
        lines.append(f"Chat's topic word: {ctx['topic']}")
    lines.append("")
    lines.append("Transcript:")
    lines.append(ctx.get("transcript") or "(nothing said)")
    lines.append("")
    lines.append("Chat (most agreed first):")
    lines += [f"- {c}" for c in (ctx.get("chat") or [])] or ["(no chat)"]
    if ctx.get("quote"):
        lines += ["", f"Best line said: {ctx['quote']}"]
    lines += ["", "Title ideas:"]
    lines += [f"{i}. {t}" for i, t in enumerate(ctx.get("candidates") or [], 1)]
    return "\n".join(lines)


def messages(ctx: dict) -> list:
    return [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": build(ctx)}]


def request(ctx: dict, seed: int = 0, max_tokens: int = 160) -> dict:
    """A body for llama-server's OpenAI-style /v1/chat/completions, constrained to SCHEMA."""
    return {"messages": messages(ctx), "temperature": 0.3, "top_p": 0.9, "seed": int(seed) & 0x7FFFFFFF,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "clip_title", "schema": SCHEMA,
                                                                       "strict": True}},
            "chat_template_kwargs": {"enable_thinking": False}}


def approx_tokens(text: str) -> int:
    """A rough count (about 4 characters a token for English) for the ~700-token budget."""
    return (len(text) + 3) // 4


def _shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 4 and sum(c.isupper() for c in letters) > 0.6 * len(letters)


def _known(ctx: dict) -> set:
    parts = [ctx.get("transcript") or "", ctx.get("streamer") or "", ctx.get("game") or "", ctx.get("quote") or "",
             ctx.get("topic") or ""] + list(ctx.get("chat") or []) + list(ctx.get("candidates") or [])
    known = set()
    for p in parts:
        for w in _WORDS.findall(str(p).lower()):
            known.add(w)
            known.add(re.sub(r"'s$", "", w))
    return known


def invented(text: str, ctx: dict) -> list:
    """Capitalised words (not the first) and numbers in text that appear in none of the inputs."""
    known = _known(ctx)
    out = []
    text = text or ""
    for mo in _WORDS.finditer(text):
        w = mo.group(0)
        low = w.lower()
        base = re.sub(r"'s$", "", low)
        before = text[:mo.start()].rstrip(" \"'“”(")
        starts = not before or before[-1] in ".!?:"         # a sentence's first word is capitalised anyway
        if low in _PRONOUN_I:
            continue
        if (any(c.isdigit() for c in w) or (not starts and w[0].isupper())) and low not in known and base not in known:
            out.append(w)
    return out


def _format_rule(text: str, source: str = "", limit: int = MAX_TITLE) -> str:
    """The first rule 1 (section 5.4) that text breaks, or ''."""
    if not text.strip():
        return "empty"
    if len(text) > limit:
        return "too_long"
    if _shouting(text) and not _shouting(source):
        return "caps"
    if _URL.search(text):
        return "link"
    if "@" in text:
        return "mention"
    if "#" in text:
        return "hashtag"
    if _EMOJI.search(text):
        return "emoji"
    return ""


def check(answer, ctx: dict, used: Optional[set] = None, builtin: str = "", cfg: Optional[dict] = None) -> dict:
    """The rules of section 5.4. Returns {"title", "caption", "pick", "outcome"}: outcome "used" with the
    accepted (tidied, masked) title, or "rejected:<rule>" with title "" (the caller keeps its own).
    builtin is the title Clip Radar would use; cfg the content-check settings (safety.load_config())."""
    import safety
    import titles
    used = used or set()
    out = {"title": "", "caption": "", "pick": 0, "outcome": ""}
    if not isinstance(answer, dict) or not isinstance(answer.get("title"), str):
        out["outcome"] = "rejected:shape"
        return out
    cands = list(ctx.get("candidates") or [])
    pick = answer.get("pick")
    pick = pick if isinstance(pick, int) and not isinstance(pick, bool) and 0 <= pick <= len(cands) else 0
    out["pick"] = pick
    raw = " ".join(answer["title"].split()).strip("\"'“”")
    source = cands[pick - 1] if pick else ""
    rule = _format_rule(raw, source)
    if not rule and invented(raw, ctx):
        rule = "invented"
    if not rule and titles.garbled(raw):
        rule = "garbled"
    title = titles.polish(raw) if not rule else ""
    if not rule and title.lower() in used:
        rule = "used"
    if not rule:
        cfg = cfg if cfg is not None else safety.load_config()
        new = {(f["cat"], f["term"]) for f in safety.scan([], title, "", cfg)["flags"]}
        old = {(f["cat"], f["term"]) for f in safety.scan([], builtin, "", cfg)["flags"]}
        if new - old:
            rule = "flagged"
        title = safety.mask_text(title, cfg)
    if rule:
        out["outcome"] = f"rejected:{rule}"
        out["title"] = ""
        return out
    cap = " ".join(str(answer.get("caption") or "").split())
    if cap and (_format_rule(cap, limit=MAX_CAPTION) or invented(cap, ctx)):
        cap = ""                                   # a bad caption is dropped; the title can still be used
    out.update(title=title, caption=cap, outcome="used")
    return out
