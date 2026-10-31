"""Lab 11 executable acceptance suite and build gate for the local worklet."""

from __future__ import annotations

from dataclasses import dataclass

from lab3_skills import LockedPlan, PlanInputs, plan
from lab5_connector import AuthenticationError, GovernedConnector, LocalSession
from lab6_runtime import GovernedRuntime, RunBudget, RuntimeRequest
from lab8_swarm import ParallelSwarmController
from lab9_grounding import CandidateOutput, EvidenceGrounder, GroundingResult
from lab10_repair import TargetedRepairCoordinator


class AcceptanceGateFailed(RuntimeError):
    """Raised when an unverified or unsafe result attempts to pass the build gate."""


@dataclass(frozen=True)
class AcceptanceCheck:
    requirement: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class AcceptanceReport:
    checks: tuple[AcceptanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_requirements(self) -> tuple[str, ...]:
        return tuple(check.requirement for check in self.checks if not check.passed)


@dataclass(frozen=True)
class ValidatedBuild:
    """A local, validated artifact only; Lab 11 intentionally does not publish it."""

    locked_plan: LockedPlan
    grounded_results: tuple[GroundingResult, ...]


class AcceptanceSuite:
    """Exercise the required Labs 1--10 behaviors through their public interfaces."""

    def __init__(self, connector: GovernedConnector, locked_plan: LockedPlan) -> None:
        self._connector = connector
        self._locked_plan = locked_plan

    def run(self) -> AcceptanceReport:
        """Run the worklet's executable, behavior-level acceptance checks."""
        swarm = ParallelSwarmController(self._connector, self._locked_plan, max_workers=2).run_parallel()
        grounded = EvidenceGrounder(self._connector).ground_swarm(swarm, self._locked_plan)
        runtime = GovernedRuntime(self._connector)
        blocked = runtime.start(_runtime_request("lab11-approval"), RunBudget(7))
        accepted = tuple(result for result in grounded if result.status == "grounded")
        rejected = EvidenceGrounder(self._connector).ground(
            CandidateOutput("sample-thread-002", ("medical billing",), self._locked_plan.blueprint_id)
        )
        repair = TargetedRepairCoordinator(
            EvidenceGrounder(self._connector), RunBudget(2), max_attempts=1
        ).repair(
            (accepted[0], rejected),
            lambda candidate, _diagnostic, _attempt: CandidateOutput(
                candidate.thread_id, ("admin routine",), candidate.locked_plan_id
            ),
        )

        unauthenticated = GovernedConnector(LocalSession("", ""))
        try:
            unauthenticated.read_inbox_threads()
        except AuthenticationError:
            connector_rejects_unauthenticated = True
        else:
            connector_rejects_unauthenticated = False

        return AcceptanceReport(
            (
                AcceptanceCheck(
                    "end-to-end triage", bool(swarm.worker_results) and len(grounded) == len(swarm.worker_results),
                    "Every sanitized thread ran through the graph, swarm, and grounding pass.",
                ),
                AcceptanceCheck(
                    "connector-only authenticated access", connector_rejects_unauthenticated,
                    "The governed read surface rejects an unauthenticated local session.",
                ),
                AcceptanceCheck(
                    "locked plan attribution", all(
                        result.evidence is not None
                        and result.evidence.locked_plan_id == self._locked_plan.blueprint_id
                        for result in grounded
                    ),
                    "Each grounded candidate remains attributed to the immutable blueprint.",
                ),
                AcceptanceCheck(
                    "evidence grounding and thread isolation", all(
                        result.status == "grounded"
                        and result.evidence is not None
                        and all(source.target_thread_id == result.candidate.thread_id for source in result.evidence.sources)
                        for result in grounded
                    ),
                    "Evidence is supported and retained with its candidate thread.",
                ),
                AcceptanceCheck(
                    "targeted repair and bounded retry", repair.status == "repaired"
                    and repair.results[0] is accepted[0]
                    and repair.attempts_used == 1,
                    "Only the rejected candidate was repaired and regrounded within its budget.",
                ),
                AcceptanceCheck(
                    "approval gate and audit", blocked.status == "awaiting_approval"
                    and bool(runtime.audit_log("lab11-approval"))
                    and all(
                        entry.intent and entry.action and entry.result and entry.timestamp
                        for entry in runtime.audit_log("lab11-approval")
                    ),
                    "The simulated risky operation is blocked and fully audited without approval.",
                ),
                AcceptanceCheck(
                    "unsafe and unsupported candidates cannot pass", rejected.status == "rejected"
                    and not self._can_build((rejected,)),
                    "Unsupported evidence is rejected before any validated build artifact exists.",
                ),
            )
        )

    def build(self, results: tuple[GroundingResult, ...]) -> ValidatedBuild:
        """Enforce the build gate; rejected candidates never become a build artifact."""
        if not self._can_build(results):
            raise AcceptanceGateFailed("Build blocked: every candidate must be grounded and plan-attributed.")
        return ValidatedBuild(self._locked_plan, results)

    def _can_build(self, results: tuple[GroundingResult, ...]) -> bool:
        return bool(results) and all(
            result.status == "grounded"
            and result.evidence is not None
            and result.evidence.locked_plan_id == self._locked_plan.blueprint_id
            for result in results
        )


def local_acceptance_plan() -> LockedPlan:
    """Create the fixed sanitized plan used by the repeatable local acceptance run."""
    return plan(
        PlanInputs(
            request="Organize sanitized student deadline and administrative messages.",
            scope="Sanitized student and administrative inbox examples.",
            priority_criteria="Student deadline questions before administrative routine notices.",
            desired_outputs=("triaged inbox outline", "draft-reply topics"),
        )
    )


def _runtime_request(execution_id: str) -> RuntimeRequest:
    return RuntimeRequest(
        execution_id=execution_id,
        request="Organize sanitized faculty examples.",
        clarification_answers=(
            ("scope", "Sanitized examples"),
            ("priority_criteria", "Deadlines first"),
            ("desired_outputs", "triage outline"),
        ),
        skill_inputs=PlanInputs(
            request="Organize sanitized faculty examples.",
            scope="Sanitized examples",
            priority_criteria="Deadlines first",
            desired_outputs=("triage outline",),
        ),
        retrieval_tag="student",
    )
