"""Lab 14 local dashboard demo tests; no browser, network, or mailbox is required."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab12_governance import GovernanceError
from lab14_dashboard import create_demo


class Lab14DashboardTests(unittest.TestCase):
    def test_dashboard_displays_required_governed_views_evidence_and_controls(self):
        page = create_demo().render()

        for heading in (
            "Triaged Inbox", "Draft-Reply Queue", "Action-Item List", "Follow-up Digest",
            "Evidence", "Owner Controls", "Audit Trail", "Approve", "Edit", "Discard", "Override",
        ):
            self.assertIn(heading, page)
        self.assertIn("sample-thread-001", page)
        self.assertNotIn("Send", page)

    def test_controls_record_owner_decisions_version_and_rollback_with_audit(self):
        dashboard = create_demo()
        dashboard.owner_action("sample-thread-001", "approve", "Approved for local versioning.")

        version = dashboard.publish_approved("sample-thread-001")
        restored = dashboard.rollback_active()
        snapshot = dashboard.snapshot()

        self.assertEqual(version, "version-001")
        self.assertIsNone(restored)
        self.assertTrue(any("owner decision" in entry for entry in snapshot.audit))
        self.assertTrue(any("publish approved result" in entry for entry in snapshot.audit))
        self.assertTrue(any("rollback publication" in entry for entry in snapshot.audit))

    def test_publish_control_cannot_bypass_explicit_owner_approval(self):
        dashboard = create_demo()

        with self.assertRaises(GovernanceError):
            dashboard.publish_approved("sample-thread-001")

        dashboard.owner_action("sample-thread-001", "edit", "Owner changed the proposed wording.")
        with self.assertRaises(GovernanceError):
            dashboard.publish_approved("sample-thread-001")


if __name__ == "__main__":
    unittest.main()
