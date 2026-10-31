"""Acceptance tests for Lab 9 connector-backed evidence grounding."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab3_skills import PlanInputs, plan
from lab5_connector import GovernedConnector, LocalSession
from lab8_swarm import ParallelSwarmController
from lab9_grounding import CandidateOutput, EvidenceGrounder


def connector() -> GovernedConnector:
    return GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))


def locked_plan():
    return plan(
        PlanInputs(
            request="Organize sanitized student deadline and administrative messages.",
            scope="Sanitized student and administrative inbox examples.",
            priority_criteria="Student deadline questions before administrative routine notices.",
            desired_outputs=("triaged inbox outline", "draft-reply topics"),
        )
    )


class Lab9EvidenceGroundingTests(unittest.TestCase):
    def setUp(self):
        self.plan = locked_plan()
        self.grounder = EvidenceGrounder(connector())

    def test_relevant_history_and_prior_reply_support_candidate(self):
        candidate = CandidateOutput(
            "sample-thread-001",
            ("Draft reply topic for sanitized deadline message.",),
            self.plan.blueprint_id,
        )

        result = self.grounder.ground(candidate)

        self.assertEqual(result.status, "grounded")
        self.assertEqual(result.evidence.supported_claims, candidate.claims)
        self.assertEqual(
            {source.source_type for source in result.evidence.sources},
            {"inbox_thread", "thread_history", "owner_prior_reply"},
        )

    def test_unsupported_claim_is_rejected_without_evidence(self):
        candidate = CandidateOutput(
            "sample-thread-001",
            ("The new deadline is Friday at noon.",),
            self.plan.blueprint_id,
        )

        result = self.grounder.ground(candidate)

        self.assertEqual(result.status, "rejected")
        self.assertIsNone(result.evidence)
        self.assertIn("Friday", result.rejection_reason)

    def test_evidence_sources_are_attributed_to_the_correct_thread(self):
        candidate = CandidateOutput(
            "sample-thread-002",
            ("Draft reply topic for sanitized admin message.",),
            self.plan.blueprint_id,
        )

        result = self.grounder.ground(candidate)

        self.assertEqual(result.status, "grounded")
        self.assertTrue(all(source.target_thread_id == "sample-thread-002" for source in result.evidence.sources))
        self.assertIn("history-admin-003", {source.source_id for source in result.evidence.sources})
        self.assertIn("prior-admin-002", {source.source_id for source in result.evidence.sources})

    def test_owner_prior_replies_are_relevant_and_do_not_leak_between_threads(self):
        deadline = self.grounder.ground(
            CandidateOutput("sample-thread-001", ("deadline",), self.plan.blueprint_id)
        )
        admin = self.grounder.ground(
            CandidateOutput("sample-thread-002", ("admin routine",), self.plan.blueprint_id)
        )

        deadline_sources = {source.source_id for source in deadline.evidence.sources}
        admin_sources = {source.source_id for source in admin.evidence.sources}
        self.assertIn("prior-deadline-001", deadline_sources)
        self.assertNotIn("prior-admin-002", deadline_sources)
        self.assertIn("prior-admin-002", admin_sources)
        self.assertNotIn("prior-deadline-001", admin_sources)

    def test_missing_thread_or_insufficient_evidence_is_rejected(self):
        missing = self.grounder.ground(
            CandidateOutput("missing-thread", ("deadline",), self.plan.blueprint_id)
        )
        insufficient = self.grounder.ground(
            CandidateOutput("sample-thread-002", ("medical billing",), self.plan.blueprint_id)
        )

        self.assertEqual(missing.status, "rejected")
        self.assertIn("No sanitized inbox thread", missing.rejection_reason)
        self.assertEqual(insufficient.status, "rejected")
        self.assertIsNone(insufficient.evidence)

    def test_grounding_pass_preserves_lab8_worker_order_and_plan_attribution(self):
        swarm = ParallelSwarmController(connector(), self.plan, max_workers=2).run_parallel()

        results = self.grounder.ground_swarm(swarm, self.plan)

        self.assertEqual([result.candidate.thread_id for result in results], list(swarm.thread_ids))
        self.assertTrue(all(result.status == "grounded" for result in results))
        self.assertTrue(all(result.evidence.locked_plan_id == self.plan.blueprint_id for result in results))


if __name__ == "__main__":
    unittest.main()
