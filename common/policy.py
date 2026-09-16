"""Organisation policy configuration shared by the deterministic variants.

Everything the rules treat as "policy" lives here so the three variants in
`deterministic/` differ only in their *reasoning*, never in their constants.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

# ---------------------------------------------------------------------------
# Identity / destination policy
# ---------------------------------------------------------------------------

ORG_DOMAIN = "corp.example"

#: Domains the organisation has verified for outbound sensitive data.
TRUSTED_DOMAINS: Set[str] = {
    "corp.example",
    "partner-a.example",
    "audit-approved.example",
    "counsel.example",
}

#: Sender trust markers meaning the mailbox is not who it claims to be.
UNTRUSTED_SENDER_STATES: Set[str] = {
    "EXTERNAL_LOOKALIKE",
    "EXTERNAL_UNKNOWN",
    "UNKNOWN",
    "UNVERIFIED",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def domain_of(address: Optional[str]) -> Optional[str]:
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[1].strip().lower()


def is_internal(address: Optional[str]) -> bool:
    return domain_of(address) == ORG_DOMAIN


def is_trusted_domain(address: Optional[str], allowlist: Optional[Set[str]] = None) -> bool:
    domain = domain_of(address)
    active = TRUSTED_DOMAINS if allowlist is None else allowlist
    return domain is not None and domain in active


def is_external(address: Optional[str], allowlist: Optional[Set[str]] = None) -> bool:
    """External == outside the verified domain allowlist."""
    return address is not None and not is_trusted_domain(address, allowlist)


def addresses_in(text: str) -> List[str]:
    return EMAIL_RE.findall(text or "")


# ---------------------------------------------------------------------------
# Data sensitivity policy
# ---------------------------------------------------------------------------

#: Tier ranking used by the rules; higher tier == stricter handling.
SENSITIVITY_TIER = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "PROPRIETARY": 2,
    "CONFIDENTIAL": 2,
    "PERSONAL_DATA": 2,
    "SENSITIVE_RESEARCH": 2,
    "FINANCIAL_CONFIDENTIAL": 3,
    "LEGAL_CONFIDENTIAL": 3,
    "SECURITY_SENSITIVE": 3,
    "CREDENTIAL": 4,
}

#: At or above this tier the data may not leave a verified destination.
SENSITIVE_TIER_THRESHOLD = 1

#: Authentication material - never disclosed, internal or not.
CREDENTIAL_SENSITIVITIES: Set[str] = {"CREDENTIAL"}


def tier(sensitivity: Optional[str]) -> int:
    return SENSITIVITY_TIER.get((sensitivity or "").upper(), 2)


def is_sensitive(sensitivity: Optional[str]) -> bool:
    return tier(sensitivity) >= SENSITIVE_TIER_THRESHOLD


def is_credential(sensitivity: Optional[str]) -> bool:
    return (sensitivity or "").upper() in CREDENTIAL_SENSITIVITIES


# ---------------------------------------------------------------------------
# Action policy
# ---------------------------------------------------------------------------

#: Actions that move data to a destination outside the local context.
EXTERNALISING_ACTIONS: Set[str] = {
    "SEND_EMAIL",
    "FORWARD_EMAIL",
    "REPLY_EMAIL",
    "SHARE_FILE",
    "APPEND_FILE",
    "CREATE_CALENDAR_EVENT",
    # READ/SUMMARIZE are local reads, but the corpus pairs them with a
    # delivery destination, so they are disclosures too.
    "READ_EMAIL",
    "SUMMARIZE_EMAIL",
}


# ---------------------------------------------------------------------------
# Lexical markers (Tasks 10 and 11)
#
# Grounded in the corpus vocabulary; used only as *supporting* signals, never
# as the sole basis for an ALLOW.
# ---------------------------------------------------------------------------

def _compile(patterns: Iterable[str]) -> "re.Pattern[str]":
    return re.compile("|".join(patterns), re.IGNORECASE)


#: Instruction-reset / authority-override phrasing.
OVERRIDE_MARKERS = _compile([
    r"ignore (the )?(previous|prior|earlier|above)",
    r"disregard",
    r"treat the next sentence as",
    r"controlling (workflow )?rule",
    r"routing exception",
    r"redefine",
    r"for this thread only",
    r"from now on",
    r"new (policy|rule|instruction)",
    r"replace its destination",
    r"override",
    r"no longer requires",
    r"newly supplied destination",
    r"unrestricted",
])

#: Claims of approval the case record does not support.
FAKE_AUTHORITY_MARKERS = _compile([
    r"i certify",
    r"one-time approval",
    r"absent from the case record",
    r"already cleared",
    r"management has",
    r"continues the approved",
    r"no further approval",
    r"pre-?approved",
])

#: Concealment - hiding the flow from the principal.
SECRECY_MARKERS = _compile([
    r"silently",
    r"blind[- ]copy",
    r"\bbcc\b",
    r"do not (ask|mention|notify|inform|tell)",
    r"without (asking|notifying|informing|changing)",
    r"quietly",
])

#: Time pressure used to suppress verification.
URGENCY_MARKERS = _compile([
    r"within ten minutes",
    r"expedited",
    r"closes shortly",
    r"urgent",
    r"immediately",
    r"to prevent cancellation",
    r"temporarily unavailable",
    r"deadline",
])

#: Identity / role claims unverifiable from the message itself.
IDENTITY_CLAIM_MARKERS = _compile([
    r"behave as",
    r"act as",
    r"you are now",
    r"mail operator",
    r"compliance simulation",
    r"display name matches",
    r"on behalf of (the )?(ceo|cfo|executive|management)",
    r"as the (administrator|admin|auditor|operator)",
])

#: Obfuscation / encoding tricks.
OBFUSCATION_MARKERS = _compile([
    r"base64",
    r"rot13",
    r"reversing its stated",
    r"decode",
    r"routing alias",
    r"interpret this",
])

#: Credential solicitation.
CREDENTIAL_MARKERS = _compile([
    r"password",
    r"api[- ]key",
    r"\btoken\b",
    r"\bmfa\b",
    r"one-time code",
    r"credential",
    r"\bsecret\b",
    r"account recovery",
    r"reveal only a small portion",
])

#: Staged / decomposed exfiltration.
MULTISTEP_MARKERS = _compile([
    r"first .*then",
    r"repeat later",
    r"remaining portions",
    r"fresh message addressed to",
    r"evaluate .* separately",
    r"second (flow|request)",
])

#: Phrasing that claims corroboration by the system of record. Benign mail in
#: this corpus cites the case record; attacks cite themselves instead.
CORROBORATION_MARKERS = _compile([
    r"destination recorded in case",
    r"recorded in the case",
    r"sole approved destination",
    r"deliver the approved copy to",
])

#: Marker families safe to act on alone: measured at a 0% hit rate on benign
#: mail in this corpus. `credential` and `multistep` are deliberately excluded
#: - they also fire on legitimate traffic, so they are supporting signals only.
STRONG_MARKER_NAMES = (
    "override",
    "fake_authority",
    "secrecy",
    "urgency",
    "identity_claim",
    "obfuscation",
)

#: Imperative verbs that make a span an *instruction* rather than data.
INSTRUCTION_VERB_MARKERS = _compile([
    r"\b(send|forward|share|transmit|route|deliver|append|reply|place|add|"
    r"copy|reveal|disclose|summarize|use)\b",
])

ALL_MARKERS = {
    "override": OVERRIDE_MARKERS,
    "fake_authority": FAKE_AUTHORITY_MARKERS,
    "secrecy": SECRECY_MARKERS,
    "urgency": URGENCY_MARKERS,
    "identity_claim": IDENTITY_CLAIM_MARKERS,
    "obfuscation": OBFUSCATION_MARKERS,
    "credential": CREDENTIAL_MARKERS,
    "multistep": MULTISTEP_MARKERS,
}


def markers_present(text: str) -> Set[str]:
    """Names of every marker family found in `text`."""
    return {name for name, rx in ALL_MARKERS.items() if rx.search(text or "")}


def strong_markers(text: str) -> Set[str]:
    """Marker families that are safe to block on without corroboration."""
    return {name for name in STRONG_MARKER_NAMES if ALL_MARKERS[name].search(text or "")}


def claims_corroboration(text: str) -> bool:
    """True when the text cites the system of record rather than itself."""
    return bool(CORROBORATION_MARKERS.search(text or ""))
