"""Hard money rules in code, enforced at the write-tool boundary. Model output can never bypass."""
from dataclasses import dataclass, field


@dataclass
class Decision:
    ok: bool
    approval: bool = False
    reasons: list = field(default_factory=list)


def check_refund(order, customer_id: str, amount: float, recent_count: int,
                 days_since_delivery, days_since_ship, pol: dict) -> Decision:
    if not order:
        return Decision(False, reasons=["order_not_found"])
    if order.get("customer_id") != customer_id:
        return Decision(False, reasons=["not_your_order"])
    if order.get("status") not in ("shipped", "delivered"):
        return Decision(False, reasons=["order_not_refundable_state"])
    remaining = round(order.get("paid_amount", 0) - order.get("refunded_amount", 0), 2)
    if amount <= 0 or amount > remaining + 1e-9:
        return Decision(False, reasons=["amount_exceeds_paid"])
    if recent_count >= pol.get("freq_limit", 2):
        return Decision(False, reasons=["frequency_cap"])
    window = pol.get("window_days", 15)
    if order.get("status") == "delivered":
        if days_since_delivery is None or days_since_delivery > window:
            return Decision(False, reasons=["out_of_window"])
    else:  # shipped: only stuck shipments may be refunded early, and only with approval
        if days_since_ship is None or days_since_ship < pol.get("stuck_days", 7):
            return Decision(False, reasons=["shipment_not_stuck"])
    reasons = []
    approval = False
    if amount > pol.get("approval_threshold", 200):
        approval = True
        reasons.append("amount_threshold")
    if order.get("status") == "shipped":
        approval = True
        reasons.append("stuck_shipment_early_refund")
    return Decision(True, approval=approval, reasons=reasons)


def check_invoice(order, pol: dict) -> Decision:
    if not order:
        return Decision(False, reasons=["order_not_found"])
    if order.get("status") != "delivered":
        return Decision(False, reasons=["invoice_requires_delivered"])
    if order.get("invoiced"):
        return Decision(False, reasons=["already_invoiced"])
    return Decision(True)