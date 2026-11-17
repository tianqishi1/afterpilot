"""Agent = YAML spec over shared runtime (platformization: non-engineers configure, not code)."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

AGENTS_DIR = config.ROOT / "agents"


@dataclass
class Spec:
    name: str
    version: str
    persona: str
    ticket_types: list
    tools: list
    policy: dict = field(default_factory=dict)
    eval_suite: str = "scenario"
    kb_slugs: list = field(default_factory=list)


def load(name: str) -> Spec:
    p = AGENTS_DIR / f"{name}.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Spec(name=d["name"], version=d.get("version", "v1"), persona=d.get("persona", ""),
                ticket_types=d.get("ticket_types", []), tools=d.get("tools", []),
                policy=d.get("policy", {}), eval_suite=d.get("eval_suite", "scenario"),
                kb_slugs=d.get("kb_slugs", []))


def list_specs() -> list[str]:
    return sorted(p.stem for p in AGENTS_DIR.glob("*.yaml") if p.stem != "kb_seed")