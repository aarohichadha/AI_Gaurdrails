"""Tests for run_cascade.py's daily-quota detection.

Regression test: `_is_daily_quota_error` originally looked for `quotaId` as
a top-level key on `details["error"]["details"]`'s entries, but the real
API response nests it one level deeper, inside each entry's own
`"violations"` list. The bug meant a genuine daily-quota exhaustion was
never recognized as one - it fell through to the ordinary retry path
instead, burning through MAX_RETRIES against a wall that only a day's wait
would clear. Caught by comparing against the exact structure Gemini's API
returned when this was diagnosed live (see cascade/README.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cascade.run_cascade import _is_daily_quota_error


class FakeAPIError(Exception):
    def __init__(self, details):
        self.details = details


def error_with_violation(quota_id: str) -> FakeAPIError:
    return FakeAPIError({
        "error": {
            "code": 429,
            "details": [
                {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                 "violations": [{"quotaMetric": "x", "quotaId": quota_id, "quotaValue": "20"}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "52s"},
            ],
        }
    })


def test_detects_the_real_per_day_quota_structure():
    exc = error_with_violation("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert _is_daily_quota_error(exc) is True


def test_does_not_flag_a_per_minute_quota_as_daily():
    exc = error_with_violation("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    assert _is_daily_quota_error(exc) is False


def test_handles_an_exception_with_no_details_attribute():
    assert _is_daily_quota_error(ValueError("network gone")) is False


def test_handles_details_with_no_violations():
    exc = FakeAPIError({"error": {"code": 503, "details": [{"@type": "Help"}]}})
    assert _is_daily_quota_error(exc) is False
