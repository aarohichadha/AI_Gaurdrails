"""Shared vocabulary for every guardrail technique.

An agent action is represented once, here, so that the deterministic rules
(Tasks 10-12), and any later technique, all judge the *same* object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Optional


class Decision(IntEnum):
    """Ordered so that `max()` yields the most restrictive decision."""

    ALLOW = 0
    FLAG = 1
    BLOCK = 2

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Channel(str, Enum):
    """Where a piece of text entered the agent's context."""

    USER = "USER"                # typed by the principal the agent acts for
    CASE_RECORD = "CASE_RECORD"  # system of record / authorization database
    EMAIL = "EMAIL"              # inbound message content
    WEBPAGE = "WEBPAGE"          # fetched page content
    TOOL_OUTPUT = "TOOL_OUTPUT"  # result returned by a tool
    MEMORY = "MEMORY"            # persisted note from an earlier turn


class Trust(str, Enum):
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


#: Task 11 provenance policy: trust is a property of the *channel*, never of
#: the content. Anything an attacker can write into must be UNTRUSTED.
CHANNEL_TRUST = {
    Channel.USER: Trust.TRUSTED,
    Channel.CASE_RECORD: Trust.TRUSTED,
    Channel.EMAIL: Trust.UNTRUSTED,
    Channel.WEBPAGE: Trust.UNTRUSTED,
    Channel.TOOL_OUTPUT: Trust.UNTRUSTED,
    Channel.MEMORY: Trust.UNTRUSTED,
}


@dataclass(frozen=True)
class InstructionSource:
    """A span of text plus the channel it arrived on."""

    channel: Channel
    text: str

    @property
    def trust(self) -> Trust:
        return CHANNEL_TRUST[self.channel]

    @property
    def is_trusted(self) -> bool:
        return self.trust is Trust.TRUSTED


class DestinationStatus(str, Enum):
    """Result of comparing the requested destination with trusted authority."""

    VERIFIED_MATCH = "VERIFIED_MATCH"  # matches the recorded authorization
    MISMATCH = "MISMATCH"              # contradicts the recorded authorization
    UNVERIFIABLE = "UNVERIFIABLE"      # no destination signal available


@dataclass
class Action:
    """One proposed agent action, as a Contextual-Integrity style flow.

    The five CI parameters are: `sender`, `recipient` (of the data flow, i.e.
    the destination), `data_asset` + `data_sensitivity` (the attribute), and
    the transmission principle, which is carried by `authorization_rule` /
    `authorization_evidence`.
    """

    # --- identity / bookkeeping ------------------------------------------
    record_id: str
    task_id: str
    sheet: str
    label: str                # BENIGN | ATTACK  (ground truth)
    expected_decision: str    # ALLOW  | BLOCK   (ground truth)
    attack_category: str
    attack_technique: str
    difficulty: str

    # --- CI parameter: sender --------------------------------------------
    sender: str
    sender_trust: str         # INTERNAL_VERIFIED | EXTERNAL_LOOKALIKE | ...
    mailbox_owner: str        # the agent's own user (`recipient` column)

    # --- CI parameter: data ----------------------------------------------
    data_asset: str
    data_sensitivity: str

    # --- CI parameter: recipient of the flow ------------------------------
    requested_action: str
    requested_destination: Optional[str]
    authorized_destination: Optional[str]

    # --- CI parameter: transmission principle -----------------------------
    authorization_rule: Optional[str]
    authorization_evidence: Optional[str]

    # --- raw context the agent would actually read ------------------------
    subject: str
    email_body: str
    user_instruction: str

    #: Provenance-tagged view of the same context (Task 11).
    sources: List[InstructionSource] = field(default_factory=list)

    # ------------------------------------------------------------------
    def source(self, channel: Channel) -> str:
        """All text that arrived on `channel`, concatenated."""
        return " ".join(s.text for s in self.sources if s.channel is channel)

    @property
    def untrusted_text(self) -> str:
        return " ".join(s.text for s in self.sources if not s.is_trusted)

    @property
    def trusted_text(self) -> str:
        return " ".join(s.text for s in self.sources if s.is_trusted)


@dataclass
class GuardrailResult:
    """A decision plus the audit trail that produced it."""

    record_id: str
    decision: Decision = Decision.ALLOW
    rules_fired: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def apply(self, decision: Decision, rule_id: str, reason: str) -> "GuardrailResult":
        """Record a rule hit, keeping the most restrictive decision seen."""
        self.rules_fired.append(rule_id)
        self.reasons.append(reason)
        self.decision = max(self.decision, decision)
        return self

    @property
    def blocked(self) -> bool:
        """FLAG is treated as 'not autonomously allowed' for scoring."""
        return self.decision is not Decision.ALLOW
