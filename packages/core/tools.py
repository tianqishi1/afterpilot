"""Tool registry: JSON Schema + permission scope + timeout/retry/breaker.
Write tools are idempotent via Idempotency-Key; mock-shop honors the key downstream."""
import hashlib
import json
import time

import httpx

from . import config


class ToolError(Exception):
    pass


class ToolDenied(ToolError):
    pass


class ToolUnavailable(ToolError):
    pass


class ToolTimeout(ToolError):
    pass


class NeedsApproval(ToolError):
    def __init__(self, payload: dict):
        super().__init__("needs_approval")
        self.payload = payload


_breaker: dict = {}


def breaker_check(name: str) -> None:
    st = _breaker.get(name)
    if st and st[0] >= config.BREAKER_THRESHOLD:
        if time.time() - st[1] < config.BREAKER_COOLDOWN:
            raise ToolUnavailable(f"circuit open: {name}")
        _breaker[name] = [0, 0.0]


def breaker_record(name: str, ok: bool) -> None:
    st = _breaker.setdefault(name, [0, 0.0])
    if ok:
        st[0] = 0
    else:
        st[0] += 1
        st[1] = time.time()


def _get(path: str, params: dict | None = None):
    r = httpx.get(config.MOCK_SHOP_URL + path, params=params, timeout=config.TOOL_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict, key: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    r = httpx.post(config.MOCK_SHOP_URL + path, json=body, headers=headers, timeout=config.TOOL_TIMEOUT)
    r.raise_for_status()
    return r.json()


TOOL_SPECS = {
    "order.query": {"scope": "read", "schema": {"type": "object", "properties": {
        "order_id": {"type": "string"}, "customer_id": {"type": "string"}}}},
    "logistics.track": {"scope": "read", "schema": {"type": "object", "properties": {
        "order_id": {"type": "string"}}, "required": ["order_id"]}},
    "refund.query": {"scope": "read", "schema": {"type": "object", "properties": {
        "order_id": {"type": "string"}, "key": {"type": "string"}}}},
    "refund.create": {"scope": "write", "schema": {"type": "object", "properties": {
        "order_id": {"type": "string"}, "amount": {"type": "number"},
        "reason": {"type": "string"}}, "required": ["order_id", "amount", "reason"]}},
    "invoice.issue": {"scope": "write", "schema": {"type": "object", "properties": {
        "order_id": {"type": "string"}, "title": {"type": "string"},
        "tax_no": {"type": "string"}}, "required": ["order_id", "title", "tax_no"]}},
    "kb.search": {"scope": "read", "schema": {"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}},
}


def fn_order_query(args: dict, key=None):
    if args.get("order_id"):
        return _get(f"/orders/{args['order_id']}")
    return {"orders": _get("/orders", {"customer_id": args.get("customer_id", "")})}


def fn_logistics_track(args: dict, key=None):
    return _get(f"/logistics/{args['order_id']}")


def fn_refund_query(args: dict, key=None):
    if args.get("key"):
        return _get(f"/refunds/by-key/{args['key']}")
    return {"refunds": _get("/refunds", {"order_id": args.get("order_id", "")})}


def fn_refund_create(args: dict, key=None):
    return _post("/refunds", {"order_id": args["order_id"], "amount": args["amount"],
                              "reason": args["reason"]}, key)


def fn_invoice_issue(args: dict, key=None):
    return _post("/invoices", {"order_id": args["order_id"], "title": args["title"],
                               "tax_no": args["tax_no"]}, key)


def fn_kb_search(args: dict, key=None):
    from .kb import search
    return {"hits": search(args["query"], int(args.get("k", 3)))}


FNS = {
    "order.query": fn_order_query, "logistics.track": fn_logistics_track,
    "refund.query": fn_refund_query, "refund.create": fn_refund_create,
    "invoice.issue": fn_invoice_issue, "kb.search": fn_kb_search,
}
RECONCILE = {"refund.create": fn_refund_query, "invoice.issue": fn_invoice_issue}


def validate_args(name: str, args: dict) -> None:
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise ToolDenied(f"unknown tool {name}")
    schema = spec["schema"]
    types = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
    for req in schema.get("required", []):
        if req not in args:
            raise ToolDenied(f"missing arg {req} for {name}")
    for k, v in args.items():
        prop = schema.get("properties", {}).get(k)
        if prop is None:
            raise ToolDenied(f"unexpected arg {k} for {name}")
        if not isinstance(v, types[prop["type"]]):
            raise ToolDenied(f"bad type for {k} in {name}")


def idem_key(ticket_id: str, name: str, args: dict) -> str:
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return f"{ticket_id}:{name}:{hashlib.sha1(canon.encode()).hexdigest()[:16]}"


def payload_hash(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]