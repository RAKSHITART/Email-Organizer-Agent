"""Approved-draft publisher: the only path from owner approval to a Gmail draft.

A draft is saved only if ALL hold: the decision exists and is "approve", it was
published (versioned, not rolled back), its evidence record is grounded, the
thread is in the faculty mailbox scope, and the draft text is non-empty. Saving
creates a Gmail DRAFT only. This module never sends email.
"""

from __future__ import annotations

from gmail_connector import DraftRecord, GmailConnector
from lab12_governance import FacultyGovernance, GovernanceError


class DraftPublisher:
    def __init__(self, connector: GmailConnector, governance: FacultyGovernance) -> None:
        self._connector = connector
        self._governance = governance

    def save_approved_draft(self, decision_id: str, draft_text: str) -> DraftRecord:
        decision = self._governance.decision(decision_id)
        if decision is None or decision.decision != "approve":
            raise GovernanceError("Only an explicitly approved decision can save a Gmail draft.")
        if not self._governance.is_published(decision_id):
            raise GovernanceError("Publish (version) the approved decision before saving a draft.")
        result = decision.result
        if result.status != "grounded" or result.evidence is None:
            raise GovernanceError("Ungrounded candidates cannot be saved as drafts.")
        if not draft_text.strip():
            raise GovernanceError("There is no draft text for this thread (e.g. automated mail).")
        thread = next(
            (t for t in self._connector.read_inbox_threads().inbox_threads
             if t.thread_id == decision.thread_id),
            None,
        )
        if thread is None:
            raise GovernanceError("Thread is outside the faculty mailbox scope.")
        key = f"{thread.thread_id}:{thread.message_id}"  # one draft per incoming message
        record = self._connector.create_draft_reply(
            thread.thread_id, draft_text, idempotency_key=key)
        self._governance.audit_external(
            "gmail draft saved (NOT sent)", record.draft_id, f"thread {record.thread_id}")
        return record
