"""Lab 5 local, authenticated, read-only connector for sanitized fixtures.

This module defines the typed read surface every downstream lab (8, 9, 12, 14)
programs against. ``GovernedConnector`` below is the sanitized FIXTURE
implementation used by tests and the offline demo. The real, live-Gmail
implementation (``gmail_connector.GmailConnector``) lives in a separate module
and populates the same dataclasses, so the rest of the pipeline never has to
know which mode it is running in -- it only ever sees these typed records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Protocol, runtime_checkable


class AuthenticationError(PermissionError):
    """Raised when a connector read is attempted without a valid local session."""


@dataclass(frozen=True)
class LocalSession:
    """Local-only authentication simulation; it is not an OAuth credential."""

    principal: str
    token: str

    @property
    def is_authenticated(self) -> bool:
        return bool(self.principal.strip() and self.token.strip())


@dataclass(frozen=True)
class ConnectorInboxThread:
    thread_id: str
    sender: str
    subject: str
    labels: tuple[str, ...]
    # Optional, additive fields populated by the real Gmail connector; fixture
    # mode leaves these at their defaults so every existing caller/test that
    # only reads thread_id/sender/subject/labels is unaffected.
    message_id: str = ""
    recipients: tuple[str, ...] = ()
    timestamp: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class ConnectorHistoryMessage:
    thread_id: str
    sender: str
    subject: str
    body: str
    tags: tuple[str, ...]
    message_id: str = ""
    recipients: tuple[str, ...] = ()
    timestamp: str = ""
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class InboxThreadsRead:
    """Read-only inbox and historical-thread payload from one connector endpoint."""

    inbox_threads: tuple[ConnectorInboxThread, ...]
    history_messages: tuple[ConnectorHistoryMessage, ...]


@dataclass(frozen=True)
class ConnectorContact:
    email: str
    display_name: str
    relationship: str


@dataclass(frozen=True)
class ConnectorPriorReply:
    """A sanitized fictional owner reply, scoped by its applicable tags."""

    reply_id: str
    body: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ContactsPriorRepliesRead:
    """Read-only contacts and sanitized prior-replies payload."""

    contacts: tuple[ConnectorContact, ...]
    prior_replies: tuple[ConnectorPriorReply, ...] = ()


@dataclass(frozen=True)
class CalendarTask:
    item_id: str
    kind: str
    summary: str
    scheduled_for: str


@runtime_checkable
class EmailConnector(Protocol):
    """Structural contract shared by the fixture connector and the real Gmail connector.

    Any object satisfying this (duck-typed) shape can be passed anywhere the
    pipeline expects a connector -- lab8 swarm, lab9 grounding, lab12
    governance, lab14 dashboard -- without those modules importing or knowing
    about Gmail at all.
    """

    @property
    def authenticated_principal(self) -> str: ...

    def read_inbox_threads(self) -> InboxThreadsRead: ...

    def read_contacts_prior_replies(self) -> ContactsPriorRepliesRead: ...

    def read_calendar_tasks(self) -> tuple[CalendarTask, ...]: ...


class GovernedConnector:
    """The only local gateway permitted to read project fixture data in Lab 5."""

    def __init__(self, session: LocalSession, fixture_directory: Path | None = None) -> None:
        self._session = session
        self._fixture_directory = fixture_directory or Path(__file__).resolve().parents[1] / "fixtures"

    @property
    def authenticated_principal(self) -> str:
        """Return the locally authenticated principal, never caller-supplied identity."""
        self._require_authentication()
        return self._session.principal.strip()

    def read_inbox_threads(self) -> InboxThreadsRead:
        """Read sanitized inbox and historical threads after local authentication."""
        self._require_authentication()
        inbox_records = self._load_records("inbox_threads.json")
        history_records = self._load_records("history_messages.json")
        return InboxThreadsRead(
            inbox_threads=tuple(
                ConnectorInboxThread(
                    thread_id=record["thread_id"],
                    sender=record["sender"],
                    subject=record["subject"],
                    labels=tuple(record["labels"]),
                )
                for record in inbox_records
            ),
            history_messages=tuple(
                ConnectorHistoryMessage(
                    thread_id=record["thread_id"],
                    sender=record["sender"],
                    subject=record["subject"],
                    body=record["body"],
                    tags=tuple(record["tags"]),
                )
                for record in history_records
            ),
        )

    def read_contacts_prior_replies(self) -> ContactsPriorRepliesRead:
        """Read sanitized contacts and fictional owner replies after authentication."""
        self._require_authentication()
        contact_records = self._load_records("contacts.json")
        reply_records = self._load_records("prior_replies.json")
        return ContactsPriorRepliesRead(
            contacts=tuple(
                ConnectorContact(
                    email=record["email"],
                    display_name=record["display_name"],
                    relationship=record["relationship"],
                )
                for record in contact_records
            ),
            prior_replies=tuple(
                ConnectorPriorReply(
                    reply_id=record["reply_id"],
                    body=record["body"],
                    tags=tuple(record["tags"]),
                )
                for record in reply_records
            ),
        )

    def read_calendar_tasks(self) -> tuple[CalendarTask, ...]:
        """Read sanitized calendar/task examples; no write operation exists."""
        self._require_authentication()
        records = self._load_records("calendar_tasks.json")
        return tuple(
            CalendarTask(
                item_id=record["item_id"],
                kind=record["kind"],
                summary=record["summary"],
                scheduled_for=record["scheduled_for"],
            )
            for record in records
        )

    def _require_authentication(self) -> None:
        if not self._session.is_authenticated:
            raise AuthenticationError("An authenticated local session is required.")

    def _load_records(self, filename: str) -> list[dict[str, object]]:
        with (self._fixture_directory / filename).open(encoding="utf-8") as fixture_file:
            return json.load(fixture_file)
