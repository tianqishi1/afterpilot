"""Ticket intake: release assignment at creation, reopen semantics, queue enqueue."""
from sqlalchemy import select

from . import config, release as rel
from .db import session_scope
from .models import Message, Ticket, uid
from .queue import enqueue
from .statemachine import transition


def create_ticket(customer_id: str, message: str, spec: str = config.DEFAULT_SPEC,
                  release_mode: str | None = None, prompt_version: str | None = None,
                  channel: str = "web") -> str:
    tid = uid()
    if release_mode is None or prompt_version is None:
        v, m = rel.assign(tid, spec)
        release_mode = release_mode or m
        prompt_version = prompt_version or v
    with session_scope() as s:
        s.add(Ticket(id=tid, customer_id=customer_id, spec=spec, channel=channel,
                     release_mode=release_mode, prompt_version=prompt_version))
        s.add(Message(ticket_id=tid, role="user", content=message))
    enqueue("agent_run", tid)
    return tid


def append_user_message(ticket_id: str, message: str) -> str:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        st = t.status
    if st in ("RESOLVED", "CLOSED"):
        transition(ticket_id, "NEW", actor="user", reason="reopen")
    with session_scope() as s:
        s.add(Message(ticket_id=ticket_id, role="user", content=message))
    enqueue("agent_run", ticket_id)
    return ticket_id


def get_ticket_view(ticket_id: str) -> dict:
    from .cost import ticket_cost
    from .models import Approval
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return {}
        msgs = s.execute(select(Message).where(Message.ticket_id == ticket_id)
                         .order_by(Message.created_at)).scalars().all()
        apps = s.execute(select(Approval).where(Approval.ticket_id == ticket_id)
                         .order_by(Approval.created_at)).scalars().all()
        return {"ticket": {"id": t.id, "customer_id": t.customer_id, "type": t.type, "status": t.status,
                           "spec": t.spec, "release_mode": t.release_mode, "prompt_version": t.prompt_version,
                           "feedback": t.feedback, "reopen_count": t.reopen_count,
                           "created_at": t.created_at.isoformat()},
                "messages": [{"role": m.role, "content": m.content, "at": m.created_at.isoformat()} for m in msgs],
                "approvals": [{"id": a.id, "kind": a.kind, "status": a.status, "payload": a.payload} for a in apps],
                "cost": ticket_cost(ticket_id)}