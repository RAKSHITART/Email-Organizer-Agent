"""Lab 8 deterministic parallel fan-out/fan-in over existing Lab 7 workers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from time import perf_counter
import re

from lab3_skills import LockedPlan
from lab5_connector import ConnectorInboxThread, GovernedConnector, InboxThreadsRead
from lab7_graph import ExecutableAgentGraph, GraphState, ValidationNode, ValidationResult


@dataclass(frozen=True)
class WorkerResult:
    """Immutable output snapshot from one thread-scoped Lab 7 graph worker."""

    thread_id: str
    alignment_score: int
    graph_status: str
    classification: str | None
    priority: int | None
    draft_reply: str | None
    action_item: str | None
    followup_required: bool
    calendar_proposals: tuple[str, ...]
    execution_order: tuple[str, ...]
    error: str | None = None
    claims: tuple[str, ...] = ()  # factual claims to ground; empty -> legacy (draft, action)


@dataclass(frozen=True)
class ExecutionStatistics:
    mode: str
    worker_count: int
    elapsed_seconds: float


@dataclass(frozen=True)
class SwarmRun:
    """Fan-in result, ordered only by locked-plan alignment then thread ID."""

    worker_results: tuple[WorkerResult, ...]
    statistics: ExecutionStatistics

    @property
    def thread_ids(self) -> tuple[str, ...]:
        return tuple(result.thread_id for result in self.worker_results)


@dataclass(frozen=True)
class ExecutionComparison:
    sequential: ExecutionStatistics
    parallel: ExecutionStatistics
    outputs_match: bool


class _ThreadScopedConnector:
    """Read-only connector view that exposes one sanitized inbox thread to a worker."""

    def __init__(self, connector: GovernedConnector, thread_id: str) -> None:
        self._connector = connector
        self._thread_id = thread_id

    @property
    def authenticated_principal(self) -> str:
        return self._connector.authenticated_principal

    def read_inbox_threads(self) -> InboxThreadsRead:
        snapshot = self._connector.read_inbox_threads()
        scoped_threads = tuple(
            thread for thread in snapshot.inbox_threads if thread.thread_id == self._thread_id
        )
        # Real mailbox messages (message_id set) are isolated to this thread; legacy
        # fixture history keeps its tag-based retrieval semantics.
        scoped_history = tuple(
            m for m in snapshot.history_messages
            if not m.message_id or m.thread_id == self._thread_id
        )
        return replace(snapshot, inbox_threads=scoped_threads, history_messages=scoped_history)

    def read_calendar_tasks(self):
        return self._connector.read_calendar_tasks()


class _ThreadScopedValidationNode(ValidationNode):
    """Apply Lab 7 validation to one email, retaining only applicable checks."""

    def validate(self, state: GraphState) -> ValidationResult:
        checks = (
            ("IngestionClassificationAgent", bool(state.classifications)),
            ("PriorityUrgencyAgent", set(state.priorities) == set(state.classifications)),
            ("DraftReplyAgent", set(state.draft_replies) == set(state.classifications)),
            (
                "ActionItemExtractionAgent",
                all(
                    thread_id in state.action_items
                    for thread_id, classification in state.classifications.items()
                    if classification == "deadline"
                ),
            ),
            (
                "FollowupReminderAgent",
                all(
                    thread_id in state.followup_reminders
                    for thread_id, priority in state.priorities.items()
                    if priority < 100
                ),
            ),
            ("CalendarTaskSyncAgent", bool(state.calendar_proposals)),
        )
        for node_name, passed in checks:
            if not passed:
                return ValidationResult(False, node_name, "required thread-scoped graph output is missing")
        return ValidationResult(True)


class ParallelSwarmController:
    """Fan out sanitized threads to independent Lab 7 workers, then merge deterministically."""

    def __init__(
        self,
        connector: GovernedConnector,
        locked_plan: LockedPlan,
        *,
        max_workers: int | None = None,
        graph_factory=None,
    ) -> None:
        self._graph_factory = graph_factory  # None -> legacy fixture graph
        self._connector = connector
        self._locked_plan = locked_plan
        self._max_workers = max_workers

    def run_sequential(self) -> SwarmRun:
        """Run one complete Lab 7 graph worker per sanitized thread in sequence."""
        start = perf_counter()
        results = [self._run_worker(thread) for thread in self._work_items()]
        return self._fan_in(results, "sequential", perf_counter() - start)

    def run_parallel(self) -> SwarmRun:
        """Run one complete Lab 7 graph worker per sanitized thread concurrently."""
        work_items = self._work_items()
        start = perf_counter()
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(self._run_worker, thread) for thread in work_items]
            results = [future.result() for future in futures]
        return self._fan_in(results, "parallel", perf_counter() - start)

    def compare_execution(self) -> ExecutionComparison:
        """Compare execution statistics while requiring equivalent merged worker output."""
        sequential = self.run_sequential()
        parallel = self.run_parallel()
        return ExecutionComparison(
            sequential=sequential.statistics,
            parallel=parallel.statistics,
            outputs_match=sequential.worker_results == parallel.worker_results,
        )

    def _work_items(self) -> tuple[ConnectorInboxThread, ...]:
        return tuple(
            sorted(
                self._connector.read_inbox_threads().inbox_threads,
                key=lambda thread: thread.thread_id,
            )
        )

    def _run_worker(self, thread: ConnectorInboxThread) -> WorkerResult:
        """Run one isolated Lab 7 state and retain even a failed worker result."""
        alignment_score = _alignment_score(thread, self._locked_plan)
        try:
            scoped = _ThreadScopedConnector(self._connector, thread.thread_id)
            if self._graph_factory is not None:
                graph = self._graph_factory(scoped)
            else:
                graph = ExecutableAgentGraph(
                    scoped,  # type: ignore[arg-type]
                    validation=_ThreadScopedValidationNode(),
                )
            graph_result = graph.run()
            state = graph_result.state
            if graph_result.status != "validated":
                return self._failed_worker(thread.thread_id, alignment_score, state, graph_result.escalation)
            return WorkerResult(
                thread_id=thread.thread_id,
                alignment_score=alignment_score,
                graph_status=graph_result.status,
                classification=state.classifications[thread.thread_id],
                priority=state.priorities[thread.thread_id],
                draft_reply=state.draft_replies[thread.thread_id],
                action_item=state.action_items.get(thread.thread_id),
                followup_required=thread.thread_id in state.followup_reminders,
                calendar_proposals=state.calendar_proposals,
                execution_order=tuple(state.execution_order),
                claims=state.claims.get(thread.thread_id, ()),
            )
        except Exception as error:  # Worker failures are merged, never silently discarded.
            return self._failed_worker(thread.thread_id, alignment_score, GraphState(), str(error))

    @staticmethod
    def _failed_worker(
        thread_id: str,
        alignment_score: int,
        state: GraphState,
        error: str | None,
    ) -> WorkerResult:
        return WorkerResult(
            thread_id=thread_id,
            alignment_score=alignment_score,
            graph_status="failed",
            classification=state.classifications.get(thread_id),
            priority=state.priorities.get(thread_id),
            draft_reply=state.draft_replies.get(thread_id),
            action_item=state.action_items.get(thread_id),
            followup_required=thread_id in state.followup_reminders,
            calendar_proposals=state.calendar_proposals,
            execution_order=tuple(state.execution_order),
            error=error or "Lab 7 graph did not validate.",
        )

    def _fan_in(self, results: list[WorkerResult], mode: str, elapsed_seconds: float) -> SwarmRun:
        merged = tuple(sorted(results, key=lambda result: (-result.alignment_score, result.thread_id)))
        return SwarmRun(
            worker_results=merged,
            statistics=ExecutionStatistics(mode, len(merged), elapsed_seconds),
        )


def _alignment_score(thread: ConnectorInboxThread, locked_plan: LockedPlan) -> int:
    thread_text = " ".join((thread.thread_id, thread.subject, *thread.labels))
    plan_text = " ".join(
        (locked_plan.request, locked_plan.scope, locked_plan.priority_criteria, *locked_plan.desired_outputs)
    )
    return len(_tokens(thread_text) & _tokens(plan_text))


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.casefold()))
