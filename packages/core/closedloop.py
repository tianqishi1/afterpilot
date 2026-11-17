"""Closed loop: every handoff/human correction becomes a candidate eval case; human review activates it."""
from sqlalchemy import select

from .db import session_scope
from .models import AuditLog, EvalCase, Handoff, Message, Ticket
from .redact import redact_obj


def on_handoff(ticket_id: str, run_id: str, reason: str, ctx: dict) -> str:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        msgs = s.execute(select(Message).where(Message.ticket_id == ticket_id)
                         .order_by(Message.created_at)).scalars().all()
        last_user = next((m.content for m in reversed(msgs) if m.role == "user"), "")
        snap = redact_obj({"ticket": {"type": t.type, "status": t.status, "slots": t.slots},
                           "messages": [{"role": m.role, "content": m.content} for m in msgs],
                           "observations": ctx.get("observations", []), "reason": reason})
        h = Handoff(ticket_id=ticket_id, run_id=run_id, reason=reason, snapshot=snap)
        s.add(h)
        s.flush()
        case = EvalCase(suite="scenario", name=f"handoff:{ticket_id[:8]}",
                        input={"customer_id": t.customer_id, "message": last_user, "type": t.type},
                        expect={"no_runtime_error": True, "judge": "rubric"},
                        source="handoff", status="candidate")
        s.add(case)
        s.flush()
        h.eval_case_id = case.id
        hid = h.id
    return hid


def review(handoff_id: str, approve: bool, correction: dict | None = None, actor: str = "admin") -> dict:
    with session_scope() as s:
        h = s.get(Handoff, handoff_id)
        if h is None:
            raise ValueError("handoff missing")
        h.reviewed = True
        h.correction = correction or {}
        case = s.get(EvalCase, h.eval_case_id) if h.eval_case_id else None
        if case:
            if approve:
                case.status = "active"
                if correction and correction.get("expect"):
                    case.expect = {**case.expect, **correction["expect"]}
            else:
                case.status = "retired"
        s.add(AuditLog(actor=actor, action="handoff_review", target=handoff_id,
                       detail={"approve": approve, "correction": correction or {}}))
        return {"handoff_id": handoff_id, "eval_case_id": h.eval_case_id,
                "case_status": case.status if case else ""}