"""Acceptance tests for the Lab 1 clarify -> plan -> revise loop."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1_planner import (
    MissingClarifications,
    ask_clarification_questions,
    create_plan,
    revise_plan,
)


class Lab1PlannerTests(unittest.TestCase):
    def setUp(self):
        self.request = "Help me organize my email."
        self.answers = {
            "scope": "Sanitized examples from a faculty inbox.",
            "priority_criteria": "Student deadlines before routine notices.",
            "desired_outputs": "A triage outline and draft-reply topics.",
        }

    def test_vague_request_asks_at_least_two_clarification_questions(self):
        questions = ask_clarification_questions(self.request)

        self.assertGreaterEqual(len(questions), 2)
        self.assertTrue(all(question.endswith("?") for question in questions))

    def test_plan_requires_answers_and_reflects_them(self):
        with self.assertRaises(MissingClarifications):
            create_plan(self.request, {"scope": "Only one answer."})

        plan = create_plan(self.request, self.answers)

        self.assertEqual(plan["scope"], self.answers["scope"])
        self.assertEqual(plan["priority_criteria"], self.answers["priority_criteria"])
        self.assertEqual(plan["desired_outputs"], self.answers["desired_outputs"])
        self.assertIn("Lab 1 is planning only.", plan["constraints"])

    def test_feedback_revises_an_editable_plan_without_mutating_original(self):
        plan = create_plan(self.request, self.answers)
        revised = revise_plan(
            plan,
            {"priority_criteria": "Meeting requests and student deadlines first."},
            "Prioritize meeting requests too.",
        )

        self.assertEqual(plan["priority_criteria"], self.answers["priority_criteria"])
        self.assertEqual(revised["priority_criteria"], "Meeting requests and student deadlines first.")
        self.assertEqual(revised["revision_history"][0]["feedback"], "Prioritize meeting requests too.")


if __name__ == "__main__":
    unittest.main()
