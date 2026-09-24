"""Email Parser - raw RFC-822 message in, structured `RawEmail` out.

This is the first stage of the Task 16 pipeline:

    RAW EMAIL -> [Email Parser] -> Feature Extractor -> Feature Vector

It deliberately depends on nothing outside the standard library, and it never
looks at any dataset. Anything that can produce an `.eml` file - a live
mailbox, an IMAP fetch, a saved message - can be fed to it.
"""
from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Bare URLs in plain-text parts.
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\])]+", re.IGNORECASE)

#: CSS that hides content from a human reader but not from a parser - the
#: classic way to smuggle instructions into an email body.
_HIDDEN_CSS_RE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em)?\b"
    r"|opacity\s*:\s*0(?:\.0+)?\b"
    r"|color\s*:\s*(?:#f{3,6}\b|white\b|rgba?\(\s*255\s*,\s*255\s*,\s*255)",
    re.IGNORECASE,
)


def _part_text(part) -> str:
    """Decode one text part from its raw bytes.

    Done by hand rather than via `get_content()`: that helper falls back to
    us-ascii when a part declares no charset, which turns characters like a
    zero-width space into surrogates and hides the invisible-character trick.
    `decode=True` also unwraps base64 / quoted-printable transfer encodings.
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        content = part.get_payload()
        return content if isinstance(content, str) else str(content or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


@dataclass
class Attachment:
    filename: str
    content_type: str
    size_bytes: int
    is_inline: bool = False

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower().lstrip(".")


@dataclass
class Link:
    href: str
    text: str = ""
    is_hidden: bool = False

    @property
    def host(self) -> str:
        match = re.match(r"(?:https?://)?(?:[^@/]*@)?([^/:?#]+)", self.href.strip(), re.IGNORECASE)
        return match.group(1).lower() if match else ""

    @property
    def scheme(self) -> str:
        match = re.match(r"([a-z][a-z0-9+.\-]*):", self.href.strip(), re.IGNORECASE)
        return match.group(1).lower() if match else ""


class _HtmlDigest(HTMLParser):
    """Pulls links, visible text and hidden spans out of an HTML part."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Link] = []
        self.text_parts: List[str] = []
        self.hidden_text: List[str] = []
        self._hidden_depth = 0
        self._open_link: Optional[Link] = None

    def _is_hidden(self, attrs: Dict[str, Optional[str]]) -> bool:
        style = attrs.get("style") or ""
        if _HIDDEN_CSS_RE.search(style):
            return True
        return attrs.get("hidden") is not None

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): value for name, value in attrs}
        if self._is_hidden(attributes):
            self._hidden_depth += 1
        if tag.lower() == "a":
            href = attributes.get("href") or ""
            if href:
                self._open_link = Link(href=href, is_hidden=self._hidden_depth > 0)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._open_link is not None:
            self.links.append(self._open_link)
            self._open_link = None
        if self._hidden_depth > 0:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not data.strip():
            return
        if self._open_link is not None:
            self._open_link.text += data.strip()
        if self._hidden_depth > 0:
            self.hidden_text.append(data.strip())
        else:
            self.text_parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self.text_parts)


@dataclass
class RawEmail:
    """One parsed message. Every field is derived from the raw bytes."""

    subject: str = ""
    sender: str = ""
    sender_display_name: str = ""
    reply_to: str = ""
    return_path: str = ""
    to: List[str] = field(default_factory=list)
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    date: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    body_text: str = ""
    body_html: str = ""
    hidden_text: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def recipients(self) -> List[str]:
        """Every destination the message is addressed to."""
        return [*self.to, *self.cc, *self.bcc]

    @property
    def text(self) -> str:
        """All readable text, hidden spans included - what an agent ingests."""
        return "\n".join(part for part in (self.subject, self.body_text, self.hidden_text) if part)

    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, raw: bytes) -> "RawEmail":
        message = email.message_from_bytes(raw, policy=policy.default)
        return cls._from_message(message)

    @classmethod
    def from_string(cls, raw: str) -> "RawEmail":
        # Parsed as bytes deliberately: `message_from_string` on a message
        # with no declared charset mangles non-ASCII (a zero-width space comes
        # back as the literal text "backslash-u200b"), which would hide exactly the
        # invisible-character trick `has_invisible_chars` looks for.
        return cls.from_bytes(raw.encode("utf-8", errors="surrogateescape"))

    @classmethod
    def from_file(cls, path) -> "RawEmail":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_parts(
        cls,
        sender: str,
        to,
        subject: str,
        body: str,
        cc=None,
        bcc=None,
        reply_to: str = "",
        html: str = "",
        attachments=None,
    ) -> "RawEmail":
        """Build one without serialising an `.eml` first (handy for tests)."""
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
        if cc:
            message["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else cc
        if bcc:
            message["Bcc"] = ", ".join(bcc) if isinstance(bcc, (list, tuple)) else bcc
        if reply_to:
            message["Reply-To"] = reply_to
        message["Subject"] = subject
        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")
        for filename, content_type, payload in attachments or []:
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(
                payload if isinstance(payload, bytes) else str(payload).encode(),
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )
        return cls._from_message(message)

    # ------------------------------------------------------------------
    @staticmethod
    def _addresses(message, header: str) -> List[str]:
        values = message.get_all(header, [])
        return [addr.lower() for _, addr in getaddresses([str(v) for v in values]) if addr]

    @classmethod
    def _from_message(cls, message) -> "RawEmail":
        display_name, sender = parseaddr(str(message.get("From", "")))
        parsed = cls(
            subject=str(message.get("Subject", "") or ""),
            sender=sender.lower(),
            sender_display_name=display_name,
            reply_to=(cls._addresses(message, "Reply-To") or [""])[0],
            return_path=(cls._addresses(message, "Return-Path") or [""])[0],
            to=cls._addresses(message, "To"),
            cc=cls._addresses(message, "Cc"),
            bcc=cls._addresses(message, "Bcc"),
            date=str(message.get("Date", "") or ""),
            message_id=str(message.get("Message-ID", "") or ""),
            in_reply_to=str(message.get("In-Reply-To", "") or ""),
            headers={k.lower(): str(v) for k, v in message.items()},
        )

        text_parts: List[str] = []
        html_parts: List[str] = []
        for part in message.walk() if message.is_multipart() else [message]:
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get_content_disposition() or "").lower()
            filename = part.get_filename()
            if disposition == "attachment" or (filename and disposition != "inline"):
                payload = part.get_payload(decode=True) or b""
                parsed.attachments.append(Attachment(
                    filename=filename or "unnamed",
                    content_type=part.get_content_type(),
                    size_bytes=len(payload),
                ))
                continue
            content = _part_text(part)
            if part.get_content_type() == "text/html":
                html_parts.append(content)
            elif part.get_content_type() == "text/plain":
                text_parts.append(content)

        parsed.body_text = "\n".join(text_parts).strip()
        parsed.body_html = "\n".join(html_parts).strip()

        if parsed.body_html:
            digest = _HtmlDigest()
            digest.feed(parsed.body_html)
            digest.close()
            parsed.links.extend(digest.links)
            parsed.hidden_text = "\n".join(digest.hidden_text)
            if not parsed.body_text:
                parsed.body_text = digest.text

        seen: set = {link.href for link in parsed.links}
        for url in URL_RE.findall(parsed.body_text):
            if url not in seen:
                parsed.links.append(Link(href=url))
                seen.add(url)

        return parsed


def parse(raw) -> RawEmail:
    """Parse bytes, str, or a path to an `.eml` file."""
    if isinstance(raw, bytes):
        return RawEmail.from_bytes(raw)
    if isinstance(raw, Path):
        return RawEmail.from_file(raw)
    text = str(raw)
    if "\n" not in text and Path(text).exists():
        return RawEmail.from_file(text)
    return RawEmail.from_string(text)
