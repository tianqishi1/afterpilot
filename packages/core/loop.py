"""Agent runtime: deterministic state machine backbone + LLM decision nodes.
Checkpoint per step; crash-safe resume; policy/approval gates at write boundary;
shadow mode simulates writes and never sends replies."""
import json

from sqlalchemy import select, update

from . import config
from . import tools as T
from .agentspec import load
from .closedloop import on_handoff
from .compliance import check as compliance_check
from .db import session_scope
from .gateway import LocalGateway
from .llm import LLMError, get_provider
from .models import AgentRun, Approval, Message, Ticket, ToolCall, now, uid
from .queue import heartbeat
from .statemachine import status_of, transition
from .tracing import add_usage, new_trace_id, span

MAX_MSGS = 6
OBS_CHARS = 1200


def _prune(obj, depth=0):
    if isinstance(obj, str):
        return obj[:200]
    if isinstance(obj, list):
        return [_prune(v, depth + 1) for v in obj[:5]]
    if isinstance(obj, dict):
        return {k: _prune(v, depth + 1) for k, v in obj.items()}
    return obj


class AgentLoop:
    def __init__(self, provider=None, gateway=None):
        self.provider = provider or get_provider()
        self.gateway = gateway or LocalGateway()

    # ---- entry points ----------------------------------------------------
    def run_ticket(self, ticket_id: str, job_id: str = "", worker_id: str = "") -> str:
        with session_scope() as s:
            t = s.get(Ticket, ticket_id)
            spec_name, pver, rmode = t.spec, t.prompt_version, t.release_mode
        spec = load(spec_name)
        trace = new_trace_id()
        run_id = uid()
        with session_scope() as s:
            s.add(AgentRun(id=run_id, ticket_id=ticket_id, trace_id=trace,
                           spec=spec_name, prompt_version=pver, model=self.provider.name))
        try:
            with span(trace, "agent.run", "run", run_id=run_id, ticket=ticket_id,
                      spec=spec_name, version=pver, mode=rmode):
                self._run(ticket_id, run_id, trace, spec, job_id, worker_id)
        except Exception as exc:  # noqa: BLE001 - runtime failure must escalate, not crash worker
            self._escalate(ticket_id, run_id, trace, f"runtime_error:{exc}", {})
        with session_scope() as s:
            return s.get(AgentRun, run_id).status

    def resume_approval(self, ticket_id: str, approval_id: str, job_id: str = "", worker_id: str = "") -> str:
        with session_scope() as s:
            a = s.get(Approval, approval_id)
            if a is None:
                raise ValueError("approval missing")
            run_id, status, payload = a.run_id, a.status, dict(a.payload)
            r = s.get(AgentRun, run_id)
            trace, spec_name = r.trace_id, r.spec
        if status == "pending":
            raise ValueError("approval still pending")
        if status == "rejected":
            self._escalate(ticket_id, run_id, trace, "approval_rejected", {})
            return "escalated"
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            r.status = "running"
            r.checkpoint = {**(r.checkpoint or {}),
                            "approved_payload": {**payload, "status": status, "approval_id": approval_id},
                            "pending_approval_id": ""}
        transition(ticket_id, "HANDLING", actor="approval", reason="approved")
        try:
            with span(trace, "agent.resume", "run", run_id=run_id, approval=approval_id):
                self._run(ticket_id, run_id, trace, load(spec_name), job_id, worker_id)
        except Exception as exc:  # noqa: BLE001
            self._escalate(ticket_id, run_id, trace, f"runtime_error:{exc}", {})
        with session_scope() as s:
            return s.get(AgentRun, run_id).status

    # ---- main loop -------------------------------------------------------
    def _run(self, ticket_id, run_id, trace, spec, job_id, worker_id):
        with session_scope() as s:
            t = s.get(Ticket, ticket_id)
            cid, ctype, rmode = t.customer_id, t.type, t.release_mode
            msgs = s.execute(select(Message).where(Message.ticket_id == ticket_id)
                             .order_by(Message.created_at.desc()).limit(MAX_MSGS)).scalars().all()
            last_user = next((m.content for m in msgs if m.role == "user"), "")
        if not ctype:
            with span(trace, "triage", "llm", run_id=run_id) as sid:
                ctype, slots2, u = self.provider.triage(last_user, {})
                add_usage(sid, u.tokens_in, u.tokens_out, u.model)
            self._usage(run_id, u)
            with session_scope() as s:
                t = s.get(Ticket, ticket_id)
                s.execute(update(Ticket).where(Ticket.id == ticket_id)
                          .values(type=ctype, slots={**(t.slots or {}), **slots2}))
            transition(ticket_id, "TRIAGED", actor="triage", reason=ctype)
        st = status_of(ticket_id)
        if st == "NEW":
            transition(ticket_id, "TRIAGED", actor="loop", reason="intake")
            st = "TRIAGED"
        if st == "TRIAGED":
            transition(ticket_id, "HANDLING", actor="loop")
        elif st == "WAIT_APPROVAL":
            transition(ticket_id, "HANDLING", actor="loop", reason="approval_resumed")
        elif st == "ESCALATED":
            transition(ticket_id, "HANDLING", actor="loop", reason="retry")
        elif st != "HANDLING":
            self._finish(run_id, "done")
            return
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            start = (r.checkpoint or {}).get("step_idx", 0)
            approved = (r.checkpoint or {}).get("approved_payload")
        for step in range(start, config.MAX_STEPS):
            if job_id:
                heartbeat(job_id, worker_id)
            ctx = self._context(ticket_id, spec, step)
            with span(trace, f"decide.step{step}", "llm", run_id=run_id, tier="main") as sid:
                try:
                    d, u = self.provider.decide(ctx, spec.tools, "main")
                except LLMError as exc:
                    self._usage(run_id, None)
                    self._escalate(ticket_id, run_id, trace, f"llm_error:{exc}", ctx)
                    return
                add_usage(sid, u.tokens_in, u.tokens_out, u.model)
            self._usage(run_id, u)
            self._checkpoint(run_id, step + 1)
            if d.action == "reply":
                ok, reasons = compliance_check(d.text or "", ctx)
                if not ok:
                    self._escalate(ticket_id, run_id, trace, "compliance:" + ",".join(reasons), ctx)
                    return
                if rmode == "shadow":
                    self._checkpoint(run_id, step + 1, {"shadow_reply": d.text})
                    self._finish(run_id, "done")
                    transition(ticket_id, "RESOLVED", actor="shadow")
                    transition(ticket_id, "CLOSED", actor="shadow", reason="shadow dry-run")
                    return
                with session_scope() as s:
                    s.add(Message(ticket_id=ticket_id, role="agent", content=d.text or ""))
                transition(ticket_id, "RESOLVED", actor="agent")
                self._finish(run_id, "done")
                return
            if d.action == "request_approval":
                self._wait_approval(ticket_id, run_id, {"tool": d.tool, "args": d.args or {},
                                                        "reasons": [d.reason or "model_request"]})
                return
            if d.action == "tool_call":
                name, args = d.tool, d.args or {}
                if rmode == "shadow" and T.TOOL_SPECS.get(name, {}).get("scope") == "write":
                    self._simulated(run_id, ticket_id, name, args)
                    continue
                try:
                    res = self.gateway.execute(trace, run_id, ticket_id, cid, name, args,
                                               spec.tools, spec.policy, approval=approved)
                except T.NeedsApproval as na:
                    self._wait_approval(ticket_id, run_id, na.payload)
                    return
                except T.ToolDenied as exc:
                    self._escalate(ticket_id, run_id, trace, f"policy_denied:{exc}", ctx)
                    return
                except T.ToolUnavailable as exc:
                    self._escalate(ticket_id, run_id, trace, f"tool_unavailable:{exc}", ctx)
                    return
                except T.ToolTimeout as exc:
                    self._escalate(ticket_id, run_id, trace, f"tool_timeout:{exc}", ctx)
                    return
                except T.ToolError as exc:
                    self._escalate(ticket_id, run_id, trace, f"tool_error:{exc}", ctx)
                    return
                continue
            if d.action == "escalate":
                self._escalate(ticket_id, run_id, trace, d.reason or "model_escalate", ctx)
                return
            self._escalate(ticket_id, run_id, trace, f"bad_action:{d.action}", ctx)
            return
        self._escalate(ticket_id, run_id, trace, "max_steps_exceeded", self._context(ticket_id, spec, config.MAX_STEPS))

    # ---- helpers ---------------------------------------------------------
    def _context(self, ticket_id, spec, step):
        with session_scope() as s:
            t = s.get(Ticket, ticket_id)
            msgs = s.execute(select(Message).where(Message.ticket_id == ticket_id)
                             .order_by(Message.created_at.desc()).limit(MAX_MSGS)).scalars().all()
            tcs = s.execute(select(ToolCall).where(ToolCall.ticket_id == ticket_id,
                                                   ToolCall.intent_status.in_(["succeeded", "simulated"]))
                            .order_by(ToolCall.created_at)).scalars().all()
            obs = [{"tool": tc.tool, "args": tc.args,
                    "result": _prune(tc.result if tc.intent_status == "succeeded" else {"simulated": True})}
                   for tc in tcs][-8:]
            ticket = {"id": t.id, "customer_id": t.customer_id, "type": t.type, "status": t.status,
                      "slots": t.slots or {}, "summary": t.summary or {}}
            messages = [{"role": m.role, "content": m.content[:500]} for m in reversed(msgs)]
        return {"ticket": ticket, "messages": messages, "observations": obs, "kb_hits": [],
                "policy": spec.policy, "persona": spec.persona, "step": step,
                "allowed_tools": spec.tools}

    def _wait_approval(self, ticket_id, run_id, payload):
        with session_scope() as s:
            a = Approval(ticket_id=ticket_id, run_id=run_id, kind=payload.get("tool", "model"), payload=payload)
            s.add(a)
            s.flush()
            aid = a.id
            r = s.get(AgentRun, run_id)
            r.status = "waiting_approval"
            r.checkpoint = {**(r.checkpoint or {}), "pending_approval_id": aid}
        transition(ticket_id, "WAIT_APPROVAL", actor="policy", reason=",".join(payload.get("reasons", [])))

    def _escalate(self, ticket_id, run_id, trace, reason, ctx):
        with span(trace, "escalate", "decision", run_id=run_id, reason=reason):
            on_handoff(ticket_id, run_id, reason, ctx or {})
            with session_scope() as s:
                s.add(Message(ticket_id=ticket_id, role="system", content=f"已转人工客服跟进（{reason}）"))
            st = status_of(ticket_id)
            if st in ("NEW", "TRIAGED", "HANDLING", "WAIT_APPROVAL"):
                transition(ticket_id, "ESCALATED", actor="loop", reason=reason)
            self._finish(run_id, "escalated", error=reason)

    def _simulated(self, run_id, ticket_id, name, args):
        with session_scope() as s:
            s.add(ToolCall(run_id=run_id, ticket_id=ticket_id, tool=name, args=args,
                           intent_status="simulated", result={"simulated": True}))

    def _checkpoint(self, run_id, step, extra=None):
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            r.step_idx = step
            r.checkpoint = {**(r.checkpoint or {}), "step_idx": step, **(extra or {})}

    def _usage(self, run_id, u):
        if u is None:
            return
        pin, pout = config.PRICES.get(u.model, (0.0, 0.0))
        cost = u.tokens_in / 1000 * pin + u.tokens_out / 1000 * pout
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            r.tokens_in += u.tokens_in
            r.tokens_out += u.tokens_out
            r.cost += cost
            r.model = u.model

    def _finish(self, run_id, status, error=""):
        with session_scope() as s:
            r = s.get(AgentRun, run_id)
            r.status = status
            r.ended_at = now()
            r.error = error