"""Behavior-level governance tests for Lab 12."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab5_connector import GovernedConnector, LocalSession
from lab9_grounding import CandidateOutput, EvidenceGrounder
from lab11_acceptance import local_acceptance_plan
from lab12_governance import FacultyGovernance, GovernanceError


FACULTY = "faculty.example@university.test"


def connector() -> GovernedConnector:
    return GovernedConnector(LocalSession(FACULTY, "local-test-token"))


class Lab12GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.plan = local_acceptance_plan()
        self.governance = FacultyGovernance(connector(), FACULTY)
        self.grounded = EvidenceGrounder(connector()).ground(
            CandidateOutput("sample-thread-001", ("deadline",), self.plan.blueprint_id)
        )
        self.rejected = EvidenceGrounder(connector()).ground(
            CandidateOutput("sample-thread-002", ("medical billing",), self.plan.blueprint_id)
        )

    def test_matching_authenticated_principal_establishes_faculty_scope(self):
        governance = FacultyGovernance(connector(), FACULTY)

        decision = governance.decide("matching", "approve", FACULTY, self.grounded)

        self.assertEqual(decision.owner_email, FACULTY)

    def test_mismatched_authenticated_principal_is_rejected_at_construction(self):
        mismatched = GovernedConnector(LocalSession("student.example@university.test", "local-test-token"))

        with self.assertRaisesRegex(GovernanceError, "authenticated connector principal"):
            FacultyGovernance(mismatched, FACULTY)

    def test_missing_authenticated_identity_is_rejected_at_construction(self):
        missing = GovernedConnector(LocalSession("", "local-test-token"))

        with self.assertRaisesRegex(GovernanceError, "authenticated faculty connector session"):
            FacultyGovernance(missing, FACULTY)

    def test_only_faculty_owner_can_record_an_in_scope_decision(self):
        with self.assertRaises(GovernanceError):
            self.governance.decide("d1", "approve", "student.example@university.test", self.grounded)
        with self.assertRaises(GovernanceError):
            self.governance.decide("d2", "approve", FACULTY, self.rejected.__class__(
                CandidateOutput("unknown", ("deadline",), self.plan.blueprint_id), "rejected", None, "missing"
            ))

    def test_explicit_owner_decisions_are_audited_and_discard_cannot_publish(self):
        decision = self.governance.decide("d1", "discard", FACULTY, self.grounded, note="Owner chose no reply.")

        self.assertEqual(decision.decision, "discard")
        self.assertEqual(self.governance.audit_log[-1].subject_id, "d1")
        with self.assertRaises(GovernanceError):
            self.governance.publish("d1")

    def test_edit_or_override_requires_later_explicit_approval_before_publication(self):
        self.governance.decide("edit", "edit", FACULTY, self.grounded, note="Owner revised wording.")
        self.governance.decide("override", "override", FACULTY, self.grounded, note="Owner retained candidate.")

        with self.assertRaisesRegex(GovernanceError, "explicitly approved"):
            self.governance.publish("edit")
        with self.assertRaisesRegex(GovernanceError, "explicitly approved"):
            self.governance.publish("override")

    def test_missing_approval_and_rejected_override_cannot_publish(self):
        with self.assertRaises(GovernanceError):
            self.governance.publish("missing")
        self.governance.decide("d1", "override", FACULTY, self.rejected)
        with self.assertRaisesRegex(GovernanceError, "ungrounded"):
            self.governance.publish("d1")

    def test_approved_versions_rollback_without_erasing_audit_history(self):
        self.governance.decide("d1", "approve", FACULTY, self.grounded)
        first = self.governance.publish("d1")
        self.governance.decide("d2", "edit", FACULTY, self.grounded, note="Owner edited wording.")
        self.governance.decide("d3", "approve", FACULTY, self.grounded)
        second = self.governance.publish("d3")
        audit_before = self.governance.audit_log

        restored = self.governance.rollback(second.version_id)

        self.assertEqual(restored, first)
        self.assertEqual(self.governance.active_version, first)
        self.assertEqual(len(self.governance.versions), 2)
        self.assertEqual(self.governance.audit_log[: len(audit_before)], audit_before)
        self.assertEqual(self.governance.audit_log[-1].action, "rollback publication")


if __name__ == "__main__":
    unittest.main()
