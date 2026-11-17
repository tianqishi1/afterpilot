"""Deterministic ticket lifecycle. LLM never moves money or state directly."""
from sqlalchemy import update

from .db import session_scope
from .models import AuditLog, Ticket, now

TRANSITIONS = {
    "NEW": {"TRIAGED", "ESCALATED"},
    "TRIAGED": {"HANDLING", "ESCALATED"},
    "HANDLING": {"WAIT_APPROVAL", "RESOLVED", "ESCALATED"},
    "WAIT_APPROVAL": {"HANDLING", "ESCALATED"},
    "RESOLVED": {"CLOSED", "NEW"},
    "ESCALATED": {"CLOSED", "HANDLING"},
    "CLOSED": {"NEW"},
}


class IllegalTransition(Exception):
    pass


def transition(ticket_id: str, to: str, actor: str = "system", reason: str = "") -> str:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            raise IllegalTransition(f"ticket {ticket_id} missing")
        frm = t.status
        if to not in TRANSITIONS.get(frm, set()):
            raise IllegalTransition(f"{frm} -> {to} not allowed")
        vals = {"status": to, "updated_at": now()}
        if to == "RESOLVED":
            vals["resolved_at"] = now()
        if to == "NEW" and frm in ("RESOLVED", "CLOSED"):
            vals["reopen_count"] = t.reopen_count + 1
        s.execute(update(Ticket).where(Ticket.id == ticket_id).values(**vals))
        s.add(AuditLog(actor=actor, action="transition", target=ticket_id,
                       detail={"from": frm, "to": to, "reason": reason}))
        return to


def status_of(ticket_id: str) -> str:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        return t.status if t else ""