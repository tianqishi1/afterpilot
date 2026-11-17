"""DB-backed spans (OTel-shaped). One trace per run; every LLM/tool/decision step a span.
Langfuse/OTel-collector export is an adapter evolution (ADR-004); DB trace is source of truth."""
import contextlib
import time

from . import config
from .db import session_scope
from .models import Span, uid
from .redact import redact_obj


def new_trace_id() -> str:
    return uid()


def _ms() -> float:
    return time.time() * 1000


@contextlib.contextmanager
def span(trace_id: str, name: str, kind: str, run_id: str = "", parent_id: str = "", **attrs):
    sid = uid()
    with session_scope() as s:
        s.add(Span(id=sid, trace_id=trace_id, run_id=run_id, parent_id=parent_id,
                   name=name, kind=kind, start_ms=_ms(), attrs=redact_obj(attrs)))
    try:
        yield sid
    except Exception as exc:
        with session_scope() as s:
            sp = s.get(Span, sid)
            sp.end_ms = _ms()
            sp.attrs = {**(sp.attrs or {}), "error": str(exc)}
        raise
    else:
        with session_scope() as s:
            sp = s.get(Span, sid)
            sp.end_ms = _ms()


def add_usage(span_id: str, tokens_in: int, tokens_out: int, model: str) -> float:
    pin, pout = config.PRICES.get(model, (0.0, 0.0))
    cost = tokens_in / 1000 * pin + tokens_out / 1000 * pout
    with session_scope() as s:
        sp = s.get(Span, span_id)
        if sp:
            sp.tokens_in += tokens_in
            sp.tokens_out += tokens_out
            sp.cost += cost
            sp.attrs = {**(sp.attrs or {}), "model": model}
    return cost


def trace_tree(trace_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            Span.__table__.select().where(Span.trace_id == trace_id).order_by(Span.start_ms)
        ).mappings().all()
    return [dict(r) for r in rows]