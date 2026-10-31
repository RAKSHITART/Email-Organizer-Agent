"""Lab 3 reusable, typed skills for planning and formatting only."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256


@dataclass(frozen=True)
class PlanInputs:
    """Explicit, sanitized inputs accepted by the reusable plan skill."""

    request: str
    scope: str
    priority_criteria: str
    desired_outputs: tuple[str, ...]


@dataclass(frozen=True)
class LockedPlan:
    """An immutable blueprint produced by the plan skill."""

    blueprint_id: str
    request: str
    scope: str
    priority_criteria: str
    desired_outputs: tuple[str, ...]
    steps: tuple[str, ...]
    constraints: tuple[str, ...]
    revision_of: str | None = None


@dataclass(frozen=True)
class TriagedTemplate:
    """Reusable structure for formatting a triaged-inbox planning document."""

    name: str
    title_prefix: str
    section_order: tuple[str, ...]
    footer: str


@dataclass(frozen=True)
class TriagedSection:
    """One typed section in a structured triaged-inbox document."""

    heading: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class TriagedDocument:
    """A structured, planning-only representation of a triaged inbox."""

    title: str
    template_name: str
    blueprint_id: str
    sections: tuple[TriagedSection, ...]
    footer: str


TRIAGED_INBOX_TEMPLATE = TriagedTemplate(
    name="standard-triaged-inbox",
    title_prefix="Triaged inbox plan",
    section_order=("Request", "Scope", "Priority rules", "Requested outputs", "Planning steps", "Constraints"),
    footer="Planning document only. No email has been read, changed, sent, archived, or deleted.",
)


def plan(inputs: PlanInputs) -> LockedPlan:
    """Create and lock a reusable blueprint from explicit sanitized inputs."""
    _require_non_empty("request", inputs.request)
    _require_non_empty("scope", inputs.scope)
    _require_non_empty("priority_criteria", inputs.priority_criteria)
    if not inputs.desired_outputs or any(not output.strip() for output in inputs.desired_outputs):
        raise ValueError("desired_outputs must contain at least one non-empty output.")

    normalized_outputs = tuple(output.strip() for output in inputs.desired_outputs)
    blueprint_id = _blueprint_id(inputs, normalized_outputs)
    return LockedPlan(
        blueprint_id=blueprint_id,
        request=inputs.request.strip(),
        scope=inputs.scope.strip(),
        priority_criteria=inputs.priority_criteria.strip(),
        desired_outputs=normalized_outputs,
        steps=(
            "Use only the agreed sanitized/example scope.",
            "Apply the locked priority rules when outlining triage.",
            "Format the requested planning outputs for human review only.",
        ),
        constraints=(
            "This is Lab 3 planning and formatting only.",
            "Do not connect to, send, modify, archive, or delete email.",
            "A change requires a new locked revision, not mutation of this blueprint.",
        ),
    )


def revise_locked_plan(original: LockedPlan, updated_inputs: PlanInputs) -> LockedPlan:
    """Create a new immutable plan revision while preserving the original plan."""
    revised = plan(updated_inputs)
    return replace(revised, revision_of=original.blueprint_id)


def format_triaged(result: LockedPlan, template: TriagedTemplate) -> TriagedDocument:
    """Format a locked plan with a reusable structured-document template."""
    values = {
        "Request": (result.request,),
        "Scope": (result.scope,),
        "Priority rules": (result.priority_criteria,),
        "Requested outputs": result.desired_outputs,
        "Planning steps": result.steps,
        "Constraints": result.constraints,
    }
    unknown_sections = set(template.section_order) - set(values)
    if unknown_sections:
        raise ValueError("Template contains unknown sections: " + ", ".join(sorted(unknown_sections)))

    return TriagedDocument(
        title=f"{template.title_prefix}: {result.request}",
        template_name=template.name,
        blueprint_id=result.blueprint_id,
        sections=tuple(
            TriagedSection(heading=heading, items=values[heading])
            for heading in template.section_order
        ),
        footer=template.footer,
    )


def _blueprint_id(inputs: PlanInputs, desired_outputs: tuple[str, ...]) -> str:
    material = "\n".join((inputs.request.strip(), inputs.scope.strip(), inputs.priority_criteria.strip(), *desired_outputs))
    return sha256(material.encode("utf-8")).hexdigest()[:12]


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} is required.")
