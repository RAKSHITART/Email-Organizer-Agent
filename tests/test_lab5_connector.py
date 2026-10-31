"""Acceptance tests for Lab 5's local authenticated connector boundary."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab2_tools import SanitizedFixtureTools
from lab5_connector import AuthenticationError, GovernedConnector, LocalSession


def authenticated_connector() -> GovernedConnector:
    return GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))


class RecordingConnector:
    """A connector-shaped test double that proves Lab 2 tools use its endpoints."""

    def __init__(self, delegate: GovernedConnector) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    def read_inbox_threads(self):
        self.calls.append("read_inbox_threads")
        return self.delegate.read_inbox_threads()

    def read_contacts_prior_replies(self):
        self.calls.append("read_contacts_prior_replies")
        return self.delegate.read_contacts_prior_replies()


class Lab5ConnectorTests(unittest.TestCase):
    def test_authenticated_principal_is_available_only_after_local_authentication(self):
        authenticated = authenticated_connector()
        unauthenticated = GovernedConnector(LocalSession("", ""))

        self.assertEqual(authenticated.authenticated_principal, "faculty.example@university.test")
        with self.assertRaises(AuthenticationError):
            _ = unauthenticated.authenticated_principal

    def test_connector_is_the_single_authenticated_read_surface(self):
        connector = authenticated_connector()

        inbox = connector.read_inbox_threads()
        contacts = connector.read_contacts_prior_replies()
        calendar_tasks = connector.read_calendar_tasks()

        self.assertEqual(len(inbox.inbox_threads), 2)
        self.assertEqual(len(inbox.history_messages), 4)
        self.assertEqual(len(contacts.contacts), 2)
        self.assertEqual(len(contacts.prior_replies), 2)
        self.assertEqual(contacts.prior_replies[0].reply_id, "prior-deadline-001")
        self.assertEqual(len(calendar_tasks), 2)
        self.assertFalse(any(hasattr(connector, name) for name in ("write", "send", "archive", "delete")))

    def test_unauthenticated_access_is_rejected_for_every_endpoint(self):
        connector = GovernedConnector(LocalSession("faculty.example@university.test", ""))

        for read in (
            connector.read_inbox_threads,
            connector.read_contacts_prior_replies,
            connector.read_calendar_tasks,
        ):
            with self.assertRaises(AuthenticationError):
                read()

    def test_lab2_tools_cannot_bypass_connector(self):
        connector = RecordingConnector(authenticated_connector())
        tools = SanitizedFixtureTools(connector)  # type: ignore[arg-type]

        inbox = tools.read_inbox()
        contacts = tools.read_contacts()

        self.assertEqual(connector.calls, ["read_inbox_threads", "read_contacts_prior_replies"])
        self.assertEqual(len(inbox), 2)
        self.assertEqual(len(contacts), 2)
        self.assertFalse(hasattr(tools, "fixture_directory"))


if __name__ == "__main__":
    unittest.main()
