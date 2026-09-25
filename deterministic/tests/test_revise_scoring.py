"""Tests for REVISE handling in the shared scorer.

Two things matter: REVISE rows must be scored as their own outcome, and
adding them must not move any ALLOW/BLOCK number that earlier tasks reported.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import evaluate, load_actions
from common.schema import Decision, GuardrailResult


def _actions():
    base = load_actions()[0]
    return [
        replace(base, record_id="A1", expected_decision="ALLOW"),
        replace(base, record_id="B1", expected_decision="BLOCK"),
        replace(base, record_id="R1", expected_decision="REVISE"),
        replace(base, record_id="R2", expected_decision="REVISE"),
        replace(base, record_id="R3", expected_decision="REVISE"),
    ]


def _guardrail(by_id):
    def run(action):
        result = GuardrailResult(record_id=action.record_id)
        decision = by_id[action.record_id]
        if decision is not Decision.ALLOW:
            result.apply(decision, "T", "test")
        return result
    return run


def test_revise_outcomes_are_counted_separately():
    actions = _actions()
    run = evaluate(_guardrail({
        "A1": Decision.ALLOW,
        "B1": Decision.BLOCK,
        "R1": Decision.FLAG,    # correct
        "R2": Decision.BLOCK,   # over-strict but contained
        "R3": Decision.ALLOW,   # miss
    }), actions, "test")

    m = run.metrics
    assert m.revise_total == 3
    assert (m.revise_flagged, m.revise_blocked, m.revise_allowed) == (1, 1, 1)
    assert m.revise_accuracy == 1 / 3
    assert m.revise_contained == 2 / 3


def test_revise_rows_stay_out_of_the_binary_confusion_matrix():
    """A REVISE row must not be counted as benign or as an attack."""
    actions = _actions()
    run = evaluate(_guardrail({a.record_id: Decision.BLOCK for a in actions}), actions, "test")

    m = run.metrics
    assert (m.tp, m.fn) == (1, 0)      # the one BLOCK row
    assert (m.tn, m.fp) == (0, 1)      # the one ALLOW row, wrongly blocked
    assert m.tp + m.fn + m.tn + m.fp == 2
    assert m.revise_total == 3


def test_metrics_dict_omits_revise_fields_when_there_are_none():
    """Existing two-class result files keep their exact shape."""
    base = load_actions()[0]
    run = evaluate(_guardrail({"X": Decision.ALLOW}),
                   [replace(base, record_id="X", expected_decision="ALLOW")], "test")
    assert "revise_total" not in run.metrics.as_dict()


def test_breakdown_reports_revise_containment():
    actions = _actions()
    run = evaluate(_guardrail({
        "A1": Decision.ALLOW, "B1": Decision.BLOCK,
        "R1": Decision.FLAG, "R2": Decision.FLAG, "R3": Decision.ALLOW,
    }), actions, "test")
    stats = run.breakdown("sheet")[actions[0].sheet]
    assert stats["revise_contained"] == round(2 / 3, 4)


def test_failures_can_list_revise_misses():
    actions = _actions()
    run = evaluate(_guardrail({
        "A1": Decision.ALLOW, "B1": Decision.BLOCK,
        "R1": Decision.FLAG, "R2": Decision.FLAG, "R3": Decision.ALLOW,
    }), actions, "test")
    missed = run.failures("revise")
    assert [a.record_id for a in missed] == ["R3"]


def test_loading_generated_rows_adds_revise_records():
    extended = load_actions(include_generated=True)
    revise = [a for a in extended if a.expected_decision == "REVISE"]
    assert len(revise) == 750
    assert len(load_actions()) == 7200      # default view unchanged
