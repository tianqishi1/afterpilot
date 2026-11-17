"""Versioned releases: shadow -> canary -> ga, eval-gated promotion, guardrail auto-rollback."""
import hashlib

from sqlalchemy import select, update

from . import config
from .db import session_scope
from .models import AuditLog, EvalRun, Release, Ticket, ToolCall, Handoff, now


def current(spec_name: str) -> dict:
    with session_scope() as s:
        r = s.execute(select(Release).where(Release.spec == spec_name, Release.active == True)  # noqa: E712
                      .order_by(Release.created_at.desc())).scalars().first()
        if r:
            return {"version": r.version, "mode": r.mode, "percent": r.percent, "id": r.id}
    from .agentspec import load
    return {"version": load(spec_name).version, "mode": config.RELEASE_MODE,
            "percent": config.CANARY_PERCENT, "id": ""}


def stable_version(spec_name: str) -> str:
    with session_scope() as s:
        r = s.execute(select(Release).where(Release.spec == spec_name, Release.mode == "ga")
                      .order_by(Release.created_at.desc())).scalars().first()
        if r:
            return r.version
    from .agentspec import load
    return load(spec_name).version


def assign(ticket_id: str, spec_name: str) -> tuple[str, str]:
    rel = current(spec_name)
    if rel["mode"] == "shadow":
        return rel["version"], "shadow"
    if rel["mode"] == "canary":
        bucket = int(hashlib.sha1(ticket_id.encode()).hexdigest(), 16) % 100
        if bucket < rel["percent"]:
            return rel["version"], "canary"
        return stable_version(spec_name), "ga"
    return rel["version"], "ga"


def set_release(spec_name: str, version: str, mode: str, percent: int = 100,
                actor: str = "admin") -> dict:
    if mode in ("canary", "ga"):
        with session_scope() as s:
            ok = s.execute(select(EvalRun).where(EvalRun.prompt_version == version,
                                                 EvalRun.passed == True)).scalars().first()  # noqa: E712
        if ok is None:
            raise ValueError(f"no passing eval run for version {version}; promotion blocked")
    with session_scope() as s:
        s.execute(update(Release).where(Release.spec == spec_name, Release.active == True)  # noqa: E712
                  .values(active=False))
        r = Release(spec=spec_name, version=version, mode=mode, percent=percent)
        s.add(r)
        s.add(AuditLog(actor=actor, action="release_set", target=spec_name,
                       detail={"version": version, "mode": mode, "percent": percent}))
        s.flush()
        return {"id": r.id, "version": version, "mode": mode, "percent": percent}


def rollback(spec_name: str, actor: str = "guardrail") -> dict:
    ver = stable_version(spec_name)
    return set_release(spec_name, ver, "ga", 100, actor=actor)


def check_guardrails(spec_name: str) -> dict | None:
    """Recent-window guardrails; breach in canary/shadow triggers auto-rollback."""
    rel = current(spec_name)
    if rel["mode"] == "ga":
        return None
    with session_scope() as s:
        tickets = s.execute(select(Ticket).where(Ticket.spec == spec_name,
                                                 Ticket.release_mode != "shadow")
                            .order_by(Ticket.created_at.desc()).limit(50)).scalars().all()
        tools = s.execute(select(ToolCall).order_by(ToolCall.created_at.desc()).limit(100)).scalars().all()
    done = [t for t in tickets if t.status in ("RESOLVED", "CLOSED", "ESCALATED")]
    if len(done) >= 10:
        esc_rate = sum(1 for t in done if t.status == "ESCALATED") / len(done)
        if esc_rate > 0.5:
            rb = rollback(spec_name)
            return {"breach": "escalation_rate", "value": round(esc_rate, 3), "rollback": rb}
    if len(tools) >= 20:
        err_rate = sum(1 for t in tools if t.intent_status in ("failed", "unknown")) / len(tools)
        if err_rate > 0.3:
            rb = rollback(spec_name)
            return {"breach": "tool_error_rate", "value": round(err_rate, 3), "rollback": rb}
    return None