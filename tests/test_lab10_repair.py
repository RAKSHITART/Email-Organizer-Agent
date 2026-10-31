"""Acceptance tests for Lab 10 targeted repair and escalation."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab3_skills import PlanInputs, plan
from lab5_connector import GovernedConnector, LocalSession
from lab6_runtime import RunBudget
from lab9_grounding import CandidateOutput, EvidenceGrounder
from lab10_repair import TargetedRepairCoordinator


def grounder() -> EvidenceGrounder:
    return EvidenceGrounder(
        GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))
    )


def locked_plan_id() -> str:
    return plan(
        PlanInputs(
            request="Organize sanitized student deadline and administrative messages.",
            scope="Sanitized student and administrative inbox examples.",
            priority_criteria="Student deadline questions first.",
            desired_outputs=("triaged inbox outline",),
        )
    ).blueprint_id


class Lab10TargetedRepairTests(unittest.TestCase):
    def setUp(self):
        self.plan_id = locked_plan_id()
        self.grounder = grounder()
        self.accepted = self.grounder.ground(
            CandidateOutput("sample-thread-001", ("deadline",), self.plan_id)
        )
        self.rejected = self.grounder.ground(
            CandidateOutput("sample-thread-002", ("medical billing",), self.plan_id)
        )

    def test_successful_targeted_repair_preserves_accepted_output_and_audit(self):
        coordinator = TargetedRepairCoordinator(self.grounder, RunBudget(2), max_attempts=2)

        repaired = coordinator.repair(
            (self.accepted, self.rejected),
            lambda candidate, _diagnostic, _attempt: CandidateOutput(
                candidate.thread_id, ("admin routine",), candidate.locked_plan_id
            ),
        )

        self.assertEqual(repaired.status, "repaired")
        self.assertIs(repaired.results[0], self.accepted)
        self.assertEqual(repaired.results[1].status, "grounded")
        self.assertEqual(repaired.attempts_used, 1)
        self.assertEqual(repaired.audit[0].thread_id, "sample-thread-002")
        self.assertEqual(repaired.audit[0].result, "grounded")

    def test_repeated_failed_repair_is_bounded_and_escalated(self):
        coordinator = TargetedRepairCoordinator(self.grounder, RunBudget(5), max_attempts=2)

        repaired = coordinator.repair(
            (self.rejected,),
            lambda candidate, _diagnostic, _attempt: candidate,
        )

        self.assertEqual(repaired.status, "escalated")
        self.assertEqual(repaired.attempts_used, 2)
        self.assertEqual(len(repaired.audit), 2)
        self.assertEqual(repaired.results[0].status, "rejected")
        self.assertIn("sample-thread-002", repaired.escalation or "")

    def test_repair_budget_exhaustion_escalates_without_touching_later_candidate(self):
        second_rejected = self.grounder.ground(
            CandidateOutput("sample-thread-001", ("unrelated medical claim",), self.plan_id)
        )
        coordinator = TargetedRepairCoordinator(self.grounder, RunBudget(1), max_attempts=2)

        repaired = coordinator.repair(
            (self.rejected, second_rejected),
            lambda candidate, _diagnostic, _attempt: CandidateOutput(
                candidate.thread_id, ("admin routine",), candidate.locked_plan_id
            ),
        )

        self.assertEqual(repaired.status, "escalated")
        self.assertEqual(repaired.results[0].status, "grounded")
        self.assertIs(repaired.results[1], second_rejected)
        self.assertIn("budget exhausted", repaired.audit[-1].result)

    def test_repair_cannot_change_thread_or_locked_plan_scope(self):
        coordinator = TargetedRepairCoordinator(self.grounder, RunBudget(2), max_attempts=1)

        with self.assertRaisesRegex(ValueError, "cannot change"):
            coordinator.repair(
                (self.rejected,),
                lambda _candidate, _diagnostic, _attempt: CandidateOutput(
                    "sample-thread-001", ("deadline",), self.plan_id
                ),
            )


if __name__ == "__main__":
    unittest.main()
