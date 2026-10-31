"""Real Gmail connector: the single governed gateway to a live mailbox.

Implements the same typed read surface as the fixture ``GovernedConnector``
(see ``lab5_connector.EmailConnector``) so every downstream lab consumes real
mail without knowing it. All Gmail API calls in the project happen here.

Safety properties (enforced by code AND by tests):
  * No method sends email. ``messages.send`` / ``drafts.send`` are never called.
  * Drafts are created only through ``create_draft_reply`` and require the
    ``gmail.compose`` scope, a thread that exists in the mailbox, and an
    idempotency key (duplicate calls return the existing draft).
  * Email content is untrusted DATA: it is normalized and length-capped but is
    never interpreted as instructions anywhere in this module.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_auth import ARCHIVE_SCOPES, DRAFT_SCOPES, GmailAuthError, GmailSession
from lab5_connector import (
    AuthenticationError,
    CalendarTask,
    ConnectorContact,
    ConnectorHistoryMessage,
    ConnectorInboxThread,
    ConnectorPriorReply,
    ContactsPriorRepliesRead,
    InboxThreadsRead,
)

MAX_BODY_CHARS = 20_000
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RELEVANT_HEADERS = ("Message-ID", "In-Reply-To", "References", "Reply-To", "List-Id", "Auto-Submitted", "Importance", "X-Priority")


class GmailApiError(RuntimeError):
    """A Gmail API call failed after bounded retries."""


class DraftError(RuntimeError):
    """A draft could not be created safely (missing scope, thread, or recipient)."""


@dataclass(frozen=True)
class DraftRecord:
    idempotency_key: str
    thread_id: str
    draft_id: str
    status: str  # "created" or "missing" (deleted/sent by the owner in Gmail)
    to: str
    subject: str
    created_at: str


class GmailConnector:
    """Governed, real-mailbox connector. Read-only unless draft scope was granted."""

    def __init__(
        self,
        session: GmailSession,
        *,
        service: Any | None = None,
        draft_log_path: Path | None = None,
        max_threads: int = 25,
        page_size: int = 25,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session
        self._service = service or build(
            "gmail", "v1", credentials=session.credentials, cache_discovery=False
        )
        self._draft_log_path = draft_log_path or (
            Path.home() / ".email_organizer_agent" / "draft_log.json"
        )
        self._max_threads = max_threads
        self._page_size = page_size
        self._max_retries = max_retries
        self._sleep = sleep
        self._cache: tuple[InboxThreadsRead, tuple[dict, ...]] | None = None

    # ------------------------------------------------------------------ identity
    @property
    def authenticated_principal(self) -> str:
        """The Gmail address verified by OAuth (never a configured value)."""
        if not self._session.verified_email:
            raise AuthenticationError("No verified Gmail session.")
        return self._session.verified_email

    @property
    def session(self) -> GmailSession:
        return self._session

    @property
    def can_create_drafts(self) -> bool:
        return set(DRAFT_SCOPES).issubset(set(self._session.scopes))

    # --------------------------------------------------------------------- reads
    def read_inbox_threads(self, *, refresh: bool = False) -> InboxThreadsRead:
        """Fetch real INBOX threads (paginated) mapped into the typed models."""
        if self._cache is None or refresh:
            self._cache = self._fetch_inbox()
        return self._cache[0]

    def read_contacts_prior_replies(self) -> ContactsPriorRepliesRead:
        """Contacts = distinct correspondents seen in the inbox (derived, not the
        People API). Prior replies = the owner's real SENT messages."""
        inbox = self.read_inbox_threads()
        owner = self.authenticated_principal.casefold()
        seen: dict[str, str] = {}
        for thread in inbox.inbox_threads:
            name, address = parseaddr(thread.sender)
            if address and address.casefold() != owner:
                seen.setdefault(address.casefold(), name or address)
        contacts = tuple(
            ConnectorContact(email=address, display_name=name, relationship="correspondent")
            for address, name in sorted(seen.items())
        )
        prior = tuple(
            ConnectorPriorReply(reply_id=m.message_id, body=m.body, tags=("sent-mail",))
            for m in self._fetch_sent_messages()
        )
        return ContactsPriorRepliesRead(contacts=contacts, prior_replies=prior)

    def read_calendar_tasks(self) -> tuple[CalendarTask, ...]:
        """Not implemented against a real Calendar/Tasks API. Returns nothing
        rather than fixture data so fixtures are never shown as live."""
        return ()

    # ----------------------------------------------------------------- archiving
    @property
    def can_archive(self) -> bool:
        return set(ARCHIVE_SCOPES).issubset(set(self._session.scopes))

    def archive_thread(self, thread_id: str) -> None:
        """Remove the INBOX label (reversible). Never deletes, trashes or sends anything."""
        self._set_inbox_label(thread_id, add=False)

    def unarchive_thread(self, thread_id: str) -> None:
        self._set_inbox_label(thread_id, add=True)

    def _set_inbox_label(self, thread_id: str, *, add: bool) -> None:
        if not self.can_archive:
            raise DraftError("Archiving requires the gmail.modify scope; re-authorize with --archive.")
        if add is False and not any(t.thread_id == thread_id for t in self.read_inbox_threads().inbox_threads):
            raise DraftError("Thread is not in the faculty inbox.")
        body = {"addLabelIds": ["INBOX"]} if add else {"removeLabelIds": ["INBOX"]}
        self._call(lambda: self._service.users().threads().modify(userId="me", id=thread_id, body=body).execute())
        self._cache = None  # next read re-fetches

    # ------------------------------------------------------------------- drafts
    def create_draft_reply(
        self, thread_id: str, body_text: str, *, idempotency_key: str
    ) -> DraftRecord:
        """Create (never send) a Gmail draft replying in an existing thread.

        Recipient = Reply-To/From of the latest message NOT written by the owner.
        Headers: In-Reply-To and References from that message's Message-ID.
        Duplicate calls with the same key return the stored record.
        """
        if not self.can_create_drafts:
            raise DraftError("Draft creation requires the gmail.compose scope; re-authorize with DRAFT_SCOPES.")
        if not idempotency_key.strip() or not body_text.strip():
            raise DraftError("An idempotency key and non-empty draft body are required.")

        log = self._load_draft_log()
        existing = log.get(idempotency_key)
        if existing is not None:
            return self.draft_status(idempotency_key)

        thread = self._call(lambda: self._service.users().threads().get(
            userId="me", id=thread_id, format="full").execute())
        messages = thread.get("messages", [])
        if not messages:
            raise DraftError("Thread has no messages; cannot reply.")
        owner = self.authenticated_principal.casefold()
        target = next(
            (m for m in reversed(messages)
             if parseaddr(_header(m, "From"))[1].casefold() != owner),
            None,
        )
        if target is None:
            raise DraftError("No incoming message in this thread to reply to.")

        reply_to = _header(target, "Reply-To") or _header(target, "From")
        recipient = parseaddr(reply_to)[1]
        if not recipient:
            raise DraftError("Could not determine a recipient for the reply.")
        subject = re.sub(r"[\x00-\x1f\x7f\s]+", " ", _header(target, "Subject")).strip() or "(no subject)"
        if re.search(r"[\x00-\x1f\x7f\s,;<>]", recipient):
            raise DraftError("Recipient address is malformed; refusing to create a draft.")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        message_id = _header(target, "Message-ID")
        references = " ".join(filter(None, (_header(target, "References"), message_id)))

        mime = EmailMessage()
        mime["To"] = recipient
        mime["From"] = self.authenticated_principal
        mime["Subject"] = subject
        if message_id:
            mime["In-Reply-To"] = message_id
            mime["References"] = references
        mime.set_content(body_text)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

        created = self._call(lambda: self._service.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": thread_id}}).execute())
        record = DraftRecord(
            idempotency_key=idempotency_key,
            thread_id=thread_id,
            draft_id=created["id"],
            status="created",
            to=recipient,
            subject=subject,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        log[idempotency_key] = record.__dict__
        self._save_draft_log(log)
        return record

    def delete_draft(self, draft_id: str) -> None:
        """Undo: delete a draft this app created, and forget its duplicate-guard entry."""
        if not self.can_create_drafts:
            raise DraftError("Deleting drafts requires the gmail.compose scope.")
        try:
            self._call(lambda: self._service.users().drafts().delete(userId="me", id=draft_id).execute())
        except GmailApiError as error:
            if "404" not in str(error):
                raise
        log = self._load_draft_log()
        self._save_draft_log({k: v for k, v in log.items() if v.get("draft_id") != draft_id})

    def draft_status(self, idempotency_key: str) -> DraftRecord:
        """Return the tracked draft, re-checking Gmail for deletion."""
        log = self._load_draft_log()
        entry = log.get(idempotency_key)
        if entry is None:
            raise DraftError("No draft recorded for this key.")
        record = DraftRecord(**entry)
        try:
            self._call(lambda: self._service.users().drafts().get(
                userId="me", id=record.draft_id).execute())
            status = "created"
        except GmailApiError as error:
            if "404" in str(error):
                status = "missing"
            else:
                raise
        if status != record.status:
            record = DraftRecord(**{**entry, "status": status})
            log[idempotency_key] = record.__dict__
            self._save_draft_log(log)
        return record

    def list_draft_records(self) -> tuple[DraftRecord, ...]:
        return tuple(DraftRecord(**e) for e in self._load_draft_log().values())

    # ----------------------------------------------------------------- internals
    def _fetch_inbox(self) -> tuple[InboxThreadsRead, tuple[dict, ...]]:
        thread_ids: list[str] = []
        page_token: str | None = None
        while len(thread_ids) < self._max_threads:
            response = self._call(lambda: self._service.users().threads().list(
                userId="me", labelIds=["INBOX"], maxResults=self._page_size,
                pageToken=page_token).execute())
            for item in response.get("threads", []) or []:
                if item.get("id") and item["id"] not in thread_ids:
                    thread_ids.append(item["id"])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        thread_ids = thread_ids[: self._max_threads]

        owner = self.authenticated_principal.casefold()
        inbox_threads: list[ConnectorInboxThread] = []
        history: list[ConnectorHistoryMessage] = []
        raw_threads: list[dict] = []
        for thread_id in thread_ids:
            thread = self._call(lambda: self._service.users().threads().get(
                userId="me", id=thread_id, format="full").execute())
            raw_threads.append(thread)
            parsed = [self._parse_message(m, thread_id) for m in thread.get("messages", [])]
            parsed = [p for p in parsed if p is not None]
            if not parsed:
                continue
            incoming = [p for p in parsed if parseaddr(p.sender)[1].casefold() != owner]
            latest = (incoming or parsed)[-1]
            labels = tuple(sorted({
                label for m in thread.get("messages", []) for label in m.get("labelIds", [])
            }))
            inbox_threads.append(ConnectorInboxThread(
                thread_id=thread_id,
                sender=latest.sender,
                subject=latest.subject,
                labels=labels,
                message_id=latest.message_id,
                recipients=latest.recipients,
                timestamp=latest.timestamp,
                snippet=thread.get("snippet", "") or latest.body[:200],
            ))
            history.extend(parsed)  # every message carries its own thread_id (isolation)
        return (
            InboxThreadsRead(inbox_threads=tuple(inbox_threads), history_messages=tuple(history)),
            tuple(raw_threads),
        )

    def _fetch_sent_messages(self, limit: int = 20) -> list[ConnectorHistoryMessage]:
        response = self._call(lambda: self._service.users().messages().list(
            userId="me", labelIds=["SENT"], maxResults=limit).execute())
        out: list[ConnectorHistoryMessage] = []
        for item in response.get("messages", []) or []:
            raw = self._call(lambda: self._service.users().messages().get(
                userId="me", id=item["id"], format="full").execute())
            parsed = self._parse_message(raw, raw.get("threadId", ""))
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_message(self, raw: dict, thread_id: str) -> ConnectorHistoryMessage | None:
        """Map a Gmail message resource into the typed model; None if unusable."""
        try:
            message_id = str(raw["id"])
            payload = raw.get("payload") or {}
            recipients = tuple(
                addr for _, addr in getaddresses(
                    [_header(raw, "To"), _header(raw, "Cc")]) if addr
            )
            headers = tuple(
                (name, _header(raw, name)) for name in _RELEVANT_HEADERS if _header(raw, name)
            )
            return ConnectorHistoryMessage(
                thread_id=thread_id or str(raw.get("threadId", "")),
                sender=_header(raw, "From"),
                subject=_header(raw, "Subject"),
                body=_extract_body(payload)[:MAX_BODY_CHARS],
                tags=tuple(sorted(raw.get("labelIds", []) or [])),
                message_id=message_id,
                recipients=recipients,
                timestamp=_timestamp(raw),
                headers=headers,
            )
        except (KeyError, TypeError, ValueError):
            return None  # malformed message: skipped, never fabricated

    def _call(self, request: Callable[[], Any]) -> Any:
        """Run a Gmail call with bounded exponential backoff on rate limits/5xx."""
        delay = 1.0
        for attempt in range(self._max_retries + 1):
            try:
                return request()
            except HttpError as error:
                status = getattr(error.resp, "status", 0)
                if status in _RETRYABLE_STATUS and attempt < self._max_retries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if status in (401, 403):
                    raise GmailAuthError(f"Gmail rejected the request ({status}): token revoked or scope insufficient.") from error
                raise GmailApiError(f"Gmail API error {status}: {error}") from error
        raise GmailApiError("Gmail API retries exhausted.")  # pragma: no cover

    def _load_draft_log(self) -> dict[str, dict]:
        if not self._draft_log_path.exists():
            return {}
        try:
            return json.loads(self._draft_log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_draft_log(self, log: dict[str, dict]) -> None:
        self._draft_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._draft_log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")


# ----------------------------------------------------------------- pure helpers
def _header(message: dict, name: str) -> str:
    for header in (message.get("payload") or {}).get("headers", []) or []:
        if str(header.get("name", "")).casefold() == name.casefold():
            return str(header.get("value", "")).strip()
    return ""


def _timestamp(message: dict) -> str:
    date_header = _header(message, "Date")
    if date_header:
        try:
            return parsedate_to_datetime(date_header).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    internal = message.get("internalDate")
    if internal:
        try:
            return datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    return ""


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


def _extract_body(payload: dict) -> str:
    """Prefer text/plain; fall back to HTML converted to text; '' if none."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = str(part.get("mimeType", ""))
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    if html:
        return _html_to_text("\n".join(html))
    return ""
