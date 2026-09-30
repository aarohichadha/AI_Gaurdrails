"""Tests for the hybrid guardrail's most-restrictive-wins combiner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.schema import Decision, GuardrailResult
from hybrid.combine import combine


def result(record_id: str, decision: Decision, rule: str, reason: str) -> GuardrailResult:
    r = GuardrailResult(record_id=record_id)
    r.apply(decision, rule, reason)
    return r


def test_combine_takes_most_restrictive_decision():
    allow = result("R1", Decision.ALLOW, "P0", "no issue")
    block = result("R1", Decision.BLOCK, "R1", "bad recipient")
    combined = combine(allow, block)
    assert combined.decision is Decision.BLOCK


def test_combine_is_order_independent():
    allow = result("R1", Decision.ALLOW, "P0", "no issue")
    flag = result("R1", Decision.FLAG, "P4", "unverified channel")
    assert combine(allow, flag).decision is combine(flag, allow).decision is Decision.FLAG


def test_combine_merges_audit_trail_from_every_input():
    a = result("R1", Decision.ALLOW, "LLM:SEND_EMAIL", "agent said fine")
    b = result("R1", Decision.BLOCK, "R1.b", "recipient domain not allowlisted")
    combined = combine(a, b)
    assert combined.rules_fired == ["LLM:SEND_EMAIL", "R1.b"]
    assert combined.reasons == ["agent said fine", "recipient domain not allowlisted"]


def test_combine_all_allow_stays_allow_and_notes_no_signal():
    a = result("R1", Decision.ALLOW, "LLM:SEND_EMAIL", "agent said fine")
    b = GuardrailResult(record_id="R1")  # no rule fired
    combined = combine(a, b)
    assert combined.decision is Decision.ALLOW
    assert "agent said fine" in combined.reasons


def test_combine_requires_at_least_one_result():
    with pytest.raises(ValueError):
        combine()


def test_combine_rejects_mismatched_record_ids():
    a = result("R1", Decision.ALLOW, "LLM:SEND_EMAIL", "fine")
    b = result("R2", Decision.BLOCK, "R1", "bad")
    with pytest.raises(ValueError):
        combine(a, b)
