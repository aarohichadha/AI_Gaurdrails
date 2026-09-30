"""Tests for the Task 25 ML -> LLM cascade.

None of these call an API: routing, fusion and bookkeeping are exercised with
the oracle and coin judges plus a stub, and the Gemini path is tested through
its cache.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from combination.ml_llm import judges
from combination.ml_llm.run_cascade import MLStage, run_cascade, route, score, UNCERTAINTY
from common import load_actions
from common.schema import Action, Decision

import numpy as np


@pytest.fixture(scope="module")
def actions():
    base = load_actions()[:6]
    return [
        replace(base[0], record_id="r0", expected_decision="ALLOW"),
        replace(base[1], record_id="r1", expected_decision="BLOCK"),
        replace(base[2], record_id="r2", expected_decision="BLOCK"),
        replace(base[3], record_id="r3", expected_decision="ALLOW"),
    ]


def stage(decisions, uncertainty) -> MLStage:
    return MLStage(name="stub", decision=decisions, uncertainty=uncertainty)


# -- uncertainty ----------------------------------------------------------

def test_margin_is_highest_when_top_two_classes_tie():
    probabilities = np.array([[0.5, 0.5, 0.0], [0.9, 0.05, 0.05]])
    values = UNCERTAINTY["margin"](probabilities)
    assert values[0] == pytest.approx(1.0)
    assert values[1] == pytest.approx(1 - 0.85)


def test_entropy_is_one_for_a_uniform_prediction():
    uniform = np.array([[1 / 3, 1 / 3, 1 / 3]])
    assert UNCERTAINTY["entropy"](uniform)[0] == pytest.approx(1.0)
    peaked = np.array([[1.0, 0.0, 0.0]])
    assert UNCERTAINTY["entropy"](peaked)[0] == pytest.approx(0.0, abs=1e-6)


def test_confidence_uncertainty_is_one_minus_top_probability():
    assert UNCERTAINTY["confidence"](np.array([[0.7, 0.2, 0.1]]))[0] == pytest.approx(0.3)


# -- routing --------------------------------------------------------------

def test_routing_picks_the_least_confident_records(actions):
    st = stage(
        {a.record_id: Decision.ALLOW for a in actions},
        {"r0": 0.9, "r1": 0.1, "r2": 0.8, "r3": 0.2},
    )
    picked = [a.record_id for a in route(st, actions, 0.5)]
    assert picked == ["r0", "r2"]


def test_zero_budget_routes_nothing(actions):
    st = stage({a.record_id: Decision.ALLOW for a in actions}, {a.record_id: 0.9 for a in actions})
    assert route(st, actions, 0.0) == []


def test_fully_certain_records_are_never_escalated(actions):
    """Spending a call on a record with zero uncertainty buys nothing."""
    st = stage(
        {a.record_id: Decision.ALLOW for a in actions},
        {"r0": 0.0, "r1": 0.0, "r2": 0.0, "r3": 0.4},
    )
    assert [a.record_id for a in route(st, actions, 1.0)] == ["r3"]


# -- fusion ---------------------------------------------------------------

def test_judge_overrides_the_ml_decision_on_escalated_records(actions):
    """ML says ALLOW for everything; the oracle must fix the two attacks."""
    st = stage(
        {a.record_id: Decision.ALLOW for a in actions},
        {a.record_id: 1.0 for a in actions},
    )
    results, stats = run_cascade(st, actions, judges.OracleJudge(), 1.0, progress=False)

    assert stats["routed_rate"] == 1.0
    assert results["r1"].decision is Decision.BLOCK
    assert results["r2"].decision is Decision.BLOCK
    assert results["r0"].decision is Decision.ALLOW
    assert score(results, actions, "t").metrics.accuracy == 1.0


def test_unescalated_records_keep_the_ml_decision(actions):
    st = stage(
        {"r0": Decision.ALLOW, "r1": Decision.BLOCK, "r2": Decision.ALLOW, "r3": Decision.ALLOW},
        {"r0": 0.0, "r1": 0.0, "r2": 0.0, "r3": 0.9},
    )
    results, stats = run_cascade(st, actions, judges.OracleJudge(), 0.25, progress=False)
    assert stats["escalated"] == 1
    assert results["r2"].decision is Decision.ALLOW      # ML kept, still wrong
    assert "ESCALATED" in results["r3"].rules_fired
    assert "ESCALATED" not in results["r1"].rules_fired


def test_escalated_records_are_marked_for_audit(actions):
    st = stage({a.record_id: Decision.ALLOW for a in actions},
               {a.record_id: 1.0 for a in actions})
    results, _ = run_cascade(st, actions, judges.OracleJudge(), 1.0, progress=False)
    assert all("ESCALATED" in r.rules_fired for r in results.values())


# -- judge vocabulary -----------------------------------------------------

def test_confirm_and_revise_both_mean_flag():
    assert judges.DECISION_MAP["CONFIRM"] is Decision.FLAG
    assert judges.DECISION_MAP["REVISE"] is Decision.FLAG


def test_invalid_judge_output_never_becomes_allow():
    """A malformed or failed judge reply must not approve the action."""
    assert judges.DECISION_MAP["INVALID"] is Decision.FLAG


def test_oracle_judge_matches_ground_truth(actions):
    judge = judges.OracleJudge()
    assert judge(actions[0]).decision is Decision.ALLOW
    assert judge(actions[1]).decision is Decision.BLOCK
    revise = replace(actions[0], expected_decision="REVISE")
    assert judge(revise).decision is Decision.FLAG


def test_coin_judge_is_deterministic_for_a_seed(actions):
    first = [judges.CoinJudge(seed=3)(a).raw_decision for a in actions]
    second = [judges.CoinJudge(seed=3)(a).raw_decision for a in actions]
    assert first == second


# -- caching --------------------------------------------------------------

def test_gemini_judge_reads_its_cache_without_calling_the_api(tmp_path, actions, monkeypatch):
    cache = tmp_path / "cache.jsonl"
    cache.write_text(json.dumps({
        "record_id": "r1", "decision": "BLOCK", "reason": "cached", "latency_s": 0.1,
    }) + "\n", encoding="utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    judge = judges.GeminiJudge(cache_path=cache)

    verdict = judge(actions[1])
    assert verdict.decision is Decision.BLOCK
    assert verdict.cached is True
    assert judge.calls == 0          # nothing was sent


def test_action_to_row_exposes_what_the_judge_prompt_needs(actions):
    row = judges.action_to_row(actions[0])
    for key in ("email_body", "subject", "sender", "user_instruction",
                "authorization_evidence", "requested_destination"):
        assert key in row


def test_judge_prompt_contains_no_ground_truth(actions):
    from llm_judge.judge import prepare_input

    action = actions[1]
    prompt = prepare_input(judges.action_to_row(action), "full_defense")
    for leaked in (action.expected_decision, action.label, action.attack_technique):
        if leaked and leaked.lower() not in ("none", ""):
            assert leaked not in prompt


# -- bookkeeping ----------------------------------------------------------

def test_stats_count_calls_and_escalation(actions):
    st = stage({a.record_id: Decision.ALLOW for a in actions},
               {a.record_id: 1.0 for a in actions})
    _, stats = run_cascade(st, actions, judges.CoinJudge(seed=1), 0.5, progress=False)
    assert stats["escalated"] == 2
    assert stats["routed_rate"] == 0.5
    assert len(stats["judge_verdicts"]) == 2
