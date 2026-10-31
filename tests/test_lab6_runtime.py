"""Acceptance tests for Lab 6's sequential governed runtime."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab3_skills import PlanInputs
from lab5_connector import GovernedConnector, LocalSession
from lab6_runtime import BudgetExhausted, GovernedRuntime, RunBudget, RuntimeRequest


def request(execution_id: str) -> RuntimeRequest:
    return RuntimeRequest(
        execution_id=execution_id,
        request="Help organize sanitized faculty examples.",
        clarification_answers=(
            ("scope", "Sanitized student and administrative examples."),
            ("priority_criteria", "Student deadlines before routine notices."),
            ("desired_outputs", "A triage outline and draft-reply topics."),
        ),
        skill_inputs=PlanInputs(
            request="Organize sanitized faculty examples.",
            scope="Sanitized student and administrative examples.",
            priority_criteria="Student deadlines before routine notices.",
            desired_outputs=("triage outline", "draft-reply topics"),
        ),
        retrieval_tag="student",
    )


def runtime() -> GovernedRuntime:
    connector = GovernedConnector(LocalSession("faculty.example@university.test", "local-test-token"))
    return GovernedRuntime(connector)


class Lab6RuntimeTests(unittest.TestCase):
    def test_every_completed_step_is_audited_and_checkpointed(self):
        controller = runtime()

        result = controller.start(request("audit-run"), RunBudget(max_steps=7), approve_risky=True)
        audit = controller.audit_log("audit-run")
        checkpoints = controller.checkpoints("audit-run")

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(checkpoints), 7)
        self.assertEqual(len(audit), 7)
        self.assertTrue(all(entry.intent and entry.action and entry.result and entry.timestamp for entry in audit))
        self.assertEqual([checkpoint.next_step for checkpoint in checkpoints], list(range(1, 8)))

    def test_resume_restores_latest_checkpoint_without_restarting(self):
        controller = runtime()

        paused = controller.start(
            request("resume-run"),
            RunBudget(max_steps=7),
            approve_risky=True,
            stop_after_step="tool_verified_plan",
        )
        resumed = controller.resume("resume-run", approve_risky=True)
        audit = controller.audit_log("resume-run")

        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.next_step, "history_retrieval")
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(sum(entry.action == "create editable plan" for entry in audit), 1)
        self.assertEqual(sum(entry.action == "restore latest checkpoint" for entry in audit), 1)
        self.assertEqual(len(controller.checkpoints("resume-run")), 7)

    def test_budget_exhaustion_blocks_next_step_and_records_it(self):
        controller = runtime()

        with self.assertRaises(BudgetExhausted):
            controller.start(request("budget-run"), RunBudget(max_steps=2), approve_risky=True)

        self.assertEqual(len(controller.checkpoints("budget-run")), 2)
        self.assertIn("budget exhausted", controller.audit_log("budget-run")[-1].result)

    def test_approval_gate_blocks_then_resume_can_continue_after_approval(self):
        controller = runtime()

        blocked = controller.start(request("approval-run"), RunBudget(max_steps=7))
        completed = controller.resume("approval-run", approve_risky=True)
        audit = controller.audit_log("approval-run")

        self.assertEqual(blocked.status, "awaiting_approval")
        self.assertEqual(blocked.next_step, "approval_gate")
        self.assertEqual(len(controller.checkpoints("approval-run")), 7)
        self.assertEqual(completed.status, "completed")
        self.assertTrue(any(entry.action == "approval gate" and "blocked" in entry.result for entry in audit))
        self.assertTrue(any(entry.action == "approval gate" and "approved simulation" in entry.result for entry in audit))

    def test_checkpoints_are_scoped_to_one_execution(self):
        controller = runtime()
        controller.start(request("first-run"), RunBudget(max_steps=7), approve_risky=True)

        with self.assertRaises(ValueError):
            controller.resume("other-run", approve_risky=True)


if __name__ == "__main__":
    unittest.main()
