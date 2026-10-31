"""Lab 12 faculty governance, approval decisions, versioning, and rollback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from lab5_connector import AuthenticationError, GovernedConnector
from lab9_grounding import GroundingResult


class GovernanceError(PermissionError):
    """Raised when a decision or publication violates the faculty governance rules."""


@dataclass(frozen=True)
class OwnerDecision:
    decision_id: str
    thread_id: str
    decision: str
    owner_email: str
    result: GroundingResult
    note: str


@dataclass(frozen=True)
class GovernanceAuditEntry:
    action: str
    subject_id: str
    result: str
    timestamp: str


@dataclass(frozen=True)
class PublicationVersion:
    version_id: str
    decision_id: str
    thread_id: str
    result: GroundingResult


class FacultyGovernance:
    """Own faculty decisions and only version grounded, explicitly approved work."""

    _DECISIONS = frozenset({"approve", "edit", "discard", "override"})

    def __init__(self, connector: GovernedConnector, faculty_email: str) -> None:
        if not faculty_email.strip():
            raise ValueError("A faculty mailbox owner is required.")
        try:
            authenticated_principal = connector.authenticated_principal
        except AuthenticationError as error:
            raise GovernanceError("An authenticated faculty connector session is required.") from error
        if authenticated_principal.casefold() != faculty_email.strip().casefold():
            raise GovernanceError("The authenticated connector principal must be the faculty mailbox owner.")
        # Authenticate and establish the only in-scope inbox without exposing fixtures.
        self._allowed_thread_ids = frozenset(
            thread.thread_id for thread in connector.read_inbox_threads().inbox_threads
        )
        self._faculty_email = faculty_email.strip().casefold()
        self._decisions: dict[str, OwnerDecision] = {}
        self._versions: dict[str, PublicationVersion] = {}
        self._active_version_id: str | None = None
        self._rolled_back: set[str] = set()
        self._audit: list[GovernanceAuditEntry] = []
        self._connector = connector

    def decide(
        self,
        decision_id: str,
        decision: str,
        owner_email: str,
        result: GroundingResult,
        *,
        note: str = "",
    ) -> OwnerDecision:
        """Record an explicit faculty approve/edit/discard/override decision."""
        if decision not in self._DECISIONS:
            raise ValueError("Unsupported owner decision.")
        if owner_email.strip().casefold() != self._faculty_email:
            raise GovernanceError("Only the faculty mailbox owner may decide.")
        if result.candidate.thread_id not in self._allowed_thread_ids:
            raise GovernanceError("Candidate is outside the faculty mailbox scope.")
        if decision_id in self._decisions:
            raise ValueError("decision_id already exists.")
        recorded = OwnerDecision(decision_id, result.candidate.thread_id, decision, owner_email, result, note)
        self._decisions[decision_id] = recorded
        self._audit_entry("owner decision", decision_id, decision)
        return recorded

    def publish(self, decision_id: str) -> PublicationVersion:
        """Version a result only after explicit approval and the hard evidence gate."""
        decision = self._decisions.get(decision_id)
        if decision is None:
            raise GovernanceError("Explicit faculty decision is required before publication.")
        if not _is_grounded(decision.result):
            raise GovernanceError("Rejected or ungrounded results cannot be published, including overrides.")
        if decision.decision != "approve":
            raise GovernanceError("Only explicitly approved results can be published.")
        version_id = f"version-{len(self._versions) + 1:03d}"
        version = PublicationVersion(version_id, decision_id, decision.thread_id, decision.result)
        self._versions[version_id] = version
        self._active_version_id = version_id
        self._audit_entry("publish approved result", version_id, "published")
        return version

    def rollback(self, version_id: str) -> PublicationVersion | None:
        """Restore the previous version; retain every historic audit record and version."""
        if version_id not in self._versions:
            raise ValueError("Unknown version_id.")
        ordered = list(self._versions)
        index = ordered.index(version_id)
        self._active_version_id = ordered[index - 1] if index else None
        self._rolled_back.add(version_id)
        self._audit_entry("rollback publication", version_id, self._active_version_id or "no active version")
        return self.active_version

    def refresh_scope(self) -> None:
        """Re-read the faculty-only inbox scope after a refresh; decisions/audit are kept."""
        self._allowed_thread_ids = frozenset(
            thread.thread_id for thread in self._connector.read_inbox_threads().inbox_threads
        )
        self._audit_entry("refresh inbox scope", "inbox", f"{len(self._allowed_thread_ids)} threads")

    def decision(self, decision_id: str) -> OwnerDecision | None:
        return self._decisions.get(decision_id)

    def is_published(self, decision_id: str) -> bool:
        """True when an approved decision has a version that was not rolled back."""
        return any(
            v.decision_id == decision_id and v.version_id not in self._rolled_back
            for v in self._versions.values()
        )

    def audit_external(self, action: str, subject_id: str, result: str) -> None:
        """Record a governed side effect (e.g. Gmail draft saved) in the audit trail."""
        self._audit_entry(action, subject_id, result)

    @property
    def decision_history(self) -> tuple[OwnerDecision, ...]:
        return tuple(self._decisions.values())

    @property
    def active_version(self) -> PublicationVersion | None:
        return self._versions.get(self._active_version_id or "")

    @property
    def audit_log(self) -> tuple[GovernanceAuditEntry, ...]:
        return tuple(self._audit)

    @property
    def versions(self) -> tuple[PublicationVersion, ...]:
        return tuple(self._versions.values())

    def _audit_entry(self, action: str, subject_id: str, result: str) -> None:
        self._audit.append(GovernanceAuditEntry(action, subject_id, result, datetime.now(timezone.utc).isoformat()))


def _is_grounded(result: GroundingResult) -> bool:
    return result.status == "grounded" and result.evidence is not None
