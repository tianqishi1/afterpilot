"""PII minimization before anything enters traces/eval snapshots."""
import re

PATTERNS = [
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("idcard", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("bankcard", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+")),
]


def redact_text(text: str) -> str:
    for name, rx in PATTERNS:
        text = rx.sub(f"[REDACTED:{name}]", text)
    return text


def redact_obj(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj