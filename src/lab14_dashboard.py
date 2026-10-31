"""Lab 14 local, standard-library dashboard.

Fixture demo (default):  python src/lab14_dashboard.py --serve
Real Gmail (read-only):  python src/lab14_dashboard.py --mode gmail --serve
Real Gmail + drafts:     python src/lab14_dashboard.py --mode gmail --drafts --serve
Open http://127.0.0.1:8765. The banner always states which mode is active.
Nothing in this module can send email; the most it can do is save a Gmail DRAFT
for an owner-approved, published, grounded result.
"""

from __future__ import annotations

import argparse
import secrets
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from datetime import date, datetime, timezone

from lab5_connector import GovernedConnector, LocalSession
from metrics import compute_metrics
from state_store import OwnerStateStore
from rules import Rules, load_rules
from lab8_swarm import ParallelSwarmController, WorkerResult
from lab9_grounding import EvidenceGrounder, GroundingResult
from lab11_acceptance import local_acceptance_plan
from lab12_governance import FacultyGovernance, GovernanceError


FACULTY_EMAIL = "faculty.example@university.test"


@dataclass(frozen=True)
class DashboardSnapshot:
    triaged_inbox: tuple[WorkerResult, ...]
    grounded_results: tuple[GroundingResult, ...]
    audit: tuple[str, ...]
    active_version: str | None


class LocalDashboard:
    """Presentation/controller layer; all mailbox evidence arrives through the connector."""

    def __init__(
        self,
        connector: GovernedConnector,
        faculty_email: str = FACULTY_EMAIL,
        *,
        mode_label: str = "FIXTURE MODE - sanitized sample data, NOT a live mailbox",
        plan=None,
        graph_factory=None,
        draft_publisher_factory=None,
        state_store: OwnerStateStore | None = None,
        calendar=None,
        rules: Rules | None = None,
        clock=None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connector = connector
        self._rules = rules if rules is not None else load_rules()
        self._seen_threads: set[str] = set()
        self._store = state_store or OwnerStateStore()
        self._calendar = calendar
        self._plan = plan or local_acceptance_plan()
        self._graph_factory = graph_factory
        self._mode_label = mode_label
        self._faculty_email = faculty_email
        self._governance = FacultyGovernance(connector, faculty_email)
        self._publisher = draft_publisher_factory(self._governance) if draft_publisher_factory else None
        self._decision_number = 0
        self._latest_decision_by_thread: dict[str, str] = {}
        self._run_pipeline()
        self._seen_threads = {w.thread_id for w in self._workers}

    def _run_pipeline(self) -> None:
        self._plans = {}
        swarm = ParallelSwarmController(
            self._connector, self._plan, max_workers=2, graph_factory=self._graph_factory
        ).run_parallel()
        self._workers = swarm.worker_results
        self._grounded = EvidenceGrounder(self._connector).ground_swarm(swarm, self._plan)

    def refresh(self, trigger: str = "owner") -> None:
        """Re-fetch the live inbox (real mode) and re-run triage; decisions/audit persist."""
        try:
            self._connector.read_inbox_threads(refresh=True)
        except TypeError:
            pass  # fixture connector has no refresh
        before = set(self._seen_threads)
        self._governance.refresh_scope()
        self._run_pipeline()
        current = {w.thread_id for w in self._workers}
        self._seen_threads = current
        self._governance.audit_external(
            f"inbox refreshed (trigger: {trigger})", "inbox", f"{len(current)} threads, {len(current - before)} new")

    def save_draft(self, thread_id: str) -> str:
        """Save the approved, published draft for this thread as a Gmail DRAFT (never sends)."""
        if self._publisher is None:
            raise GovernanceError("Draft saving is not enabled in this mode.")
        decision_id = self._latest_decision_by_thread.get(thread_id)
        if decision_id is None:
            raise GovernanceError("Explicit owner approval is required before saving a draft.")
        record = self._publisher.save_approved_draft(decision_id, self.draft_text(thread_id))
        info = self.info(thread_id)
        self._store.set_draft(thread_id, info.message_id if info else "", record.draft_id, record.subject, record.to)
        return record.draft_id

    # ---- read helpers used by the Gmail-style UI -------------------------------
    @property
    def mode_label(self) -> str:
        return self._mode_label

    @property
    def faculty_email(self) -> str:
        return self._faculty_email

    @property
    def can_save_drafts(self) -> bool:
        return self._publisher is not None

    @property
    def has_calendar(self) -> bool:
        return self._calendar is not None

    def info(self, thread_id: str):
        return next((t for t in self._connector.read_inbox_threads().inbox_threads
                     if t.thread_id == thread_id), None)

    def messages_for(self, thread_id: str) -> tuple:
        return tuple(m for m in self._connector.read_inbox_threads().history_messages
                     if m.thread_id == thread_id and (m.message_id or True))

    def worker(self, thread_id: str):
        return next((w for w in self._workers if w.thread_id == thread_id), None)

    def grounding(self, thread_id: str):
        return next((r for r in self._grounded if r.candidate.thread_id == thread_id), None)

    def default_draft(self, thread_id: str) -> str:
        """The agent's own draft; for meetings it already reflects the owner's calendar."""
        worker = self.worker(thread_id)
        base = (worker.draft_reply if worker else "") or ""
        plan = self.meeting_plan(thread_id)
        return self._apply_meeting_plan(base, plan) if base and plan else base

    def draft_text(self, thread_id: str) -> str:
        info = self.info(thread_id)
        edited = self._store.get_edit(thread_id, info.message_id if info else "")
        return edited if edited is not None else self.default_draft(thread_id)

    _CONFIRM_LINE = "I will review my schedule and confirm the meeting separately."

    @staticmethod
    def _fmt_slot(a, b) -> str:
        return a.strftime("%a %d %b %Y, %I:%M %p").replace(" 0", " ") + " to " + b.strftime("%I:%M %p").lstrip("0")

    def _apply_meeting_plan(self, base: str, plan: dict) -> str:
        status, proposal, slots = plan["status"], plan.get("proposal"), plan.get("slots", [])
        if status == "free" and proposal is not None:
            line = f"I am happy to confirm our meeting on {self._fmt_slot(proposal.start, proposal.end)}."
        elif status == "busy":
            line = "Unfortunately I am not available at that time."
            if slots:
                line += " I am available at:\n" + "\n".join("- " + self._fmt_slot(a, b) for a, b in slots)
        elif status == "no_time" and slots:
            line = "I am available at:\n" + "\n".join("- " + self._fmt_slot(a, b) for a, b in slots)
        else:
            return base
        return base.replace(self._CONFIRM_LINE, line)

    def meeting_plan(self, thread_id: str) -> dict | None:
        """Automatic availability check for a meeting thread (cached until the next refresh)."""
        if thread_id in self._plans:
            return self._plans[thread_id]
        worker = self.worker(thread_id)
        plan = None
        if worker is not None and worker.classification == "meeting":
            proposal = self.meeting_proposal(thread_id)
            plan = {"status": "calendar_off" if proposal else "no_time", "proposal": proposal, "slots": []}
            if proposal is not None and proposal.start <= self._clock():
                plan["status"] = "past"  # never schedule or confirm a time that has already passed
            elif self._calendar is not None:
                try:
                    if proposal is None:
                        plan["slots"] = self._calendar.overview(self._rules, count=3)["slots"]
                    else:
                        busy = self._calendar.busy_periods(proposal)
                        plan["status"] = "busy" if busy else "free"
                        if busy:
                            plan["slots"] = self._calendar.overview(self._rules, count=3)["slots"]
                except Exception as error:  # show the reason, keep the page working
                    plan.update(status="error", detail=str(error)[:200])
        self._plans[thread_id] = plan
        return plan

    def saved_draft_id(self, thread_id: str) -> str | None:
        info = self.info(thread_id)
        return self._store.draft_for(thread_id, info.message_id if info else "")

    def decision_state(self, thread_id: str) -> str:
        decision_id = self._latest_decision_by_thread.get(thread_id)
        if decision_id is None:
            return "none"
        decision = self._governance.decision(decision_id)
        if decision is None:
            return "none"
        if decision.decision != "approve":
            return decision.decision
        return "published" if self._governance.is_published(decision_id) else "approved"

    def metrics(self):
        history = self._connector.read_inbox_threads().history_messages
        automated = frozenset(w.thread_id for w in self._workers if w.classification == "automated")
        return compute_metrics(history, self._faculty_email, exclude_thread_ids=automated)

    def audit_lines(self) -> tuple[str, ...]:
        return tuple(
            f"{entry.timestamp[:19].replace('T', ' ')}  {entry.action}: {entry.subject_id} -> {entry.result}"
            for entry in self._governance.audit_log
        )

    # ---- owner actions ---------------------------------------------------------
    def approve_reply(self, thread_id: str, text: str | None = None) -> dict:
        """The owner's single approval: record decision, version it, save the Gmail draft and, for a
        meeting whose time is free on the calendar, put it on the calendar with reminders.
        Nothing is sent. Returns {"draft_id", "event_id" or None, "meeting_note"}."""
        if self._publisher is None:
            raise GovernanceError("Draft saving is not enabled; start with --drafts.")
        info = self.info(thread_id)
        edited = bool(text and text.strip() and text.strip() != self.default_draft(thread_id).strip())
        if edited:
            self._store.set_edit(thread_id, info.message_id if info else "", text.strip())
        self.owner_action(thread_id, "approve", "owner edited the draft" if edited else "")
        self.publish_approved(thread_id)
        draft_id = self.save_draft(thread_id)
        event_id, note = None, ""
        plan = self.meeting_plan(thread_id)
        if plan is not None:
            existing = self._store.event_for(thread_id)
            if existing:
                event_id = existing["event_id"]
            elif self._calendar is None:
                note = "Calendar is not connected, so no event was created."
            elif plan.get("proposal") is None:
                note = "No exact time was stated, so no event was created."
            elif plan["status"] == "past":
                note = "The time in the email has already passed, so no event was created."
            elif plan["status"] == "busy":
                note = "That time conflicts with your calendar, so no event was created."
            elif plan["status"] == "free":
                event_id = self._schedule_event(thread_id, plan["proposal"])
                if event_id is None:
                    note = "The time was taken in the meantime, so no event was created."
        return {"draft_id": draft_id, "event_id": event_id, "meeting_note": note}

    def approve_and_save(self, thread_id: str, text: str | None = None) -> str:
        return self.approve_reply(thread_id, text)["draft_id"]

    def _schedule_event(self, thread_id: str, proposal) -> str | None:
        if self._calendar.busy_periods(proposal):  # re-check right before writing
            return None
        info = self.info(thread_id)
        from email.utils import parseaddr
        who = parseaddr(info.sender)[0] or parseaddr(info.sender)[1]
        summary = f"Meeting with {who}: {info.subject}"[:200]
        result = self._calendar.create_event(
            summary, proposal, owner_confirmed=True, idempotency_key=f"{thread_id}:{info.message_id}",
            description=f"Requested by {info.sender}. Added by Email Organizer after you approved the reply.")
        self._store.set_event(thread_id, result["id"], result.get("htmlLink", ""), proposal.start.isoformat(), summary)
        self._governance.audit_external("calendar event created after approval (reminders set, nobody invited)", thread_id, result["id"])
        return result["id"]

    def approve_all_ready(self) -> dict:
        """Owner-triggered bulk approval of every ready, grounded, not-yet-saved reply."""
        done, skipped = [], []
        for w in list(self._workers):
            ready = (w.draft_reply and w.classification != "automated" and self.saved_draft_id(w.thread_id) is None
                     and (self.grounding(w.thread_id) is not None and self.grounding(w.thread_id).status == "grounded"))
            if not ready:
                continue
            try:
                self.approve_reply(w.thread_id)
                done.append(w.thread_id)
            except (GovernanceError, ValueError, PermissionError, RuntimeError) as error:
                skipped.append((w.thread_id, str(error)))
        return {"saved": done, "skipped": skipped}

    def ready_reply_count(self) -> int:
        return sum(1 for w in self._workers if w.draft_reply and w.classification != "automated"
                   and self.saved_draft_id(w.thread_id) is None
                   and self.grounding(w.thread_id) is not None and self.grounding(w.thread_id).status == "grounded")

    def undo_reply(self, thread_id: str) -> str:
        """Take back an approval: delete the Gmail draft (and the calendar event it created)."""
        draft_id = self.saved_draft_id(thread_id)
        if draft_id is None:
            raise GovernanceError("There is no saved draft for this thread.")
        self._connector.delete_draft(draft_id)
        self._store.clear_draft(thread_id)
        event = self._store.event_for(thread_id)
        if event and self._calendar is not None:
            self._calendar.delete_event(event["event_id"])
            self._store.clear_event(thread_id)
        self._latest_decision_by_thread.pop(thread_id, None)
        self._plans.pop(thread_id, None)
        self._governance.audit_external("reply undone (draft" + (" and event" if event else "") + " deleted)", thread_id, draft_id)
        return draft_id

    def summary(self, thread_id: str) -> str:
        from mailbox_nodes import summarize
        info = self.info(thread_id)
        return summarize(info, list(self.messages_for(thread_id)), self._faculty_email.casefold()) if info else ""

    def upcoming_meetings(self) -> list[dict]:
        now = self._clock()
        out = []
        for thread_id, e in self._store.events().items():
            try:
                start = datetime.fromisoformat(e.get("start", ""))
            except ValueError:
                continue
            if start.astimezone(timezone.utc) >= now:
                out.append({"thread_id": thread_id, "start": start, "summary": e.get("summary", "")})
        return sorted(out, key=lambda m: m["start"])

    def briefing(self) -> dict:
        d = self.followup_digest()
        live = [w for w in self._workers if w.classification != "automated"]
        waiting = [m for m in self.meeting_threads() if not m["event"]]
        return {"urgent": sum(1 for w in live if (w.priority or 0) >= 95),
                "needs_reply": sum(1 for w in live if w.followup_required),
                "ready": self.ready_reply_count(),
                "meetings_waiting": len(waiting), "due": len(d["due"]), "overdue": len(d["overdue"]),
                "next_meeting": (self.upcoming_meetings() or [None])[0]}

    def discard(self, thread_id: str) -> None:
        self.owner_action(thread_id, "discard", "owner discarded the suggested draft")
        self._store.clear_edit(thread_id)

    def set_reply_later(self, thread_id: str, due_iso: str) -> None:
        info = self.info(thread_id)
        self._store.set_reply_later(thread_id, due_iso, info.message_id if info else "")
        self._governance.audit_external("reply later set", thread_id, due_iso)

    def clear_reply_later(self, thread_id: str) -> None:
        self._store.clear_reply_later(thread_id)
        self._governance.audit_external("reply later cleared", thread_id, "")

    def followup_digest(self, today: date | None = None) -> dict:
        today = today or date.today()
        due, upcoming = [], []
        for thread_id, entry in sorted(self._store.reply_later().items(), key=lambda kv: kv[1]["due"]):
            if self.info(thread_id) is None:
                continue
            (due if date.fromisoformat(entry["due"]) <= today else upcoming).append((thread_id, entry["due"]))
        m = self.metrics()
        return {"due": due, "upcoming": upcoming, "overdue": list(m.overdue_thread_ids),
                "awaiting": [w.thread_id for w in self._workers if w.followup_required]}

    # ---- analysis, action items, archive, reply log -----------------------------
    @property
    def rules(self) -> Rules:
        return self._rules

    @property
    def can_archive(self) -> bool:
        return bool(getattr(self._connector, "can_archive", False))

    def analysis(self, thread_id: str):
        from mailbox_nodes import analyze
        info, worker = self.info(thread_id), self.worker(thread_id)
        if info is None:
            return None
        return analyze(info, list(self.messages_for(thread_id)), self._faculty_email.casefold(), self._rules,
                       worker.classification if worker else None)

    def action_items(self) -> list[dict]:
        import hashlib
        out = []
        for w in self._workers:
            found = self.analysis(w.thread_id)
            for text in (found.action_items if found else ()):
                key = f"{w.thread_id}:{hashlib.sha1(text.encode()).hexdigest()[:10]}"
                out.append({"thread_id": w.thread_id, "text": text, "key": key, "done": self._store.is_done(key)})
        return out

    def mark_done(self, key: str, confirmed: bool, done: bool = True) -> None:
        """Completion needs the owner's explicit confirmation (proposal guardrail)."""
        if not any(item["key"] == key for item in self.action_items()):
            raise GovernanceError("Unknown action item.")
        if done and not confirmed:
            raise GovernanceError("Tick the confirmation box: an item is only completed with your explicit OK.")
        (self._store.mark_done if done else self._store.unmark_done)(key)
        self._governance.audit_external("action item " + ("completed (owner confirmed)" if done else "reopened"), key, "")

    def archive(self, thread_id: str, confirmed: bool) -> None:
        if not self.can_archive:
            raise GovernanceError("Archiving is not enabled; start with --archive.")
        if not confirmed:
            raise GovernanceError("Tick the confirmation box to archive this thread.")
        info = self.info(thread_id)
        if info is None:
            raise GovernanceError("Thread is outside the faculty mailbox scope.")
        self._connector.archive_thread(thread_id)
        self._store.set_archived(thread_id, info.subject, info.sender)
        self._governance.audit_external("thread archived (resolved, reversible)", thread_id, "INBOX label removed")
        self.refresh(trigger="archive")

    def unarchive(self, thread_id: str) -> None:
        if thread_id not in self._store.archived():
            raise GovernanceError("This thread was not archived by this app.")
        self._connector.unarchive_thread(thread_id)
        self._store.clear_archived(thread_id)
        self._governance.audit_external("thread moved back to inbox", thread_id, "INBOX label restored")
        self.refresh(trigger="unarchive")

    def archived_threads(self) -> dict[str, dict]:
        return self._store.archived()

    def approval_history(self) -> list[dict]:
        out = []
        for d in self._governance.decision_history:
            info = self.info(d.thread_id)
            out.append({"decision": d.decision, "thread_id": d.thread_id, "subject": info.subject if info else "",
                        "by": d.owner_email, "note": d.note, "published": self._governance.is_published(d.decision_id)})
        return out

    def reply_log(self) -> list[dict]:
        return self._store.draft_log()

    # ---- calendar (owner-confirmed, optional) --------------------------------
    def calendar_overview(self) -> dict:
        if self._calendar is None:
            return {"enabled": False}
        try:
            data = self._calendar.overview(self._rules)
            return {"enabled": True, "timezone": self._calendar.timezone, **data}
        except Exception as error:  # show the reason on the page instead of crashing it
            return {"enabled": True, "error": str(error)}

    def meeting_threads(self) -> list[dict]:
        out = []
        for w in self._workers:
            if w.classification == "meeting":
                info = self.info(w.thread_id)
                out.append({"thread_id": w.thread_id, "subject": info.subject if info else "",
                            "sender": info.sender if info else "", "proposal": self.meeting_proposal(w.thread_id),
                            "event": self._store.event_for(w.thread_id)})
        return out

    def append_slots_to_draft(self, thread_id: str) -> None:
        """Add 'I am available at ...' (free slots from YOUR calendar) to the editable draft."""
        if self._calendar is None:
            raise GovernanceError("Calendar access is not enabled; start with --calendar.")
        slots = self._calendar.overview(self._rules, count=3)["slots"]
        if not slots:
            raise GovernanceError("No free slot found in the next 7 days inside your working hours.")
        info = self.info(thread_id)
        lines = "\n".join("- " + a.strftime("%a %d %b %Y, %I:%M %p") + " to " + b.strftime("%I:%M %p") for a, b in slots)
        text = self.draft_text(thread_id).rstrip() + "\n\nI am available at:\n" + lines
        self._store.set_edit(thread_id, info.message_id if info else "", text)
        self._governance.audit_external("free slots added to draft (owner reviews)", thread_id, f"{len(slots)} slots")


    def meeting_proposal(self, thread_id: str):
        from calendar_connector import DEFAULT_TIMEZONE, parse_proposed_time
        worker = self.worker(thread_id)
        if worker is None or worker.classification != "meeting":
            return None
        owner = self._faculty_email.casefold()
        from email.utils import parseaddr
        incoming = [m for m in self.messages_for(thread_id) if parseaddr(m.sender)[1].casefold() != owner]
        if not incoming:
            return None
        tz = self._calendar.timezone if self._calendar else DEFAULT_TIMEZONE
        return parse_proposed_time(incoming[-1].body, tz)

    def calendar_check(self, thread_id: str) -> dict:
        proposal = self.meeting_proposal(thread_id)
        if proposal is None:
            return {"status": "no_time", "detail": "The email does not state an explicit date (with year) and clock time."}
        if self._calendar is None:
            return {"status": "calendar_off", "detail": "Calendar access is not enabled (start with --calendar).", "proposal": proposal}
        busy = self._calendar.busy_periods(proposal)
        self._governance.audit_external("calendar availability checked", thread_id, "busy" if busy else "free")
        return {"status": "busy" if busy else "free", "busy": busy, "proposal": proposal}

    def calendar_add(self, thread_id: str, confirmed: bool) -> str:
        if self._calendar is None:
            raise GovernanceError("Calendar access is not enabled.")
        if not confirmed:
            raise GovernanceError("Tick the confirmation box: creating a calendar event needs your explicit OK.")
        proposal = self.meeting_proposal(thread_id)
        info = self.info(thread_id)
        if proposal is None or info is None:
            raise GovernanceError("No explicit meeting time was found in this thread.")
        result = self._calendar.create_event(
            f"Meeting: {info.subject}"[:200], proposal, owner_confirmed=True,
            idempotency_key=f"{thread_id}:{info.message_id}",
            description=f"Requested by {info.sender}. Created by Email Organizer after owner confirmation.")
        self._store.set_event(thread_id, result["id"], result.get("htmlLink", ""), proposal.start.isoformat(), f"Meeting: {info.subject}"[:200])
        self._governance.audit_external("calendar event created (no invites sent)", thread_id, result["id"])
        return result["id"]

    def snapshot(self) -> DashboardSnapshot:
        return DashboardSnapshot(
            self._workers,
            self._grounded,
            tuple(f"{entry.action}: {entry.result}" for entry in self._governance.audit_log),
            self._governance.active_version.version_id if self._governance.active_version else None,
        )

    def owner_action(self, thread_id: str, action: str, note: str = "") -> None:
        """Record an explicit owner decision; it cannot send, confirm, or modify email."""
        result = self._result_for(thread_id)
        self._decision_number += 1
        decision_id = f"dashboard-{self._decision_number}"
        self._governance.decide(decision_id, action, self._faculty_email, result, note=note)
        self._latest_decision_by_thread[thread_id] = decision_id

    def publish_approved(self, thread_id: str) -> str:
        """Version only the latest explicit, grounded non-discard decision for this thread."""
        decision_id = self._latest_decision_by_thread.get(thread_id)
        if decision_id is None:
            raise GovernanceError("Explicit owner approval is required before publication.")
        return self._governance.publish(decision_id).version_id

    def rollback_active(self) -> str | None:
        active = self._governance.active_version
        if active is None:
            return None
        restored = self._governance.rollback(active.version_id)
        return restored.version_id if restored else None

    def render(self) -> str:
        snapshot = self.snapshot()
        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(worker.thread_id), escape(worker.classification or ""), worker.priority or "", 
                "grounded" if result.status == "grounded" else "rejected",
            )
            for worker, result in zip(snapshot.triaged_inbox, snapshot.grounded_results)
        )
        drafts = "".join(f"<li>{escape(worker.draft_reply or '')}</li>" for worker in self._workers)
        actions = "".join(f"<li>{escape(worker.action_item or 'No action item')}</li>" for worker in self._workers)
        followups = "".join(
            f"<li>{escape(worker.thread_id)}: {'reminder required' if worker.followup_required else 'none'}</li>"
            for worker in self._workers
        )
        evidence = "".join(
            f"<li>{escape(result.candidate.thread_id)}: {escape(', '.join(source.source_id for source in (result.evidence.sources if result.evidence else ())))}</li>"
            for result in self._grounded
        )
        controls = "".join(
            "<form method='post'><input type='hidden' name='thread_id' value='{}'>"
            "<input name='note' placeholder='owner note'>"
            "<button name='action' value='approve'>Approve</button>"
            "<button name='action' value='edit'>Edit</button>"
            "<button name='action' value='discard'>Discard</button>"
            "<button name='action' value='override'>Override</button>"
            "<button name='action' value='publish'>Publish approved</button>{}</form>".format(
                escape(worker.thread_id),
                "<button name='action' value='save_draft'>Save approved draft to Gmail (not sent)</button>"
                if self._publisher else "",
            )
            for worker in self._workers
        )
        audit = "".join(f"<li>{escape(entry)}</li>" for entry in snapshot.audit)
        return f"""<!doctype html><title>Email Organizer</title>
<h1>Email Organizer</h1><p><strong>{escape(self._mode_label)}</strong></p>
{self._extra_controls()}<p>Active version: {escape(snapshot.active_version or 'none')}</p>
<h2>Triaged Inbox</h2><table><tr><th>Thread</th><th>Class</th><th>Priority</th><th>Validation</th></tr>{rows}</table>
<h2>Draft-Reply Queue</h2><ul>{drafts}</ul><h2>Action-Item List</h2><ul>{actions}</ul>
<h2>Follow-up Digest</h2><ul>{followups}</ul><h2>Evidence</h2><ul>{evidence}</ul>
<h2>Owner Controls</h2>{controls}<h2>Audit Trail</h2><ul>{audit}</ul>
<p>This app cannot dispatch email, confirm meetings, complete tasks or publish automatically.</p>"""

    def _extra_controls(self) -> str:
        if self._publisher is None and "LIVE" not in self._mode_label:
            return ""
        return "<form method='post'><button name='action' value='refresh'>Refresh inbox</button></form>"

    def _result_for(self, thread_id: str) -> GroundingResult:
        for result in self._grounded:
            if result.candidate.thread_id == thread_id:
                return result
        raise ValueError("Unknown sanitized thread.")


def create_demo() -> LocalDashboard:
    return LocalDashboard(GovernedConnector(LocalSession(FACULTY_EMAIL, "local-demo-token")))


def create_live(
    expected_email: str | None = None, *, allow_drafts: bool = False, allow_calendar: bool = False,
    allow_archive: bool = False, timezone_name: str | None = None, **auth_paths
) -> LocalDashboard:
    """Build the REAL-Gmail dashboard after the fixture acceptance gate passes."""
    import hashlib
    from connector_mode import PROFESSOR_EMAIL, build_connector
    from gmail_auth import ARCHIVE_SCOPES, CALENDAR_SCOPES, DEFAULT_CREDENTIALS_DIR
    from lab3_skills import PlanInputs, plan
    from lab11_acceptance import AcceptanceSuite
    from mailbox_nodes import mailbox_graph_factory

    gate = AcceptanceSuite(
        GovernedConnector(LocalSession(FACULTY_EMAIL, "local-demo-token")), local_acceptance_plan()
    ).run()
    if not gate.passed:
        raise RuntimeError("Acceptance gate failed; live mode refused: " + ", ".join(gate.failed_requirements))

    email = expected_email or PROFESSOR_EMAIL
    connector, label = build_connector(
        "gmail", email, allow_drafts=allow_drafts,
        extra_scopes=(CALENDAR_SCOPES if allow_calendar else ()) + (ARCHIVE_SCOPES if allow_archive else ()), **auth_paths)
    calendar = None
    if allow_calendar:
        from calendar_connector import DEFAULT_TIMEZONE, GoogleCalendar
        calendar = GoogleCalendar(connector.session, timezone=timezone_name or DEFAULT_TIMEZONE)
        label = label.replace(")", ", calendar)", 1) if label.endswith(")") else label
    live_plan = plan(PlanInputs(
        request="Triage the faculty Gmail inbox: classify, prioritise, draft grounded replies, extract actions.",
        scope="Threads in the authenticated faculty Gmail INBOX only.",
        priority_criteria="Deadlines first, then meetings, student mail, administrative mail, routine, automated.",
        desired_outputs=("triaged inbox", "draft replies", "action items", "follow-up reminders"),
    ))
    publisher_factory = None
    if allow_drafts:
        from draft_publisher import DraftPublisher
        publisher_factory = lambda governance: DraftPublisher(connector, governance)  # noqa: E731
    owner = connector.authenticated_principal
    store = OwnerStateStore(DEFAULT_CREDENTIALS_DIR / f"state_{hashlib.sha1(owner.encode()).hexdigest()[:10]}.json")
    return LocalDashboard(
        connector, owner, mode_label=label, plan=live_plan,
        graph_factory=mailbox_graph_factory, draft_publisher_factory=publisher_factory,
        state_store=store, calendar=calendar,
    )


def serve(dashboard: LocalDashboard, port: int = 8765, poll_minutes: float = 0) -> None:
    import threading
    import time
    from urllib.parse import quote, urlsplit
    import gmail_ui

    csrf_token = secrets.token_urlsafe(24)
    lock = threading.RLock()  # the poller and the web handler never touch the dashboard together

    if poll_minutes and poll_minutes > 0:
        def poll() -> None:
            while True:
                time.sleep(poll_minutes * 60)
                with lock:
                    try:
                        dashboard.refresh(trigger="schedule")
                    except Exception as error:  # keep running; record why
                        dashboard._governance.audit_external("scheduled refresh failed", "inbox", str(error)[:200])
        threading.Thread(target=poll, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            with lock:
                self._get()

        def _get(self) -> None:
            url = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/classic":
                page = dashboard.render().replace(
                    "<form method='post'>",
                    f"<form method='post'><input type='hidden' name='csrf' value='{csrf_token}'><input type='hidden' name='classic' value='1'>")
            elif url.path == "/":
                page = gmail_ui.render_app(
                    dashboard, view=q.get("view", "inbox"), thread_id=q.get("thread", ""), csrf=csrf_token,
                    msg=q.get("msg", ""), err=q.get("err", ""), category=q.get("category", "all"), sort=q.get("sort", "priority"))
            else:
                self.send_error(404)
                return
            self._respond(page)

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            with lock:
                self._post()

        def _post(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            if not secrets.compare_digest(form.get("csrf", ""), csrf_token):
                self.send_error(403, "Invalid or missing form token.")
                return
            action, thread_id, view = form.get("action", ""), form.get("thread_id", ""), form.get("view", "inbox")
            classic = bool(form.get("classic"))
            message = error = ""
            try:
                if action == "refresh":
                    dashboard.refresh()
                    message = "Inbox refreshed."
                elif action == "approve_save":
                    outcome = dashboard.approve_reply(thread_id, form.get("text"))
                    message = "Reply saved to your Gmail drafts. Nothing was sent."
                    if outcome["event_id"]:
                        message += " The meeting is on your calendar with reminders (email 1 day before, popup 30 min before)."
                    elif outcome["meeting_note"]:
                        message += " " + outcome["meeting_note"]
                elif action == "approve_all":
                    outcome = dashboard.approve_all_ready()
                    message = f"{len(outcome['saved'])} replies saved to your Gmail drafts. Nothing was sent."
                    if outcome["skipped"]:
                        error = f"{len(outcome['skipped'])} could not be saved: " + "; ".join(r for _, r in outcome["skipped"][:2])
                elif action == "undo":
                    dashboard.undo_reply(thread_id)
                    message = "Undone: the draft (and calendar event, if any) was deleted."
                elif action == "discard":
                    dashboard.discard(thread_id)
                    message = "Suggested draft discarded."
                elif action == "later_set":
                    dashboard.set_reply_later(thread_id, form.get("due", ""))
                    message = "Reminder set."
                elif action == "later_clear":
                    dashboard.clear_reply_later(thread_id)
                    message = "Reminder cleared."
                elif action == "cal_check":
                    result = dashboard.calendar_check(thread_id)
                    message = {"free": "You appear to be free at that time.",
                               "busy": "You have a conflict at that time."}.get(result["status"], result.get("detail", ""))
                elif action == "cal_add":
                    dashboard.calendar_add(thread_id, form.get("confirm") == "yes")
                    message = "Added to your Google Calendar. No invitations were sent."
                elif action == "cal_slots":
                    dashboard.append_slots_to_draft(thread_id)
                    message = "Free slots added to the draft. Review it before saving."
                elif action in ("done", "reopen"):
                    dashboard.mark_done(form.get("key", ""), form.get("confirm") == "yes", done=(action == "done"))
                    message = "Action item updated."
                elif action == "archive":
                    dashboard.archive(thread_id, form.get("confirm") == "yes")
                    message = "Thread archived. You can move it back from the Archived view."
                    thread_id = ""
                elif action == "unarchive":
                    dashboard.unarchive(thread_id)
                    message = "Thread moved back to your inbox."
                    thread_id = ""
                elif action == "save_draft":
                    dashboard.save_draft(thread_id)
                    message = "Draft saved to Gmail."
                elif action == "publish":
                    dashboard.publish_approved(thread_id)
                    message = "Published."
                else:
                    dashboard.owner_action(thread_id, action, form.get("note", ""))
                    message = "Decision recorded."
            except (GovernanceError, ValueError, PermissionError, RuntimeError, KeyError) as exc:
                error = str(exc)
            if classic:
                target = "/classic"
            else:
                target = f"/?view={quote(view)}" + (f"&thread={quote(thread_id)}" if thread_id and action != "refresh" else "")
                target += (f"&msg={quote(message)}" if message else "") + (f"&err={quote(error)}" if error else "")
            self.send_response(303)
            self.send_header("Location", target)
            self.end_headers()

        def _respond(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; img-src data:; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass  # browser closed the connection; harmless

        def log_message(self, _format: str, *_args: object) -> None:
            return

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the sanitized local Email Organizer dashboard.")
    parser.add_argument("--serve", action="store_true", help="serve the local dashboard on 127.0.0.1:8765")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=("fixture", "gmail"), default="fixture")
    parser.add_argument("--drafts", action="store_true", help="gmail mode: also request draft-write scope")
    parser.add_argument("--calendar", action="store_true", help="gmail mode: enable Google Calendar availability check and owner-confirmed events")
    parser.add_argument("--archive", action="store_true", help="gmail mode: allow archiving resolved threads (gmail.modify, reversible)")
    parser.add_argument("--poll", type=float, default=0, help="auto-refresh the inbox every N minutes")
    parser.add_argument("--timezone", default=None, help="IANA timezone for calendar (default Asia/Kolkata)")
    parser.add_argument("--account", default=None, help="expected Gmail address (default: professor account)")
    arguments = parser.parse_args()
    if arguments.mode == "gmail":
        demo = create_live(arguments.account, allow_drafts=arguments.drafts,
                           allow_calendar=arguments.calendar, allow_archive=arguments.archive,
                           timezone_name=arguments.timezone)
    else:
        demo = create_demo()
    if arguments.serve:
        serve(demo, arguments.port, poll_minutes=arguments.poll)
    else:
        print(demo.render())
