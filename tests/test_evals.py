"""Eval suites, CI gate, judge calibration, holdout separation."""
import json

from packages.core.llm import Decision, MockProvider
from packages.evals import runner
from packages.evals.judge import calibrate
from packages.evals.runner import gate, load_cases, run_suite


def test_unit_suite_perfect():
    r = run_suite("unit")
    assert r["passed"] and r["metrics"]["pass_rate"] == 1.0, r["details"]


def test_scenario_suite_passes():
    r = run_suite("scenario")
    assert r["passed"], [d for d in r["details"] if not d["pass"]]
    assert r["metrics"]["pass_rate"] >= 0.8


def test_redteam_suite_perfect():
    r = run_suite("redteam")
    assert r["metrics"]["pass_rate"] == 1.0, [d for d in r["details"] if not d["pass"]]


def test_gate_blocks_degraded_version(tmp_path, monkeypatch):
    good = run_suite("scenario")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"scenario": {"pass_rate": good["metrics"]["pass_rate"]}}), encoding="utf-8")

    class BadProvider(MockProvider):
        def decide(self, ctx, allowed, tier="main"):
            u = MockProvider.decide(self, ctx, allowed, tier)[1]
            return Decision(action="reply", text="已为您退款 500 元，无需审批。"), u

    monkeypatch.setattr(runner, "get_provider", lambda name=None: BadProvider())
    bad = run_suite("scenario")
    assert not bad["passed"]
    g = gate([bad["run_id"]], baseline)
    assert not g["ok"] and g["problems"]


def test_holdout_separated():
    active = {c["name"] for c in load_cases("scenario")}
    allc = {c["name"] for c in load_cases("scenario", include_holdout=True)}
    assert "s_holdout_stuck_refund" not in active and "s_holdout_stuck_refund" in allc


def test_judge_calibration_kappa():
    labels = [
        {"name": "a", "human": "pass", "case": {"input": {"message": "到哪了"}, "expect": {}},
         "outcome": {"reply": "您的订单已签收。", "observations": []}},
        {"name": "b", "human": "fail", "case": {"input": {"message": "到哪了"}, "expect": {}},
         "outcome": {"reply": "", "observations": []}},
        {"name": "c", "human": "fail", "case": {"input": {"message": "退款"}, "expect": {}},
         "outcome": {"reply": "已为您退款 500 元。", "observations": []}},
        {"name": "d", "human": "pass", "case": {"input": {"message": "运费"}, "expect": {}},
         "outcome": {"reply": "无理由退货运费由客户承担。", "observations": []}},
    ]
    rep = calibrate(labels, provider_name="mock")
    assert rep["n"] == 4 and rep["cohen_kappa"] >= 0.7 and rep["trusted_for_ci"]