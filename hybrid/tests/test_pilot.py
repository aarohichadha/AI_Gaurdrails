"""Tests for the shared 100-pair pilot slice (loads the real dataset)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hybrid.pilot import pilot_actions, pilot_record_ids


def test_pilot_record_ids_is_200_unique_records_for_100_pairs():
    ids = pilot_record_ids(n_pairs=100, seed=42)
    assert len(ids) == 200
    assert len(set(ids)) == 200


def test_pilot_record_ids_is_reproducible_for_a_fixed_seed():
    assert pilot_record_ids(seed=42) == pilot_record_ids(seed=42)


def test_pilot_actions_view_never_touches_labels():
    actions = pilot_actions(view="destination_blind", n_pairs=10)
    assert len(actions) == 20
    assert {a.expected_decision for a in actions} == {"ALLOW", "BLOCK"}
    # destination_blind must still withhold the destination oracle
    assert all(a.requested_destination is None for a in actions)
