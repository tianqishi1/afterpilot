"""Output guard: deterministic rules always; optional cheap-model check when provider=openai.
Blocks false money claims, prompt leakage, PII in replies."""
import re

from .redact import redact_text

LEAK_RX = re.compile(r"system prompt|系统提示词|内部指令|ignore (all )?previous", re.I)
CLAIM_RX = re.compile(r"已(为您)?退款|退款已完成|退款已执行|发票已开具")


def check(reply: str, ctx: dict) -> tuple[bool, list[str]]:
    reasons = []
    obs = {o["tool"]: o["result"] for o in ctx.get("observations", [])}
    if CLAIM_RX.search(reply):
        if "退款" in reply and not obs.get("refund.create"):
            reasons.append("false_refund_claim")
        if "发票" in reply and not obs.get("invoice.issue"):
            reasons.append("false_invoice_claim")
    if LEAK_RX.search(reply):
        reasons.append("prompt_leak")
    if redact_text(reply) != reply:
        reasons.append("pii_in_reply")
    return (not reasons), reasons