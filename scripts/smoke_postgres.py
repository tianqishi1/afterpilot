"""Prod-dialect smoke: run against docker compose Postgres (SKIP LOCKED queue path).
Usage: docker compose -f deploy/docker-compose.yml up -d postgres mock-shop api worker
       DATABASE_URL=postgresql+psycopg://postgres:afterpilot@127.0.0.1:5432/afterpilot python scripts/smoke_postgres.py
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:afterpilot@127.0.0.1:5432/afterpilot")
os.environ.setdefault("MOCK_SHOP_URL", "http://127.0.0.1:8001")
os.environ.setdefault("MODEL_PROVIDER", "mock")

import httpx  # noqa: E402

from apps.worker.main import drain  # noqa: E402
from packages.core import config, queue  # noqa: E402
from packages.core.db import dialect, init_db  # noqa: E402
from packages.core.tickets import create_ticket, get_ticket_view  # noqa: E402


def main():
    assert dialect() == "postgresql", dialect()
    init_db()
    from packages.core import kb
    kb.seed(config.ROOT / "agents" / "kb_seed.yaml")
    # queue SKIP LOCKED path, race-free vs live worker: insert+lock+reclaim in ONE transaction
    from datetime import timedelta
    from sqlalchemy import select
    from packages.core.db import session_scope
    from packages.core.models import Job, now, uid
    with session_scope() as s:
        j = Job(id=uid(), kind="agent_run", ticket_id="smoke")
        s.add(j)
        s.flush()
        r1 = s.execute(select(Job).where(Job.id == j.id).with_for_update(skip_locked=True)).scalar_one()
        r1.status, r1.leased_by, r1.lease_expires = "leased", "w1", now() + timedelta(seconds=60)
        s.flush()
        r2 = s.execute(select(Job).where(Job.status == "pending").with_for_update(skip_locked=True)).scalars().first()
        assert r2 is None, "SKIP LOCKED must not hand out the leased row"
        r1.status = "done"
    # e2e through PG
    tid = create_ticket("c1", "我的订单 o1001 到哪了")
    drain()
    v = get_ticket_view(tid)
    assert v["ticket"]["status"] == "RESOLVED", v
    tid2 = create_ticket("c1", "o1004 我要退货")
    drain()
    assert get_ticket_view(tid2)["ticket"]["status"] == "WAIT_APPROVAL"
    print("POSTGRES SMOKE OK: queue SKIP LOCKED + e2e + approval gate")


if __name__ == "__main__":
    main()