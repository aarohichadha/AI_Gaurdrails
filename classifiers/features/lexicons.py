"""Lexicons for the text-side features of Task 16.

Each family is a list of regular expressions with a plain-language name, so a
match can be reported as evidence rather than as an opaque number.

These are written to be **corpus-independent**: they describe how attacks are
phrased in email generally, not how this project's dataset happens to word
them. (`common/policy.py` holds the dataset-tuned markers used by the
deterministic guardrails - a different job, deliberately kept separate.)
"""
from __future__ import annotations

import re
from typing import Dict, List, Pattern, Tuple


def _compile(patterns: List[str]) -> List[Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# ---------------------------------------------------------------------------
# Urgency and pressure
# ---------------------------------------------------------------------------

URGENCY = _compile([
    r"\burgent(ly)?\b",
    r"\bimmediate(ly)?\b",
    r"\basap\b",
    r"right away",
    r"as soon as possible",
    r"\btoday\b.{0,20}\b(deadline|cutoff|close)",
    r"within (the next )?\w+ (minutes?|hours?)",
    r"before (the )?(end of day|eod|close of business|cob)",
    r"\bexpedite[ds]?\b|\bexpedited\b",
    r"time[- ]sensitive",
    r"\bdeadline\b",
    r"closes? (shortly|soon)",
    r"last (chance|warning|reminder)",
    r"to (prevent|avoid) (cancellation|suspension|penalt|clos)",
    r"will be (suspended|closed|terminated|cancelled)",
    r"final notice",
    r"don'?t delay|no time to",
])

# ---------------------------------------------------------------------------
# Instruction override - text trying to replace the governing instructions
# ---------------------------------------------------------------------------

INSTRUCTION_OVERRIDE = _compile([
    r"ignore (all |any |the )?(previous|prior|earlier|above|preceding)",
    r"disregard (all |any |the )?(previous|prior|earlier|above|instructions?|policy)",
    r"forget (everything|all|your) (previous|prior|instructions?|rules?)",
    r"new (instructions?|rules?|policy|directive)s? (are|follow|below)",
    r"from now on,? (you|treat|act|use)",
    r"treat (the|this) (next|following) (sentence|line|message) as",
    r"(this|the following) (is|becomes) (the )?(controlling|governing|authoritative)",
    r"overrid(e|ing|es) (the |any |all )?(previous|prior|policy|rules?|restrictions?)",
    r"for this (thread|message|request) only,? (redefine|treat|ignore)",
    r"redefin(e|ing) (the )?(approved|allowed|policy|rule)",
    r"no longer (requires?|needs?|applies)",
    r"routing exception",
    r"replace (its|the) destination",
    r"system prompt|developer message",
    r"you are (now|an?) (unrestricted|unlimited|dan\b|jailbroken)",
])

# ---------------------------------------------------------------------------
# Authorization claims - asserted approval the message cannot itself prove
# ---------------------------------------------------------------------------

AUTHORIZATION_CLAIM = _compile([
    r"i (certify|confirm|authorize|approve)",
    r"(has|have|was|were|been) (already )?(been )?(approved|authorized|cleared|signed off)",
    r"one[- ]time (approval|exception|authorization)",
    r"(pre|previously )[- ]?approved",
    r"management (has|have|already)",
    r"(with|per|under) (the )?(approval|authority|authorization) of",
    r"on behalf of (the )?(ceo|cfo|coo|cto|director|manager|executive|legal|compliance)",
    r"no further (approval|sign[- ]?off|authorization) (is )?(needed|required)",
    r"absent from the case record",
    r"(approved|authorized) (thread|request|destination|exception)",
    r"as (the |your )?(administrator|admin|auditor|compliance officer|security team|it (support|desk))",
    r"this (request )?continues the approved",
    r"per (our|the) (earlier|previous) (call|conversation|discussion|agreement)",
])

# ---------------------------------------------------------------------------
# Suspicious language - grouped so the evidence says *which* kind
# ---------------------------------------------------------------------------

SUSPICIOUS: Dict[str, List[Pattern[str]]] = {
    "secrecy": _compile([
        r"\bsilently\b|\bquietly\b|\bdiscreet(ly)?\b",
        r"blind[- ]?cop(y|ied)|\bbcc\b",
        r"do not (tell|inform|notify|mention|ask|confirm|reply to)",
        r"without (telling|informing|notifying|asking|confirmation|approval)",
        r"keep (this|it) (between us|confidential|quiet|off)",
        r"no need to (confirm|verify|check|ask)",
        r"delete (this|the) (message|email) after",
    ]),
    "impersonation": _compile([
        r"behave as|act as (if|an?|the)|pretend to be|role[- ]?play",
        r"you are now an?",
        r"unrestricted (mail|email|assistant|operator|mode)",
        r"(compliance|security|penetration) (simulation|test|exercise|drill)",
        r"display name matches",
        r"this is (the real|actually) ",
    ]),
    "obfuscation": _compile([
        r"\bbase64\b|\brot13\b|\bhex(adecimal)? encoded\b",
        r"decode (the|this|following)",
        r"reverse (the|this|its) (string|text|order|stated)",
        r"routing alias",
        r"interpret this as",
        r"character by character|letter by letter",
    ]),
    "payment_redirect": _compile([
        r"(updated?|new|changed?) (bank|banking|account|payment|wire|remittance) (details?|information|instructions?)",
        r"(wire|transfer|remit) (the )?(funds?|payment|money)",
        r"change (the )?(payee|beneficiary|account number)",
        r"invoice (attached|payment) (is )?(overdue|due)",
        r"gift cards?",
    ]),
    "credential_request": _compile([
        r"(verify|confirm|update|re[- ]?enter) your (account|password|credentials?|identity|details)",
        r"(send|share|provide|forward) (me )?(the |your )?(password|passcode|api[- ]?key|token|otp|one[- ]time code|mfa code|2fa)",
        r"sign in (here|using|at)|log ?in (here|using|at)",
        r"reset (your )?password",
        r"reveal (only )?(a )?(small )?(portion|part)",
    ]),
    "exfiltration": _compile([
        # Deliberately NOT a generic "send it to X": ordinary mail says that
        # all the time, and whether the destination is acceptable is decided
        # by the metadata features, not by wording.
        r"(make|place) (it|them|a copy|the \w+) available (at|to)",
        r"(also|additionally|and) (place|put|drop) a (copy|duplicate)",
        r"external (mailbox|address|drive|share)",
        r"personal (email|address|account|drive)",
        r"(first|step 1).{0,60}(then|step 2)",
        r"in (a )?(fresh|new|separate) (message|email) addressed to",
    ]),
}

# ---------------------------------------------------------------------------
# Sensitive-data indicators, grouped by kind of data
# ---------------------------------------------------------------------------

SENSITIVE_DATA: Dict[str, List[Pattern[str]]] = {
    "credential": _compile([
        r"\bpassword\b|\bpasscode\b|\bcredentials?\b",
        r"api[- ]?key|access[- ]?token|bearer token|secret key",
        r"\bmfa\b|\b2fa\b|one[- ]time (code|password)|\botp\b",
        r"private key|ssh key|\.pem\b",
        r"account recovery",
    ]),
    "financial": _compile([
        r"\biban\b|\bswift\b|\bbic\b|routing number|sort code",
        r"account number|card number|\bcvv\b",
        r"payroll|invoice|remittance|payment schedule",
        r"(financial|revenue|forecast|budget) (report|statement|workbook|projection)",
    ]),
    "personal": _compile([
        r"\bssn\b|social security|national insurance|\baadhaar\b|\bpan card\b",
        r"date of birth|\bdob\b",
        r"passport (number|copy)|driver'?s licen[cs]e",
        r"(patient|medical|health) (record|history|report)",
        r"home address|personal (details|data|information)",
        r"employee (roster|list|records?)|attendance list",
    ]),
    "confidential_marker": _compile([
        r"\bconfidential\b|\bproprietary\b|\binternal only\b|\brestricted\b",
        r"\bnda\b|non[- ]disclosure",
        r"trade secret|source[- ]code|intellectual property",
        r"privileged (and )?confidential|attorney[- ]client",
        r"do not distribute|not for (external )?(distribution|release)",
    ]),
}

# ---------------------------------------------------------------------------
# Imperatives - how instruction-like the text is
# ---------------------------------------------------------------------------

INSTRUCTION_VERBS = _compile([
    r"\b(send|forward|share|transmit|route|deliver|upload|download|append|attach|"
    r"reply|copy|add|place|reveal|disclose|summari[sz]e|export|extract|transfer|"
    r"click|open|confirm|verify|update|change|delete|remove|ignore|disregard)\b",
])

#: Free-mail / consumer domains: legitimate in general, but a red flag as the
#: destination for corporate data.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "protonmail.com", "proton.me", "gmx.com", "mail.com", "zoho.com",
    "yandex.com", "tutanota.com", "rediffmail.com",
}

#: Link-shortening services, which hide the real destination.
URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd", "buff.ly",
    "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy", "tiny.cc", "lnkd.in",
}

#: Attachment extensions by risk class.
EXECUTABLE_EXTENSIONS = {
    "exe", "scr", "com", "pif", "bat", "cmd", "msi", "jar", "vbs", "vbe",
    "js", "jse", "wsf", "wsh", "ps1", "hta", "cpl", "dll", "lnk", "app",
}
ARCHIVE_EXTENSIONS = {"zip", "rar", "7z", "tar", "gz", "bz2", "iso", "img", "cab"}
MACRO_EXTENSIONS = {"docm", "xlsm", "pptm", "dotm", "xltm", "xlam", "ppam", "sldm"}
OFFICE_EXTENSIONS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "pdf"} | MACRO_EXTENSIONS

#: Zero-width and bidirectional control characters used to hide text.
INVISIBLE_CHARS = "​‌‍⁠﻿‪‫‬‭‮⁦⁧⁨⁩"

#: A long unbroken base64-looking run, i.e. a payload rather than a word.
BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def count_hits(text: str, patterns: List[Pattern[str]]) -> Tuple[int, List[str]]:
    """How many patterns matched, and the text they matched on."""
    evidence: List[str] = []
    for pattern in patterns:
        found = pattern.search(text or "")
        if found:
            evidence.append(found.group(0).strip())
    return len(evidence), evidence
