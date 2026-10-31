"""Deterministic, content-based agent nodes for REAL mailbox mode.

These replace the fixture graph's canned logic (label lookup, fixed draft
strings) when mail comes from Gmail. They are plain rules -- no LLM, no
network -- so behaviour is reproducible and auditable:

  * Classification: keyword rules over the thread's OWN messages (first match wins:
    automated > deadline > meeting > student > admin > routine).
  * Priority: fixed owner-rule scores (deadline 100, meeting 90, student 70,
    admin 50, routine 30, automated 10).
  * Drafts: a fixed courtesy template plus verbatim phrases quoted from the thread.
    Only the quoted facts are treated as grounding claims; the template text is
    owner-reviewable boilerplate. Drafts never confirm meetings or commit to dates.
  * Action items / dates: extracted verbatim from the email text, never invented.
  * Calendar: only *proposals* (text); nothing is written to any calendar.

Email text is untrusted DATA. Nothing here executes or obeys instructions in it.
"""

from __future__ import annotations

import re
from email.utils import parseaddr

from dataclasses import dataclass
from response_library import load_library, match as match_response
from rules import Rules, load_rules
from lab7_graph import (
    AgentNode,
    ExecutableAgentGraph,
    GraphState,
    ValidationNode,
    ValidationResult,
)

PRIORITY_RULES = {"deadline": 100, "meeting": 90, "student": 70, "admin": 50, "routine": 30, "automated": 10}

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deadline", re.compile(r"\b(deadline|due (on|by|date)|last date|submit(ted)? by|submission|by (eod|end of day))\b", re.I)),
    ("meeting", re.compile(r"\b(meeting|meet\b|appointment|schedule a|call me|available (on|at)|time slot|discuss in person)\b", re.I)),
    ("student", re.compile(r"\b(student|assignment|exam|marks|grade|attendance|project|viva|course|semester|lab)\b", re.I)),
    ("admin", re.compile(r"\b(circular|form|committee|department|hod|notice|office|approval|leave|principal)\b", re.I)),
)
_AUTOMATED_SENDER = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|notifications?@|newsletter)", re.I)
_REQUEST = re.compile(r"\b(please|kindly|request|could you|can you|would you|need you to|submit|send me|share|confirm)\b", re.I)
_DATE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(\s+\d{4})?"
    r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(st|nd|rd|th)?(,?\s+\d{4})?"
    r"|(on|by|before)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|tomorrow|today|next week)\b", re.I)
_TIME = re.compile(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", re.I)


def clean(text: str, limit: int = 300) -> str:
    """Collapse whitespace/control characters (also blocks header injection)."""
    return re.sub(r"[\x00-\x1f\x7f\s]+", " ", text or "").strip()[:limit]


def strip_re(subject: str) -> str:
    return re.sub(r"^\s*((re|fwd?)\s*:\s*)+", "", clean(subject), flags=re.I).strip()


def _own_messages(connector, thread_id):
    snap = connector.read_inbox_threads()
    thread = next(t for t in snap.inbox_threads if t.thread_id == thread_id)
    msgs = [m for m in snap.history_messages if m.thread_id == thread_id]
    return thread, msgs


def _latest_incoming(thread, msgs, owner: str):
    incoming = [m for m in msgs if parseaddr(m.sender)[1].casefold() != owner]
    return (incoming or msgs or [None])[-1]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


_ESCALATION = re.compile(r"\b(urgent|asap|immediately|escalat\w*|high priority|time[- ]sensitive|important)\b", re.I)
_COMMITMENT = re.compile(r"\b(i will|i'll|i shall|will share|will send|will check|will review|will get back)\b", re.I)


@dataclass(frozen=True)
class Analysis:
    sender_category: str          # student | admin | collaborator | vendor | other
    category_source: str          # "rule" (your domain lists), "content" (keywords) or "none"
    signals: tuple[str, ...]      # why urgency was raised
    uncertain: bool               # a date is mentioned but no urgency wording: owner judgment
    action_items: tuple[str, ...] # requests (<=3) + the owner's own commitments (<=2)


def _domain(sender: str) -> str:
    return parseaddr(sender)[1].casefold().rpartition("@")[2]


def analyze(thread, msgs, owner: str, rules: Rules, label: str | None = None) -> Analysis:
    """Deterministic sender category, urgency signals and action items for ONE thread."""
    latest = _latest_incoming(thread, msgs, owner)
    body = latest.body if latest else ""
    text = clean(thread.subject + " " + body, 5000)
    domain = _domain(thread.sender)
    category, source = "other", "none"
    for name, domains in (("student", rules.student_domains), ("admin", rules.admin_domains),
                          ("collaborator", rules.collaborator_domains), ("vendor", rules.vendor_domains)):
        if domain and any(domain == d or domain.endswith("." + d) for d in domains):
            category, source = name, "rule"
            break
    if source == "none":
        for name, rx in _RULES:
            if name in ("student", "admin") and rx.search(text):
                category, source = name, "content"
                break
    signals = []
    if _RULES[0][1].search(text):
        signals.append("deadline mention")
    if _ESCALATION.search(text):
        signals.append("escalation language")
    headers = dict(latest.headers) if latest else {}
    if headers.get("Importance", "").lower() == "high" or headers.get("X-Priority", "")[:1] in ("1", "2"):
        signals.append("explicit priority marker")
    if any(p in text.casefold() for p in rules.priority_phrases):
        signals.append("your priority phrase")
    uncertain = bool(_DATE.search(body)) and not signals and label not in ("automated",)
    requests = [clean(s, 200) for s in _sentences(body) if _REQUEST.search(s)][:3]
    commitments = [clean(s, 200) for m in msgs if parseaddr(m.sender)[1].casefold() == owner
                   for s in _sentences(m.body) if _COMMITMENT.search(s)][:2]
    items = tuple(f"Review request: {r}" for r in requests) + tuple(f"Your commitment: {c}" for c in commitments)
    return Analysis(category, source, tuple(signals), uncertain, items)


class MailboxClassificationAgent:
    name = "IngestionClassificationAgent"

    def __init__(self, connector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        owner = self._connector.authenticated_principal.casefold()
        for thread in self._connector.read_inbox_threads().inbox_threads:
            _, msgs = _own_messages(self._connector, thread.thread_id)
            latest = _latest_incoming(thread, msgs, owner)
            headers = dict(latest.headers) if latest else {}
            sender_addr = parseaddr(thread.sender)[1]
            if (_AUTOMATED_SENDER.search(sender_addr) or "List-Id" in headers
                    or headers.get("Auto-Submitted", "no").lower() not in ("", "no")):
                label = "automated"
            else:
                text = clean(thread.subject + " " + (latest.body if latest else ""), 5000)
                label = next((name for name, rx in _RULES if rx.search(text)), "routine")
            state.classifications[thread.thread_id] = label
        state.execution_order.append(self.name)
        return state


class MailboxPriorityAgent:
    """Base score by class; escalation language / priority marker / your phrase lifts to 95."""

    name = "PriorityUrgencyAgent"

    def __init__(self, connector=None, rules: Rules | None = None) -> None:
        self._connector = connector
        self._rules = rules

    def step(self, state: GraphState) -> GraphState:
        priorities = {t: PRIORITY_RULES[c] for t, c in state.classifications.items()}
        if self._connector is not None:
            rules = self._rules if self._rules is not None else load_rules()
            owner = self._connector.authenticated_principal.casefold()
            for thread_id, label in state.classifications.items():
                if label == "automated":
                    continue
                thread, msgs = _own_messages(self._connector, thread_id)
                found = analyze(thread, msgs, owner, rules, label)
                if any(sig in found.signals for sig in ("escalation language", "explicit priority marker", "your priority phrase")):
                    priorities[thread_id] = max(priorities[thread_id], 95)
        state.priorities = priorities
        state.execution_order.append(self.name)
        return state


class MailboxDraftAgent:
    """Draft reply + the verbatim facts it relies on (the grounding claims)."""

    name = "DraftReplyAgent"

    def __init__(self, connector, library=None) -> None:
        self._connector = connector
        self._library = library if library is not None else load_library()

    def step(self, state: GraphState) -> GraphState:
        owner = self._connector.authenticated_principal.casefold()
        for thread_id, label in state.classifications.items():
            thread, msgs = _own_messages(self._connector, thread_id)
            latest = _latest_incoming(thread, msgs, owner)
            body = latest.body if latest else ""
            subject = strip_re(thread.subject)
            claims: list[str] = [subject] if subject else []
            if label == "automated":
                state.draft_replies[thread_id] = ""  # no reply drafted for automated mail
                state.claims[thread_id] = tuple(claims)  # classification fact only; no reply text
                continue
            name = parseaddr(thread.sender)[0]
            lines = [f"Hello {clean(name, 60)}," if name else "Hello,", "",
                     f'Thank you for your email regarding "{subject or "your message"}".']
            template = match_response(self._library, clean(subject + " " + body, 5000))
            if template is not None:
                lines.append(template.body)  # owner-editable standard response (not a factual claim)
            date = _DATE.search(body)
            if label in ("deadline", "meeting") and date:
                phrase = clean(date.group(0), 60)
                lines.append(f"I have noted the date you mentioned: {phrase}.")
                claims.append(phrase)
            if label == "meeting":
                lines.append("I will review my schedule and confirm the meeting separately.")
            lines += ["", "I will review this and reply to you shortly.", "", "Regards"]
            state.draft_replies[thread_id] = "\n".join(lines)
            state.claims[thread_id] = tuple(claims)
        state.execution_order.append(self.name)
        return state


class MailboxActionItemAgent:
    name = "ActionItemExtractionAgent"

    def __init__(self, connector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        owner = self._connector.authenticated_principal.casefold()
        for thread_id, label in state.classifications.items():
            if label == "automated":
                continue
            thread, msgs = _own_messages(self._connector, thread_id)
            latest = _latest_incoming(thread, msgs, owner)
            sentence = next((s for s in _sentences(latest.body if latest else "") if _REQUEST.search(s)), None)
            if sentence:
                sentence = clean(sentence, 200)
                state.action_items[thread_id] = f"Review request: {sentence}"
                state.claims[thread_id] = state.claims.get(thread_id, ()) + (sentence,)
        state.execution_order.append(self.name)
        return state


class MailboxFollowupAgent:
    """A thread whose newest message is incoming and needs a reply gets a reminder."""

    name = "FollowupReminderAgent"

    def __init__(self, connector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        owner = self._connector.authenticated_principal.casefold()
        pending = []
        for thread_id, label in state.classifications.items():
            if label == "automated":
                continue
            _, msgs = _own_messages(self._connector, thread_id)
            if msgs and parseaddr(msgs[-1].sender)[1].casefold() != owner:
                pending.append(thread_id)
        state.followup_reminders = tuple(pending)
        state.execution_order.append(self.name)
        return state


class MailboxCalendarProposalAgent:
    """Text-only proposals. Never writes to a calendar; never confirms a meeting."""

    name = "CalendarTaskSyncAgent"

    def __init__(self, connector) -> None:
        self._connector = connector

    def step(self, state: GraphState) -> GraphState:
        owner = self._connector.authenticated_principal.casefold()
        proposals = []
        for thread_id, label in state.classifications.items():
            if label != "meeting":
                continue
            thread, msgs = _own_messages(self._connector, thread_id)
            latest = _latest_incoming(thread, msgs, owner)
            body = latest.body if latest else ""
            when = " ".join(filter(None, (
                clean(m.group(0), 40) for m in (_DATE.search(body), _TIME.search(body)) if m)))
            proposals.append(
                f"Unconfirmed meeting request from {clean(thread.sender, 80)}: "
                + (f"time mentioned '{when}'" if when else "no time stated")
                + " - requires faculty approval")
        state.calendar_proposals = tuple(proposals)
        state.execution_order.append(self.name)
        return state


class MailboxValidationNode(ValidationNode):
    """Hard gate: required outputs per thread; nothing is required that real mail may lack."""

    def validate(self, state: GraphState) -> ValidationResult:
        checks = (
            ("IngestionClassificationAgent", bool(state.classifications)),
            ("PriorityUrgencyAgent", set(state.priorities) == set(state.classifications)),
            ("DraftReplyAgent", all(
                (c == "automated") or (state.draft_replies.get(t) and state.claims.get(t))
                for t, c in state.classifications.items())),
        )
        for node_name, passed in checks:
            if not passed:
                return ValidationResult(False, node_name, "required mailbox output is missing")
        return ValidationResult(True)


def make_graph_factory(rules: Rules | None = None, library=None):
    def factory(scoped_connector) -> ExecutableAgentGraph:
        nodes: tuple[AgentNode, ...] = (
            MailboxClassificationAgent(scoped_connector),
            MailboxPriorityAgent(scoped_connector, rules),
            MailboxDraftAgent(scoped_connector, library),
            MailboxActionItemAgent(scoped_connector),
            MailboxFollowupAgent(scoped_connector),
            MailboxCalendarProposalAgent(scoped_connector),
        )
        return ExecutableAgentGraph(scoped_connector, validation=MailboxValidationNode(), nodes=nodes)
    return factory


mailbox_graph_factory = make_graph_factory()


def summarize(thread, msgs, owner: str) -> str:
    """One deterministic line: who wrote, what they ask (first request or the subject), dates quoted."""
    latest = _latest_incoming(thread, msgs, owner)
    body = latest.body if latest else ""
    name = parseaddr(thread.sender)[0] or parseaddr(thread.sender)[1] or "Sender"
    ask = next((clean(x, 140) for x in _sentences(body) if _REQUEST.search(x)), "") or strip_re(thread.subject)
    dates = []
    for m in _DATE.finditer(body):
        phrase = clean(m.group(0), 40)
        if phrase.casefold() not in (d.casefold() for d in dates):
            dates.append(phrase)
    extra = f" (date mentioned: {', '.join(dates[:2])})" if dates else ""
    return clean(f"{clean(name, 40)}: {ask}{extra}", 260)
