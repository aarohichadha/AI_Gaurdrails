"""Combine a prompt-agent decision with a deterministic-rule decision.

`common.schema.Decision` is an `IntEnum` ordered `ALLOW < FLAG < BLOCK`
specifically so `max()` yields the most restrictive verdict (see
`GuardrailResult.apply`). `deterministic/README.md` already recommends this
for the three rule sets ("a deployed guardrail should run all three and take
the most restrictive decision"); this module extends the same combination
rule to include the prompt-based agent as a fourth vote, not just the three
deterministic ones.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.schema import GuardrailResult

NAME_SEP = "+"


def combine(*results: GuardrailResult) -> GuardrailResult:
    """Merge two or more `GuardrailResult`s for the same record.

    The combined decision is the most restrictive of the inputs; the audit
    trail (`rules_fired`, `reasons`) is the concatenation of all of them, so
    it stays possible to see which guardrail(s) actually fired.
    """
    if not results:
        raise ValueError("combine() requires at least one GuardrailResult")
    record_ids = {r.record_id for r in results}
    if len(record_ids) > 1:
        raise ValueError(f"combine() called with mismatched record_ids: {record_ids}")

    combined = GuardrailResult(record_id=results[0].record_id)
    for result in results:
        combined.decision = max(combined.decision, result.decision)
        combined.rules_fired.extend(result.rules_fired)
        combined.reasons.extend(result.reasons)
    if not combined.rules_fired:
        combined.reasons.append("no rule/agent signal fired")
    return combined
