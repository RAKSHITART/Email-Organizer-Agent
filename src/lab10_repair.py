"""Lab 10 targeted, evidence-preserving repair for rejected grounding candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from lab6_runtime import RunBudget
from lab9_grounding import CandidateOutput, EvidenceGrounder, GroundingResult


@dataclass(frozen=True)
class FailureDiagnostic:
    """Typed explanation of one rejected candidate that may be repaired."""

    thread_id: str
    failed_claims: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RepairAuditEntry:
    thread_id: str
    attempt: int
    action: str
    result: str
    timestamp: str


@dataclass(frozen=True)
class RepairRun:
    status: str
    results: tuple[GroundingResult, ...]
    diagnostics: tuple[FailureDiagnostic, ...]
    audit: tuple[RepairAuditEntry, ...]
    attempts_used: int
    escalation: str | None = None


RepairFunction = Callable[[CandidateOutput, FailureDiagnostic, int], CandidateOutput | None]


class TargetedRepairCoordinator:
    """Repair only rejected candidates with bounded, connector-grounded retries."""

    def __init__(self, grounder: EvidenceGrounder, budget: RunBudget, *, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self._grounder = grounder
        self._budget = budget
        self._max_attempts = max_attempts

    def repair(
        self,
        results: tuple[GroundingResult, ...],
        repair_function: RepairFunction,
    ) -> RepairRun:
        """Repair rejected candidates only; accepted results are retained unchanged."""
        current = list(results)
        diagnostics: list[FailureDiagnostic] = []
        audit: list[RepairAuditEntry] = []
        per_candidate_attempts: dict[str, int] = {}
        attempts_used = 0

        for index, result in enumerate(current):
            if result.status == "grounded":
                continue

            diagnostic = _diagnose(result)
            diagnostics.append(diagnostic)
            while current[index].status != "grounded":
                attempt = per_candidate_attempts.get(diagnostic.thread_id, 0)
                if attempt >= self._max_attempts:
                    return _escalated(current, diagnostics, audit, attempts_used, diagnostic)
                if attempts_used >= self._budget.max_steps:
                    audit.append(
                        _audit(diagnostic.thread_id, attempt, "enforce repair budget", "budget exhausted")
                    )
                    return _escalated(current, diagnostics, audit, attempts_used, diagnostic)

                repaired_candidate = repair_function(current[index].candidate, diagnostic, attempt + 1)
                attempts_used += 1
                per_candidate_attempts[diagnostic.thread_id] = attempt + 1
                if repaired_candidate is None:
                    audit.append(
                        _audit(diagnostic.thread_id, attempt + 1, "targeted repair", "no repair candidate supplied")
                    )
                    continue
                _assert_same_scope(current[index].candidate, repaired_candidate)

                repaired_result = self._grounder.ground(repaired_candidate)
                current[index] = repaired_result
                audit.append(
                    _audit(
                        diagnostic.thread_id,
                        attempt + 1,
                        "reground targeted candidate",
                        repaired_result.status,
                    )
                )
                if repaired_result.status == "rejected":
                    diagnostic = _diagnose(repaired_result)
                    diagnostics.append(diagnostic)

        return RepairRun("repaired", tuple(current), tuple(diagnostics), tuple(audit), attempts_used)


def _diagnose(result: GroundingResult) -> FailureDiagnostic:
    return FailureDiagnostic(
        thread_id=result.candidate.thread_id,
        failed_claims=result.candidate.claims,
        reason=result.rejection_reason or "Candidate was not grounded.",
    )


def _assert_same_scope(original: CandidateOutput, repaired: CandidateOutput) -> None:
    if (
        repaired.thread_id != original.thread_id
        or repaired.locked_plan_id != original.locked_plan_id
    ):
        raise ValueError("A targeted repair cannot change the candidate thread or locked plan.")


def _escalated(
    current: list[GroundingResult],
    diagnostics: list[FailureDiagnostic],
    audit: list[RepairAuditEntry],
    attempts_used: int,
    diagnostic: FailureDiagnostic,
) -> RepairRun:
    return RepairRun(
        "escalated",
        tuple(current),
        tuple(diagnostics),
        tuple(audit),
        attempts_used,
        f"Targeted repair exhausted for {diagnostic.thread_id}: {diagnostic.reason}",
    )


def _audit(thread_id: str, attempt: int, action: str, result: str) -> RepairAuditEntry:
    return RepairAuditEntry(thread_id, attempt, action, result, datetime.now(timezone.utc).isoformat())
