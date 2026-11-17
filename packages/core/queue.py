"""DB-backed lease queue. sqlite: atomic UPDATE..RETURNING; postgres: SKIP LOCKED.
No Redis in MVP (ADR-001); lease expiry gives crash-safe reclaim."""
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from . import config
from .db import dialect, session_scope
from .models import Job, now, uid


def enqueue(kind: str, ticket_id: str = "", payload: dict | None = None) -> str:
    jid = uid()
    with session_scope() as s:
        s.add(Job(id=jid, kind=kind, ticket_id=ticket_id, payload=payload or {}))
    return jid


def _claimable():
    n = now()
    return or_(Job.status == "pending", and_(Job.status == "leased", Job.lease_expires < n))


def claim(worker_id: str) -> dict | None:
    lease = now() + timedelta(seconds=config.LEASE_SECONDS)
    with session_scope() as s:
        if dialect() == "postgresql":
            row = s.execute(
                select(Job).where(_claimable(), Job.attempts < config.MAX_JOB_ATTEMPTS)
                .order_by(Job.created_at).limit(1).with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status, row.leased_by, row.lease_expires = "leased", worker_id, lease
            row.attempts += 1
            s.flush()
            return _snap(row)
        # sqlite: single atomic statement = write-lock serialized, no lost claims
        sub = (
            select(Job.id).where(_claimable(), Job.attempts < config.MAX_JOB_ATTEMPTS)
            .order_by(Job.created_at).limit(1).scalar_subquery()
        )
        row = s.execute(
            update(Job).where(Job.id == sub)
            .values(status="leased", leased_by=worker_id, lease_expires=lease, attempts=Job.attempts + 1)
            .returning(Job)
        ).scalar_one_or_none()
        return _snap(row) if row is not None else None


def _snap(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "ticket_id": j.ticket_id, "payload": j.payload, "attempts": j.attempts}


def heartbeat(job_id: str, worker_id: str) -> None:
    with session_scope() as s:
        s.execute(
            update(Job).where(Job.id == job_id, Job.leased_by == worker_id)
            .values(lease_expires=now() + timedelta(seconds=config.LEASE_SECONDS))
        )


def finish(job_id: str, status: str = "done", error: str = "") -> None:
    with session_scope() as s:
        s.execute(update(Job).where(Job.id == job_id).values(status=status, last_error=error))


def dead_letter(job_id: str, error: str) -> None:
    finish(job_id, "dead", error)


def counts() -> dict:
    with session_scope() as s:
        rows = s.execute(select(Job.status, Job.id)).all()
    out: dict = {}
    for st, _ in rows:
        out[st] = out.get(st, 0) + 1
    return out