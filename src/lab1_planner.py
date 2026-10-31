"""Lab 1: clarify, plan, and revise a triaged-inbox plan.

This module deliberately handles planning only. It does not read, modify, send,
archive, or otherwise act on email.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from lab2_tools import EmailOrganizationTools, VerifiedValues


CLARIFICATION_QUESTIONS = {
    "scope": "Which sanitized email examples or inbox scope should the plan cover?",
    "priority_criteria": "Which categories or signals should determine triage priority?",
    "desired_outputs": (
        "Which planning outputs are needed: draft replies, action items, "
        "meeting requests, or follow-up reminders?"
    ),
}


class MissingClarifications(ValueError):
    """Raised when a plan is requested before required answers are supplied."""


def identify_ambiguity(request: str) -> list[str]:
    """Return unanswered planning areas for a request.

    Lab 1 treats a free-text request as incomplete until scope, priority rules,
    and desired outputs have been explicitly provided. This makes ambiguity
    visible instead of guessing about real mailbox behavior.
    """
    if not request or not request.strip():
        raise ValueError("A non-empty email-organization request is required.")
    return list(CLARIFICATION_QUESTIONS)


def ask_clarification_questions(request: str) -> list[str]:
    """Ask the questions needed to turn a vague request into a plan."""
    return [CLARIFICATION_QUESTIONS[field] for field in identify_ambiguity(request)]


def create_plan(request: str, answers: Mapping[str, str]) -> dict[str, Any]:
    """Create an editable plan from a request and its explicit answers."""
    missing = [field for field in identify_ambiguity(request) if not answers.get(field, "").strip()]
    if missing:
        raise MissingClarifications(
            "Answer these areas before planning: " + ", ".join(missing)
        )

    return {
        "title": "Editable triaged-inbox plan",
        "request": request.strip(),
        "scope": answers["scope"].strip(),
        "priority_criteria": answers["priority_criteria"].strip(),
        "desired_outputs": answers["desired_outputs"].strip(),
        "steps": [
            "Review only the agreed sanitized/example email set.",
            "Apply the agreed priority criteria to outline a triaged inbox.",
            "Prepare the agreed draft outputs for user review; take no email action.",
        ],
        "constraints": [
            "Lab 1 is planning only.",
            "Do not connect to, send, modify, archive, or delete email.",
            "Keep the plan editable and revise it from user feedback.",
        ],
        "revision_history": [],
    }


def create_plan_from_tools(
    request: str, answers: Mapping[str, str], tools: EmailOrganizationTools
) -> dict[str, Any]:
    """Create a Lab 1 plan using only values obtained through Lab 2 tools."""
    inbox = tools.read_inbox()
    contacts = tools.read_contacts()
    verified = tools.validate_values(inbox, contacts)
    if not verified.is_valid:
        raise ValueError("Fixture values failed validation: " + "; ".join(verified.errors))

    plan = create_plan(request, answers)
    plan["tool_verified_context"] = _verified_context(verified)
    return plan


def _verified_context(verified: VerifiedValues) -> dict[str, Any]:
    """Expose only the planner-relevant values returned by validation."""
    return {
        "inbox_thread_count": verified.inbox_thread_count,
        "contact_count": verified.contact_count,
        "known_senders": list(verified.known_senders),
    }


def revise_plan(
    plan: Mapping[str, Any], changes: Mapping[str, str], feedback: str
) -> dict[str, Any]:
    """Return an edited copy of a plan and retain the user's feedback."""
    editable_fields = {"scope", "priority_criteria", "desired_outputs"}
    unknown = set(changes) - editable_fields
    if unknown:
        raise ValueError("Only scope, priority_criteria, and desired_outputs are editable.")
    if not feedback.strip():
        raise ValueError("Feedback is required to revise a plan.")

    revised = deepcopy(dict(plan))
    for field, value in changes.items():
        if not value.strip():
            raise ValueError(f"{field} cannot be blank.")
        revised[field] = value.strip()
    revised["revision_history"].append(
        {"feedback": feedback.strip(), "changes": dict(changes)}
    )
    return revised
