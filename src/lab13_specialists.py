"""Lab 13 typed, replaceable specialists under the governed Coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from lab12_governance import FacultyGovernance, OwnerDecision
from lab9_grounding import GroundingResult


class SpecialistError(RuntimeError):
    """Raised when a specialist fails or a protected governance gate is bypassed."""


@dataclass
class SpecialistContext:
    result: GroundingResult
    owner_email: str
    owner_decision: str
    execution_order: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    decision: OwnerDecision | None = None


class Specialist(Protocol):
    name: str

    def execute(self, context: SpecialistContext) -> SpecialistContext: ...


class _RecordedSpecialist:
    """Deterministic specialist with no connector or fixture access capability."""

    def __init__(self, name: str) -> None:
        self.name = name

    def execute(self, context: SpecialistContext) -> SpecialistContext:
        context.execution_order.append(self.name)
        context.notes.append(f"{self.name} processed {context.result.candidate.thread_id}.")
        return context


class IngestionClassificationSpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Ingestion & Classification")


class PriorityUrgencySpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Priority & Urgency")


class DraftReplySpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Draft Reply")


class ActionItemExtractionSpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Action-Item Extraction")


class FollowupReminderSpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Follow-up / Reminder")


class CalendarTaskSyncSpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Calendar & Task Sync")


class FacultyReviewSpecialist:
    name = "Faculty Review"

    def __init__(self, governance: FacultyGovernance) -> None:
        self._governance = governance

    def execute(self, context: SpecialistContext) -> SpecialistContext:
        context.decision = self._governance.decide(
            f"specialist-{context.result.candidate.thread_id}",
            context.owner_decision,
            context.owner_email,
            context.result,
        )
        context.execution_order.append(self.name)
        return context


class ArchiveLoggingSpecialist(_RecordedSpecialist):
    def __init__(self) -> None: super().__init__("Archive & Logging")


class SpecialistCoordinator:
    """Runs typed specialists in worklet order, with evidence and owner gates intact."""

    _ORDER = (
        "Ingestion & Classification", "Priority & Urgency", "Draft Reply",
        "Action-Item Extraction", "Follow-up / Reminder", "Calendar & Task Sync",
        "Faculty Review", "Archive & Logging",
    )
    _PROTECTED = frozenset({"Faculty Review", "Archive & Logging"})

    def __init__(self, governance: FacultyGovernance) -> None:
        self._specialists: dict[str, Specialist] = {
            "Ingestion & Classification": IngestionClassificationSpecialist(),
            "Priority & Urgency": PriorityUrgencySpecialist(),
            "Draft Reply": DraftReplySpecialist(),
            "Action-Item Extraction": ActionItemExtractionSpecialist(),
            "Follow-up / Reminder": FollowupReminderSpecialist(),
            "Calendar & Task Sync": CalendarTaskSyncSpecialist(),
            "Faculty Review": FacultyReviewSpecialist(governance),
            "Archive & Logging": ArchiveLoggingSpecialist(),
        }

    def register(self, name: str, specialist: Specialist) -> None:
        """Replace a domain specialist without allowing governance gates to be replaced."""
        if name not in self._ORDER:
            raise ValueError("Unknown specialist name.")
        if name in self._PROTECTED:
            raise SpecialistError("Governance specialists cannot be replaced.")
        if specialist.name != name:
            raise ValueError("Replacement specialist name must match its registered role.")
        self._specialists[name] = specialist

    def execute(self, result: GroundingResult, *, owner_email: str, owner_decision: str) -> SpecialistContext:
        """Validate first, then execute in order and require explicit Faculty Review."""
        if result.status != "grounded" or result.evidence is None:
            raise SpecialistError("Evidence validation is a hard gate before specialists run.")
        context = SpecialistContext(result, owner_email, owner_decision)
        for name in self._ORDER:
            try:
                context = self._specialists[name].execute(context)
            except Exception as error:
                raise SpecialistError(f"{name} failed: {error}") from error
        return context
