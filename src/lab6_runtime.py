"""Lab 6 governed runtime: budgets, checkpoints, audit, and simulated approval."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from lab1_planner import create_plan, create_plan_from_tools
from lab2_tools import SanitizedFixtureTools
from lab3_skills import LockedPlan, PlanInputs, TriagedDocument, format_triaged, plan, TRIAGED_INBOX_TEMPLATE
from lab4_memory import RunState, StoredHistory
from lab5_connector import GovernedConnector


class BudgetExhausted(RuntimeError):
    """Raised when a run cannot start another step within its configured budget."""


class ApprovalRequired(RuntimeError):
    """Raised internally when a simulated risky action does not have approval."""


@dataclass(frozen=True)
class RunBudget:
    """Maximum completed runtime steps permitted for one execution."""

    max_steps: int

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1.")


@dataclass(frozen=True)
class RuntimeRequest:
    """Sanitized request inputs used by this local Lab 6 execution."""

    execution_id: str
    request: str
    clarification_answers: tuple[tuple[str, str], ...]
    skill_inputs: PlanInputs
    retrieval_tag: str

    def answers(self) -> dict[str, str]:
        return dict(self.clarification_answers)


@dataclass(frozen=True)
class AuditEntry:
    """One append-only description of a runtime intent, action, and result."""

    intent: str
    action: str
    result: str
    timestamp: str


@dataclass(frozen=True)
class RuntimeCheckpoint:
    """Private execution snapshot captured after one completed runtime step."""

    execution_id: str
    next_step: int
    context: dict[str, Any]
    current_draft: str | None
    notes: tuple[str, ...]
    timestamp: str


@dataclass(frozen=True)
class RuntimeResult:
    execution_id: str
    status: str
    next_step: str | None
    document: TriagedDocument | None


@dataclass
class _Execution:
    request: RuntimeRequest
    budget: RunBudget
    run_state: RunState
    context: dict[str, Any] = field(default_factory=dict)
    next_step: int = 0
    steps_used: int = 0
    audit: list[AuditEntry] = field(default_factory=list)
    checkpoints: list[RuntimeCheckpoint] = field(default_factory=list)


class GovernedRuntime:
    """Owns state, budget, checkpoints, audit records, and approval in Lab 6."""

    _STEP_NAMES = (
        "editable_plan",
        "tool_verified_plan",
        "history_retrieval",
        "locked_plan",
        "format_triaged",
        "calendar_task_context",
        "approval_gate",
    )

    def __init__(self, connector: GovernedConnector) -> None:
        self._connector = connector
        self._executions: dict[str, _Execution] = {}

    def start(
        self,
        request: RuntimeRequest,
        budget: RunBudget,
        *,
        approve_risky: bool = False,
        stop_after_step: str | None = None,
    ) -> RuntimeResult:
        """Create one isolated execution and run it sequentially until it stops."""
        if request.execution_id in self._executions:
            raise ValueError("execution_id already exists; use resume for that execution.")
        execution = _Execution(
            request=request,
            budget=budget,
            run_state=RunState(execution_id=request.execution_id),
        )
        self._executions[request.execution_id] = execution
        return self._run(execution, approve_risky=approve_risky, stop_after_step=stop_after_step)

    def resume(
        self,
        execution_id: str,
        *,
        approve_risky: bool = False,
        stop_after_step: str | None = None,
    ) -> RuntimeResult:
        """Restore the latest checkpoint for one execution and continue from it."""
        execution = self._executions.get(execution_id)
        if execution is None:
            raise ValueError("Unknown execution_id.")
        if not execution.checkpoints:
            raise ValueError("This execution has no completed-step checkpoint to resume.")

        checkpoint = execution.checkpoints[-1]
        execution.next_step = checkpoint.next_step
        execution.context = deepcopy(checkpoint.context)
        execution.run_state.current_draft = checkpoint.current_draft
        execution.run_state.notes = list(checkpoint.notes)
        self._audit(
            execution,
            "resume execution",
            "restore latest checkpoint",
            f"restored after step {checkpoint.next_step}",
        )
        return self._run(execution, approve_risky=approve_risky, stop_after_step=stop_after_step)

    def audit_log(self, execution_id: str) -> tuple[AuditEntry, ...]:
        return tuple(self._execution(execution_id).audit)

    def checkpoints(self, execution_id: str) -> tuple[RuntimeCheckpoint, ...]:
        """Return defensive copies of this execution's per-run checkpoints."""
        return tuple(deepcopy(self._execution(execution_id).checkpoints))

    def _run(
        self,
        execution: _Execution,
        *,
        approve_risky: bool,
        stop_after_step: str | None,
    ) -> RuntimeResult:
        while execution.next_step < len(self._STEP_NAMES):
            step_name = self._STEP_NAMES[execution.next_step]
            if step_name == "approval_gate" and not approve_risky:
                self._audit(
                    execution,
                    "protect simulated risky operation",
                    "approval gate",
                    "blocked: explicit approval is required; no action was performed",
                )
                return self._result(execution, "awaiting_approval")

            self._enforce_budget(execution)
            intent, action, result = self._complete_step(execution, step_name)
            execution.steps_used += 1
            execution.next_step += 1
            self._audit(execution, intent, action, result)
            self._checkpoint(execution)
            if stop_after_step == step_name:
                return self._result(execution, "paused")

        return self._result(execution, "completed")

    def _complete_step(self, execution: _Execution, step_name: str) -> tuple[str, str, str]:
        request = execution.request
        if step_name == "editable_plan":
            editable = create_plan(request.request, request.answers())
            execution.context["editable_plan"] = editable
            execution.run_state.set_current_draft(editable["title"])
            return "clarify and plan", "create editable plan", "editable plan created"

        if step_name == "tool_verified_plan":
            tools = SanitizedFixtureTools(self._connector)
            tool_plan = create_plan_from_tools(request.request, request.answers(), tools)
            execution.context["tool_verified_plan"] = tool_plan
            return "use typed tools", "read connector-backed examples and validate values", "verified plan context created"

        if step_name == "history_retrieval":
            history = StoredHistory.from_connector(self._connector)
            matches = history.retrieve_by_tag(request.retrieval_tag)
            execution.context["history_match_ids"] = tuple(message.thread_id for message in matches)
            return "retrieve stored history", "retrieve by sanitized tag", f"{len(matches)} history matches"

        if step_name == "locked_plan":
            locked = plan(request.skill_inputs)
            execution.context["locked_plan"] = locked
            return "lock blueprint", "create immutable locked plan", f"blueprint {locked.blueprint_id} locked"

        if step_name == "format_triaged":
            locked = self._locked_plan(execution)
            document = format_triaged(locked, TRIAGED_INBOX_TEMPLATE)
            execution.context["document"] = document
            return "format triaged inbox", "apply reusable template", "structured planning document created"

        if step_name == "calendar_task_context":
            calendar_tasks = self._connector.read_calendar_tasks()
            execution.context["calendar_task_ids"] = tuple(item.item_id for item in calendar_tasks)
            return "read calendar and tasks", "read connector calendar/task endpoint", f"{len(calendar_tasks)} read-only items"

        if step_name == "approval_gate":
            return (
                "authorize simulated risky operation",
                "approval gate",
                "approved simulation only; no publish, send, or external action performed",
            )

        raise AssertionError(f"Unknown runtime step: {step_name}")

    def _enforce_budget(self, execution: _Execution) -> None:
        if execution.steps_used >= execution.budget.max_steps:
            self._audit(
                execution,
                "enforce run budget",
                "block next runtime step",
                f"budget exhausted after {execution.steps_used} completed steps",
            )
            raise BudgetExhausted("Run budget exhausted before the next step.")

    def _checkpoint(self, execution: _Execution) -> None:
        execution.checkpoints.append(
            RuntimeCheckpoint(
                execution_id=execution.request.execution_id,
                next_step=execution.next_step,
                context=deepcopy(execution.context),
                current_draft=execution.run_state.current_draft,
                notes=tuple(execution.run_state.notes),
                timestamp=_timestamp(),
            )
        )

    def _audit(self, execution: _Execution, intent: str, action: str, result: str) -> None:
        execution.audit.append(AuditEntry(intent, action, result, _timestamp()))

    def _result(self, execution: _Execution, status: str) -> RuntimeResult:
        next_step = (
            self._STEP_NAMES[execution.next_step]
            if execution.next_step < len(self._STEP_NAMES)
            else None
        )
        return RuntimeResult(
            execution_id=execution.request.execution_id,
            status=status,
            next_step=next_step,
            document=execution.context.get("document"),
        )

    def _execution(self, execution_id: str) -> _Execution:
        execution = self._executions.get(execution_id)
        if execution is None:
            raise ValueError("Unknown execution_id.")
        return execution

    @staticmethod
    def _locked_plan(execution: _Execution) -> LockedPlan:
        locked = execution.context.get("locked_plan")
        if not isinstance(locked, LockedPlan):
            raise RuntimeError("The locked plan step has not completed.")
        return locked


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
