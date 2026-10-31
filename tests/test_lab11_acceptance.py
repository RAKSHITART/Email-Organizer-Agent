"""End-to-end acceptance and build-gate tests for Lab 11."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab5_connector import GovernedConnector, LocalSession
from lab9_grounding import CandidateOutput, EvidenceGrounder
from lab11_acceptance import AcceptanceGateFailed, AcceptanceSuite, local_acceptance_plan


def connector() -> GovernedConnector:
    return GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))


class Lab11AcceptanceSuiteTests(unittest.TestCase):
    def setUp(self):
        self.plan = local_acceptance_plan()
        self.suite = AcceptanceSuite(connector(), self.plan)

    def test_requirement_mapped_end_to_end_acceptance_suite_passes(self):
        report = self.suite.run()

        self.assertTrue(report.passed, report.failed_requirements)
        self.assertEqual(
            {check.requirement for check in report.checks},
            {
                "end-to-end triage",
                "connector-only authenticated access",
                "locked plan attribution",
                "evidence grounding and thread isolation",
                "targeted repair and bounded retry",
                "approval gate and audit",
                "unsafe and unsupported candidates cannot pass",
            },
        )

    def test_locked_plan_and_supported_results_can_enter_the_build_gate(self):
        grounded = EvidenceGrounder(connector()).ground(
            CandidateOutput("sample-thread-001", ("deadline",), self.plan.blueprint_id)
        )

        artifact = self.suite.build((grounded,))

        self.assertEqual(artifact.locked_plan.blueprint_id, self.plan.blueprint_id)
        self.assertEqual(artifact.grounded_results, (grounded,))
        with self.assertRaises(FrozenInstanceError):
            artifact.locked_plan.scope = "changed"  # type: ignore[misc]

    def test_unsupported_candidate_cannot_bypass_the_build_gate(self):
        unsupported = EvidenceGrounder(connector()).ground(
            CandidateOutput("sample-thread-002", ("medical billing",), self.plan.blueprint_id)
        )

        self.assertEqual(unsupported.status, "rejected")
        with self.assertRaises(AcceptanceGateFailed):
            self.suite.build((unsupported,))


if __name__ == "__main__":
    unittest.main()
