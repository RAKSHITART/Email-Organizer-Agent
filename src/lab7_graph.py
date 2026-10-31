"""Lab 7 explicit sequential agent node graph over sanitized connector data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from lab5_connector import GovernedConnector


MAX_ATTEMPTS = 2


@dataclass
class GraphState:
    """Shared mutable execution state passed into and returned from every node."""

    classifications: dict[str, str] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)
    draft_replies: dict[str, str] = field(default_factory=dict)
    action_items: dict[str, str] = field(default_factory=dict)
    followup_reminders: tuple[str, ...] = ()
    calendar_proposals: tuple[str, ...] = ()
    # Optional per-thread factual claims to ground (real mode); empty -> legacy behaviour.
    claims: dict[str, tuple[str, ...]] = field(default_factory=dict)
    execution_order: list[str] = field(default_factory=list)
    regenerated_nodes: list[str] = field(default_factory=list)


class AgentNode(Protocol):
    name: str

    def step(self, state: GraphState) -> GraphState: ...


class IngestionClassificationAgent:
    name = "IngestionClassificationAgent"

    def __init__(self, connector: GovernedConnector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        threads = self._connector.read_inbox_threads().inbox_threads
        state.classifications = {
            thread.thread_id: _classification_from_labels(thread.labels)
            for thread in threads
        }
        state.execution_order.append(self.name)
        return state


class PriorityUrgencyAgent:
    name = "PriorityUrgencyAgent"

    def step(self, state: GraphState) -> GraphState:
        state.priorities = {
            thread_id: 100 if classification == "deadline" else 50
            for thread_id, classification in state.classifications.items()
        }
        state.execution_order.append(self.name)
        return state


class DraftReplyAgent:
    name = "DraftReplyAgent"

    def step(self, state: GraphState) -> GraphState:
        state.draft_replies = {
            thread_id: f"Draft reply topic for sanitized {classification} message."
            for thread_id, classification in state.classifications.items()
        }
        state.execution_order.append(self.name)
        return state


class ActionItemExtractionAgent:
    name = "ActionItemExtractionAgent"

    def step(self, state: GraphState) -> GraphState:
        state.action_items = {
            thread_id: "Review sanitized deadline request."
            for thread_id, classification in state.classifications.items()
            if classification == "deadline"
        }
        state.execution_order.append(self.name)
        return state


class FollowupReminderAgent:
    name = "FollowupReminderAgent"

    def step(self, state: GraphState) -> GraphState:
        state.followup_reminders = tuple(
            thread_id
            for thread_id, priority in state.priorities.items()
            if priority < 100
        )
        state.execution_order.append(self.name)
        return state


class CalendarTaskSyncAgent:
    name = "CalendarTaskSyncAgent"

    def __init__(self, connector: GovernedConnector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        state.calendar_proposals = tuple(
            f"Proposed review: {item.summary}"
            for item in self._connector.read_calendar_tasks()
        )
        state.execution_order.append(self.name)
        return state


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    failing_node: str | None = None
    reason: str | None = None


class ValidationNode:
    """Validate graph output and optionally simulate deterministic test failures."""

    name = "ValidationNode"

    def __init__(self, injected_failures: Mapping[str, int] | None = None) -> None:
        self._remaining_failures = dict(injected_failures or {})

    def validate(self, state: GraphState) -> ValidationResult:
        for node_name, remaining in self._remaining_failures.items():
            if remaining > 0:
                self._remaining_failures[node_name] -= 1
                return ValidationResult(False, node_name, "deterministic injected validation failure")

        checks = (
            ("IngestionClassificationAgent", bool(state.classifications)),
            ("PriorityUrgencyAgent", set(state.priorities) == set(state.classifications)),
            ("DraftReplyAgent", set(state.draft_replies) == set(state.classifications)),
            ("ActionItemExtractionAgent", bool(state.action_items)),
            ("FollowupReminderAgent", bool(state.followup_reminders)),
            ("CalendarTaskSyncAgent", bool(state.calendar_proposals)),
        )
        for node_name, passed in checks:
            if not passed:
                return ValidationResult(False, node_name, "required sanitized graph output is missing")
        return ValidationResult(True)


@dataclass(frozen=True)
class GraphResult:
    status: str
    state: GraphState
    validation: ValidationResult
    attempts: int
    escalation: str | None = None


class ExecutableAgentGraph:
    """One explicit, sequential Lab 7 graph with targeted bounded regeneration."""

    def __init__(
        self,
        connector: GovernedConnector,
        *,
        validation: ValidationNode | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        nodes: tuple[AgentNode, ...] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self._nodes: tuple[AgentNode, ...] = nodes or (
            IngestionClassificationAgent(connector),
            PriorityUrgencyAgent(),
            DraftReplyAgent(),
            ActionItemExtractionAgent(),
            FollowupReminderAgent(),
            CalendarTaskSyncAgent(connector),
        )
        self._nodes_by_name = {node.name: node for node in self._nodes}
        self._validation = validation or ValidationNode()
        self._max_attempts = max_attempts

    def run(self, state: GraphState | None = None) -> GraphResult:
        """Run all explicit nodes once, then regenerate only failed nodes if needed."""
        shared_state = state or GraphState()
        for node in self._nodes:
            shared_state = node.step(shared_state)

        attempts = 0
        report = self._validation.validate(shared_state)
        while not report.passed:
            if attempts >= self._max_attempts:
                return GraphResult(
                    status="escalated",
                    state=shared_state,
                    validation=report,
                    attempts=attempts,
                    escalation=(
                        f"Validation failed for {report.failing_node} after "
                        f"{attempts} targeted regeneration attempts: {report.reason}"
                    ),
                )

            failing_node = self._nodes_by_name.get(report.failing_node or "")
            if failing_node is None:
                return GraphResult(
                    status="escalated",
                    state=shared_state,
                    validation=report,
                    attempts=attempts,
                    escalation="Validation reported an unknown node; escalation is required.",
                )
            shared_state.regenerated_nodes.append(failing_node.name)
            shared_state = failing_node.step(shared_state)
            attempts += 1
            report = self._validation.validate(shared_state)

        return GraphResult("validated", shared_state, report, attempts)


def _classification_from_labels(labels: tuple[str, ...]) -> str:
    if "deadline" in labels:
        return "deadline"
    if "student" in labels:
        return "student"
    if "admin" in labels:
        return "admin"
    return "routine"
