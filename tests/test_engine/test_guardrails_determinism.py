"""Guardrail evaluation is reproducible (spec 006 FR-022, SC-010).

Same inputs → same findings, every run. No clock read inside evaluation, no randomness,
no LLM call anywhere in the path.
"""
from __future__ import annotations

import inspect
from datetime import date

from app.engine import baselines, guardrails
from app.engine.guardrails import (
    evaluate_acwr,
    evaluate_hrv,
    evaluate_monotony,
    evaluate_ramp_rate,
    evaluate_resting_hr,
)
from app.services import response_verification
from app.services.response_verification import MetricRegistry, verify_response

D = date(2026, 8, 28)


def test_workload_evaluators_are_pure_and_repeatable():
    runs = [
        (
            evaluate_acwr(atl=140.0, ctl=100.0, finding_date=D),
            evaluate_ramp_rate(6.5, finding_date=D),
            evaluate_monotony(2.6, finding_date=D),
        )
        for _ in range(50)
    ]
    assert all(r == runs[0] for r in runs)


def test_recovery_evaluators_are_pure_and_repeatable():
    runs = [
        (
            evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D),
            evaluate_resting_hr(observed=57.0, baseline=50.0, finding_date=D),
        )
        for _ in range(50)
    ]
    assert all(r == runs[0] for r in runs)


def test_verification_is_repeatable():
    r = MetricRegistry()
    r.register("ctl", 45.9)
    r.register("tsb", -18.8)
    text = "Ton CTL est à 62 et ton TSB à -18.8, séance de 2h en Z2."
    results = [verify_response(text, r) for _ in range(50)]
    first = results[0]
    for res in results:
        assert [c.metric for c in res.failures] == [c.metric for c in first.failures]
        assert len(res.passed) == len(first.passed)


def test_no_datetime_now_or_random_in_evaluation_modules():
    """A clock read or an RNG call inside evaluation would break SC-010. Checks the
    parsed AST (not the source text — a docstring may legitimately say 'randomness')."""
    import ast

    for module in (guardrails, baselines, response_verification):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name for a in node.names}
                assert "random" not in names, f"{module.__name__} imports random"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "random", f"{module.__name__} imports from random"
            if isinstance(node, ast.Attribute) and node.attr in ("now", "today", "time"):
                # datetime.now(...) / date.today() / time.time() — a clock read
                assert False, f"{module.__name__} reads the clock via .{node.attr}"
