"""Lab 2 typed tools backed exclusively by sanitized fixture files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from lab5_connector import GovernedConnector


@dataclass(frozen=True)
class InboxThread:
    """A sanitized thread summary available to the Lab 2 planner."""

    thread_id: str
    sender: str
    subject: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Contact:
    """A sanitized contact record available to the Lab 2 planner."""

    email: str
    display_name: str
    relationship: str


@dataclass(frozen=True)
class VerifiedValues:
    """The result of validating values supplied by typed tools."""

    is_valid: bool
    inbox_thread_count: int
    contact_count: int
    known_senders: tuple[str, ...]
    errors: tuple[str, ...]


class EmailOrganizationTools(Protocol):
    """The Lab 2 data and verification interface used by the planner."""

    def read_inbox(self) -> list[InboxThread]: ...

    def read_contacts(self) -> list[Contact]: ...

    def validate_values(
        self, inbox: Sequence[InboxThread], contacts: Sequence[Contact]
    ) -> VerifiedValues: ...


class SanitizedFixtureTools:
    """Connector-backed Lab 2 tools; they never open fixture files themselves."""

    def __init__(self, connector: GovernedConnector) -> None:
        self._connector = connector

    def read_inbox(self) -> list[InboxThread]:
        records = self._connector.read_inbox_threads().inbox_threads
        return [
            InboxThread(
                thread_id=record.thread_id,
                sender=record.sender,
                subject=record.subject,
                labels=record.labels,
            )
            for record in records
        ]

    def read_contacts(self) -> list[Contact]:
        records = self._connector.read_contacts_prior_replies().contacts
        return [
            Contact(
                email=record.email,
                display_name=record.display_name,
                relationship=record.relationship,
            )
            for record in records
        ]

    def validate_values(
        self, inbox: Sequence[InboxThread], contacts: Sequence[Contact]
    ) -> VerifiedValues:
        errors: list[str] = []
        thread_ids = [thread.thread_id for thread in inbox]
        if len(thread_ids) != len(set(thread_ids)):
            errors.append("Inbox thread IDs must be unique.")

        contact_emails = {contact.email for contact in contacts}
        unknown_senders = sorted({thread.sender for thread in inbox} - contact_emails)
        if unknown_senders:
            errors.append("Every example sender must exist in the contacts fixture.")

        return VerifiedValues(
            is_valid=not errors,
            inbox_thread_count=len(inbox),
            contact_count=len(contacts),
            known_senders=tuple(sorted({thread.sender for thread in inbox} & contact_emails)),
            errors=tuple(errors),
        )
