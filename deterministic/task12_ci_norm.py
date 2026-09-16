"""Task 12 - Contextual Integrity (CI) norm guardrail.

Contextual Integrity (Nissenbaum) says information flows are appropriate or
not *relative to the context they occur in*. Every action is represented as a
flow of four parameters, and a flow is permitted only if it matches a norm
declared in advance for that context:

    sender                 - who is initiating the flow
    recipient              - where the information ends up
    data (+ attribute)     - what is flowing, and how sensitive it is
    transmission principle - the condition under which the flow is allowed

The crucial difference from Tasks 10 and 11 is the **direction of the
default**. Basic rules and provenance rules look for evidence that something
is *wrong* and allow otherwise. A CI norm must be positively *affirmed*: if
the transmission principle cannot be shown to hold, the flow is not normal and
is blocked. That is what keeps it working when the destination is invisible or
the allowlist is stale - the attacker has to produce evidence, not merely
avoid tripping a pattern.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import policy
from common.schema import Action, Channel, Decision, GuardrailResult

NAME = "ci_norm"


# ---------------------------------------------------------------------------
# The flow representation
# ---------------------------------------------------------------------------

@dataclass
class Flow:
    """One information flow, expressed in Contextual Integrity parameters."""

    sender: str
    sender_state: str
    recipient: Optional[str]
    data: str
    attribute: str              # the sensitivity label
    transmission_principle: str
    context: str                # the workflow this flow belongs to

    def describe(self) -> str:
        return (
            f"{self.sender} -> {self.recipient or '<unobservable>'} : "
            f"{self.data} [{self.attribute}] under {self.transmission_principle}"
        )


# ---------------------------------------------------------------------------
# Transmission principles
# ---------------------------------------------------------------------------

CASE_RECORDED_DESTINATION = "CASE_RECORDED_DESTINATION"
VERIFIED_DOMAIN_ONLY = "VERIFIED_DOMAIN_ONLY"


@dataclass
class Norm:
    """A declared-appropriate flow pattern for one context."""

    norm_id: str
    description: str
    applies: Callable[[Action], bool]
    principle: str
    allowed_sender_states: Set[str]


#: Norms are ordered most specific first; the first match governs.
CI_NORMS: List[Norm] = [
    Norm(
        norm_id="N1",
        description=(
            "Authentication material flows only to the destination named in the "
            "case record, and only from a verified internal sender."
        ),
        applies=lambda a: policy.is_credential(a.data_sensitivity),
        principle=CASE_RECORDED_DESTINATION,
        allowed_sender_states={"INTERNAL_VERIFIED"},
    ),
    Norm(
        norm_id="N2",
        description=(
            "Confidential business data flows to the case-recorded destination "
            "under a verified authorization."
        ),
        applies=lambda a: policy.tier(a.data_sensitivity) >= 2,
        principle=CASE_RECORDED_DESTINATION,
        allowed_sender_states={"INTERNAL_VERIFIED"},
    ),
    Norm(
        norm_id="N3",
        description="Internal data flows only to verified organisation domains.",
        applies=lambda a: policy.tier(a.data_sensitivity) >= 1,
        principle=VERIFIED_DOMAIN_ONLY,
        allowed_sender_states={"INTERNAL_VERIFIED"},
    ),
    Norm(
        norm_id="N4",
        description="Public data may flow to any verified domain.",
        applies=lambda a: True,
        principle=VERIFIED_DOMAIN_ONLY,
        allowed_sender_states={"INTERNAL_VERIFIED"},
    ),
]


def governing_norm(action: Action) -> Norm:
    for norm in CI_NORMS:
        if norm.applies(action):
            return norm
    return CI_NORMS[-1]


def to_flow(action: Action) -> Flow:
    norm = governing_norm(action)
    return Flow(
        sender=action.sender,
        sender_state=action.sender_trust,
        recipient=action.requested_destination,
        data=action.data_asset,
        attribute=action.data_sensitivity,
        transmission_principle=norm.principle,
        context=action.task_id,
    )


# ---------------------------------------------------------------------------
# Norm checking
# ---------------------------------------------------------------------------

def _principle_affirmed(action: Action, flow: Flow, allowlist: Optional[Set[str]]):
    """Can the transmission principle be positively affirmed?

    Returns `(affirmed, evidence)`. Absence of evidence is never affirmation.
    """
    record = action.source(Channel.CASE_RECORD)
    trusted = action.trusted_text
    untrusted = action.untrusted_text

    if flow.recipient is not None:
        # The destination is observable: the record is the authority.
        if record.strip():
            if flow.recipient.lower() in trusted.lower():
                return True, "destination is named by the case record"
            return False, (
                f"case record does not name {flow.recipient} "
                f"(it names {action.authorized_destination or 'another destination'})"
            )
        # No record available: fall back to the weaker claim-of-record test.
        if not policy.claims_corroboration(untrusted):
            return False, "no case record and the request cites no recorded authorization"
        if flow.recipient and policy.is_external(flow.recipient, allowlist):
            return False, f"destination {flow.recipient} is outside the verified domains"
        return True, "request cites a recorded authorization and stays within verified domains"

    # The destination is not observable at all (hidden BCC, alias, link).
    # A flow whose recipient cannot be established cannot be affirmed unless
    # the request is anchored to a recorded authorization.
    if policy.claims_corroboration(untrusted):
        return True, "recipient unobservable, but the request is anchored to the case record"
    return False, "recipient cannot be established and no recorded authorization is cited"


def check(action: Action, allowlist: Optional[Set[str]] = None) -> GuardrailResult:
    result = GuardrailResult(record_id=action.record_id)
    flow = to_flow(action)
    norm = governing_norm(action)
    result.reasons.append(f"governing norm {norm.norm_id}: {flow.describe()}")

    # -- CI parameter 1: sender ----------------------------------------
    if flow.sender_state.upper() not in norm.allowed_sender_states:
        result.apply(
            Decision.BLOCK,
            f"{norm.norm_id}.sender",
            f"sender state {flow.sender_state or 'UNKNOWN'} is not permitted by {norm.norm_id}",
        )
    if policy.is_external(flow.sender, allowlist):
        result.apply(
            Decision.BLOCK,
            f"{norm.norm_id}.sender.domain",
            f"sender {flow.sender} is outside the organisation",
        )

    # -- CI parameter 2: recipient -------------------------------------
    if flow.recipient is not None and policy.is_external(flow.recipient, allowlist):
        result.apply(
            Decision.BLOCK,
            f"{norm.norm_id}.recipient",
            f"recipient {flow.recipient} is not a verified destination",
        )

    # -- CI parameter 3: data ------------------------------------------
    if action.requested_action not in policy.EXTERNALISING_ACTIONS and policy.is_credential(flow.attribute):
        result.apply(
            Decision.FLAG,
            f"{norm.norm_id}.data",
            "authentication material handled outside a recognised delivery action",
        )

    # -- CI parameter 4: transmission principle ------------------------
    # Default-deny: the principle must be shown to hold.
    affirmed, evidence = _principle_affirmed(action, flow, allowlist)
    if affirmed:
        result.reasons.append(f"principle {flow.transmission_principle} affirmed: {evidence}")
    else:
        result.apply(
            Decision.BLOCK,
            f"{norm.norm_id}.principle",
            f"principle {flow.transmission_principle} not satisfied: {evidence}",
        )

    # -- context integrity ---------------------------------------------
    # Norms describe a context. Text that tries to redefine the context is
    # itself evidence that this flow is not the one the norm anticipated.
    hits = policy.strong_markers(action.untrusted_text)
    if hits:
        result.apply(
            Decision.BLOCK,
            f"{norm.norm_id}.context",
            f"the request tries to renegotiate the context ({', '.join(sorted(hits))})",
        )

    return result


def make_guardrail(allowlist: Optional[Set[str]] = None):
    return lambda action: check(action, allowlist)


def main() -> None:
    from common import apply_view, allowlist_for, evaluate, format_breakdown, format_metrics_table, load_actions

    view = sys.argv[1] if len(sys.argv) > 1 else "full"
    actions = apply_view(load_actions(), view)
    run = evaluate(make_guardrail(allowlist_for(view)), actions, NAME, view)

    print(f"Task 12 - contextual-integrity norm guardrail  (view: {view})\n")
    print(format_metrics_table([run.metrics]))
    print()
    print(format_breakdown("Recall by attack category", run.breakdown("attack_category")))
    print()
    print(format_breakdown("Recall by sheet", run.breakdown("sheet"), key_width=30))

    for kind, title in (("fn", "missed attacks"), ("fp", "blocked benign")):
        rows = run.failures(kind, limit=3)
        if rows:
            print(f"\nExample {title}:")
            for action in rows:
                print(f"  {action.record_id} [{action.attack_technique}] {action.email_body[:90]}")


if __name__ == "__main__":
    main()
