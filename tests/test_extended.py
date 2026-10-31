"""Rules, sender category, urgency, action items, calendar page/slots, archive, reply log, polling, organize-inbox."""
import io, json, sys, tempfile, threading, time, unittest, socket
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import email_agent, gmail_ui
from calendar_connector import GoogleCalendar, free_slots
from gmail_auth import ARCHIVE_SCOPES, CALENDAR_SCOPES, DRAFT_SCOPES, GmailSession
from gmail_connector import DraftError, GmailConnector
from lab12_governance import GovernanceError
from lab14_dashboard import LocalDashboard, serve
from mailbox_nodes import analyze, make_graph_factory
from rules import Rules, load_rules
from state_store import OwnerStateStore
from draft_publisher import DraftPublisher
from fake_gmail import FakeCalendar, FakeGmail, OWNER, make_message, sample_threads
from lab3_skills import PlanInputs, plan
from lab5_connector import ConnectorInboxThread as T, ConnectorHistoryMessage as H

IST = ZoneInfo("Asia/Kolkata")
PLAN = plan(PlanInputs("Triage", "INBOX", "Deadlines first", ("t",)))


def dash_with(threads=None, rules=None, calendar=None, scopes=DRAFT_SCOPES, fake=None):
    fake = fake or FakeGmail(threads or sample_threads())
    conn = GmailConnector(GmailSession(None, OWNER, tuple(scopes)), service=fake,
                          draft_log_path=Path(tempfile.mkdtemp()) / "d.json", sleep=lambda s: None)
    d = LocalDashboard(conn, OWNER, mode_label="LIVE GMAIL - test", plan=PLAN,
                       graph_factory=make_graph_factory(rules or Rules()),
                       draft_publisher_factory=lambda g: DraftPublisher(conn, g),
                       state_store=OwnerStateStore(), calendar=calendar, rules=rules or Rules(),
                       clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))
    return d, fake, conn


def thread(sender, subject, body, headers=()):
    t = T("t", sender, subject, ("INBOX",), message_id="m")
    m = H("t", sender, subject, body, (), message_id="m", headers=tuple(headers))
    return t, [m]


class RulesTests(unittest.TestCase):
    def test_load_and_defaults(self):
        p = Path(tempfile.mkdtemp()) / "r.json"
        p.write_text(json.dumps({"priority_phrases": ["NAAC"], "student_domains": ["student.edu"],
                                 "working_hours": {"start": 9, "end": 13}, "slot_minutes": 20}))
        r = load_rules(p)
        self.assertEqual((r.priority_phrases, r.student_domains, r.work_start_hour, r.slot_minutes), (("naac",), ("student.edu",), 9, 20))
        p.write_text("{bad"); self.assertEqual(load_rules(p), Rules())
        p.write_text(json.dumps({"working_hours": {"start": 20, "end": 5}})); self.assertEqual(load_rules(p), Rules())


class AnalysisTests(unittest.TestCase):
    def test_sender_category_rules_then_content(self):
        t, m = thread("A <a@student.edu>", "Hello", "Hi")
        self.assertEqual(analyze(t, m, OWNER, Rules(student_domains=("student.edu",))).sender_category, "student")
        self.assertEqual(analyze(t, m, OWNER, Rules(vendor_domains=("student.edu",))).sender_category, "vendor")
        t, m = thread("A <a@x.example>", "Attendance report", "Please share")
        f = analyze(t, m, OWNER, Rules())
        self.assertEqual((f.sender_category, f.category_source), ("student", "content"))
        t, m = thread("A <a@x.example>", "Hello", "Hi")
        self.assertEqual(analyze(t, m, OWNER, Rules()).category_source, "none")

    def test_urgency_signals_and_uncertainty(self):
        t, m = thread("A <a@x.example>", "URGENT: help", "Please reply ASAP")
        self.assertIn("escalation language", analyze(t, m, OWNER, Rules()).signals)
        t, m = thread("A <a@x.example>", "Hi", "Hello", [("Importance", "high")])
        self.assertIn("explicit priority marker", analyze(t, m, OWNER, Rules()).signals)
        t, m = thread("A <a@x.example>", "Hi", "About exam season")
        self.assertIn("your priority phrase", analyze(t, m, OWNER, Rules(priority_phrases=("exam season",))).signals)
        t, m = thread("A <a@x.example>", "Lunch", "Can we talk on 3 November 2026?")
        self.assertTrue(analyze(t, m, OWNER, Rules()).uncertain)
        t, m = thread("A <a@x.example>", "Lunch", "Urgent: talk on 3 November 2026")
        self.assertFalse(analyze(t, m, OWNER, Rules()).uncertain)

    def test_commitments_come_from_owner_messages(self):
        t, m = thread("A <a@x.example>", "Marks", "Please send marks.")
        m.append(H("t", f"P <{OWNER}>", "Re: Marks", "I will share the marks on Friday.", (), message_id="m2"))
        items = analyze(t, m, OWNER, Rules()).action_items
        self.assertIn("Review request: Please send marks.", items)
        self.assertIn("Your commitment: I will share the marks on Friday.", items)

    def test_escalation_lifts_priority_to_95_but_not_above_deadline(self):
        threads = {"t1": [make_message("m1", "t1", "a@x.example", "Urgent matter", "Please call me immediately.")],
                   "t2": [make_message("m2", "t2", "a@x.example", "Deadline", "The submission is due on 1 Nov 2026. Urgent.")]}
        d, _, _ = dash_with(threads)
        by = {w.thread_id: w for w in d._workers}
        self.assertEqual(by["t1"].priority, 95)
        self.assertEqual(by["t2"].priority, 100)


class CalendarTests(unittest.TestCase):
    def test_free_slots_rules(self):
        now = datetime(2026, 10, 23, 9, 0, tzinfo=IST)  # a Friday
        busy = [(datetime(2026, 10, 23, 10, 0, tzinfo=IST), datetime(2026, 10, 23, 11, 0, tzinfo=IST))]
        slots = free_slots(busy, now, 5, Rules(), IST, count=6)
        self.assertEqual(slots[0][0].hour, 11)
        self.assertTrue(all(a.weekday() < 5 and 10 <= a.hour < 17 for a, _ in slots))
        self.assertTrue(all(not (b[0] < e and a < b[1]) for a, e in slots for b in busy))
        self.assertLessEqual(sum(1 for a, _ in slots if a.date() == now.date()), 2)
        self.assertEqual(free_slots([], now, 0, Rules(work_start_hour=10, work_end_hour=10), IST), [])

    def cal(self, busy=None):
        return GoogleCalendar(GmailSession(None, OWNER, DRAFT_SCOPES + CALENDAR_SCOPES), service=FakeCalendar(busy), sleep=lambda x: None)

    def test_overview_parses_busy(self):
        busy = [{"start": "2026-10-23T10:00:00+05:30", "end": "2026-10-23T11:00:00+05:30"}]
        data = self.cal(busy).overview(Rules(), now=datetime(2026, 10, 23, 9, 0, tzinfo=IST))
        self.assertEqual(len(data["busy"]), 1)
        self.assertTrue(data["slots"])

    def test_calendar_view_visible_off_and_on(self):
        d, _, _ = dash_with()
        page = gmail_ui.render_app(d, view="calendar", csrf="T")
        self.assertIn("Calendar is not connected", page)
        self.assertIn(">Calendar<", gmail_ui.render_app(d, csrf="T"))
        d2, _, _ = dash_with(calendar=self.cal([{"start": "2099-01-01T10:00:00+05:30", "end": "2099-01-01T11:00:00+05:30"}]))
        page = gmail_ui.render_app(d2, view="calendar", csrf="T")
        for text in ("Meetings the agent scheduled for you", "Meeting requests in your inbox", "Project discussion", "Free/busy only"):
            self.assertIn(text, page)

    def test_legacy_slots_button_still_works(self):
        import base64
        d, fake, _ = dash_with(calendar=self.cal())
        d.append_slots_to_draft("t-meeting")
        self.assertIn("I am available at:", d.draft_text("t-meeting"))
        did = d.approve_and_save("t-meeting")
        raw = fake.draft_store[did]["message"]["raw"]
        self.assertIn(b"I am available at:", base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))

    def test_slots_need_calendar(self):
        d, _, _ = dash_with()
        with self.assertRaises(GovernanceError):
            d.append_slots_to_draft("t-meeting")


class ActionAndLogTests(unittest.TestCase):
    def setUp(self):
        self.d, self.fake, _ = dash_with()

    def test_done_requires_confirmation_and_is_audited(self):
        item = next(i for i in self.d.action_items() if i["thread_id"] == "t-deadline")
        with self.assertRaises(GovernanceError):
            self.d.mark_done(item["key"], confirmed=False)
        self.d.mark_done(item["key"], confirmed=True)
        self.assertTrue(next(i for i in self.d.action_items() if i["key"] == item["key"])["done"])
        self.assertIn("completed (owner confirmed)", " ".join(self.d.snapshot().audit))
        self.d.mark_done(item["key"], confirmed=False, done=False)
        with self.assertRaises(GovernanceError):
            self.d.mark_done("nope:key", confirmed=True)

    def test_reply_log_and_approval_history(self):
        self.d.approve_and_save("t-deadline")
        log = self.d.reply_log()[0]
        self.assertEqual((log["thread_id"], log["to"]), ("t-deadline", "asha@student.example"))
        self.assertTrue(log["saved_at"])
        hist = self.d.approval_history()[0]
        self.assertEqual((hist["decision"], hist["published"], hist["by"]), ("approve", True, OWNER))
        page = gmail_ui.render_app(self.d, view="audit", csrf="T")
        for text in ("Approval history", "Reply log", "saved, not sent", "Full audit trail"):
            self.assertIn(text, page)

    def test_refresh_audit_records_trigger_and_new_threads(self):
        self.fake.thread_store["t-x"] = [make_message("x1", "t-x", "z@x.example", "Doubt", "I have a doubt.")]
        self.d.refresh(trigger="schedule")
        self.assertTrue(any("trigger: schedule" in a and "1 new" in a for a in self.d.snapshot().audit))

    def test_views_render(self):
        for view in ("actions", "settings", "needs", "meetings", "drafts", "insights"):
            self.assertIn("<html", gmail_ui.render_app(self.d, view=view, csrf="T"))
        self.assertIn("Mark done", gmail_ui.render_app(self.d, view="actions", csrf="T"))
        self.assertIn("Student domains", gmail_ui.render_app(self.d, view="settings", csrf="T"))
        self.assertIn("Agent triage", gmail_ui.render_app(self.d, thread_id="t-deadline", csrf="T"))
        self.assertIn("Evidence (this thread only)", gmail_ui.render_app(self.d, thread_id="t-deadline", csrf="T"))

    def test_urgent_and_uncertain_chips(self):
        threads = {"t1": [make_message("m1", "t1", "a@x.example", "Urgent matter", "Call me immediately.")],
                   "t2": [make_message("m2", "t2", "a@x.example", "Lunch", "Maybe on 3 November 2026?")]}
        d, _, _ = dash_with(threads)
        page = gmail_ui.render_app(d, csrf="T")
        self.assertIn("Urgent</span>", page); self.assertIn("Check urgency", page)


class ArchiveTests(unittest.TestCase):
    def test_archive_requires_scope_confirmation_and_is_reversible(self):
        d, fake, conn = dash_with(scopes=DRAFT_SCOPES + ARCHIVE_SCOPES)
        self.assertTrue(d.can_archive)
        with self.assertRaises(GovernanceError):
            d.archive("t-deadline", confirmed=False)
        d.archive("t-deadline", confirmed=True)
        self.assertNotIn("t-deadline", [w.thread_id for w in d._workers])
        self.assertIn("t-deadline", d.archived_threads())
        self.assertIn("Move back to inbox", gmail_ui.render_app(d, view="archived", csrf="T"))
        d.unarchive("t-deadline")
        self.assertIn("t-deadline", [w.thread_id for w in d._workers])
        self.assertFalse([c for c in fake.calls if "send" in c[0] or "delete" in c[0] or "trash" in c[0]])

    def test_archive_off_without_scope(self):
        d, fake, conn = dash_with()
        self.assertFalse(d.can_archive)
        with self.assertRaises(GovernanceError):
            d.archive("t-deadline", confirmed=True)
        with self.assertRaises(DraftError):
            conn.archive_thread("t-deadline")
        self.assertNotIn("Archive thread", gmail_ui.render_app(d, thread_id="t-deadline", csrf="T"))
        self.assertNotIn(">Archived<", gmail_ui.render_app(d, csrf="T"))


class PollingTests(unittest.TestCase):
    def test_scheduled_refresh_runs_and_is_audited(self):
        d, _, _ = dash_with()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        threading.Thread(target=serve, args=(d, port, 0.001), daemon=True).start()
        deadline = time.time() + 5
        while time.time() < deadline and not any("trigger: schedule" in a for a in d.snapshot().audit):
            time.sleep(0.1)
        self.assertTrue(any("trigger: schedule" in a for a in d.snapshot().audit))


class OrganizeInboxTests(unittest.TestCase):
    def test_report_has_all_outputs_and_never_saves(self):
        d, fake, conn = dash_with()
        from response_library import STARTER_LIBRARY
        text = email_agent.run_report(conn, Rules(), STARTER_LIBRARY)
        for h in ("Triaged list", "Draft replies", "Action items", "Meeting requests", "no auto-send"):
            self.assertIn(h, text)
        self.assertIn("Assignment 2 deadline", text)
        self.assertFalse([c for c in fake.calls if c[0] == "drafts.create" or "send" in c[0]])
        only = email_agent.run_report(conn, Rules(), STARTER_LIBRARY, ("triaged list",))
        self.assertNotIn("Draft replies", only)

    def test_cli_wires_priority_rules_and_connector(self):
        d, fake, conn = dash_with()
        with mock.patch.object(email_agent, "build_connector", return_value=(conn, "LIVE")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(email_agent.main(["organize-inbox", "--priority-rules", "project discussion", "--outputs", "triaged list"]), 0)
        self.assertIn("Triaged list", out.getvalue())


if __name__ == "__main__":
    unittest.main()
