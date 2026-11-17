"""HTTP surface: customer chat API + admin console API + /metrics + static web console.
Admin endpoints require X-Admin-Token (least privilege; chat API is customer-scoped)."""
import pathlib

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from packages.core import config, cost, kb, metrics, release as rel
from packages.core.agentspec import list_specs, load
from packages.core.closedloop import review
from packages.core.db import init_db, session_scope
from packages.core.models import (Approval, AuditLog, EvalCase, EvalRun, Handoff, KbDoc,
                                  Message, Release, Ticket, ToolCall, AgentRun, now, uid)
from packages.core import queue
from packages.core.tickets import append_user_message, create_ticket, get_ticket_view
from packages.core.tracing import trace_tree
from packages.evals.runner import gate, run_suite, seed_datasets

app = FastAPI(title="afterpilot-api")
ADMIN_TOKEN = config.env("ADMIN_TOKEN", "dev-admin-token")


class ChatIn(BaseModel):
    customer_id: str
    message: str
    ticket_id: str | None = None
    spec: str | None = None


class DecideIn(BaseModel):
    decision: str
    reason: str = ""
    actor: str = "admin"


class ReviewIn(BaseModel):
    approve: bool
    correction: dict | None = None


class KbIn(BaseModel):
    slug: str
    title: str
    body: str


class ReleaseIn(BaseModel):
    spec: str = config.DEFAULT_SPEC
    version: str
    mode: str
    percent: int = 100


class ShadowIn(BaseModel):
    customer_id: str
    message: str
    spec: str = config.DEFAULT_SPEC


class FeedbackIn(BaseModel):
    value: str


def _admin(x_admin_token: str | None):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


@app.on_event("startup")
def startup():
    init_db()
    with session_scope() as s:
        n = s.execute(select(KbDoc)).scalars().all()
    if not n:
        kb.seed(config.ROOT / "agents" / "kb_seed.yaml")
    seed_datasets()


@app.get("/health")
def health():
    return {"ok": True, "provider": config.MODEL_PROVIDER, "db": config.DATABASE_URL.split("://")[0]}


@app.get("/metrics", response_class=PlainTextResponse)
def prom_metrics():
    return metrics.render()


@app.post("/api/chat")
def chat(body: ChatIn):
    if body.ticket_id:
        tid = append_user_message(body.ticket_id, body.message)
    else:
        tid = create_ticket(body.customer_id, body.message, spec=body.spec or config.DEFAULT_SPEC)
    return {"ticket_id": tid}


@app.get("/api/tickets/{ticket_id}")
def ticket_view(ticket_id: str):
    v = get_ticket_view(ticket_id)
    if not v:
        raise HTTPException(404, "ticket not found")
    return v


@app.post("/api/tickets/{ticket_id}/feedback")
def feedback(ticket_id: str, body: FeedbackIn):
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            raise HTTPException(404, "ticket not found")
        t.feedback = body.value
    if body.value == "bad":
        append_user_message(ticket_id, "[用户负反馈] 请重新处理该工单")
    elif body.value == "good":
        from packages.core.statemachine import transition, status_of
        if status_of(ticket_id) == "RESOLVED":
            transition(ticket_id, "CLOSED", actor="user", reason="positive feedback")
    return {"ok": True}


@app.get("/api/admin/tickets")
def admin_tickets(status: str = "", limit: int = 50, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        q = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
        if status:
            q = q.where(Ticket.status == status)
        rows = s.execute(q).scalars().all()
        return [{"id": t.id, "customer_id": t.customer_id, "type": t.type, "status": t.status,
                 "spec": t.spec, "release_mode": t.release_mode, "version": t.prompt_version,
                 "created_at": t.created_at.isoformat()} for t in rows]


@app.get("/api/admin/approvals")
def admin_approvals(status: str = "pending", x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(Approval).where(Approval.status == status)
                         .order_by(Approval.created_at.desc())).scalars().all()
        return [{"id": a.id, "ticket_id": a.ticket_id, "kind": a.kind, "status": a.status,
                 "payload": a.payload, "created_at": a.created_at.isoformat()} for a in rows]


@app.post("/api/admin/approvals/{approval_id}/decide")
def admin_decide(approval_id: str, body: DecideIn, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        if a is None:
            raise HTTPException(404, "approval not found")
        if a.status != "pending":
            raise HTTPException(409, "already decided")
        a.status, a.decided_by, a.reason, a.decided_at = body.decision, body.actor, body.reason, now()
        tid = a.ticket_id
        s.add(AuditLog(actor=body.actor, action="approval_decide", target=approval_id,
                       detail={"decision": body.decision, "reason": body.reason, "payload": a.payload}))
    queue.enqueue("resume_approval", tid, {"approval_id": approval_id})
    return {"ok": True, "ticket_id": tid}


@app.get("/api/admin/runs")
def admin_runs(ticket_id: str = "", x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(100)
        if ticket_id:
            q = q.where(AgentRun.ticket_id == ticket_id)
        rows = s.execute(q).scalars().all()
        return [{"id": r.id, "ticket_id": r.ticket_id, "trace_id": r.trace_id, "status": r.status,
                 "step_idx": r.step_idx, "version": r.prompt_version, "model": r.model,
                 "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost": round(r.cost, 4),
                 "error": r.error} for r in rows]


@app.get("/api/admin/traces/{trace_id}")
def admin_trace(trace_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return trace_tree(trace_id)


@app.get("/api/admin/handoffs")
def admin_handoffs(reviewed: bool | None = None, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        q = select(Handoff).order_by(Handoff.created_at.desc()).limit(100)
        if reviewed is not None:
            q = q.where(Handoff.reviewed == reviewed)
        rows = s.execute(q).scalars().all()
        return [{"id": h.id, "ticket_id": h.ticket_id, "reason": h.reason, "reviewed": h.reviewed,
                 "eval_case_id": h.eval_case_id, "snapshot": h.snapshot,
                 "created_at": h.created_at.isoformat()} for h in rows]


@app.post("/api/admin/handoffs/{handoff_id}/review")
def admin_review(handoff_id: str, body: ReviewIn, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return review(handoff_id, body.approve, body.correction)


@app.get("/api/admin/candidates")
def admin_candidates(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(EvalCase).where(EvalCase.status == "candidate")
                         .order_by(EvalCase.created_at.desc())).scalars().all()
        return [{"id": c.id, "suite": c.suite, "name": c.name, "input": c.input,
                 "expect": c.expect, "source": c.source} for c in rows]


@app.post("/api/admin/evals/run")
def admin_eval_run(suite: str = "scenario", provider: str | None = None, version: str = "v1",
                   x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return run_suite(suite, provider_name=provider, prompt_version=version)


@app.get("/api/admin/evals/runs")
def admin_eval_runs(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50)).scalars().all()
        return [{"id": r.id, "suite": r.suite, "version": r.prompt_version, "provider": r.provider,
                 "metrics": r.metrics, "passed": r.passed, "created_at": r.created_at.isoformat()} for r in rows]


@app.post("/api/admin/evals/gate")
def admin_eval_gate(run_ids: list[str], baseline: str = "deploy/eval_baseline.json",
                    x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return gate(run_ids, config.ROOT / baseline)


@app.get("/api/admin/costs")
def admin_costs(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(Ticket).order_by(Ticket.created_at.desc()).limit(100)).scalars().all()
        ids = [t.id for t in rows]
    return {"summary": cost.summary(), "tickets": [cost.ticket_cost(i) for i in ids]}


@app.get("/api/admin/kb")
def admin_kb(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(KbDoc).where(KbDoc.active == True)).scalars().all()  # noqa: E712
        return [{"slug": d.slug, "version": d.version, "title": d.title, "body": d.body} for d in rows]


@app.post("/api/admin/kb")
def admin_kb_upsert(body: KbIn, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    ver = kb.upsert(body.slug, body.title, body.body)
    with session_scope() as s:
        s.add(AuditLog(actor="admin", action="kb_upsert", target=body.slug, detail={"version": ver}))
    return {"slug": body.slug, "version": ver}


@app.get("/api/admin/specs")
def admin_specs(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    out = []
    for name in list_specs():
        sp = load(name)
        out.append({"name": sp.name, "version": sp.version, "tools": sp.tools,
                    "ticket_types": sp.ticket_types, "policy": sp.policy,
                    "release": rel.current(name)})
    return out


@app.get("/api/admin/releases")
def admin_releases(spec: str = config.DEFAULT_SPEC, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(Release).where(Release.spec == spec)
                         .order_by(Release.created_at.desc()).limit(20)).scalars().all()
        return [{"id": r.id, "version": r.version, "mode": r.mode, "percent": r.percent,
                 "active": r.active, "created_at": r.created_at.isoformat()} for r in rows]


@app.post("/api/admin/releases")
def admin_release_set(body: ReleaseIn, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    try:
        return rel.set_release(body.spec, body.version, body.mode, body.percent)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/admin/guardrails/check")
def admin_guardrails(spec: str = config.DEFAULT_SPEC, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"breach": rel.check_guardrails(spec)}


@app.post("/api/admin/shadow")
def admin_shadow(body: ShadowIn, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    cur = rel.current(body.spec)
    tid = create_ticket(body.customer_id, body.message, spec=body.spec,
                        release_mode="shadow", prompt_version=cur["version"])
    return {"ticket_id": tid, "version": cur["version"]}


@app.get("/api/admin/audit")
def admin_audit(limit: int = 100, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    with session_scope() as s:
        rows = s.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()
        return [{"actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail,
                 "at": a.created_at.isoformat()} for a in rows]


app.mount("/", StaticFiles(directory=pathlib.Path(config.ROOT) / "web", html=True), name="web")