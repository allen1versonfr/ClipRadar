"""
List Clip Radar's HTTP routes (read from src/server.py) and the API paths a UI calls.

  python scripts/api_routes.py              routes as a Markdown table (docs/api-routes.md)
  python scripts/api_routes.py --ui         every API path the UIs call, and whether it exists

The parsing is plain text matching on purpose: it needs no imports from the app, so it also runs
in CI before anything is installed. tests/test_api_contract.py uses the same functions.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SERVER = os.path.join(ROOT, "src", "server.py")
# where UI code lives: the original page and the React app's sources (when it exists)
UI_SOURCES = [os.path.join(ROOT, "src", "ui"), os.path.join(ROOT, "web", "src")]
UI_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".html")


def server_routes(path: str = SERVER) -> list[dict]:
    """[{method, path, pattern}] in the order the handler checks them. `pattern` is a regex for
    routes with an id in them (/api/job/(\\w+)), else None."""
    src = open(path, encoding="utf-8").read().splitlines()
    out, method = [], None
    prefix, prefix_indent = None, 0           # `if p.startswith("/api/x/"): act = ...; if act == "y"`
    for line in src:
        s = line.strip()
        indent = len(line) - len(line.lstrip())
        if prefix and s and indent <= prefix_indent:
            prefix = None
        m = re.match(r'if p\.startswith\("(/api/[^"]+/)"\)', s)
        if m and method:
            prefix, prefix_indent = m.group(1), indent
        if prefix and method:
            for act in re.findall(r'act == "(\w+)"', s):
                out.append({"method": method, "path": prefix + act, "pattern": None})
        if s.startswith("def do_GET"):
            method = "GET"
        elif s.startswith("def _post"):
            method = "POST"
        elif method and re.match(r"^(def|class) ", line):       # a top-level def ends the handler
            method = None
        if not method:
            continue
        for lit in re.findall(r'p == "(/[^"]*)"', s):
            out.append({"method": method, "path": lit, "pattern": None})
        m = re.search(r"p in \(([^)]*)\)", s)
        if m:
            for lit in re.findall(r'"(/[^"]*)"', m.group(1)):
                out.append({"method": method, "path": lit, "pattern": None})
        for rx in re.findall(r're\.fullmatch\(r"(/[^"]+)", p\)', s):
            shown = re.sub(r"\(\\w\+\)", "{id}", rx)
            shown = re.sub(r"\(([\w|]+)\)", lambda g: "{" + g.group(1) + "}", shown)
            shown = shown.replace("(.+)", "{path}").replace("([\\w.-]+)", "{name}")
            out.append({"method": method, "path": shown, "pattern": rx})
    seen, uniq = set(), []
    for r in out:
        k = (r["method"], r["path"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def _scan_path(text: str, i: int) -> str:
    """Read the API path that starts at text[i] (a quote), following `'a' + x + 'b'` and
    template literals. Expressions become X; a parenthesised add-on (a ?query) is dropped."""
    out = []
    while True:
        q = text[i]
        j = i + 1
        buf = []
        while j < len(text) and text[j] != q:
            if q == "`" and text.startswith("${", j):
                depth, j = 1, j + 2
                while j < len(text) and depth:
                    depth += {"{": 1, "}": -1}.get(text[j], 0)
                    j += 1
                buf.append("X")
                continue
            buf.append(text[j])
            j += 1
        out.append("".join(buf))
        k = j + 1
        while k < len(text) and text[k] in " \t\n":
            k += 1
        if k >= len(text) or text[k] != "+":
            break
        k += 1
        while k < len(text) and text[k] in " \t\n":
            k += 1
        if k < len(text) and text[k] in "'\"`":
            i = k
            continue
        # an expression: skip to the next quote at depth 0 (more path follows) or the end of the call
        depth, start, paren = 0, k, text[k] == "("
        while k < len(text):
            c = text[k]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and c in ",;":
                break
            elif depth == 0 and c in "'\"`":
                break
            k += 1
        if not paren or "?" not in text[start:k]:
            out.append("X")
        if k < len(text) and text[k] in "'\"`":
            # drop the "+" before the next literal
            i = k
            continue
        break
    return "".join(out).split("?")[0]


def ui_calls(roots=UI_SOURCES) -> list[tuple[str, str]]:
    """[(file, path)] for every '/api/...' string a UI source builds."""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("node_modules", "dist")]
            for f in files:
                if not f.endswith(UI_EXT):
                    continue
                fp = os.path.join(dirpath, f)
                text = open(fp, encoding="utf-8", errors="replace").read()
                for m in re.finditer(r"""['"`]/api/""", text):
                    found.append((os.path.relpath(fp, ROOT), _scan_path(text, m.start())))
    return found


def route_for(path: str, routes: list[dict]):
    """The route a UI path reaches (X stands for an id), or None."""
    for r in routes:
        if r["pattern"] is None:
            if r["path"] == path:
                return r
        elif re.fullmatch(r["pattern"], path):
            return r
    return None


def main():
    routes = server_routes()
    if "--ui" in sys.argv:
        bad = 0
        for f, p in sorted(set(ui_calls())):
            hit = route_for(p, routes)
            bad += hit is None
            print(f"{'ok  ' if hit else 'MISSING'} {p:45} {f}")
        sys.exit(1 if bad else 0)
    lines = ["| Method | Path |", "|---|---|"] + [f"| {r['method']} | `{r['path']}` |" for r in routes]
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
