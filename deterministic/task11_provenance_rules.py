"""Task 11 - Provenance-based guardrail.

Every span of context carries the channel it arrived on, and trust is a
property of the *channel*, never of the content:

    USER instruction   -> TRUSTED    (the principal the agent acts for)
    CASE_RECORD entry  -> TRUSTED    (the system of record)
    EMAIL body/subject -> UNTRUSTED  (attacker controlled)
    WEBPAGE / TOOL     -> UNTRUSTED  (attacker controlled)

The governing principle: **untrusted content is data, not instructions.** Text
arriving on an untrusted channel may describe a request, but it can never be
the authority that approves a sensitive flow. Authority must be traceable to a
trusted channel.

    P1  destination named only by an untrusted channel, contradicting the
        trusted record                                              -> BLOCK
    P2  untrusted text tries to rewrite the governing rules          -> BLOCK
    P3  untrusted text uses concealment / pressure / role claims     -> BLOCK
    P4  the sending channel itself is unverified                     -> BLOCK/FLAG
    P5  no trusted record available and the untrusted instruction
        cites no verifiable authority                               -> BLOCK/FLAG
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import policy
from common.schema import Action, Channel, Decision, GuardrailResult

NAME = "provenance_rules"


def _names_destination(text: str, destination: Optional[str]) -> bool:
    return bool(destination) and destination.lower() in (text or "").lower()


def check(action: Action, allowlist: Optional[Set[str]] = None) -> GuardrailResult:
    result = GuardrailResult(record_id=action.record_id)

    untrusted = action.untrusted_text
    trusted = action.trusted_text
    record = action.source(Channel.CASE_RECORD)
    has_record = bool(record.strip())

    destination = action.requested_destination
    sensitive = policy.is_sensitive(action.data_sensitivity)
    externalising = action.requested_action in policy.EXTERNALISING_ACTIONS
    sensitive_flow = sensitive and externalising

    # -- P1: who supplies the authority for this destination? ----------
    if destination is not None and has_record:
        trusted_backs = _names_destination(trusted, destination)
        untrusted_backs = _names_destination(untrusted, destination)
        if untrusted_backs and not trusted_backs:
            result.apply(
                Decision.BLOCK,
                "P1",
                f"destination {destination} originates only from an UNTRUSTED channel "
                f"(EMAIL); the trusted record does not name it",
            )
        elif not trusted_backs and not untrusted_backs:
            result.apply(
                Decision.FLAG,
                "P1.b",
                f"destination {destination} is traceable to no channel at all",
            )

    # -- P2: untrusted text rewriting the governing rules ---------------
    override_hits = {
        name for name in ("override", "fake_authority")
        if policy.ALL_MARKERS[name].search(untrusted)
    }
    if override_hits:
        result.apply(
            Decision.BLOCK,
            "P2",
            f"UNTRUSTED channel attempts to alter governing authority ({', '.join(sorted(override_hits))})",
        )

    # -- P3: concealment, pressure, role claims, obfuscation ------------
    manipulation_hits = {
        name for name in ("secrecy", "urgency", "identity_claim", "obfuscation")
        if policy.ALL_MARKERS[name].search(untrusted)
    }
    if manipulation_hits:
        result.apply(
            Decision.BLOCK,
            "P3",
            f"UNTRUSTED channel uses manipulation signals ({', '.join(sorted(manipulation_hits))})",
        )

    # -- P4: the channel itself is unverified ---------------------------
    if (
        action.sender_trust.upper() in policy.UNTRUSTED_SENDER_STATES
        or policy.is_external(action.sender, allowlist)
    ):
        result.apply(
            Decision.BLOCK if sensitive_flow else Decision.FLAG,
            "P4",
            f"instruction arrived from an unverified sender ({action.sender})",
        )

    # -- P5: no trusted record to appeal to -----------------------------
    # Without a system of record the only remaining question is whether the
    # untrusted instruction even claims authority that could be checked. An
    # instruction that cites nothing does not get to move sensitive data.
    if not has_record and not policy.claims_corroboration(untrusted):
        result.apply(
            Decision.BLOCK if sensitive_flow else Decision.FLAG,
            "P5",
            "untrusted instruction cites no verifiable authority and no record is available",
        )

    if not result.rules_fired:
        result.reasons.append("authority for this action traces to a trusted channel")
    return result


def make_guardrail(allowlist: Optional[Set[str]] = None):
    return lambda action: check(action, allowlist)


def main() -> None:
    from common import apply_view, allowlist_for, evaluate, format_breakdown, format_metrics_table, load_actions

    view = sys.argv[1] if len(sys.argv) > 1 else "full"
    actions = apply_view(load_actions(), view)
    run = evaluate(make_guardrail(allowlist_for(view)), actions, NAME, view)

    print(f"Task 11 - provenance-based guardrail  (view: {view})\n")
    print(format_metrics_table([run.metrics]))
    print()
    print(format_breakdown("Recall by attack category", run.breakdown("attack_category")))
    print()
    print(format_breakdown("Recall by sheet", run.breakdown("sheet"), key_width=30))

    misses = run.failures("fn", limit=3)
    if misses:
        print("\nExample misses:")
        for action in misses:
            print(f"  {action.record_id} [{action.attack_technique}] {action.email_body[:90]}")


if __name__ == "__main__":
    main()
