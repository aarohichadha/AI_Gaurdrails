"""Tests for the confidence-aware judge variant's response parsing (no
network call - `UncertainGeminiJudge.__post_init__` creates a real client,
so these test the pure `parse_response` function instead)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cascade.uncertain_judge import VALID, parse_response


def test_uncertain_is_a_valid_decision():
    assert "UNCERTAIN" in VALID


def test_parses_uncertain_response():
    raw = '{"decision": "uncertain", "reason": "no destination to compare, ambiguous intent"}'
    result = parse_response(raw)
    assert result["decision"] == "UNCERTAIN"
    assert result["status"] == "SUCCESS"


def test_still_parses_the_original_four_decisions():
    for decision in ("ALLOW", "BLOCK", "CONFIRM", "REVISE"):
        raw = f'{{"decision": "{decision}", "reason": "x"}}'
        assert parse_response(raw)["decision"] == decision


def test_unknown_decision_is_invalid_not_silently_accepted():
    raw = '{"decision": "MAYBE", "reason": "x"}'
    result = parse_response(raw)
    assert result["decision"] == "INVALID"
    assert result["status"] == "INVALID"


def test_malformed_json_is_invalid():
    result = parse_response("not json at all")
    assert result["decision"] == "INVALID"
