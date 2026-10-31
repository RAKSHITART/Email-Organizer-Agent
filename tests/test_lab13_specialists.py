"""Lab 13 specialist substitution, order, failure, and governance tests."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab5_connector import GovernedConnector, LocalSession
from lab9_grounding import CandidateOutput, EvidenceGrounder
from lab11_acceptance import local_acceptance_plan
from lab12_governance import FacultyGovernance
from lab13_specialists import SpecialistCoordinator, SpecialistError


FACULTY = "faculty.example@university.test"


class ReplacementPriority:
    name = "Priority & Urgency"

    def execute(self, context):
        context.execution_order.append(self.name)
        context.notes.append("Replacement applied faculty priority rules.")
        return context


class FailingDraft:
    name = "Draft Reply"

    def execute(self, context):
        raise RuntimeError("deterministic failure")


class Lab13SpecialistTests(unittest.TestCase):
    def setUp(self):
        connector = GovernedConnector(LocalSession(FACULTY, "local-test-token"))
        self.coordinator = SpecialistCoordinator(FacultyGovernance(connector, FACULTY))
        plan = local_acceptance_plan()
        self.grounded = EvidenceGrounder(connector).ground(
            CandidateOutput("sample-thread-001", ("deadline",), plan.blueprint_id)
        )
        self.rejected = EvidenceGrounder(connector).ground(
            CandidateOutput("sample-thread-002", ("medical billing",), plan.blueprint_id)
        )

    def test_specialists_run_in_exact_worklet_order_after_evidence_validation(self):
        run = self.coordinator.execute(self.grounded, owner_email=FACULTY, owner_decision="approve")

        self.assertEqual(run.execution_order, list(self.coordinator._ORDER))
        self.assertEqual(run.decision.decision, "approve")

    def test_domain_specialist_can_be_replaced_without_bypassing_faculty_review(self):
        self.coordinator.register("Priority & Urgency", ReplacementPriority())

        run = self.coordinator.execute(self.grounded, owner_email=FACULTY, owner_decision="edit")

        self.assertIn("Replacement applied faculty priority rules.", run.notes)
        self.assertEqual(run.decision.decision, "edit")
        with self.assertRaises(SpecialistError):
            self.coordinator.register("Faculty Review", ReplacementPriority())

    def test_failure_propagates_and_ungrounded_result_never_reaches_owner_review(self):
        self.coordinator.register("Draft Reply", FailingDraft())
        with self.assertRaisesRegex(SpecialistError, "Draft Reply failed"):
            self.coordinator.execute(self.grounded, owner_email=FACULTY, owner_decision="approve")

        fresh = SpecialistCoordinator(self.coordinator._specialists["Faculty Review"]._governance)
        with self.assertRaisesRegex(SpecialistError, "Evidence validation"):
            fresh.execute(self.rejected, owner_email=FACULTY, owner_decision="override")


if __name__ == "__main__":
    unittest.main()
