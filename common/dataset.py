"""Load the email-agent security corpus into `Action` objects.

Also defines the **evaluation views**. A view controls *what the guardrail is
allowed to see*, which is what makes the Task 13 comparison meaningful: on the
raw corpus the requested destination is a perfect oracle, so every variant
scores 100%. The ablation views withhold that oracle without ever touching the
ground-truth labels.
"""
from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from .policy import CORROBORATION_MARKERS as CORROBORATION_RE
from .policy import EMAIL_RE, TRUSTED_DOMAINS, addresses_in
from .schema import Action, Channel, InstructionSource

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "email_agent_security_dataset.xlsx"

#: The six scenario sheets (the workbook also holds Validation + Taxonomy).
DATA_SHEETS = [
    "Base Dataset",
    "Paired Benign-Attack",
    "Contextual Manipulation",
    "Implement Flow Separation",
    "Attack Taxonomy",
    "Adaptive Attack Generation",
]


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _opt(value) -> Optional[str]:
    text = _text(value).strip()
    return text or None


def _build_sources(row) -> List[InstructionSource]:
    """Attach a provenance channel to every span of context (Task 11).

    The user instruction is typed by the principal, the authorization evidence
    comes from the system of record, and the mail subject/body are attacker
    controlled.
    """
    sources = [
        InstructionSource(Channel.USER, _text(row.user_instruction)),
        InstructionSource(Channel.EMAIL, _text(row.subject)),
        InstructionSource(Channel.EMAIL, _text(row.email_body)),
    ]
    evidence = _text(row.authorization_evidence)
    if evidence:
        sources.append(InstructionSource(Channel.CASE_RECORD, evidence))
    return sources


def _to_action(row, sheet: str) -> Action:
    return Action(
        record_id=_text(row.record_id),
        task_id=_text(row.task_id),
        sheet=sheet,
        label=_text(row.label),
        expected_decision=_text(row.expected_decision),
        attack_category=_text(row.attack_category) or "NONE",
        attack_technique=_text(row.attack_technique) or "none",
        difficulty=_text(row.difficulty),
        sender=_text(row.sender),
        sender_trust=_text(row.sender_trust),
        mailbox_owner=_text(row.recipient),
        data_asset=_text(row.data_asset),
        data_sensitivity=_text(row.data_sensitivity),
        requested_action=_text(row.requested_action),
        requested_destination=_opt(row.requested_destination),
        authorized_destination=_opt(row.authorized_destination),
        authorization_rule=_opt(row.authorization_rule),
        authorization_evidence=_opt(row.authorization_evidence),
        subject=_text(row.subject),
        email_body=_text(row.email_body),
        user_instruction=_text(row.user_instruction),
        sources=_build_sources(row),
    )


@lru_cache(maxsize=1)
def load_actions(path: str = str(DATASET)) -> List[Action]:
    """Read every scenario sheet once and cache the result."""
    workbook = pd.ExcelFile(path)
    actions: List[Action] = []
    for sheet in DATA_SHEETS:
        if sheet not in workbook.sheet_names:
            continue
        frame = workbook.parse(sheet)
        actions.extend(_to_action(row, sheet) for row in frame.itertuples(index=False))
    return actions


# ---------------------------------------------------------------------------
# Evaluation views (feature ablations)
# ---------------------------------------------------------------------------

def _view_full(action: Action) -> Action:
    """Everything available, including the curated destination columns."""
    return action


def _view_text_only(action: Action) -> Action:
    """No curated columns: the destination must be parsed out of the body.

    Models an agent that only sees raw mail plus a static domain allowlist.
    """
    parsed = [a for a in addresses_in(action.email_body)]
    return replace(
        action,
        requested_destination=parsed[-1] if parsed else None,
        authorized_destination=None,
        authorization_evidence=None,
        sources=[s for s in action.sources if s.channel is not Channel.CASE_RECORD],
    )


def _scrub(text: str) -> str:
    return EMAIL_RE.sub("[ADDRESS-WITHHELD]", text or "")


def _view_destination_blind(action: Action) -> Action:
    """The destination string is not observable.

    Models the realistic case where the exfiltration channel is not a visible
    To: field - a hidden BCC, an alias, a reply-to, a link, an attachment - so
    no address comparison is possible. The case record still exists.
    """
    return replace(
        action,
        requested_destination=None,
        subject=_scrub(action.subject),
        email_body=_scrub(action.email_body),
        sources=[
            InstructionSource(s.channel, _scrub(s.text)) if s.channel is Channel.EMAIL else s
            for s in action.sources
        ],
    )


def _view_blind_no_cue(action: Action) -> Action:
    """Destination invisible *and* the corroboration phrasing removed.

    Benign mail in this corpus always contains the template phrase "the
    destination recorded in case N", and attack mail never does - so that one
    cue is doing a lot of work in the blind view. This view deletes it to show
    what each variant falls back on when no lexical anchor survives. It is a
    worst case, not a realistic distribution.
    """
    blind = _view_destination_blind(action)
    stripped = CORROBORATION_RE.sub("[CUE-REMOVED]", blind.email_body)
    return replace(
        blind,
        email_body=stripped,
        sources=[
            InstructionSource(s.channel, CORROBORATION_RE.sub("[CUE-REMOVED]", s.text))
            if s.channel is Channel.EMAIL else s
            for s in blind.sources
        ],
    )


#: Domains an attacker controls in this corpus. Used only by the stale-policy
#: view, to simulate an allowlist that has gone out of date.
ATTACKER_DOMAINS: Set[str] = {
    "free-check.example",
    "temp-transfer.example",
    "personal-box.example",
    "quick-share.example",
    "outside-audit.example",
    "open-review.example",
}

#: Allowlist used by the stale-policy view.
STALE_ALLOWLIST: Set[str] = TRUSTED_DOMAINS | ATTACKER_DOMAINS


VIEWS: Dict[str, Dict] = {
    "full": {
        "fn": _view_full,
        "allowlist": None,
        "description": "All curated fields, including authorized_destination.",
    },
    "text_only": {
        "fn": _view_text_only,
        "allowlist": None,
        "description": "Raw mail text plus a static domain allowlist; no case record.",
    },
    "destination_blind": {
        "fn": _view_destination_blind,
        "allowlist": None,
        "description": "Destination address not observable (hidden BCC / alias channel).",
    },
    "blind_no_cue": {
        "fn": _view_blind_no_cue,
        "allowlist": None,
        "description": "Destination invisible and the corroboration cue removed (worst case).",
    },
    "stale_allowlist": {
        "fn": _view_text_only,
        "allowlist": STALE_ALLOWLIST,
        "description": (
            "Raw mail text and a domain allowlist that has gone stale: the attacker "
            "domains are (wrongly) trusted, and there is no case record to fall back on."
        ),
    },
}


def apply_view(actions: List[Action], view: str) -> List[Action]:
    if view not in VIEWS:
        raise KeyError(f"unknown view {view!r}; choose from {sorted(VIEWS)}")
    fn = VIEWS[view]["fn"]
    return [fn(a) for a in actions]


def allowlist_for(view: str) -> Optional[Set[str]]:
    return VIEWS[view]["allowlist"]
