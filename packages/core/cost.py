"""Cost ledger per effective resolution: model tokens + tool calls + amortized human minutes."""
from sqlalchemy import select

from . import config
from .db import session_scope
from .models import AgentRun, Handoff, ToolCall


def ticket_cost(ticket_id: str) -> dict:
    with session_scope() as s:
        runs = s.execute(select(AgentRun).where(AgentRun.ticket_id == ticket_id)).scalars().all()
        tools = s.execute(select(ToolCall).where(ToolCall.ticket_id == ticket_id)).scalars().all()
        hands = s.execute(select(Handoff).where(Handoff.ticket_id == ticket_id)).scalars().all()
    model = sum(r.cost for r in runs)
    human = len(hands) * config.HANDOFF_EST_MIN * config.HUMAN_COST_PER_MIN
    return {
        "ticket_id": ticket_id,
        "model_cost": round(model, 4),
        "tool_calls": len(tools),
        "handoffs": len(hands),
        "human_cost": round(human, 2),
        "total_cost": round(model + human, 4),
        "tokens_in": sum(r.tokens_in for r in runs),
        "tokens_out": sum(r.tokens_out for r in runs),
    }


def summary() -> dict:
    with session_scope() as s:
        tickets = s.execute(select(Handoff.ticket_id)).all()
        runs = s.execute(select(AgentRun.ticket_id, AgentRun.cost)).all()
        resolved = s.execute(
            select(AgentRun.ticket_id).where(AgentRun.status == "done")
        ).scalars().all()
    hand_tickets = {t for (t,) in tickets}
    model_total = sum(c for _, c in runs)
    human_total = len(hand_tickets) * config.HANDOFF_EST_MIN * config.HUMAN_COST_PER_MIN
    n_eff = len(set(resolved) - hand_tickets)
    return {
        "model_cost_total": round(model_total, 4),
        "human_cost_total": round(human_total, 2),
        "effective_resolutions": n_eff,
        "cost_per_effective_resolution": round((model_total + human_total) / n_eff, 4) if n_eff else None,
    }