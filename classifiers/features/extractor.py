"""Feature Extractor - `RawEmail` in, model-ready `FeatureVector` out.

    RAW EMAIL -> Email Parser -> [Feature Extractor] -> Feature Vector
                                        |
                        +---------------+---------------+
                        |                               |
                  Metadata features               Text features
                  sender domain                   urgency
                  recipient domain                override language
                  attachments                     authorization claim
                  links                           sensitive-data indicators

Every feature is a float, so the vector drops straight into scikit-learn or
XGBoost in Task 17. Alongside the numbers the extractor keeps `evidence`: the
exact phrase that fired each text feature, so a prediction can be explained.

The three-class target (`SAFE` / `REVISE` / `ATTACK`) is *not* decided here -
that is the classifier's job in Task 17. This stage only describes the email.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from . import lexicons as lex
from .parser import RawEmail

#: Task 17's label space, defined here so both stages agree on it.
LABELS = ("SAFE", "REVISE", "ATTACK")

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IP_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_CONFUSABLE = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "$": "s", "@": "a"})


@dataclass
class ExtractorConfig:
    """What this organisation considers internal and already-trusted.

    These are the only knobs. They are deployment facts (your own domains),
    not tuning parameters fitted to a dataset.
    """

    internal_domains: Set[str] = field(default_factory=set)
    #: External domains approved to receive data (partners, counsel, auditors).
    trusted_domains: Set[str] = field(default_factory=set)
    #: Contacts seen before; anything else counts as an unknown sender.
    known_senders: Set[str] = field(default_factory=set)

    def is_internal(self, address: str) -> bool:
        return _domain(address) in {d.lower() for d in self.internal_domains}

    def is_trusted(self, address: str) -> bool:
        domain = _domain(address)
        return domain in {d.lower() for d in self.internal_domains | self.trusted_domains}


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1].strip().lower() if address and "@" in address else ""


def _registrable(host: str) -> str:
    """Best-effort eTLD+1 without a public-suffix list."""
    parts = [p for p in (host or "").lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")


def _looks_alike(domain: str, targets: Iterable[str]) -> bool:
    """Is `domain` a near-miss of one of `targets` without being one?

    Catches c0rp-example.com, corp-example.com, corpexample.net and
    corp.example.attacker.com, while leaving our own real subdomains
    (mail.corp.example) alone.
    """
    if not domain:
        return False
    targets = {t.lower() for t in targets if t}
    if not targets or domain in targets:
        return False
    # A genuine subdomain of one of our own domains is not a lookalike.
    if _registrable(domain) in targets:
        return False

    normalised = domain.replace("-", "").replace("_", "").translate(_CONFUSABLE)
    flat_domain = normalised.replace(".", "")
    for target in targets:
        target_norm = target.replace("-", "").replace("_", "").translate(_CONFUSABLE)
        if normalised == target_norm:
            return True
        if difflib.SequenceMatcher(None, domain, target).ratio() >= 0.82:
            return True
        # Same letters once separators and confusable digits are folded away.
        flat_target = target_norm.replace(".", "")
        if flat_target and flat_target in flat_domain:
            return True
        # Our domain used as a subdomain of someone else's.
        if domain.endswith("." + target):
            return True
    return False


@dataclass
class FeatureVector:
    """Named numeric features plus the evidence behind the text ones."""

    values: Dict[str, float]
    evidence: Dict[str, List[str]] = field(default_factory=dict)

    def __getitem__(self, name: str) -> float:
        return self.values[name]

    @property
    def names(self) -> List[str]:
        return list(self.values)

    def to_list(self, names: Optional[Sequence[str]] = None) -> List[float]:
        return [float(self.values[n]) for n in (names or self.values)]

    def to_dict(self) -> Dict[str, float]:
        return dict(self.values)

    def nonzero(self) -> Dict[str, float]:
        return {k: v for k, v in self.values.items() if v}

    def explain(self) -> List[str]:
        lines = []
        for feature, phrases in sorted(self.evidence.items()):
            if phrases:
                lines.append(f"{feature}: " + ", ".join(sorted(set(phrases))[:6]))
        return lines


class FeatureExtractor:
    """Turns a parsed email into the Task 16 feature vector."""

    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or ExtractorConfig()

    # ------------------------------------------------------------------
    def extract(self, message: RawEmail) -> FeatureVector:
        values: Dict[str, float] = {}
        evidence: Dict[str, List[str]] = {}

        self._sender_features(message, values)
        self._recipient_features(message, values)
        self._attachment_features(message, values)
        self._link_features(message, values)
        self._structure_features(message, values)
        self._text_features(message, values, evidence)

        return FeatureVector(values=values, evidence=evidence)

    def extract_many(self, messages: Iterable[RawEmail]) -> List[FeatureVector]:
        return [self.extract(m) for m in messages]

    @property
    def feature_names(self) -> List[str]:
        """Stable column order - build it once from an empty message."""
        return self.extract(RawEmail()).names

    # -- metadata: sender ----------------------------------------------
    def _sender_features(self, message: RawEmail, out: Dict[str, float]) -> None:
        sender_domain = _domain(message.sender)
        internal = {d.lower() for d in self.config.internal_domains}

        out["sender_is_internal"] = float(sender_domain in internal)
        out["sender_is_trusted_domain"] = float(self.config.is_trusted(message.sender))
        out["sender_is_freemail"] = float(sender_domain in lex.FREEMAIL_DOMAINS)
        out["sender_is_unknown"] = float(
            bool(self.config.known_senders) and message.sender not in {
                s.lower() for s in self.config.known_senders
            }
        )
        out["sender_domain_is_lookalike"] = float(_looks_alike(sender_domain, internal))
        out["sender_domain_depth"] = float(len([p for p in sender_domain.split(".") if p]))
        out["sender_domain_has_digits"] = float(bool(re.search(r"\d", sender_domain)))

        # A display name that names a different domain than the envelope does.
        display = message.sender_display_name or ""
        claimed = re.findall(r"[\w.+-]+@([\w.-]+)", display)
        out["display_name_domain_mismatch"] = float(
            any(_domain(f"x@{d}") != sender_domain for d in claimed)
        )
        out["display_name_claims_authority"] = float(
            bool(re.search(r"\b(ceo|cfo|cto|coo|president|director|admin|it|support|helpdesk|security)\b",
                           display, re.IGNORECASE))
        )

        reply_to_domain = _domain(message.reply_to)
        out["reply_to_differs_from_sender"] = float(
            bool(message.reply_to) and message.reply_to != message.sender
        )
        out["reply_to_domain_differs"] = float(
            bool(reply_to_domain) and reply_to_domain != sender_domain
        )
        return_path_domain = _domain(message.return_path)
        out["return_path_mismatch"] = float(
            bool(return_path_domain) and return_path_domain != sender_domain
        )

    # -- metadata: recipients ------------------------------------------
    def _recipient_features(self, message: RawEmail, out: Dict[str, float]) -> None:
        recipients = message.recipients
        external = [r for r in recipients if not self.config.is_trusted(r)]
        freemail = [r for r in recipients if _domain(r) in lex.FREEMAIL_DOMAINS]

        out["n_recipients"] = float(len(recipients))
        out["n_external_recipients"] = float(len(external))
        out["has_external_recipient"] = float(bool(external))
        out["external_recipient_ratio"] = float(len(external) / len(recipients)) if recipients else 0.0
        out["n_freemail_recipients"] = float(len(freemail))
        out["has_freemail_recipient"] = float(bool(freemail))
        out["n_recipient_domains"] = float(len({_domain(r) for r in recipients if r}))
        out["n_cc"] = float(len(message.cc))
        out["n_bcc"] = float(len(message.bcc))
        out["has_bcc"] = float(bool(message.bcc))
        out["has_external_bcc"] = float(any(not self.config.is_trusted(r) for r in message.bcc))
        out["recipient_lookalike"] = float(any(
            _looks_alike(_domain(r), {d.lower() for d in self.config.internal_domains})
            for r in recipients
        ))

    # -- metadata: attachments -----------------------------------------
    def _attachment_features(self, message: RawEmail, out: Dict[str, float]) -> None:
        attachments = message.attachments
        extensions = [a.extension for a in attachments]

        out["n_attachments"] = float(len(attachments))
        out["has_attachment"] = float(bool(attachments))
        out["attachment_total_kb"] = float(sum(a.size_bytes for a in attachments) / 1024)
        out["has_executable_attachment"] = float(any(e in lex.EXECUTABLE_EXTENSIONS for e in extensions))
        out["has_archive_attachment"] = float(any(e in lex.ARCHIVE_EXTENSIONS for e in extensions))
        out["has_macro_attachment"] = float(any(e in lex.MACRO_EXTENSIONS for e in extensions))
        out["has_office_attachment"] = float(any(e in lex.OFFICE_EXTENSIONS for e in extensions))
        out["has_double_extension"] = float(any(
            len(re.findall(r"\.[A-Za-z0-9]{2,4}(?=\.|$)", a.filename)) > 1 for a in attachments
        ))
        out["has_unnamed_attachment"] = float(any(a.filename == "unnamed" for a in attachments))

    # -- metadata: links -----------------------------------------------
    def _link_features(self, message: RawEmail, out: Dict[str, float]) -> None:
        links = message.links
        hosts = [l.host for l in links if l.host]
        registrable = {_registrable(h) for h in hosts}
        internal = {d.lower() for d in self.config.internal_domains}

        out["n_links"] = float(len(links))
        out["has_link"] = float(bool(links))
        out["n_link_domains"] = float(len(registrable))
        out["n_external_links"] = float(sum(1 for h in hosts if _registrable(h) not in internal))
        out["has_external_link"] = float(any(_registrable(h) not in internal for h in hosts))
        out["has_shortened_link"] = float(any(_registrable(h) in lex.URL_SHORTENERS for h in hosts))
        out["has_ip_literal_link"] = float(any(_IP_HOST_RE.match(h) for h in hosts))
        out["has_punycode_link"] = float(any("xn--" in h for h in hosts))
        out["has_lookalike_link"] = float(any(_looks_alike(_registrable(h), internal) for h in hosts))
        out["has_credential_path_link"] = float(any(
            re.search(r"(login|signin|verify|account|reset|password|auth)", l.href, re.IGNORECASE)
            for l in links
        ))
        out["has_hidden_link"] = float(any(l.is_hidden for l in links))
        # Anchor text naming one domain while the href points at another.
        out["has_link_text_mismatch"] = float(any(
            l.text and re.search(r"[\w-]+\.[a-z]{2,}", l.text, re.IGNORECASE)
            and _registrable(l.host) not in l.text.lower()
            for l in links
        ))
        out["has_non_http_link"] = float(any(l.scheme not in ("", "http", "https") for l in links))

    # -- structure ------------------------------------------------------
    def _structure_features(self, message: RawEmail, out: Dict[str, float]) -> None:
        body = message.body_text or ""
        words = body.split()

        out["subject_length"] = float(len(message.subject))
        out["body_length"] = float(len(body))
        out["body_word_count"] = float(len(words))
        out["body_line_count"] = float(len(body.splitlines()))
        out["has_html_part"] = float(bool(message.body_html))
        out["has_hidden_text"] = float(bool(message.hidden_text.strip()))
        out["hidden_text_length"] = float(len(message.hidden_text))
        out["is_reply_or_forward"] = float(
            bool(message.in_reply_to)
            or bool(re.match(r"\s*(re|fwd?|aw|tr)\s*:", message.subject, re.IGNORECASE))
        )
        out["subject_is_empty"] = float(not message.subject.strip())
        out["exclamation_count"] = float(body.count("!"))
        letters = [c for c in body if c.isalpha()]
        out["uppercase_ratio"] = float(sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0
        out["has_invisible_chars"] = float(any(c in lex.INVISIBLE_CHARS for c in message.text))
        out["has_base64_blob"] = float(bool(lex.BASE64_BLOB_RE.search(body)))

        # Addresses named in the *text* rather than in the headers. An inbound
        # mail that asks the reader to route something to an address in its own
        # body is a different thing from one addressed there directly.
        mentioned = {a.lower() for a in _ADDRESS_RE.findall(message.text)}
        mentioned -= {message.sender, *message.recipients}
        external = [a for a in mentioned if not self.config.is_trusted(a)]
        out["n_body_addresses"] = float(len(mentioned))
        out["has_body_address"] = float(bool(mentioned))
        out["n_body_external_addresses"] = float(len(external))
        out["has_body_external_address"] = float(bool(external))
        out["has_body_freemail_address"] = float(
            any(_domain(a) in lex.FREEMAIL_DOMAINS for a in mentioned)
        )
        out["has_body_lookalike_address"] = float(any(
            _looks_alike(_domain(a), {d.lower() for d in self.config.internal_domains})
            for a in mentioned
        ))

    # -- text features ---------------------------------------------------
    def _text_features(self, message: RawEmail, out: Dict[str, float], evidence: Dict[str, List[str]]) -> None:
        text = message.text  # subject + body + hidden spans
        words = max(len(text.split()), 1)

        def record(name: str, patterns) -> int:
            hits, found = lex.count_hits(text, patterns)
            out[f"{name}_hits"] = float(hits)
            out[f"has_{name}"] = float(hits > 0)
            if found:
                evidence[name] = found
            return hits

        urgency = record("urgency", lex.URGENCY)
        override = record("instruction_override", lex.INSTRUCTION_OVERRIDE)
        authorization = record("authorization_claim", lex.AUTHORIZATION_CLAIM)

        suspicious_total = 0
        for family, patterns in lex.SUSPICIOUS.items():
            suspicious_total += record(f"suspicious_{family}", patterns)
        out["suspicious_language_hits"] = float(suspicious_total)
        out["has_suspicious_language"] = float(suspicious_total > 0)

        sensitive_total = 0
        for kind, patterns in lex.SENSITIVE_DATA.items():
            sensitive_total += record(f"sensitive_{kind}", patterns)
        out["sensitive_data_hits"] = float(sensitive_total)
        out["has_sensitive_data"] = float(sensitive_total > 0)

        verb_hits, verbs = lex.count_hits(text, lex.INSTRUCTION_VERBS)
        all_verbs = lex.INSTRUCTION_VERBS[0].findall(text)
        out["instruction_verb_count"] = float(len(all_verbs))
        out["instruction_density"] = float(len(all_verbs) / words)
        if verbs:
            evidence["instruction_verbs"] = sorted(set(all_verbs))[:6]

        # A compact summary of the text side: how many distinct families fired.
        out["text_signal_families"] = float(sum([
            urgency > 0, override > 0, authorization > 0,
            suspicious_total > 0, sensitive_total > 0,
        ]))
        out["text_signal_density"] = float(
            (urgency + override + authorization + suspicious_total + sensitive_total) / math.log2(words + 2)
        )


def extract_features(message: RawEmail, config: Optional[ExtractorConfig] = None) -> FeatureVector:
    """One-shot helper: parsed email in, feature vector out."""
    return FeatureExtractor(config).extract(message)
