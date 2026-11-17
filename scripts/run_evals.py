"""Eval CLI: run suites, optionally write baseline or enforce CI gate. Exit code = gate result."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from packages.core.db import init_db  # noqa: E402
from packages.evals.runner import gate, run_suite  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="+", default=["unit", "scenario", "redteam"])
    ap.add_argument("--provider", default=None)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--write-baseline", default=None)
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--baseline", default="deploy/eval_baseline.json")
    ap.add_argument("--include-holdout", action="store_true")
    args = ap.parse_args()
    init_db()
    runs = {}
    for suite in args.suites:
        r = run_suite(suite, provider_name=args.provider, prompt_version=args.version,
                      include_holdout=args.include_holdout)
        runs[suite] = r
        print(f"[{suite}] n={r['metrics']['n']} pass_rate={r['metrics']['pass_rate']} passed={r['passed']}")
        for d in r["details"]:
            if not d["pass"]:
                print("   FAIL", d["name"], d["fails"])
    if args.write_baseline:
        base = {s: {"pass_rate": r["metrics"]["pass_rate"]} for s, r in runs.items()}
        pathlib.Path(args.write_baseline).write_text(json.dumps(base, indent=2), encoding="utf-8")
        print("baseline written:", args.write_baseline)
    if args.gate:
        g = gate([r["run_id"] for r in runs.values()], args.baseline)
        print("GATE:", json.dumps(g, ensure_ascii=False))
        sys.exit(0 if g["ok"] else 1)
    sys.exit(0 if all(r["passed"] for r in runs.values()) else 1)


if __name__ == "__main__":
    main()