"""Layer 1 (Task 27) - a confidence-scored deterministic guardrail.

Built as a new file rather than editing `deterministic/task10-12.py`: it
calls their existing public `check()` / `make_guardrail()` API unchanged and
adds a genuine confidence score on top, because none of the three exposes one
by itself. `ci_norm`'s `Decision.FLAG` path (`N.data`, credential data
handled by a non-externalising action) looked like the natural "uncertain"
signal, but it never fires anywhere in this corpus (0/7,200 records, every
view - see `cascade/README.md`), so a cascade keyed on it alone would never
escalate past Layer 1.

Confidence here is **agreement across the three rule sets**. Each of
`basic_rules` (Task 10), `provenance_rules` (Task 11) and `ci_norm` (Task 12)
casts a binary vote - "blocked" (`GuardrailResult.blocked`, i.e. FLAG or
BLOCK) or "allowed". `confidence` is the fraction of votes agreeing with the
majority:

    3-0 unanimous  -> confidence 1.0  -> terminal (that decision is final)
    2-1 split      -> confidence 0.67 -> uncertain -> escalate to Layer 2

With only three voters this is a coarse, two-valued "score" rather than a
continuous probability - an honest limitation, not hidden: see
`cascade/README.md` for what a richer version would need.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.schema import Action, Decision, GuardrailResult

from deterministic import task10_basic_rules as basic
from deterministic import task11_provenance_rules as provenance
from deterministic import task12_ci_norm as ci_norm

RULES = [
    (basic.NAME, basic.make_guardrail),
    (provenance.NAME, provenance.make_guardrail),
    (ci_norm.NAME, ci_norm.make_guardrail),
]


@dataclass
class ConfidenceResult:
    record_id: str
    votes: Dict[str, GuardrailResult]
    confidence: float           # fraction of the 3 votes agreeing with the majority
    majority_blocked: bool
    confident: bool             # True only when unanimous (confidence == 1.0)

    def to_guardrail_result(self) -> GuardrailResult:
        """The majority's decision, terminal or not - callers check
        `.confident` themselves to decide whether to escalate instead of
        using this result."""
        result = GuardrailResult(record_id=self.record_id)
        decision = Decision.BLOCK if self.majority_blocked else Decision.ALLOW
        for name, vote in self.votes.items():
            result.rules_fired.extend(f"{name}.{rule}" for rule in vote.rules_fired)
            result.reasons.extend(f"[{name}] {reason}" for reason in vote.reasons)
        result.decision = decision
        result.reasons.append(
            f"agreement {self.confidence:.2f} across basic_rules/provenance_rules/ci_norm "
            f"({'unanimous' if self.confident else 'split'})"
        )
        return result


def score(action: Action, allowlist: Optional[Set[str]] = None) -> ConfidenceResult:
    votes = {name: factory(allowlist)(action) for name, factory in RULES}
    blocked_votes = sum(1 for v in votes.values() if v.blocked)
    allow_votes = len(votes) - blocked_votes
    majority_blocked = blocked_votes > allow_votes
    agree = max(blocked_votes, allow_votes)
    confidence = agree / len(votes)
    return ConfidenceResult(
        record_id=action.record_id,
        votes=votes,
        confidence=confidence,
        majority_blocked=majority_blocked,
        confident=confidence == 1.0,
    )


def make_scorer(allowlist: Optional[Set[str]] = None):
    return lambda action: score(action, allowlist)
