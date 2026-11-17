"""Chaos: kill -9 worker between downstream commit and checkpoint; lease expiry + reconcile => no double refund."""
import os
import subprocess
import sys
import time

import httpx
from sqlalchemy import select

from apps.worker.main import drain
from packages.core import config, tools
from packages.core.db import session_scope
from packages.core.models import ToolCall
from packages.core.tickets import create_ticket, get_ticket_view

ROOT = config.ROOT


def test_kill9_mid_refund_no_double_refund():
    env = {**os.environ, "MOCK_SHOP_URL": config.MOCK_SHOP_URL, "MODEL_PROVIDER": "mock",
           "PYTHONPATH": str(ROOT), "LEASE_SECONDS": "2"}
    httpx.post(config.MOCK_SHOP_URL + "/admin/fault", json={"mode": "timeout_after_commit", "sleep": 6})
    tid = create_ticket("c1", "o1001 不想要了，退货")
    proc = subprocess.Popen([sys.executable, "-m", "apps.worker.main", "--workers", "1"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    key = tools.idem_key(tid, "refund.create",
                         {"order_id": "o1001", "amount": 199.0, "reason": "customer_return_request"})
    try:
        seen = False
        deadline = time.time() + 20
        while time.time() < deadline:
            with session_scope() as s:
                tc = s.execute(select(ToolCall).where(ToolCall.ticket_id == tid,
                                                      ToolCall.tool == "refund.create")).scalars().first()
            rec = httpx.get(config.MOCK_SHOP_URL + f"/refunds/by-key/{key}").json()
            # crash window: downstream committed, client intent still executing (no checkpoint yet)
            if tc and tc.intent_status == "executing" and rec.get("status") == "succeeded":
                seen = True
                break
            time.sleep(0.05)
        assert seen, "never observed the commit-before-checkpoint crash window"
        proc.kill()  # SIGKILL-equivalent on Windows: TerminateProcess
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    rec = httpx.get(config.MOCK_SHOP_URL + f"/refunds/by-key/{key}").json()
    assert rec.get("status") == "succeeded", "downstream should have committed before the kill"
    httpx.post(config.MOCK_SHOP_URL + "/admin/fault", json={"mode": "none"}, timeout=15)
    time.sleep(2.3)  # lease expiry -> restarted worker may reclaim
    drain()
    v = get_ticket_view(tid)
    assert v["ticket"]["status"] == "RESOLVED", v
    refunds = httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": "o1001"}).json()
    assert len(refunds) == 1, "DOUBLE REFUND DETECTED"
    with session_scope() as s:
        tc = s.execute(select(ToolCall).where(ToolCall.ticket_id == tid,
                                              ToolCall.tool == "refund.create")).scalars().one()
        assert tc.intent_status == "succeeded"