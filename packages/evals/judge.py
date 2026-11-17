"""LLM-as-judge with deterministic rule floor. Calibration vs human labels required before CI trust."""
import json

from packages.core.compliance import check as compliance_check
from packages.core.llm import get_provider

RUBRIC = """You are a calibrated QA judge for an e-commerce after-sales agent.
Score the agent reply against the customer ask and the tool observations.
Criteria: factual consistency with observations (no invented logistics/refund state),
policy compliance (no promises beyond policy), completeness (answers the ask), tone.
Return JSON only: {"verdict":"pass"|"fail","score":0..1,"reason":"..."}."""


def judge_scenario(case: dict, outcome: dict, provider=None) -> dict:
    provider = provider or get_provider()
    reply = outcome.get("reply", "")
    score = 1.0
    reasons = []
    if not reply:
        score = 0.0
        reasons.append("no_reply")
    ok, creasons = compliance_check(reply, {"observations": outcome.get("observations", [])})
    if not ok:
        score = 0.0
        reasons += creasons
    for kw in case.get("expect", {}).get("reply_contains", []):
        if kw not in reply:
            score -= 0.3
            reasons.append(f"missing:{kw}")
    score = max(0.0, score)
    verdict = "pass" if score >= 0.7 else "fail"
    llm = None
    if provider.name == "openai" and reply:
        try:
            raw, _u = provider.complete(
                [{"role": "system", "content": RUBRIC},
                 {"role": "user", "content": json.dumps(
                     {"ask": case.get("input", {}).get("message", ""), "reply": reply,
                      "observations": outcome.get("observations", [])}, ensure_ascii=False)}],
                "judge", schema=True)
            llm = json.loads(raw)
            if llm.get("verdict") == "fail":
                verdict = "fail"
                score = min(score, float(llm.get("score", 0.0)))
                reasons.append("llm_judge:" + llm.get("reason", ""))
        except Exception as exc:  # noqa: BLE001 - judge outage must not silently pass
            verdict = "fail"
            reasons.append(f"judge_error:{exc}")
    return {"score": round(score, 3), "verdict": verdict, "reason": ";".join(reasons) or "ok",
            "judge": provider.name, "llm": llm}


def calibrate(labels: list[dict], provider_name: str | None = None) -> dict:
    """labels: [{name, human: pass|fail, case: {...}, outcome: {...}}]; returns agreement + Cohen kappa."""
    provider = get_provider(provider_name)
    pairs = []
    for row in labels:
        j = judge_scenario(row["case"], row["outcome"], provider)
        pairs.append((row["human"], j["verdict"]))
    n = len(pairs)
    if not n:
        return {"n": 0}
    po = sum(1 for h, m in pairs if h == m) / n
    ph = sum(1 for h, _ in pairs if h == "pass") / n
    pm = sum(1 for _, m in pairs if m == "pass") / n
    pe = ph * pm + (1 - ph) * (1 - pm)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {"n": n, "agreement": round(po, 3), "cohen_kappa": round(kappa, 3),
            "trusted_for_ci": kappa >= 0.7, "pairs": pairs}