"""Tests for the batched judge's prompt construction and response parsing
(no network call - BatchGeminiJudge.__post_init__ creates a real client, so
these test the pure functions instead)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cascade.batch_judge import VALID, parse_batch_response, prepare_batch_input


def make_row(record_id="R1", **overrides):
    row = dict(
        record_id=record_id, sender="a@corp.example", subject="x", email_body="hello",
        user_instruction="review", authorization_evidence="case 1", authorization_rule="rule",
        authorized_destination="finance@corp.example", requested_action="SEND_EMAIL",
        requested_destination="finance@corp.example", data_asset="doc", data_sensitivity="LOW",
    )
    row.update(overrides)
    return row


def test_prepare_batch_input_numbers_cases_in_order():
    rows = [make_row("R1"), make_row("R2"), make_row("R3")]
    prompt = prepare_batch_input(rows)
    assert "=== Case 1 ===" in prompt
    assert "=== Case 2 ===" in prompt
    assert "=== Case 3 ===" in prompt
    assert prompt.index("Case 1") < prompt.index("Case 2") < prompt.index("Case 3")


def test_parse_batch_response_happy_path():
    raw = json.dumps([
        {"index": 1, "decision": "allow", "reason": "fine"},
        {"index": 2, "decision": "BLOCK", "reason": "bad destination"},
        {"index": 3, "decision": "uncertain", "reason": "no destination to check"},
    ])
    parsed = parse_batch_response(raw, n_expected=3)
    assert parsed[1]["decision"] == "ALLOW"
    assert parsed[2]["decision"] == "BLOCK"
    assert parsed[3]["decision"] == "UNCERTAIN"
    assert all(p["status"] == "SUCCESS" for p in parsed.values())


def test_parse_batch_response_handles_fenced_json():
    raw = "```json\n" + json.dumps([{"index": 1, "decision": "ALLOW", "reason": "x"}]) + "\n```"
    parsed = parse_batch_response(raw, n_expected=1)
    assert parsed[1]["decision"] == "ALLOW"


def test_parse_batch_response_missing_case_is_backfilled_by_caller_not_dropped():
    """The parser itself only returns what it found; BatchGeminiJudge.judge_batch
    is what guarantees every row gets a result (tested via the INVALID
    backfill contract, since judge_batch needs a live client to test end to
    end - see the missing-index behavior asserted here instead)."""
    raw = json.dumps([{"index": 1, "decision": "ALLOW", "reason": "x"}])  # case 2 missing
    parsed = parse_batch_response(raw, n_expected=2)
    assert 1 in parsed
    assert 2 not in parsed  # caller (judge_batch) is responsible for backfilling this as INVALID


def test_parse_batch_response_malformed_json_marks_every_case_invalid():
    parsed = parse_batch_response("not json at all", n_expected=4)
    assert len(parsed) == 4
    assert all(p["decision"] == "INVALID" for p in parsed.values())


def test_parse_batch_response_not_a_list_marks_every_case_invalid():
    parsed = parse_batch_response('{"decision": "ALLOW"}', n_expected=2)
    assert len(parsed) == 2
    assert all(p["decision"] == "INVALID" for p in parsed.values())


def test_parse_batch_response_unknown_decision_in_one_item_is_skipped_not_fatal():
    raw = json.dumps([
        {"index": 1, "decision": "MAYBE", "reason": "x"},   # invalid - dropped
        {"index": 2, "decision": "BLOCK", "reason": "y"},   # valid - kept
    ])
    parsed = parse_batch_response(raw, n_expected=2)
    assert 1 not in parsed
    assert parsed[2]["decision"] == "BLOCK"


def test_parse_batch_response_duplicate_index_keeps_first_only():
    raw = json.dumps([
        {"index": 1, "decision": "ALLOW", "reason": "first"},
        {"index": 1, "decision": "BLOCK", "reason": "duplicate"},
    ])
    parsed = parse_batch_response(raw, n_expected=1)
    assert parsed[1]["decision"] == "ALLOW"
    assert parsed[1]["reason"] == "first"


def test_valid_includes_uncertain():
    assert "UNCERTAIN" in VALID
