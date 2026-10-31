"""Acceptance tests for Lab 8's deterministic per-thread parallel swarm."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab3_skills import PlanInputs, plan
from lab5_connector import GovernedConnector, LocalSession
from lab8_swarm import ParallelSwarmController


def locked_plan():
    return plan(
        PlanInputs(
            request="Organize sanitized student deadline and administrative messages.",
            scope="Sanitized student and administrative inbox examples.",
            priority_criteria="Student deadline questions before administrative routine notices.",
            desired_outputs=("triaged inbox outline", "draft-reply topics"),
        )
    )


def controller() -> ParallelSwarmController:
    connector = GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))
    return ParallelSwarmController(connector, locked_plan(), max_workers=2)


class FailingCalendarConnector(GovernedConnector):
    """Connector test double that lets fan-in exercise worker failure retention."""

    def read_calendar_tasks(self):
        raise RuntimeError("sanitized calendar fixture is unavailable")


class Lab8ParallelSwarmTests(unittest.TestCase):
    def test_multiple_workers_execute_complete_lab7_graphs(self):
        result = controller().run_parallel()

        self.assertEqual(result.statistics.worker_count, 2)
        self.assertEqual(len(result.worker_results), 2)
        for worker in result.worker_results:
            self.assertEqual(worker.graph_status, "validated")
            self.assertEqual(
                worker.execution_order,
                (
                    "IngestionClassificationAgent",
                    "PriorityUrgencyAgent",
                    "DraftReplyAgent",
                    "ActionItemExtractionAgent",
                    "FollowupReminderAgent",
                    "CalendarTaskSyncAgent",
                ),
            )

    def test_fan_in_merge_preserves_all_sanitized_threads(self):
        result = controller().run_parallel()

        self.assertEqual(set(result.thread_ids), {"sample-thread-001", "sample-thread-002"})

    def test_merge_order_is_deterministic_and_plan_aligned(self):
        swarm = controller()
        first = swarm.run_parallel()
        second = swarm.run_parallel()

        self.assertEqual(first.worker_results, second.worker_results)
        self.assertEqual(
            first.thread_ids,
            tuple(
                result.thread_id
                for result in sorted(first.worker_results, key=lambda result: (-result.alignment_score, result.thread_id))
            ),
        )
        self.assertEqual(first.thread_ids, ("sample-thread-001", "sample-thread-002"))

    def test_parallel_output_matches_sequential_output_and_reports_statistics(self):
        swarm = controller()
        sequential = swarm.run_sequential()
        parallel = swarm.run_parallel()
        comparison = swarm.compare_execution()

        self.assertEqual(sequential.worker_results, parallel.worker_results)
        self.assertEqual(sequential.statistics.worker_count, parallel.statistics.worker_count)
        self.assertEqual(comparison.sequential.mode, "sequential")
        self.assertEqual(comparison.parallel.mode, "parallel")
        self.assertTrue(comparison.outputs_match)

    def test_worker_results_are_independent_immutable_snapshots(self):
        result = controller().run_parallel()
        first, second = result.worker_results

        self.assertIsNot(first, second)
        self.assertNotEqual(first.thread_id, second.thread_id)
        with self.assertRaises(FrozenInstanceError):
            first.thread_id = "mutated"  # type: ignore[misc]

    def test_failed_workers_are_retained_in_the_deterministic_merge(self):
        failing_connector = FailingCalendarConnector(
            LocalSession("faculty.example@university.test", "local-test-token")
        )
        swarm = ParallelSwarmController(failing_connector, locked_plan(), max_workers=2)

        result = swarm.run_parallel()

        self.assertEqual(result.thread_ids, ("sample-thread-001", "sample-thread-002"))
        self.assertTrue(all(worker.graph_status == "failed" for worker in result.worker_results))
        self.assertTrue(all(worker.error for worker in result.worker_results))


if __name__ == "__main__":
    unittest.main()
