"""Prometheus text exposition computed from DB (consistent across api/worker processes)."""
from sqlalchemy import select

from .cost import ticket_cost
from .db import session_scope
from .models import AgentRun, Approval, Handoff, Job, Message, Ticket, ToolCall

BUCKETS_RUN = [1, 5, 10, 30, 60, 120, 300]
BUCKETS_COST = [0.1, 0.5, 1, 2, 5, 10]
BUCKETS_FIRST = [1, 3, 5, 10, 30]


def _hist(name, values, buckets, help_text):
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
    for b in buckets:
        lines.append(f'{name}_bucket{{le="{b}"}} {sum(1 for v in values if v <= b)}')
    lines.append(f'{name}_bucket{{le="+Inf"}} {len(values)}')
    lines.append(f"{name}_sum {sum(values):.4f}")
    lines.append(f"{name}_count {len(values)}")
    return lines


def render() -> str:
    with session_scope() as s:
        tickets = s.execute(select(Ticket).where(Ticket.release_mode != "shadow")).scalars().all()
        runs = s.execute(select(AgentRun)).scalars().all()
        tools = s.execute(select(ToolCall)).scalars().all()
        apps = s.execute(select(Approval).where(Approval.status == "pending")).scalars().all()
        hands = s.execute(select(Handoff)).scalars().all()
        jobs = s.execute(select(Job)).scalars().all()
        msgs = s.execute(select(Message).where(Message.role == "agent")).scalars().all()
    out = []
    by_status: dict = {}
    for t in tickets:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    out += ["# HELP tickets_by_status Tickets by lifecycle status", "# TYPE tickets_by_status gauge"]
    out += [f'tickets_by_status{{status="{k}"}} {v}' for k, v in sorted(by_status.items())]
    hand_tickets = {h.ticket_id for h in hands}
    auto = sum(1 for t in tickets if t.status in ("RESOLVED", "CLOSED") and t.id not in hand_tickets)
    out += ["# HELP auto_resolved_total Tickets resolved without human intervention", "# TYPE auto_resolved_total counter",
            f"auto_resolved_total {auto}",
            "# HELP handoffs_total Human handoffs", "# TYPE handoffs_total counter", f"handoffs_total {len(hands)}",
            "# HELP approvals_pending Pending human approvals", "# TYPE approvals_pending gauge", f"approvals_pending {len(apps)}"]
    tc_status: dict = {}
    for tc in tools:
        tc_status[tc.intent_status] = tc_status.get(tc.intent_status, 0) + 1
    out += ["# HELP tool_calls_by_intent Tool intent outcomes", "# TYPE tool_calls_by_intent gauge"]
    out += [f'tool_calls_by_intent{{intent="{k}"}} {v}' for k, v in sorted(tc_status.items())]
    jb: dict = {}
    for j in jobs:
        jb[j.status] = jb.get(j.status, 0) + 1
    out += ["# HELP jobs_by_status Queue jobs by status", "# TYPE jobs_by_status gauge"]
    out += [f'jobs_by_status{{status="{k}"}} {v}' for k, v in sorted(jb.items())]
    durations = [(r.ended_at - r.started_at).total_seconds() for r in runs if r.ended_at]
    out += _hist("run_seconds", durations, BUCKETS_RUN, "Agent run duration")
    costs = [ticket_cost(t.id)["total_cost"] for t in tickets if t.status in ("RESOLVED", "CLOSED", "ESCALATED")]
    out += _hist("cost_per_ticket_yuan", costs, BUCKETS_COST, "All-in cost per ticket incl human")
    first = {}
    for m in msgs:
        first.setdefault(m.ticket_id, m.created_at)
    tmap = {t.id: t.created_at for t in tickets}
    fv = [(first[k] - tmap[k]).total_seconds() for k in first if k in tmap]
    out += _hist("first_response_seconds", fv, BUCKETS_FIRST, "Time to first agent reply")
    return "\n".join(out) + "\n"