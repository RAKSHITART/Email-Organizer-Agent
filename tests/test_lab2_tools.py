"""Acceptance tests for Lab 2 typed tools."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1_planner import create_plan_from_tools
from lab2_tools import Contact, InboxThread, SanitizedFixtureTools, VerifiedValues
from lab5_connector import GovernedConnector, LocalSession


class RecordingTools:
    """Test double proving that the planner requests every Lab 2 tool."""

    def __init__(self):
        self.calls: list[str] = []
        self.inbox = [
            InboxThread("sample-thread-101", "student@example.test", "Example question", ("student",))
        ]
        self.contacts = [Contact("student@example.test", "Example Student", "student")]

    def read_inbox(self):
        self.calls.append("read_inbox")
        return self.inbox

    def read_contacts(self):
        self.calls.append("read_contacts")
        return self.contacts

    def validate_values(self, inbox, contacts):
        self.calls.append("validate_values")
        self.asserted_inputs = (inbox, contacts)
        return VerifiedValues(True, 1, 1, ("student@example.test",), ())


class Lab2TypedToolsTests(unittest.TestCase):
    def setUp(self):
        self.request = "Help me organize my email."
        self.answers = {
            "scope": "Sanitized example messages only.",
            "priority_criteria": "Student deadlines first.",
            "desired_outputs": "A triage outline.",
        }

    def test_fixture_tools_return_typed_and_verified_values(self):
        tools = SanitizedFixtureTools(
            GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))
        )

        inbox = tools.read_inbox()
        contacts = tools.read_contacts()
        verified = tools.validate_values(inbox, contacts)

        self.assertTrue(verified.is_valid)
        self.assertTrue(all(isinstance(thread, InboxThread) for thread in inbox))
        self.assertTrue(all(isinstance(contact, Contact) for contact in contacts))
        self.assertEqual(verified.inbox_thread_count, 2)
        self.assertEqual(verified.contact_count, 2)

    def test_planner_uses_every_tool_and_returns_verified_values(self):
        tools = RecordingTools()

        plan = create_plan_from_tools(self.request, self.answers, tools)

        self.assertEqual(tools.calls, ["read_inbox", "read_contacts", "validate_values"])
        self.assertEqual(tools.asserted_inputs, (tools.inbox, tools.contacts))
        self.assertEqual(
            plan["tool_verified_context"],
            {
                "inbox_thread_count": 1,
                "contact_count": 1,
                "known_senders": ["student@example.test"],
            },
        )

    def test_planner_rejects_unverified_fixture_values(self):
        class InvalidTools(RecordingTools):
            def validate_values(self, inbox, contacts):
                super().validate_values(inbox, contacts)
                return VerifiedValues(False, 1, 1, (), ("Example validation failure.",))

        with self.assertRaisesRegex(ValueError, "Fixture values failed validation"):
            create_plan_from_tools(self.request, self.answers, InvalidTools())


if __name__ == "__main__":
    unittest.main()
