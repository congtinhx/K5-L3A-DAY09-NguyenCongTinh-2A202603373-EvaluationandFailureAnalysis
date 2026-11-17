"""Regression test from docs/trace_case.md: seeded_regression C-014@11.

First wrong step: event 5 `proposal` 120000 (quote was 150000) after event 4 `refund_history` 30000 -- the agent
deducted the prior refund itself (FM-05); the host deducted it again, so the ledger holds 90.000đ while the reply
says 120.000đ (FM-08). The same claim@seed on `fixed` is clean.
"""
import pytest

pytestmark = pytest.mark.student


def _graded(shape: str):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, "C-014", 11)
    ctx = EvaluatorContext(name="trace_case", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    return case["trial"], {name: cls().evaluate(ctx).value for name, cls in graders.GRADERS.items()}


def test_lab9_trace_case_seeded_regression_c014_is_caught():
    trial, v = _graded("seeded_regression")
    assert trial["snapshot"]["refund_vnd"] == 90000
    assert v["reply_matches_ledger"] is False        # reply 120.000đ vs ledger 90.000đ (FM-08)
    assert v["refund_amount_correct"] is False       # ledger 90.000đ vs label 120.000đ
    assert v["quote_kept"] is False                  # proposal 120000 != quote 150000 (FM-05)


def test_lab9_trace_case_fixed_c014_is_clean():
    trial, v = _graded("fixed")
    assert trial["snapshot"]["refund_vnd"] == 120000
    assert v["reply_matches_ledger"] is True
    assert v["refund_amount_correct"] is True
    assert v["quote_kept"] is True
