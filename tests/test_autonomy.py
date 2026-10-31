"""Agentic flow: automatic availability check, one-click approve+schedule with reminders, undo, bulk, briefing."""
import base64, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmail_ui
from calendar_connector import GoogleCalendar
from gmail_auth import CALENDAR_SCOPES, DRAFT_SCOPES, GmailSession
from lab12_governance import GovernanceError
from rules import Rules
from fake_gmail import FakeCalendar, FakeGmail, OWNER, make_message, sample_threads
from test_extended import dash_with


def cal(busy=None):
    return GoogleCalendar(GmailSession(None, OWNER, DRAFT_SCOPES + CALENDAR_SCOPES), service=FakeCalendar(busy), sleep=lambda x: None)


def raw_text(fake, draft_id):
    raw = fake.draft_store[draft_id]["message"]["raw"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


class MeetingAutomationTests(unittest.TestCase):
    def test_free_time_confirms_in_draft_and_on_approval_creates_event_with_reminders(self):
        c = cal(); d, fake, _ = dash_with(calendar=c)
        self.assertEqual(d.meeting_plan("t-meeting")["status"], "free")
        self.assertIn("I am happy to confirm our meeting on Tue 20 Oct 2026, 3:00 PM", d.draft_text("t-meeting"))
        self.assertEqual(c._service.event_store, {})                      # nothing on the calendar before approval
        out = d.approve_reply("t-meeting")
        ev = list(c._service.event_store.values())
        self.assertEqual(len(ev), 1)
        self.assertEqual(out["event_id"], ev[0]["id"])
        self.assertEqual({r["method"]: r["minutes"] for r in ev[0]["reminders"]["overrides"]}, {"email": 1440, "popup": 30})
        self.assertFalse(ev[0]["reminders"]["useDefault"]); self.assertNotIn("attendees", ev[0])
        self.assertEqual([x for x in c._service.calls if x[0] == "insert"][0][1], "none")
        self.assertIn("I am happy to confirm", raw_text(fake, out["draft_id"]))
        self.assertIn("calendar event created after approval", " ".join(d.snapshot().audit))
        self.assertIsNotNone(d._store.event_for("t-meeting"))
        self.assertFalse([x for x in fake.calls if "send" in x[0]])

    def test_busy_time_proposes_alternatives_and_creates_no_event(self):
        busy = [{"start": "2026-10-20T14:30:00+05:30", "end": "2026-10-20T16:00:00+05:30"}]
        c = cal(busy); d, fake, _ = dash_with(calendar=c)
        self.assertEqual(d.meeting_plan("t-meeting")["status"], "busy")
        text = d.draft_text("t-meeting")
        self.assertIn("Unfortunately I am not available at that time.", text)
        self.assertIn("I am available at:", text)
        out = d.approve_reply("t-meeting")
        self.assertIsNone(out["event_id"]); self.assertIn("conflicts", out["meeting_note"])
        self.assertEqual(c._service.event_store, {})

    def test_no_time_stated_proposes_own_free_slots_only_in_draft(self):
        t = {"t": [make_message("m", "t", "Dr A <a@x.example>", "Meeting request", "Can we meet next week to discuss the project?")]}
        c = cal(); d, _, _ = dash_with(t, calendar=c)
        self.assertEqual(d.meeting_plan("t")["status"], "no_time")
        self.assertIn("I am available at:", d.draft_text("t"))
        out = d.approve_reply("t")
        self.assertIsNone(out["event_id"]); self.assertEqual(c._service.event_store, {})

    def test_conflict_appearing_after_page_load_blocks_event(self):
        c = cal(); d, _, _ = dash_with(calendar=c)
        d.meeting_plan("t-meeting")
        c._service.busy = [{"start": "2026-10-20T15:00:00+05:30", "end": "2026-10-20T15:30:00+05:30"}]
        out = d.approve_reply("t-meeting")
        self.assertIsNone(out["event_id"]); self.assertEqual(c._service.event_store, {})

    def test_without_calendar_meeting_draft_is_unchanged(self):
        d, _, _ = dash_with()
        self.assertIn("confirm the meeting separately", d.draft_text("t-meeting"))
        self.assertIn("Calendar is not connected", d.approve_reply("t-meeting")["meeting_note"])

    def test_no_event_without_approval_and_edit_still_wins(self):
        c = cal(); d, fake, _ = dash_with(calendar=c)
        d.owner_action("t-meeting", "approve")             # approval alone (no publish) creates nothing
        self.assertEqual(c._service.event_store, {})
        out = d.approve_reply("t-meeting", "Custom text from the professor.")
        self.assertIn("Custom text from the professor.", raw_text(fake, out["draft_id"]))

    def test_undo_deletes_draft_and_event_and_allows_new_approval(self):
        c = cal(); d, fake, _ = dash_with(calendar=c)
        first = d.approve_reply("t-meeting")
        d.undo_reply("t-meeting")
        self.assertEqual(fake.draft_store, {}); self.assertEqual(c._service.event_store, {})
        self.assertIsNone(d.saved_draft_id("t-meeting"))
        self.assertIn("reply undone", " ".join(d.snapshot().audit))
        second = d.approve_reply("t-meeting")
        self.assertEqual(len(fake.draft_store), 1); self.assertEqual(d.saved_draft_id("t-meeting"), second["draft_id"])
        with self.assertRaises(GovernanceError):
            d.undo_reply("t-deadline")

    def test_bulk_approve_saves_each_ready_reply_once(self):
        d, fake, _ = dash_with()
        n = d.ready_reply_count()
        self.assertEqual(n, 3)
        out = d.approve_all_ready()
        self.assertEqual((len(out["saved"]), out["skipped"]), (3, []))
        self.assertEqual(d.ready_reply_count(), 0)
        self.assertEqual(len(fake.draft_store), 3)
        self.assertEqual(d.approve_all_ready()["saved"], [])
        self.assertFalse([x for x in fake.calls if "send" in x[0]])


class PastTimeTests(unittest.TestCase):
    def test_past_time_is_never_confirmed_or_scheduled(self):
        c = cal(); d, fake, _ = dash_with(calendar=c)
        d._clock = lambda: __import__("datetime").datetime(2026, 11, 1, tzinfo=__import__("datetime").timezone.utc)
        d._plans = {}
        self.assertEqual(d.meeting_plan("t-meeting")["status"], "past")
        self.assertIn("confirm the meeting separately", d.draft_text("t-meeting"))
        out = d.approve_reply("t-meeting")
        self.assertIsNone(out["event_id"]); self.assertIn("already passed", out["meeting_note"])
        self.assertEqual(c._service.event_store, {})
        self.assertIn("already passed", gmail_ui.render_app(d, thread_id="t-meeting", csrf="T"))


class UiAutonomyTests(unittest.TestCase):
    def test_thread_page_is_conversation_plus_reply_box(self):
        d, _, _ = dash_with(calendar=cal())
        p = gmail_ui.render_app(d, thread_id="t-meeting", csrf="T")
        for text in ("Approve reply &amp; schedule meeting", "You are free then", "email 1 day before", "Reply drafted by your agent", 'class="summary"', "Why the agent decided this"):
            self.assertIn(text, p)
        self.assertNotIn("Suggested free slots", p)
        self.assertNotIn("Add my free slots", p)

    def test_busy_card_and_scheduled_card_and_undo(self):
        busy = [{"start": "2026-10-20T15:00:00+05:30", "end": "2026-10-20T15:30:00+05:30"}]
        d, _, _ = dash_with(calendar=cal(busy))
        self.assertIn("You have a conflict", gmail_ui.render_app(d, thread_id="t-meeting", csrf="T"))
        d2, _, _ = dash_with(calendar=cal())
        d2.approve_reply("t-meeting")
        p = gmail_ui.render_app(d2, thread_id="t-meeting", csrf="T")
        for text in ("On your calendar", "Saved in Gmail Drafts", 'value="undo"'):
            self.assertIn(text, p)

    def test_briefing_and_bulk_button(self):
        d, _, _ = dash_with(calendar=cal())
        p = gmail_ui.render_app(d, csrf="T")
        for text in ("Today", "replies ready to approve", "meeting requests to confirm", "waiting for your reply"):
            self.assertIn(text, p)
        self.assertIn("Approve &amp; save all 3 ready replies", gmail_ui.render_app(d, view="drafts", csrf="T"))
        future = {"t": [make_message("m", "t", "Dr A <a@x.example>", "Meeting request", "Can we meet on 20 October 2099 at 3 pm?")]}
        d2, _, _ = dash_with(future, calendar=cal())
        self.assertEqual(d2.briefing()["next_meeting"], None)
        d2.approve_reply("t")
        self.assertEqual(len(d2.upcoming_meetings()), 1)
        self.assertIn("Next meeting:", gmail_ui.render_app(d2, csrf="T"))
        self.assertIn("Reminders on", gmail_ui.render_app(d2, view="calendar", csrf="T"))

    def test_summary_line(self):
        d, _, _ = dash_with()
        self.assertEqual(d.summary("t-deadline"), "Asha Rao: Please confirm whether I can submit by Friday. (date mentioned: 15 October 2026, by Friday)")


if __name__ == "__main__":
    unittest.main()
