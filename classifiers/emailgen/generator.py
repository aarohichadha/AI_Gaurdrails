"""Assemble synthetic `.eml` messages for the three Task 17 classes.

The label comes from the **scenario**, never from the feature values: the
generator decides "this is a credential-harvesting phish" and then builds a
message for it. Whether the extractor happens to notice is exactly what
Task 17 measures.

Class definitions (also in the README - these are the contract):

    SAFE    routine business mail that is within policy: internal or approved
            destination, authorisation referenced where one is needed, no
            adversarial intent.

    REVISE  a real colleague with a plausible business reason, but something
            is missing or ambiguous, so a human should confirm or amend
            before the agent acts - no authorisation reference for a flow
            that needs one, an unnamed destination, a brand-new contact, a
            personal mailbox, or scope creep on a past approval.

    ATTACK  adversarial intent: spoofed or lookalike sender, credential
            harvesting, payment redirect, hidden instructions, malicious
            attachment, or exfiltration to a mailbox the recipient does not
            control.

Realism knobs kept deliberately independent of class, so no single structural
feature gives the label away: attachments, links, HTML parts, Cc/Bcc, reply
chains and urgency all appear in every class at different rates.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import content as C

LABELS = ("SAFE", "REVISE", "ATTACK")


@dataclass
class GeneratedEmail:
    record_id: str
    label: str
    scenario: str
    message: EmailMessage
    notes: str = ""
    #: What made it this class, for auditing the corpus by hand.
    rationale: str = ""

    def as_bytes(self) -> bytes:
        return self.message.as_bytes()


@dataclass
class GeneratorConfig:
    seed: int = 0
    #: Share of each class built as a "hard" case - a SAFE mail that looks
    #: alarming, or an ATTACK with no trigger wording at all.
    hard_case_rate: float = 0.35
    attachment_rate: float = 0.35
    link_rate: float = 0.30
    html_rate: float = 0.30
    cc_rate: float = 0.25
    thread_rate: float = 0.30


class EmailGenerator:
    def __init__(self, config: Optional[GeneratorConfig] = None):
        self.config = config or GeneratorConfig()
        self.random = random.Random(self.config.seed)
        self._counter = 0

    # ------------------------------------------------------------------
    def _next_id(self, label: str) -> str:
        self._counter += 1
        return f"GEN-{label[:1]}{self._counter:05d}"

    def _pick(self, items):
        return self.random.choice(list(items))

    def _chance(self, probability: float) -> bool:
        return self.random.random() < probability

    def _msgid(self) -> str:
        """Deterministic for a given seed, so a corpus is reproducible."""
        return f"<{self.random.getrandbits(64):016x}@mail.example>"

    def _date(self) -> str:
        base = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        return format_datetime(base + timedelta(
            days=self.random.randint(0, 25),
            minutes=self.random.randint(0, 600),
        ))

    # ------------------------------------------------------------------
    def generate(self, label: str) -> GeneratedEmail:
        if label == "SAFE":
            return self._safe()
        if label == "REVISE":
            return self._revise()
        if label == "ATTACK":
            return self._attack()
        raise ValueError(f"unknown label {label!r}")

    def generate_many(self, counts: Dict[str, int]) -> List[GeneratedEmail]:
        out: List[GeneratedEmail] = []
        for label, count in counts.items():
            out.extend(self.generate(label) for _ in range(count))
        self.random.shuffle(out)
        return out

    # ------------------------------------------------------------------
    def _build(
        self,
        sender_display: str,
        sender_address: str,
        subject: str,
        body: str,
        *,
        to: Optional[List[str]] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: str = "",
        html_body: str = "",
        attachments: Optional[List[Tuple[str, str, bytes]]] = None,
        in_reply_to: str = "",
        return_path: str = "",
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = f"{sender_display} <{sender_address}>"
        if return_path:
            message["Return-Path"] = return_path
        message["To"] = ", ".join(to or [C.MAILBOX_OWNER])
        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)
        if reply_to:
            message["Reply-To"] = reply_to
        message["Subject"] = subject
        message["Date"] = self._date()
        message["Message-ID"] = self._msgid()
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        for filename, content_type, payload in attachments or []:
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(
                payload, maintype=maintype or "application",
                subtype=subtype or "octet-stream", filename=filename,
            )
        return message

    def _filler(self, document: C.Document) -> bytes:
        size = self.random.randint(400, 9000)
        return (f"{document.name} contents ".encode() * (size // 20 + 1))[:size]

    def _html_for(self, text: str, links: List[str], hidden: str = "") -> str:
        paragraphs = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
        anchors = "".join(
            f'<p><a href="{href}">{self._pick(["corp.example/resources", "view document", "open the portal"])}</a></p>'
            for href in links
        )
        hidden_div = (
            f'<div style="display:none;color:#ffffff;font-size:0px">{hidden}</div>'
            if hidden else ""
        )
        return f"<html><body>{paragraphs}{anchors}{hidden_div}</body></html>"

    def _slots(self, sender: C.Person, destination: str, document: C.Document) -> Dict[str, str]:
        return {
            "project": self._pick(C.PROJECTS),
            "doc": document.name,
            "dest": destination,
            "link": self._pick(C.INTERNAL_LINKS),
            "sender_first": sender.first,
            "sender_last": sender.last,
            "role": sender.role,
            "owner_first": "Sam",
            "other": self._pick(C.INTERNAL_PEOPLE).display,
            "ref": str(self.random.randint(1000, 9999)),
            "newcontact": self._pick(C.PARTNER_PEOPLE).display,
            "b64": C.b64_decoy(destination),
        }

    def _compose_body(self, intent: str, slots: Dict[str, str]) -> str:
        """Wrap one class-specific intent in shared, class-neutral padding.

        Greeting, small talk, operational detail, sign-off, signature, quoted
        thread and footer are drawn from pools shared by all three classes, so
        body length and shape carry no label information.
        """
        parts: List[str] = [self._pick(C.GREETINGS)]
        if self._chance(0.55):
            parts.append(self._pick(C.SMALL_TALK))
        parts.append(intent)
        if self._chance(0.45):
            parts.append(self._pick(C.OPERATIONAL_DETAIL))
        parts.append(self._pick(C.SIGN_OFFS) + "\n" + self._pick(C.SIGNATURE_BLOCKS))
        if self._chance(0.35):
            parts.append(self._pick(C.QUOTED_THREADS))
        if self._chance(0.30):
            parts.append(self._pick(C.FOOTERS))
        return "\n\n".join(part.format(**slots) for part in parts)

    def _subject(self, slots: Dict[str, str], alarming_rate: float) -> str:
        pool = C.ALARMING_SUBJECTS if self._chance(alarming_rate) else C.NEUTRAL_SUBJECTS
        return self._pick(pool).format(**slots)

    # -- SAFE ----------------------------------------------------------
    def _safe(self) -> GeneratedEmail:
        hard = self._chance(self.config.hard_case_rate)
        pool = C.INTERNAL_PEOPLE + (C.PARTNER_PEOPLE if hard else [])
        # A contractor or candidate writing from a consumer mailbox is normal
        # traffic: freemail on its own must not be a tell.
        if hard and self._chance(0.35):
            pool = C.FREEMAIL_PEOPLE
        sender = self._pick(pool)
        document = self._pick(C.DOCUMENTS if hard else C.DOCUMENTS)
        destination = self._pick(
            [f"{self._pick(['finance', 'legal', 'ops'])}@{C.ORG_DOMAIN}"]
            + ([f"{self._pick(['review', 'audit'])}@{self._pick(C.PARTNER_DOMAINS)}"] if hard else [])
        )
        slots = self._slots(sender, destination, document)
        body = self._compose_body(self._pick(C.SAFE_INTENTS), slots)
        # Legitimate mail is occasionally shouty too.
        subject = self._subject(slots, alarming_rate=0.06)

        attachments = []
        if self._chance(self.config.attachment_rate):
            pick = document if hard else self._pick(C.BENIGN_ATTACHMENTS)
            attachments.append((pick.filename, pick.content_type, self._filler(pick)))

        links = [self._pick(C.INTERNAL_LINKS + C.PARTNER_LINKS)] if self._chance(self.config.link_rate) else []
        html = self._html_for(body, links) if links and self._chance(self.config.html_rate) else ""

        message = self._build(
            sender.display, sender.address, subject, body,
            cc=[self._pick(C.INTERNAL_PEOPLE).address] if self._chance(self.config.cc_rate) else None,
            html_body=html, attachments=attachments,
            in_reply_to=self._msgid() if self._chance(self.config.thread_rate) else "",
        )
        return GeneratedEmail(
            record_id=self._next_id("SAFE"), label="SAFE",
            scenario="routine_business" if not hard else "authorised_external_share",
            message=message,
            rationale="within policy; destination approved and referenced where needed",
            notes="hard_case" if hard else "",
        )

    # -- REVISE --------------------------------------------------------
    def _revise(self) -> GeneratedEmail:
        sender = self._pick(
            C.FREEMAIL_PEOPLE if self._chance(0.2) else C.INTERNAL_PEOPLE + C.PARTNER_PEOPLE
        )
        document = self._pick(C.SENSITIVE_ATTACHMENTS)
        destination = self._pick([
            f"{self._pick(['review', 'newstarter', 'projects'])}@{self._pick(C.PARTNER_DOMAINS)}",
            f"{sender.first.lower()}.{sender.last.lower()}@{self._pick(C.FREEMAIL_DOMAINS)}",
            f"{self._pick(['team', 'all-hands'])}@{C.ORG_DOMAIN}",
        ])
        slots = self._slots(sender, destination, document)
        body = self._compose_body(self._pick(C.REVISE_INTENTS), slots)
        subject = self._subject(slots, alarming_rate=0.10)

        attachments = []
        if self._chance(self.config.attachment_rate + 0.15):
            attachments.append((document.filename, document.content_type, self._filler(document)))
        links = [self._pick(C.INTERNAL_LINKS)] if self._chance(self.config.link_rate) else []
        html = self._html_for(body, links) if links and self._chance(self.config.html_rate) else ""

        message = self._build(
            sender.display, sender.address, subject, body,
            cc=[self._pick(C.INTERNAL_PEOPLE).address] if self._chance(self.config.cc_rate) else None,
            html_body=html, attachments=attachments,
            in_reply_to=self._msgid() if self._chance(self.config.thread_rate) else "",
        )
        return GeneratedEmail(
            record_id=self._next_id("REVISE"), label="REVISE",
            scenario="missing_authorisation",
            message=message,
            rationale="legitimate sender and intent, but authorisation, destination or scope is unresolved",
        )

    # -- ATTACK --------------------------------------------------------
    def _attack(self) -> GeneratedEmail:
        hard = self._chance(self.config.hard_case_rate)
        # A hard attack comes from a plausible internal address and uses no
        # trigger wording; only the destination gives it away.
        if hard:
            sender = self._pick(C.INTERNAL_PEOPLE)
            sender_address = sender.address
        else:
            impostor = self._pick(C.INTERNAL_PEOPLE)
            sender = impostor
            # Lookalike domain, throwaway host, or a plain consumer mailbox.
            # Freemail appears on both sides of the label so it cannot be
            # read as "attack" on its own.
            roll = self.random.random()
            if roll < 0.5:
                sender_address = f"{impostor.first.lower()}.{impostor.last.lower()}@{self._pick(C.LOOKALIKE_DOMAINS)}"
            elif roll < 0.8:
                sender_address = f"{impostor.first.lower()}{self.random.randint(10, 99)}@{self._pick(C.DISPOSABLE_DOMAINS)}"
            else:
                sender_address = f"{impostor.first.lower()}.{impostor.last.lower()}{self.random.randint(1, 9)}@{self._pick(C.FREEMAIL_DOMAINS)}"

        document = self._pick(C.SENSITIVE_ATTACHMENTS)
        destination = self._pick([
            f"{self._pick(['collector', 'archive', 'recovery'])}-{self.random.randint(10, 99)}@{self._pick(C.DISPOSABLE_DOMAINS)}",
            f"{self._pick(['finance.team', 'it.helpdesk'])}@{self._pick(C.FREEMAIL_DOMAINS)}",
        ])
        slots = self._slots(sender, destination, document)

        # "Quiet" intents carry no wording the lexicons match; a hard attack
        # draws only from those, so only the destination betrays it.
        quiet = [C.ATTACK_INTENTS[i] for i in (4, 5, 6, 8, 10)]
        body = self._compose_body(self._pick(quiet if hard else C.ATTACK_INTENTS), slots)
        subject = self._subject(slots, alarming_rate=0.10 if hard else 0.45)

        attachments = []
        if self._chance(self.config.attachment_rate) and not hard:
            filename, content_type = self._pick(C.MALICIOUS_ATTACHMENTS)
            attachments.append((filename, content_type, b"MZ\x90\x00" + self._filler(document)))
        elif self._chance(0.25):
            attachments.append((document.filename, document.content_type, self._filler(document)))

        links = [] if hard else ([self._pick(C.MALICIOUS_LINKS)] if self._chance(self.config.link_rate + 0.2) else [])
        hidden = ""
        if not hard and self._chance(0.35):
            hidden = self._pick(C.HIDDEN_INJECTIONS).format(**slots)
        html = self._html_for(body, links, hidden) if (links or hidden) else ""

        display = sender.display
        if not hard and self._chance(0.4):
            # Spoofed display name: a role the reader trusts, or one that
            # names a different address than the envelope actually uses.
            display = self._pick([
                "IT Helpdesk", "Security Team", "Office of the CFO",
                "Payroll Administrator", f"{sender.display} (corp.example)",
                f"{sender.display} <{sender.first.lower()}@{C.ORG_DOMAIN}>",
            ])

        cc = None
        if not hard and self._chance(0.15):
            cc = [f"{self._pick(['records', 'archive'])}@{self._pick(C.LOOKALIKE_DOMAINS)}"]

        message = self._build(
            display, sender_address, subject, body,
            cc=cc,
            reply_to=destination if (not hard and self._chance(0.4)) else "",
            bcc=[destination] if (not hard and self._chance(0.25)) else None,
            html_body=html, attachments=attachments,
            in_reply_to=self._msgid() if self._chance(self.config.thread_rate) else "",
            return_path=destination if (not hard and self._chance(0.3)) else "",
        )
        return GeneratedEmail(
            record_id=self._next_id("ATTACK"), label="ATTACK",
            scenario="quiet_exfiltration" if hard else "overt_attack",
            message=message,
            rationale="adversarial: data directed to a mailbox outside the organisation's control",
            notes="hard_case" if hard else "",
        )
