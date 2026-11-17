"""End-to-end flows through the real queue + loop + gateway + mock-shop."""
import httpx
from sqlalchemy import select, update

from apps.worker.main import drain
from packages.core import config, queue
from packages.core.closedloop import review
from packages.core.db import session_scope
from packages.core.models import Approval, EvalCase, EvalRun, Handoff, Message, Ticket, ToolCall
from packages.core import release as rel
from packages.core.tickets import create_ticket, get_ticket_view


def _refunds(order_id):
    return httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": order_id}).json()


def test_five_happy_flows():
    cases = [
        ("c1", "我的订单 o1001 到哪了", "签收"),
        ("c1", "o1002 物流十天没更新了", "停滞"),
        ("c1", "o1001 退款进度", "暂无退款记录"),
        ("c1", "给 o1001 开发票 抬头:个人", "发票"),
        ("c1", "退货运费谁出", "运费"),
    ]
    for cid, msg, kw in cases:
        tid = create_ticket(cid, msg)
        drain()
        v = get_ticket_view(tid)
        assert v["ticket"]["status"] == "RESOLVED", (msg, v["ticket"]["status"])
        assert any(m["role"] == "agent" and kw in m["content"] for m in v["messages"]), msg


def test_return_small_auto_refund():
    tid = create_ticket("c1", "o1001 不想要了，退货")
    drain()
    v = get_ticket_view(tid)
    assert v["ticket"]["status"] == "RESOLVED"
    assert len(_refunds("o1001")) == 1


def test_high_amount_approval_then_approve():
    tid = create_ticket("c1", "o1004 我要退货")
    drain()
    assert get_ticket_view(tid)["ticket"]["status"] == "WAIT_APPROVAL"
    assert len(_refunds("o1004")) == 0
    with session_scope() as s:
        a = s.execute(select(Approval).where(Approval.ticket_id == tid)).scalars().one()
        aid = a.id
        s.execute(update(Approval).where(Approval.id == aid).values(status="approved", decided_by="qa"))
    queue.enqueue("resume_approval", tid, {"approval_id": aid})
    drain()
    v = get_ticket_view(tid)
    assert v["ticket"]["status"] == "RESOLVED"
    rs = _refunds("o1004")
    assert len(rs) == 1 and rs[0]["amount"] == 5999.0


def test_high_amount_approval_rejected():
    tid = create_ticket("c1", "o1004 我要退货")
    drain()
    with session_scope() as s:
        a = s.execute(select(Approval).where(Approval.ticket_id == tid)).scalars().one()
        aid = a.id
        s.execute(update(Approval).where(Approval.id == aid).values(status="rejected", decided_by="qa"))
    queue.enqueue("resume_approval", tid, {"approval_id": aid})
    drain()
    assert get_ticket_view(tid)["ticket"]["status"] == "ESCALATED"
    assert len(_refunds("o1004")) == 0


def test_out_of_window_escalates_and_creates_candidate():
    tid = create_ticket("c2", "o1003 退货")
    drain()
    assert get_ticket_view(tid)["ticket"]["status"] == "ESCALATED"
    assert len(_refunds("o1003")) == 0
    with session_scope() as s:
        h = s.execute(select(Handoff).where(Handoff.ticket_id == tid)).scalars().one()
        c = s.get(EvalCase, h.eval_case_id)
        assert c.status == "candidate" and c.source == "handoff"
        hid, cid = h.id, c.id
    out = review(hid, True, {"expect": {"reply_contains": ["人工"]}})
    assert out["case_status"] == "active"
    with session_scope() as s:
        assert "人工" in s.get(EvalCase, cid).expect["reply_contains"]


def test_shadow_mode_no_side_effects():
    tid = create_ticket("c1", "o1004 我要退货", release_mode="shadow", prompt_version="v1")
    drain()
    v = get_ticket_view(tid)
    assert v["ticket"]["status"] == "CLOSED"
    assert not any(m["role"] == "agent" for m in v["messages"])
    assert len(_refunds("o1004")) == 0
    with session_scope() as s:
        sims = s.execute(select(ToolCall).where(ToolCall.ticket_id == tid,
                                                ToolCall.intent_status == "simulated")).scalars().all()
        assert sims


def test_release_eval_gate_canary_and_guardrail_rollback():
    try:
        rel.set_release("after_sales", "v2", "ga")
        raise AssertionError("promotion without eval must be blocked")
    except ValueError:
        pass
    with session_scope() as s:
        s.add(EvalRun(suite="scenario", prompt_version="v2", passed=True, metrics={"pass_rate": 0.9}))
    rel.set_release("after_sales", "v2", "ga")
    with session_scope() as s:
        s.add(EvalRun(suite="scenario", prompt_version="v3", passed=True, metrics={"pass_rate": 0.9}))
    rel.set_release("after_sales", "v3", "canary", 0)
    tid = create_ticket("c1", "hi")
    with session_scope() as s:
        t = s.get(Ticket, tid)
        assert t.prompt_version == "v2" and t.release_mode == "ga"  # 0% canary -> stable
    rel.set_release("after_sales", "v3", "canary", 100)
    with session_scope() as s:
        for i in range(12):
            s.add(Ticket(customer_id="c1", status="ESCALATED", spec="after_sales", release_mode="ga"))
    breach = rel.check_guardrails("after_sales")
    assert breach and breach["breach"] == "escalation_rate"
    assert rel.current("after_sales")["mode"] == "ga"
    assert rel.current("after_sales")["version"] == "v2"


def test_negative_feedback_reopens():
    tid = create_ticket("c1", "我的订单 o1001 到哪了")
    drain()
    from packages.core.tickets import append_user_message
    append_user_message(tid, "[用户负反馈] 请重新处理该工单")
    with session_scope() as s:
        assert s.get(Ticket, tid).status == "NEW"
    drain()
    assert get_ticket_view(tid)["ticket"]["status"] == "RESOLVED"


def test_metrics_exposition():
    from packages.core import metrics
    tid = create_ticket("c1", "我的订单 o1001 到哪了")
    drain()
    text = metrics.render()
    assert "run_seconds" in text and "tickets_by_status" in text and "cost_per_ticket_yuan" in text