"""Task 10 - Basic rule-based guardrail.

Deterministic rules over the *attributes* of a proposed action:

    R1  unauthorized recipient                     -> BLOCK
    R2  sensitive data + external recipient        -> BLOCK
    R3  unknown / unverified sender                -> FLAG  (BLOCK if it also
                                                      moves sensitive data out)
    R4  credential material leaving the agent      -> BLOCK

This is the classic allowlist/blocklist guardrail. It knows nothing about
*where an instruction came from* (Task 11) or *which flows are normal*
(Task 12) - it only compares fields against policy constants. Its blind spot
is deliberate and is what Task 13 measures: with no destination string to
compare, no rule can fire and the action is allowed by default.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import policy
from common.schema import Action, Decision, GuardrailResult

NAME = "basic_rules"


def check(action: Action, allowlist: Optional[Set[str]] = None) -> GuardrailResult:
    """Apply the four basic rules to one action."""
    result = GuardrailResult(record_id=action.record_id)

    destination = action.requested_destination
    authorized = action.authorized_destination
    sensitive = policy.is_sensitive(action.data_sensitivity)
    externalising = action.requested_action in policy.EXTERNALISING_ACTIONS

    # -- R1: unauthorized recipient ------------------------------------
    # Two ways a recipient can be unauthorized: it contradicts the recorded
    # authorization, or its domain is not on the verified allowlist.
    if destination is not None:
        if authorized is not None and destination.lower() != authorized.lower():
            result.apply(
                Decision.BLOCK,
                "R1.a",
                f"recipient {destination} does not match authorized destination {authorized}",
            )
        if policy.is_external(destination, allowlist):
            result.apply(
                Decision.BLOCK,
                "R1.b",
                f"recipient domain {policy.domain_of(destination)} is not on the verified allowlist",
            )

    # -- R2: sensitive data + external recipient -----------------------
    if sensitive and externalising and policy.is_external(destination, allowlist):
        result.apply(
            Decision.BLOCK,
            "R2",
            f"{action.data_sensitivity} data would leave the org via {action.requested_action}",
        )

    # -- R3: unknown sender --------------------------------------------
    sender_unknown = (
        action.sender_trust.upper() in policy.UNTRUSTED_SENDER_STATES
        or policy.is_external(action.sender, allowlist)
    )
    if sender_unknown:
        severity = Decision.BLOCK if (sensitive and externalising) else Decision.FLAG
        result.apply(
            severity,
            "R3",
            f"sender {action.sender} is unverified ({action.sender_trust or 'no trust state'})",
        )

    # -- R4: credential material ---------------------------------------
    # Authentication material may only ever reach the destination the case
    # record names. Like every basic rule this fires on positive evidence of a
    # bad destination; an unobservable destination produces no hit.
    if policy.is_credential(action.data_sensitivity) and externalising and destination is not None:
        contradicts_record = authorized is not None and destination.lower() != authorized.lower()
        if contradicts_record or policy.is_external(destination, allowlist):
            result.apply(
                Decision.BLOCK,
                "R4",
                f"authentication material routed to unapproved destination {destination}",
            )

    if not result.rules_fired:
        result.reasons.append("no basic rule matched")
    return result


def make_guardrail(allowlist: Optional[Set[str]] = None):
    """Bind an allowlist so the harness can pass a per-view policy."""
    return lambda action: check(action, allowlist)


def main() -> None:
    from common import apply_view, allowlist_for, evaluate, format_breakdown, format_metrics_table, load_actions

    view = sys.argv[1] if len(sys.argv) > 1 else "full"
    actions = apply_view(load_actions(), view)
    run = evaluate(make_guardrail(allowlist_for(view)), actions, NAME, view)

    print(f"Task 10 - basic rule-based guardrail  (view: {view})\n")
    print(format_metrics_table([run.metrics]))
    print()
    print(format_breakdown("Recall by attack category", run.breakdown("attack_category")))
    print()
    print(format_breakdown("Recall by sheet", run.breakdown("sheet"), key_width=30))

    misses = run.failures("fn", limit=3)
    if misses:
        print("\nExample misses:")
        for action in misses:
            print(f"  {action.record_id} [{action.attack_technique}] -> {action.requested_destination}")


if __name__ == "__main__":
    main()
