"""Deterministic core: state machine, policy matrix, queue lease, kb, redact, idempotency, timeout reconcile."""
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from packages.core import config, queue, tools
from packages.core.db import session_scope
from packages.core.gateway import LocalGateway
from packages.core.kb import search, upsert
from packages.core.models import Job, Ticket, ToolCall, now, uid
from packages.core.policy import check_refund
from packages.core.redact import redact_text
from packages.core.statemachine import IllegalTransition, transition
from packages.core.tickets import create_ticket

POL = {"approval_threshold": 200, "window_days": 15, "freq_limit": 2, "stuck_days": 7}


def _order(status="delivered", paid=199.0, refunded=0.0, cust="c1"):
    return {"id": "oX", "customer_id": cust, "status": status, "paid_amount": paid, "refunded_amount": refunded}


def test_statemachine_illegal():
    tid = create_ticket("c1", "hi")
    with pytest.raises(IllegalTransition):
        transition(tid, "RESOLVED")  # NEW -> RESOLVED not allowed
    transition(tid, "TRIAGED")
    transition(tid, "HANDLING")
    transition(tid, "WAIT_APPROVAL")
    transition(tid, "HANDLING")
    transition(tid, "RESOLVED")
    transition(tid, "CLOSED")
    transition(tid, "NEW")  # reopen
    with session_scope() as s:
        assert s.get(Ticket, tid).reopen_count == 1


def test_policy_matrix():
    assert not check_refund(None, "c1", 10, 0, 1, 1, POL).ok
    assert not check_refund(_order(cust="c2"), "c1", 10, 0, 1, 1, POL).ok
    assert not check_refund(_order(), "c1", 500, 0, 1, 1, POL).ok
    assert not check_refund(_order(), "c1", 10, 2, 1, 1, POL).ok
    assert not check_refund(_order(), "c1", 10, 0, 40, 1, POL).ok
    assert not check_refund(_order(status="shipped"), "c1", 10, 0, None, 2, POL).ok
    d = check_refund(_order(status="shipped"), "c1", 10, 0, None, 10, POL)
    assert d.ok and d.approval
    d = check_refund(_order(paid=5999), "c1", 5999, 0, 1, 1, POL)
    assert d.ok and d.approval
    d = check_refund(_order(), "c1", 199, 0, 3, 1, POL)
    assert d.ok and not d.approval


def test_queue_lease_reclaim():
    jid = queue.enqueue("agent_run", "t1")
    a = queue.claim("w1")
    assert a and a["id"] == jid
    assert queue.claim("w2") is None
    with session_scope() as s:
        s.execute(update(Job).where(Job.id == jid).values(lease_expires=now() - timedelta(seconds=1)))
    b = queue.claim("w2")
    assert b and b["id"] == jid and b["attempts"] == 2
    queue.finish(jid, "done")
    assert queue.claim("w3") is None


def test_kb_versioning_and_retrieval():
    v1 = upsert("return_policy", "退换货政策", "签收后 15 天内支持无理由退货。")
    hits = search("多少天内可以退货")
    assert hits and hits[0]["slug"] == "return_policy"
    v2 = upsert("return_policy", "退换货政策", "签收后 30 天内支持无理由退货。")
    assert v2 == v1 + 1
    hits = search("退货 天数")
    assert hits[0]["version"] == v2 and "30" in hits[0]["snippet"]


def test_redact():
    assert "[REDACTED:phone]" in redact_text("联系 13812345678 谢谢")
    assert "[REDACTED:email]" in redact_text("mail a.b@x.com ok")
    assert redact_text("普通文本") == "普通文本"


def test_idempotent_replay_single_refund():
    tid = create_ticket("c1", "o1001 退货")
    g = LocalGateway()
    args = {"order_id": "o1001", "amount": 199.0, "reason": "test"}
    r1 = g.execute("tr1", "run1", tid, "c1", "refund.create", args, ["refund.create", "order.query", "refund.query"], POL)
    r2 = g.execute("tr2", "run2", tid, "c1", "refund.create", args, ["refund.create", "order.query", "refund.query"], POL)
    assert r1["id"] == r2["id"]
    refunds = httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": "o1001"}).json()
    assert len(refunds) == 1


def test_timeout_after_commit_reconciles():
    httpx.post(config.MOCK_SHOP_URL + "/admin/fault", json={"mode": "timeout_after_commit", "sleep": 5})
    tid = create_ticket("c1", "o1001 退货")
    g = LocalGateway()
    args = {"order_id": "o1001", "amount": 199.0, "reason": "test"}
    res = g.execute("tr1", "run1", tid, "c1", "refund.create", args, ["refund.create", "order.query", "refund.query"], POL)
    assert res["status"] == "succeeded"
    refunds = httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": "o1001"}).json()
    assert len(refunds) == 1
    with session_scope() as s:
        tc = s.execute(select(ToolCall).where(ToolCall.ticket_id == tid)).scalars().one()
        assert tc.intent_status == "succeeded"


def test_crash_leftover_executing_reconciles():
    """Simulate process death between downstream commit and checkpoint: intent stays executing."""
    tid = create_ticket("c1", "o1001 退货")
    args = {"order_id": "o1001", "amount": 199.0, "reason": "test"}
    key = tools.idem_key(tid, "refund.create", args)
    committed = httpx.post(config.MOCK_SHOP_URL + "/refunds", json=args, headers={"Idempotency-Key": key}).json()
    with session_scope() as s:
        s.add(ToolCall(id=uid(), run_id="dead-run", ticket_id=tid, tool="refund.create",
                       args=args, idempotency_key=key, intent_status="executing"))
    g = LocalGateway()
    res = g.execute("tr2", "run2", tid, "c1", "refund.create", args, ["refund.create", "order.query", "refund.query"], POL)
    assert res["id"] == committed["id"]
    refunds = httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": "o1001"}).json()
    assert len(refunds) == 1


def test_schema_and_allowlist_denials():
    g = LocalGateway()
    with pytest.raises(tools.ToolDenied):
        g.execute("tr", "r", "t", "c1", "refund.create", {"order_id": "o1001"}, ["refund.create"], POL)
    with pytest.raises(tools.ToolDenied):
        g.execute("tr", "r", "t", "c1", "order.query", {}, ["kb.search"], POL)
    with pytest.raises(tools.ToolDenied):
        g.execute("tr", "r", "t", "c1", "admin.reset", {}, ["admin.reset"], POL)