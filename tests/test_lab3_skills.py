"""Acceptance tests for Lab 3 reusable planning and formatting skills."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab3_skills import (
    PlanInputs,
    TRIAGED_INBOX_TEMPLATE,
    format_triaged,
    plan,
    revise_locked_plan,
)


class Lab3ReusableSkillsTests(unittest.TestCase):
    def setUp(self):
        self.teaching_request = PlanInputs(
            request="Organize sanitized teaching-week messages.",
            scope="Sanitized student and administrative examples.",
            priority_criteria="Student deadlines first.",
            desired_outputs=("triaged inbox outline", "draft-reply topics"),
        )
        self.collaboration_request = PlanInputs(
            request="Organize sanitized collaboration messages.",
            scope="Sanitized collaborator and vendor examples.",
            priority_criteria="Meeting requests first.",
            desired_outputs=("triaged inbox outline", "meeting-request list"),
        )

    def test_same_plan_and_format_skills_support_two_requests(self):
        teaching_plan = plan(self.teaching_request)
        collaboration_plan = plan(self.collaboration_request)

        teaching_document = format_triaged(teaching_plan, TRIAGED_INBOX_TEMPLATE)
        collaboration_document = format_triaged(collaboration_plan, TRIAGED_INBOX_TEMPLATE)

        self.assertNotEqual(teaching_plan.blueprint_id, collaboration_plan.blueprint_id)
        self.assertEqual(teaching_document.template_name, collaboration_document.template_name)
        self.assertEqual(teaching_document.sections[1].items, (self.teaching_request.scope,))
        self.assertEqual(collaboration_document.sections[1].items, (self.collaboration_request.scope,))

    def test_locked_plan_is_immutable(self):
        locked_plan = plan(self.teaching_request)

        with self.assertRaises(FrozenInstanceError):
            locked_plan.scope = "A different scope"
        with self.assertRaises(AttributeError):
            locked_plan.desired_outputs.append("another output")

    def test_feedback_creates_a_new_locked_revision(self):
        original = plan(self.teaching_request)
        updated_inputs = PlanInputs(
            request=original.request,
            scope=original.scope,
            priority_criteria="Student deadlines and meetings first.",
            desired_outputs=original.desired_outputs,
        )

        revised = revise_locked_plan(original, updated_inputs)

        self.assertEqual(original.priority_criteria, "Student deadlines first.")
        self.assertEqual(revised.priority_criteria, "Student deadlines and meetings first.")
        self.assertEqual(revised.revision_of, original.blueprint_id)
        with self.assertRaises(FrozenInstanceError):
            revised.priority_criteria = "Mutated"

    def test_document_has_reusable_structured_template_sections(self):
        document = format_triaged(plan(self.teaching_request), TRIAGED_INBOX_TEMPLATE)

        self.assertEqual(
            tuple(section.heading for section in document.sections),
            TRIAGED_INBOX_TEMPLATE.section_order,
        )
        self.assertIn("No email has been read", document.footer)


if __name__ == "__main__":
    unittest.main()
