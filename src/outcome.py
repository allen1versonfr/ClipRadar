"""
How each attempt at something that touches the outside world ended (docs/plans/hardening.md, phase 3): one line in
clipradar.log per outcome, the way timing.py writes its Timing lines. Until now only failures were recorded
(errors.jsonl), so there was no failure rate. scripts/reliability_report.py reads these lines back.

    Outcome upload site=tiktok result=failed step="caption box"
    Outcome download platform=twitch result=ok fresh=yes
    Outcome render result=failed kind=encoder

result is ok, failed or cancelled. The other fields say which kind (site, platform) and, for a failure, where (step,
kind). Values are short words chosen here, never a title, caption, file name or link. Only writes a line; never raises.
"""

from __future__ import annotations

import json
import re

FEATURES = ("upload", "download", "live", "render", "discord")
RESULTS = ("ok", "failed", "cancelled")
_PLAIN = re.compile(r"[\w.:/+-]+")
_VARIES = re.compile(r"[\d,(%].*$")      # "Downloading the VOD 40%" is one step, not a hundred


def step_name(text: str) -> str:
    """A job's step line as a step name: without its percentage or count."""
    text = str(text or "")
    return _VARIES.sub("", text).strip() or text.strip()


def _value(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    s = str(v)[:80]
    return s if _PLAIN.fullmatch(s) else json.dumps(s, ensure_ascii=True)


def line(feature: str, result: str, **fields) -> str:
    bits = [f"{k}={_value(v)}" for k, v in fields.items() if v not in (None, "")]
    return " ".join([f"Outcome {feature}"] + bits + [f"result={result}"])


def log(feature: str, result: str, **fields) -> None:
    """Print one Outcome line to clipradar.log."""
    try:
        print("  " + line(feature, result, **fields), flush=True)
    except Exception:
        pass
