"""Release drill: shadow dry-run -> eval-gated canary -> guardrail rollback. Writes var/drill_release.json."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from apps.worker.main import drain  # noqa: E402
from packages.core import config, release as rel  # noqa: E402
from packages.core.db import init_db, session_scope  # noqa: E402
from packages.core.models import Ticket, ToolCall  # noqa: E402
from packages.core.tickets import create_ticket, get_ticket_view  # noqa: E402
from packages.evals.runner import run_suite  # noqa: E402


def main():
    init_db()
    report = {"steps": []}
    cur = rel.current(config.DEFAULT_SPEC)
    tid = create_ticket("c1", "o1004 我要退货", release_mode="shadow", prompt_version=cur["version"])
    drain()
    v = get_ticket_view(tid)
    refunds = httpx.get(config.MOCK_SHOP_URL + "/refunds", params={"order_id": "o1004"}).json()
    with session_scope() as s:
        sim = s.execute(select(ToolCall).where(ToolCall.ticket_id == tid,
                                               ToolCall.intent_status == "simulated")).scalars().all()
    ok_shadow = v["ticket"]["status"] == "CLOSED" and not refunds and bool(sim) \
        and not any(m["role"] == "agent" for m in v["messages"])
    report["steps"].append({"step": "shadow", "ok": ok_shadow, "ticket": tid})
    ev = run_suite("scenario", prompt_version=cur["version"])
    rel.set_release(config.DEFAULT_SPEC, cur["version"], "canary", 50)
    report["steps"].append({"step": "canary_promotion", "ok": ev["passed"],
                            "eval_pass_rate": ev["metrics"]["pass_rate"]})
    with session_scope() as s:
        for _ in range(12):
            s.add(Ticket(customer_id="c1", status="ESCALATED", spec=config.DEFAULT_SPEC, release_mode="ga"))
    breach = rel.check_guardrails(config.DEFAULT_SPEC)
    after = rel.current(config.DEFAULT_SPEC)
    report["steps"].append({"step": "guardrail_rollback", "ok": bool(breach) and after["mode"] == "ga",
                            "breach": breach, "now": after})
    report["ok"] = all(s["ok"] for s in report["steps"])
    out = config.ROOT / "var" / "drill_release.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()