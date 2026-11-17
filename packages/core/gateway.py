"""Tool execution boundary: allowlist -> schema -> intent log -> policy -> idempotent execute.
Crash/timeout safety: query-before-retry; unknown write state escalates to human, never blind retry."""
import httpx
from sqlalchemy import select

from . import config, tools
from .db import session_scope
from .models import ToolCall, uid
from .policy import check_invoice, check_refund
from .tracing import span


class LocalGateway:
    def execute(self, trace_id: str, run_id: str, ticket_id: str, customer_id: str,
                name: str, args: dict, allowed: list, pol: dict,
                approval: dict | None = None) -> dict:
        if name not in allowed:
            raise tools.ToolDenied(f"tool {name} not in agent allowlist")
        tools.validate_args(name, args)
        scope = tools.TOOL_SPECS[name]["scope"]
        key = tools.idem_key(ticket_id, name, args) if scope == "write" else None
        with span(trace_id, f"tool.{name}", "tool", run_id=run_id, args=args, scope=scope) as sid:
            with session_scope() as s:
                tc = None
                if key:
                    tc = s.execute(select(ToolCall).where(ToolCall.idempotency_key == key)).scalar_one_or_none()
                if tc is None:
                    tc = ToolCall(id=uid(), run_id=run_id, ticket_id=ticket_id, tool=name,
                                  args=args, idempotency_key=key, intent_status="planned")
                    s.add(tc)
                    s.flush()
                tc_id = tc.id
                prior = tc.intent_status
                prior_result = tc.result
            if scope == "write":
                if prior == "succeeded":
                    return prior_result  # idempotent replay, no downstream call
                if prior == "unknown":
                    raise tools.ToolError("write state unknown; human reconciliation required")
                if prior == "executing":  # crash leftover: reconcile before doing anything
                    rec = self._reconcile(name, key)
                    if rec is not None:
                        self._mark(tc_id, "succeeded", rec)
                        return rec
                    self._mark(tc_id, "planned")
                self._policy_gate(name, args, customer_id, pol, approval, key)
            tools.breaker_check(name)
            self._mark(tc_id, "executing")  # intent log BEFORE downstream call: crash-safe
            result = self._call_with_retry(name, args, key, scope, tc_id)
            self._mark(tc_id, "succeeded", result)
            tools.breaker_record(name, True)
            return result

    def _policy_gate(self, name, args, customer_id, pol, approval, key):
        if name == "refund.create":
            order = tools.fn_order_query({"order_id": args["order_id"]})
            if order.get("error"):
                raise tools.ToolDenied("order_not_found")
            recent = tools.fn_refund_query({"order_id": args["order_id"]})
            count = len([r for r in recent.get("refunds", []) if r.get("customer_id") == customer_id])
            from datetime import datetime, timezone
            def days(iso):
                if not iso:
                    return None
                return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).days
            dec = check_refund(order, customer_id, float(args["amount"]), count,
                               days(order.get("delivered_at")), days(order.get("shipped_at")), pol)
            if not dec.ok:
                raise tools.ToolDenied(",".join(dec.reasons))
            if dec.approval:
                payload = {"tool": name, "args": args, "reasons": dec.reasons,
                           "order_paid": order.get("paid_amount")}
                ph = tools.payload_hash(payload)
                payload["payload_hash"] = ph
                if approval and approval.get("status") == "approved" and approval.get("payload_hash") == ph:
                    return
                raise tools.NeedsApproval(payload)
        elif name == "invoice.issue":
            order = tools.fn_order_query({"order_id": args["order_id"]})
            dec = check_invoice(order, pol)
            if not dec.ok:
                raise tools.ToolDenied(",".join(dec.reasons))

    def _reconcile(self, name, key):
        fn = tools.RECONCILE.get(name)
        if fn is None:
            return None
        try:
            rec = fn({"key": key})
        except Exception:
            return None
        if rec and not rec.get("error"):
            return rec
        return None

    def _call_with_retry(self, name, args, key, scope, tc_id):
        fn = tools.FNS[name]
        attempts = config.TOOL_RETRIES + 1
        last = None
        for i in range(attempts):
            try:
                return fn(args, key)
            except httpx.TimeoutException as exc:
                last = exc
                if scope == "write":
                    rec = self._reconcile(name, key)
                    if rec is not None:
                        return rec  # downstream committed despite timeout
            except httpx.HTTPStatusError as exc:
                self._mark(tc_id, "failed", error=str(exc))
                tools.breaker_record(name, False)
                raise tools.ToolError(f"{name} http {exc.response.status_code}") from exc
        if scope == "write":
            rec = self._reconcile(name, key)
            if rec is not None:
                return rec
            self._mark(tc_id, "unknown")  # never blind-retry an unknown write
            raise tools.ToolTimeout(f"{name} state unknown after timeout")
        self._mark(tc_id, "failed", error="timeout")
        raise tools.ToolTimeout(f"{name} timeout")

    def _mark(self, tc_id, status, result=None, error=""):
        with session_scope() as s:
            tc = s.get(ToolCall, tc_id)
            tc.intent_status = status
            if result is not None:
                tc.result = result
            tc.error = error