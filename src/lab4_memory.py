"""Lab 4 temporary run state and read-only sanitized retrieval history."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from lab5_connector import GovernedConnector


@dataclass
class RunState:
    """Temporary mutable state for exactly one execution; it has no history reference."""

    execution_id: str
    current_draft: str | None = None
    notes: list[str] = field(default_factory=list)

    def set_current_draft(self, draft: str) -> None:
        self.current_draft = draft

    def add_note(self, note: str) -> None:
        self.notes.append(note)


@dataclass(frozen=True)
class HistoryMessage:
    """One immutable sanitized item in long-term stored history."""

    thread_id: str
    sender: str
    subject: str
    body: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class StoredHistory:
    """Read-only long-term history, supplied only through the Lab 5 connector."""

    messages: tuple[HistoryMessage, ...]

    @classmethod
    def from_connector(cls, connector: GovernedConnector) -> StoredHistory:
        records = connector.read_inbox_threads().history_messages
        return cls(
            messages=tuple(
                HistoryMessage(
                    thread_id=record.thread_id,
                    sender=record.sender,
                    subject=record.subject,
                    body=record.body,
                    tags=record.tags,
                )
                for record in records
            )
        )

    def retrieve_by_tag(self, tag: str) -> tuple[HistoryMessage, ...]:
        """Return history items carrying an exact normalized tag without altering history."""
        normalized_tag = tag.strip().casefold()
        if not normalized_tag:
            raise ValueError("tag is required.")
        return tuple(
            message
            for message in self.messages
            if normalized_tag in {message_tag.casefold() for message_tag in message.tags}
        )

    def retrieve_similar(self, query: str, k: int) -> tuple[HistoryMessage, ...]:
        """Return up to k closest items using deterministic token-overlap scoring."""
        if k < 1:
            raise ValueError("k must be at least 1.")
        query_tokens = _tokens(query)
        if not query_tokens:
            raise ValueError("query must contain at least one word token.")

        ranked = sorted(
            self.messages,
            key=lambda message: (-_similarity_score(query_tokens, message), message.thread_id),
        )
        return tuple(ranked[:k])


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.casefold()))


def _similarity_score(query_tokens: frozenset[str], message: HistoryMessage) -> float:
    message_tokens = _tokens(" ".join((message.subject, message.body, " ".join(message.tags))))
    return len(query_tokens & message_tokens) / len(query_tokens)
