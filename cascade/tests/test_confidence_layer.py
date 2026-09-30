"""Tests for Layer 1's agreement-based confidence score (loads the real
dataset - same precedent as `deterministic/tests/test_revise_scoring.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import allowlist_for
from hybrid.pilot import pilot_actions

from cascade.confidence_layer import score


def test_benign_records_are_unanimous_on_destination_blind():
    """Every one of the 100 benign pilot records agrees 3/3 on this view -
    matches the manual count done while designing this layer."""
    actions = pilot_actions(view="destination_blind", n_pairs=20)
    allowlist = allowlist_for("destination_blind")
    benign = [a for a in actions if a.label == "BENIGN"]
    assert benign
    for action in benign:
        result = score(action, allowlist)
        assert result.confident, action.record_id
        assert result.confidence == 1.0
        assert result.majority_blocked is False


def test_attack_records_split_on_destination_blind():
    """basic_rules fails open here (recall 0.037 per deterministic/README.md)
    while provenance_rules/ci_norm catch it - a 2-1 split, so Layer 1 must
    treat these as uncertain rather than silently trusting the majority."""
    actions = pilot_actions(view="destination_blind", n_pairs=20)
    allowlist = allowlist_for("destination_blind")
    attacks = [a for a in actions if a.label == "ATTACK"]
    assert attacks
    for action in attacks:
        result = score(action, allowlist)
        assert not result.confident, action.record_id
        assert round(result.confidence, 3) == round(2 / 3, 3)
        # still correct on the majority vote, just not unanimous
        assert result.majority_blocked is True


def test_to_guardrail_result_reflects_majority_and_carries_audit_trail():
    actions = pilot_actions(view="destination_blind", n_pairs=5)
    allowlist = allowlist_for("destination_blind")
    action = next(a for a in actions if a.label == "ATTACK")
    result = score(action, allowlist).to_guardrail_result()
    assert result.decision.name == "BLOCK"
    assert any("basic_rules" in r or "provenance_rules" in r or "ci_norm" in r for r in result.reasons)
