"""Metrics, owner state, standard responses, calendar, MCP tools, CLI, UI and HTTP flow (all mocked)."""
import io, socket, sys, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmail_cli, gmail_ui, mcp_tools
from calendar_connector import CalendarError, GoogleCalendar, parse_proposed_time
from gmail_auth import CALENDAR_SCOPES, DRAFT_SCOPES, GmailAuthError, GmailSession, READ_ONLY_SCOPES
from lab14_dashboard import create_demo, serve
from metrics import compute_metrics
from response_library import STARTER_LIBRARY, load_library, match
from state_store import OwnerStateStore
from fake_gmail import FakeCalendar, FakeGmail, OWNER, make_message, sample_threads
from test_real_mode_pipeline import live_dashboard
from lab5_connector import ConnectorHistoryMessage as H


class MetricsTests(unittest.TestCase):
    def test_response_times_and_overdue(self):
        h = [H("a", "x <x@s.example>", "s", "b", (), timestamp="2026-10-01T08:00:00+00:00", message_id="1"),
             H("a", f"P <{OWNER}>", "s", "b", (), timestamp="2026-10-01T12:00:00+00:00", message_id="2"),
             H("b", "y <y@s.example>", "s", "b", (), timestamp="2026-10-01T08:00:00+00:00", message_id="3")]
        m = compute_metrics(h, OWNER, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual((m.replied_pairs, m.avg_response_hours, m.awaiting_reply, m.overdue), (1, 4.0, 1, 1))
        self.assertEqual(m.overdue_thread_ids, ("b",))

    def test_excluded_and_untimed_ignored(self):
        h = [H("b", "y <y@s.example>", "s", "b", (), timestamp="", message_id="3")]
        self.assertEqual(compute_metrics(h, OWNER).threads, 0)


class StateTests(unittest.TestCase):
    def test_persist_and_validate(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "s.json"
        s = OwnerStateStore(path)
        s.set_reply_later("t", "2026-11-01", "m1"); s.set_edit("t", "m1", "text"); s.set_draft("t", "m1", "d1")
        s2 = OwnerStateStore(path)
        self.assertEqual(s2.reply_later()["t"]["due"], "2026-11-01")
        self.assertEqual((s2.get_edit("t", "m1"), s2.get_edit("t", "OTHER"), s2.draft_for("t", "m1")), ("text", None, "d1"))
        with self.assertRaises(ValueError):
            s.set_reply_later("t", "not-a-date")

    def test_corrupt_state_is_empty(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "s.json"; path.write_text("{bad")
        self.assertEqual(OwnerStateStore(path).reply_later(), {})

    def test_reply_later_digest(self):
        dash, _, _ = live_dashboard()
        dash.set_reply_later("t-meeting", "2026-01-01"); dash.set_reply_later("t-deadline", "2099-01-01")
        d = dash.followup_digest(today=date(2026, 6, 1))
        self.assertEqual(d["due"], [("t-meeting", "2026-01-01")])
        self.assertEqual(d["upcoming"], [("t-deadline", "2099-01-01")])
        dash.clear_reply_later("t-meeting")
        self.assertEqual(dash.followup_digest(today=date(2026, 6, 1))["due"], [])


class LibraryTests(unittest.TestCase):
    def test_starter_size_and_match(self):
        self.assertTrue(10 <= len(STARTER_LIBRARY) <= 15)
        self.assertEqual(match(STARTER_LIBRARY, "Request for extension of the deadline").id, "extension")
        self.assertIsNone(match(STARTER_LIBRARY, "completely unrelated words here"))

    def test_user_library_overrides(self):
        import json, tempfile
        p = Path(tempfile.mkdtemp()) / "lib.json"
        p.write_text(json.dumps([{"id": "x", "title": "T", "keywords": ["Banana"], "body": "B"}]))
        self.assertEqual(match(load_library(p), "banana split").id, "x")
        p.write_text("nope")
        self.assertEqual(load_library(p), STARTER_LIBRARY)


class CalendarTests(unittest.TestCase):
    def test_parses_only_explicit_date_and_time(self):
        p = parse_proposed_time("Can we meet on 20 October 2026 at 3 pm?")
        self.assertEqual((p.start.year, p.start.month, p.start.day, p.start.hour), (2026, 10, 20, 15))
        self.assertIsNotNone(parse_proposed_time("Meet 15/10/2026 at 14:30"))
        self.assertEqual(parse_proposed_time("on 15/10/2026 at 9:30 am").start.month, 10)
        self.assertIsNone(parse_proposed_time("Can we meet on Tuesday at 3 pm?"))
        self.assertIsNone(parse_proposed_time("Let us meet on 20 October at 3 pm"))
        self.assertIsNone(parse_proposed_time("Let us meet on 20 October 2026"))
        self.assertIsNone(parse_proposed_time("on 31/02/2026 at 3 pm"))

    def cal(self, busy=None):
        s = GmailSession(None, OWNER, DRAFT_SCOPES + CALENDAR_SCOPES)
        fake = FakeCalendar(busy)
        return GoogleCalendar(s, service=fake, sleep=lambda x: None), fake

    def test_requires_scopes(self):
        with self.assertRaises(GmailAuthError):
            GoogleCalendar(GmailSession(None, OWNER, READ_ONLY_SCOPES), service=object())

    def test_busy_and_confirmed_event_without_invites(self):
        cal, fake = self.cal([{"start": "a", "end": "b"}])
        p = parse_proposed_time("on 20 October 2026 at 3 pm")
        self.assertEqual(len(cal.busy_periods(p)), 1)
        with self.assertRaises(CalendarError):
            cal.create_event("S", p, owner_confirmed=False, idempotency_key="k")
        self.assertEqual([c for c in fake.calls if c[0] == "insert"], [])
        cal.create_event("S", p, owner_confirmed=True, idempotency_key="k")
        call = [c for c in fake.calls if c[0] == "insert"][0]
        self.assertEqual(call[1], "none")
        self.assertNotIn("attendees", call[2])

    def test_duplicate_event_is_idempotent(self):
        cal, fake = self.cal(); p = parse_proposed_time("on 20 October 2026 at 3 pm")
        a = cal.create_event("S", p, owner_confirmed=True, idempotency_key="k")
        b = cal.create_event("S", p, owner_confirmed=True, idempotency_key="k")
        self.assertTrue(b["duplicate"]); self.assertEqual(a["id"], b["id"]); self.assertEqual(len(fake.event_store), 1)

    def test_dashboard_calendar_flow_needs_confirmation(self):
        cal, fake = self.cal()
        dash, _, _ = live_dashboard(calendar=cal)
        self.assertEqual(dash.calendar_check("t-meeting")["status"], "free")
        from lab12_governance import GovernanceError
        with self.assertRaises(GovernanceError):
            dash.calendar_add("t-meeting", confirmed=False)
        dash.calendar_add("t-meeting", confirmed=True)
        self.assertIn("calendar event created", " ".join(dash.snapshot().audit))
        self.assertIsNotNone(dash._store.event_for("t-meeting"))
        self.assertEqual(dash.calendar_check("t-deadline")["status"], "no_time")

    def test_calendar_off_by_default(self):
        dash, _, _ = live_dashboard()
        self.assertEqual(dash.calendar_check("t-meeting")["status"], "calendar_off")


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self.dash, self.fake, self.conn = live_dashboard()

    def test_read_only_tools_flag_untrusted_content(self):
        inbox = mcp_tools.list_inbox(self.conn, 2)
        self.assertEqual(len(inbox["threads"]), 2); self.assertIn("UNTRUSTED", inbox["notice"])
        self.assertIn("15 October 2026", mcp_tools.get_thread(self.conn, "t-deadline")["messages"][0]["body"])
        self.assertIn("error", mcp_tools.get_thread(self.conn, "nope"))
        t = {x["thread_id"]: x for x in mcp_tools.triage_summary(self.conn)["threads"]}
        self.assertEqual(t["t-deadline"]["classification"], "deadline")

    def test_server_exposes_no_write_tools(self):
        import asyncio, mcp_server
        names = {t.name for t in asyncio.run(mcp_server.build_server(self.conn).list_tools())}
        self.assertEqual(names, {"list_inbox", "get_thread", "triage_summary"})
        self.assertFalse(any(c[0] != "threads.list" and c[0] != "threads.get" for c in self.fake.calls))


class CliTests(unittest.TestCase):
    def test_verify_output_and_failure(self):
        out = io.StringIO()
        with mock.patch.object(gmail_cli, "authorize", return_value=GmailSession(object(), OWNER, READ_ONLY_SCOPES)), redirect_stdout(out):
            self.assertEqual(gmail_cli.main(["verify"]), 0)
        self.assertIn(OWNER, out.getvalue())
        err = io.StringIO()
        with mock.patch.object(gmail_cli, "authorize", side_effect=GmailAuthError("nope")), redirect_stderr(err):
            self.assertEqual(gmail_cli.main(["verify", "--drafts", "--calendar"]), 1)

    def test_calendar_flag_requests_calendar_scopes(self):
        with mock.patch.object(gmail_cli, "authorize", return_value=GmailSession(object(), OWNER, ())) as a, redirect_stdout(io.StringIO()):
            gmail_cli.main(["verify", "--drafts", "--calendar"])
        self.assertTrue(set(CALENDAR_SCOPES) <= set(a.call_args.kwargs["scopes"]))


class UiTests(unittest.TestCase):
    def setUp(self):
        self.dash, _, _ = live_dashboard()

    def page(self, **kw):
        return gmail_ui.render_app(self.dash, csrf="TOK", **kw)

    def test_inbox_looks_like_mail_client_with_sender_subject(self):
        p = self.page()
        for text in ("Asha Rao", "Assignment 2 deadline", "LIVE GMAIL", "Refresh inbox", "Needs reply", "Insights", 'name="csrf" value="TOK"'):
            self.assertIn(text, p)
        self.assertIn("row unread", p)
        self.assertLess(p.index("Asha Rao"), p.index("Campus News"))  # ranked by priority

    def test_every_view_renders(self):
        self.dash.set_reply_later("t-deadline", "2020-01-01")
        for view in ("inbox", "needs", "later", "actions", "meetings", "drafts", "insights", "audit"):
            self.assertIn("<html", self.page(view=view))
        self.assertIn("Follow-up digest", self.page(view="later"))
        self.assertIn("Due now", self.page(view="later"))
        self.assertIn("Average response time", self.page(view="insights"))

    def test_thread_page_has_draft_editor_and_calendar_card(self):
        p = self.page(thread_id="t-meeting")
        for text in ("Reply drafted by your agent", "Approve &amp; save to Gmail drafts", "Remind me", "Meeting", "20 October 2026"):
            self.assertIn(text, p)

    def test_html_in_email_is_escaped(self):
        d, _, _ = live_dashboard(FakeGmail({"t": [make_message("m", "t", "<script>x</script>@e.example", "<b>hi</b>", "<img src=x onerror=1>")]}))
        p = gmail_ui.render_app(d, csrf="T", thread_id="t")
        self.assertNotIn("<script>x", p); self.assertNotIn("<img src=x", p)

    def test_saved_status_and_fixture_banner(self):
        self.dash.approve_and_save("t-deadline")
        self.assertIn("Saved in Gmail Drafts", self.page(thread_id="t-deadline"))
        self.assertIn("FIXTURE MODE", gmail_ui.render_app(create_demo(), csrf="T"))

    def test_ui_contains_no_dispatch_control(self):
        p = self.page(thread_id="t-deadline")
        self.assertNotIn('value="send"', p.lower())


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class HttpFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dash, cls.fake, _ = live_dashboard()
        cls.port = free_port()
        threading.Thread(target=serve, args=(cls.dash, cls.port), daemon=True).start()
        time.sleep(0.4)
        cls.base = f"http://127.0.0.1:{cls.port}"

    def get(self, path):
        return urllib.request.urlopen(self.base + path).read().decode()

    def post(self, data):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(urllib.request.Request(self.base + "/", data=urllib.parse.urlencode(data).encode()))
        except urllib.error.HTTPError as e:
            return e
        raise AssertionError("expected redirect")

    def token(self):
        import re
        return re.search(r'name="csrf" value="([^"]+)"', self.get("/")).group(1)

    def test_pages_and_classic(self):
        self.assertIn("Asha Rao", self.get("/"))
        self.assertIn("Triaged Inbox", self.get("/classic"))
        self.assertIn("Email Organizer", self.get("/?view=insights"))

    def test_post_without_token_forbidden(self):
        self.assertEqual(self.post({"action": "refresh"}).code, 403)

    def test_full_approve_save_flow_and_errors_are_friendly(self):
        t = self.token()
        r = self.post({"csrf": t, "action": "approve_save", "thread_id": "t-deadline", "view": "inbox", "text": "Edited by owner."})
        self.assertEqual(r.code, 303)
        self.assertIn("msg=", r.headers["Location"])
        self.assertTrue(any(c[0] == "drafts.create" for c in self.fake.calls))
        r = self.post({"csrf": t, "action": "approve_save", "thread_id": "t-news", "view": "inbox"})
        self.assertIn("err=", r.headers["Location"])
        r = self.post({"csrf": t, "action": "later_set", "thread_id": "t-meeting", "due": "2026-12-01", "view": "inbox"})
        self.assertIn("Reminder", urllib.parse.unquote(r.headers["Location"]))
        self.assertFalse([c for c in self.fake.calls if "send" in c[0]])

    def test_security_headers(self):
        resp = urllib.request.urlopen(self.base + "/")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", resp.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
