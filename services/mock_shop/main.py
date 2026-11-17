"""Mock shop downstream: orders/logistics/refunds/invoices with seed data and fault injection.
Honors Idempotency-Key downstream (double-submit safe). Fault modes:
none | latency | error | timeout_after_commit (commits then sleeps past client timeout).
Reconcile endpoints (/by-key) bypass faults so query-before-retry always works."""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="afterpilot-mock-shop")
LOCK = threading.Lock()
STATE: dict = {}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if dt else None


def seed():
    n = _now()
    STATE["orders"] = {
        "o1001": {"id": "o1001", "customer_id": "c1", "status": "delivered", "paid_amount": 199.0,
                  "refunded_amount": 0.0, "invoiced": False, "created_at": _iso(n - timedelta(days=6)),
                  "shipped_at": _iso(n - timedelta(days=5)), "delivered_at": _iso(n - timedelta(days=3)),
                  "items": [{"sku": "sku-1", "name": "无线鼠标", "qty": 1}]},
        "o1002": {"id": "o1002", "customer_id": "c1", "status": "shipped", "paid_amount": 89.0,
                  "refunded_amount": 0.0, "invoiced": False, "created_at": _iso(n - timedelta(days=12)),
                  "shipped_at": _iso(n - timedelta(days=10)), "delivered_at": None,
                  "items": [{"sku": "sku-2", "name": "手机壳", "qty": 2}]},
        "o1003": {"id": "o1003", "customer_id": "c2", "status": "delivered", "paid_amount": 259.0,
                  "refunded_amount": 0.0, "invoiced": False, "created_at": _iso(n - timedelta(days=45)),
                  "shipped_at": _iso(n - timedelta(days=43)), "delivered_at": _iso(n - timedelta(days=40)),
                  "items": [{"sku": "sku-3", "name": "机械键盘", "qty": 1}]},
        "o1004": {"id": "o1004", "customer_id": "c1", "status": "delivered", "paid_amount": 5999.0,
                  "refunded_amount": 0.0, "invoiced": False, "created_at": _iso(n - timedelta(days=4)),
                  "shipped_at": _iso(n - timedelta(days=3)), "delivered_at": _iso(n - timedelta(days=2)),
                  "items": [{"sku": "sku-4", "name": "笔记本电脑", "qty": 1}]},
        "o1005": {"id": "o1005", "customer_id": "c2", "status": "delivered", "paid_amount": 59.0,
                  "refunded_amount": 0.0, "invoiced": True, "created_at": _iso(n - timedelta(days=3)),
                  "shipped_at": _iso(n - timedelta(days=2)), "delivered_at": _iso(n - timedelta(days=1)),
                  "items": [{"sku": "sku-5", "name": "数据线", "qty": 1}]},
    }
    STATE["logistics"] = {
        "o1001": {"order_id": "o1001", "status": "delivered", "days_in_transit": 2,
                  "last_event": "已签收，签收人：本人", "stuck": False, "eta_days": 0},
        "o1002": {"order_id": "o1002", "status": "in_transit", "days_in_transit": 10,
                  "last_event": "到达华东转运中心", "stuck": True, "eta_days": None},
        "o1003": {"order_id": "o1003", "status": "delivered", "days_in_transit": 3,
                  "last_event": "已签收", "stuck": False, "eta_days": 0},
        "o1004": {"order_id": "o1004", "status": "delivered", "days_in_transit": 1,
                  "last_event": "已签收，签收人：前台", "stuck": False, "eta_days": 0},
        "o1005": {"order_id": "o1005", "status": "delivered", "days_in_transit": 1,
                  "last_event": "已签收", "stuck": False, "eta_days": 0},
    }
    STATE["refunds"] = {}
    STATE["refunds_by_key"] = {}
    STATE["invoices"] = {}
    STATE["invoices_by_key"] = {}
    STATE["fault"] = {"mode": "none", "sleep": 5.0}


seed()


def _fault_sleep(commit_done: bool):
    f = STATE["fault"]
    if f["mode"] == "latency":
        time.sleep(f["sleep"])
    elif f["mode"] == "timeout_after_commit" and commit_done:
        time.sleep(f["sleep"])


@app.get("/health")
def health():
    return {"ok": True, "fault": STATE["fault"]}


@app.get("/admin/fault")
def get_fault():
    return STATE["fault"]


@app.post("/admin/fault")
def set_fault(body: dict):
    # no LOCK: fault switch must stay responsive while a write sleeps holding LOCK
    STATE["fault"] = {"mode": body.get("mode", "none"), "sleep": float(body.get("sleep", 5.0))}
    return STATE["fault"]


@app.post("/admin/reset")
def reset():
    with LOCK:
        seed()
    return {"ok": True}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    _fault_sleep(False)
    if STATE["fault"]["mode"] == "error":
        raise HTTPException(500, "injected error")
    return STATE["orders"].get(order_id, {"error": "not_found"})


@app.get("/orders")
def list_orders(customer_id: str = ""):
    _fault_sleep(False)
    if STATE["fault"]["mode"] == "error":
        raise HTTPException(500, "injected error")
    return [o for o in STATE["orders"].values() if not customer_id or o["customer_id"] == customer_id]


@app.get("/logistics/{order_id}")
def get_logistics(order_id: str):
    _fault_sleep(False)
    if STATE["fault"]["mode"] == "error":
        raise HTTPException(500, "injected error")
    return STATE["logistics"].get(order_id, {"error": "not_found"})


@app.post("/refunds")
def create_refund(body: dict, idempotency_key: str = Header(default=None)):
    with LOCK:
        if idempotency_key and idempotency_key in STATE["refunds_by_key"]:
            return STATE["refunds_by_key"][idempotency_key]  # idempotent replay
        if STATE["fault"]["mode"] == "error":
            raise HTTPException(500, "injected error")
        o = STATE["orders"].get(body.get("order_id", ""))
        if not o:
            raise HTTPException(400, "order_not_found")
        amount = float(body.get("amount", 0))
        if amount <= 0 or amount > o["paid_amount"] - o["refunded_amount"] + 1e-9:
            raise HTTPException(400, "amount_exceeds_paid")
        rid = "r" + uuid.uuid4().hex[:8]
        refund = {"id": rid, "order_id": o["id"], "customer_id": o["customer_id"], "amount": amount,
                  "reason": body.get("reason", ""), "status": "succeeded", "created_at": _iso(_now())}
        o["refunded_amount"] = round(o["refunded_amount"] + amount, 2)
        STATE["refunds"][rid] = refund
        if idempotency_key:
            STATE["refunds_by_key"][idempotency_key] = refund
        _fault_sleep(True)  # may sleep past client timeout AFTER commit
        return refund


@app.get("/refunds/by-key/{key}")
def refund_by_key(key: str):
    return STATE["refunds_by_key"].get(key, {"error": "not_found"})


@app.get("/refunds")
def list_refunds(order_id: str = ""):
    return [r for r in STATE["refunds"].values() if not order_id or r["order_id"] == order_id]


@app.post("/invoices")
def create_invoice(body: dict, idempotency_key: str = Header(default=None)):
    with LOCK:
        if idempotency_key and idempotency_key in STATE["invoices_by_key"]:
            return STATE["invoices_by_key"][idempotency_key]
        if STATE["fault"]["mode"] == "error":
            raise HTTPException(500, "injected error")
        o = STATE["orders"].get(body.get("order_id", ""))
        if not o:
            raise HTTPException(400, "order_not_found")
        if o["status"] != "delivered":
            raise HTTPException(400, "invoice_requires_delivered")
        if o["invoiced"]:
            raise HTTPException(400, "already_invoiced")
        iid = "inv" + uuid.uuid4().hex[:8]
        inv = {"id": iid, "order_id": o["id"], "title": body.get("title", "个人"),
               "tax_no": body.get("tax_no", ""), "status": "issued", "created_at": _iso(_now())}
        o["invoiced"] = True
        STATE["invoices"][iid] = inv
        if idempotency_key:
            STATE["invoices_by_key"][idempotency_key] = inv
        _fault_sleep(True)
        return inv


@app.get("/invoices/by-key/{key}")
def invoice_by_key(key: str):
    return STATE["invoices_by_key"].get(key, {"error": "not_found"})