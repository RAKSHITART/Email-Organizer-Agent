"""Acceptance tests for Lab 7's explicit executable agent node graph."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab5_connector import GovernedConnector, LocalSession
from lab7_graph import MAX_ATTEMPTS, ExecutableAgentGraph, ValidationNode


NODE_ORDER = [
    "IngestionClassificationAgent",
    "PriorityUrgencyAgent",
    "DraftReplyAgent",
    "ActionItemExtractionAgent",
    "FollowupReminderAgent",
    "CalendarTaskSyncAgent",
]


def connector() -> GovernedConnector:
    return GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))


class Lab7GraphTests(unittest.TestCase):
    def test_graph_executes_nodes_once_in_exact_worklet_order(self):
        result = ExecutableAgentGraph(connector()).run()

        self.assertEqual(result.status, "validated")
        self.assertEqual(result.state.execution_order, NODE_ORDER)
        self.assertEqual(result.attempts, 0)

    def test_validation_passes_for_sanitized_fixture_outputs(self):
        result = ExecutableAgentGraph(connector()).run()

        self.assertTrue(result.validation.passed)
        self.assertEqual(set(result.state.draft_replies), set(result.state.classifications))
        self.assertTrue(result.state.calendar_proposals)

    def test_validation_failure_regenerates_only_the_failing_node(self):
        validation = ValidationNode({"DraftReplyAgent": 1})
        result = ExecutableAgentGraph(connector(), validation=validation).run()

        self.assertEqual(result.status, "validated")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.state.regenerated_nodes, ["DraftReplyAgent"])
        self.assertEqual(result.state.execution_order, NODE_ORDER + ["DraftReplyAgent"])
        self.assertEqual(result.state.execution_order.count("PriorityUrgencyAgent"), 1)

    def test_retry_is_bounded_by_max_attempts(self):
        validation = ValidationNode({"DraftReplyAgent": MAX_ATTEMPTS + 1})
        result = ExecutableAgentGraph(connector(), validation=validation).run()

        self.assertEqual(result.status, "escalated")
        self.assertEqual(result.attempts, MAX_ATTEMPTS)
        self.assertEqual(result.state.regenerated_nodes, ["DraftReplyAgent"] * MAX_ATTEMPTS)
        self.assertEqual(result.state.execution_order.count("DraftReplyAgent"), MAX_ATTEMPTS + 1)

    def test_retry_exhaustion_returns_escalation_instead_of_looping(self):
        validation = ValidationNode({"ActionItemExtractionAgent": 99})
        result = ExecutableAgentGraph(connector(), validation=validation, max_attempts=1).run()

        self.assertEqual(result.status, "escalated")
        self.assertEqual(result.attempts, 1)
        self.assertIn("ActionItemExtractionAgent", result.escalation or "")
        self.assertEqual(result.state.execution_order.count("ActionItemExtractionAgent"), 2)


if __name__ == "__main__":
    unittest.main()
