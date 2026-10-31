"""Acceptance tests for Lab 4 run state and read-only sanitized retrieval."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab4_memory import RunState, StoredHistory
from lab5_connector import GovernedConnector, LocalSession


class Lab4MemoryTests(unittest.TestCase):
    def setUp(self):
        connector = GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))
        self.history = StoredHistory.from_connector(connector)

    def test_run_state_is_temporary_and_independent_from_read_only_history(self):
        original_messages = self.history.messages
        run_state = RunState(execution_id="sanitized-run-001")

        run_state.set_current_draft("Temporary draft outline for sanitized examples.")
        run_state.add_note("Temporary planning note.")

        self.assertEqual(self.history.messages, original_messages)
        self.assertEqual(run_state.current_draft, "Temporary draft outline for sanitized examples.")
        self.assertEqual(run_state.notes, ["Temporary planning note."])
        with self.assertRaises(FrozenInstanceError):
            self.history.messages = ()

    def test_tagged_retrieval_returns_matching_sanitized_history(self):
        deadline_items = self.history.retrieve_by_tag("deadline")
        student_items = self.history.retrieve_by_tag("STUDENT")

        self.assertEqual([item.thread_id for item in deadline_items], ["history-deadline-002"])
        self.assertEqual(
            [item.thread_id for item in student_items],
            ["history-deadline-002", "history-student-004"],
        )

    def test_similarity_retrieval_returns_closest_examples_without_mutating_history(self):
        original_messages = self.history.messages

        meeting_items = self.history.retrieve_similar("schedule a meeting request", k=2)
        office_hours_items = self.history.retrieve_similar("student office hours question", k=1)

        self.assertEqual(meeting_items[0].thread_id, "history-meeting-001")
        self.assertEqual(office_hours_items[0].thread_id, "history-student-004")
        self.assertEqual(self.history.messages, original_messages)

    def test_similarity_retrieval_rejects_invalid_k(self):
        with self.assertRaises(ValueError):
            self.history.retrieve_similar("meeting", k=0)


if __name__ == "__main__":
    unittest.main()
