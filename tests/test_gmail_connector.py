"""Mocked Gmail connector tests: parsing, pagination, errors, drafts, no-send."""
import base64, email, re, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gmail_auth import DRAFT_SCOPES, READ_ONLY_SCOPES, GmailAuthError, GmailSession
from gmail_connector import DraftError, GmailApiError, GmailConnector
from fake_gmail import FakeGmail, OWNER, make_message, sample_threads


def connector(fake, scopes=DRAFT_SCOPES, **kw):
    log = Path(tempfile.mkdtemp()) / "drafts.json"
    return GmailConnector(GmailSession(None, OWNER, tuple(scopes)), service=fake, draft_log_path=log, sleep=lambda s: None, **kw)


def decode_draft(fake, draft_id):
    body = fake.draft_store[draft_id]
    raw = body["message"]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))), body["message"]["threadId"]


class ReadTests(unittest.TestCase):
    def test_maps_ids_headers_recipients_timestamp_body(self):
        read = connector(FakeGmail(sample_threads())).read_inbox_threads()
        t = next(x for x in read.inbox_threads if x.thread_id == "t-deadline")
        self.assertEqual((t.message_id, t.subject), ("m1", "Assignment 2 deadline"))
        self.assertEqual(t.recipients, (OWNER,))
        self.assertTrue(t.timestamp.startswith("2026-10-05T10:00:00"))
        m = next(x for x in read.history_messages if x.message_id == "m1")
        self.assertIn("15 October 2026", m.body)
        self.assertIn(("Message-ID", "<m1@mail.example>"), m.headers)

    def test_empty_inbox(self):
        read = connector(FakeGmail({})).read_inbox_threads()
        self.assertEqual((read.inbox_threads, read.history_messages), ((), ()))

    def test_pagination_and_cap(self):
        threads = {f"t{i}": [make_message(f"m{i}", f"t{i}", "a@x.example", f"S{i}", "hi")] for i in range(7)}
        fake = FakeGmail(threads, page_size_limit=3)
        self.assertEqual(len(connector(fake, max_threads=50, page_size=3).read_inbox_threads().inbox_threads), 7)
        self.assertGreaterEqual(sum(1 for c in fake.calls if c[0] == "threads.list"), 3)
        self.assertEqual(len(connector(FakeGmail(threads, page_size_limit=3), max_threads=4, page_size=3).read_inbox_threads().inbox_threads), 4)

    def test_html_only_body(self):
        read = connector(FakeGmail(sample_threads())).read_inbox_threads()
        body = next(m.body for m in read.history_messages if m.thread_id == "t-news")
        self.assertIn("Events this week", body)
        self.assertNotIn("x()", body)

    def test_malformed_messages_skipped(self):
        good = make_message("g", "t1", "a@x.example", "ok", "body")
        bad = {"threadId": "t1", "payload": {}}
        nobody = {"id": "n", "threadId": "t1", "labelIds": ["INBOX"], "payload": {"headers": []}}
        read = connector(FakeGmail({"t1": [bad, good, nobody]})).read_inbox_threads()
        self.assertEqual([m.message_id for m in read.history_messages], ["g", "n"])

    def test_rate_limit_retried(self):
        fake = FakeGmail(sample_threads()); fake.fail_next = [429, 503]
        self.assertEqual(len(connector(fake).read_inbox_threads().inbox_threads), 4)

    def test_persistent_error_bounded(self):
        fake = FakeGmail(sample_threads()); fake.fail_next = [500] * 10
        with self.assertRaises(GmailApiError):
            connector(fake, max_retries=2).read_inbox_threads()

    def test_403_is_auth_error(self):
        fake = FakeGmail(sample_threads()); fake.fail_next = [403]
        with self.assertRaises(GmailAuthError):
            connector(fake).read_inbox_threads()

    def test_principal_and_no_fake_calendar(self):
        c = connector(FakeGmail({}))
        self.assertEqual(c.authenticated_principal, OWNER)
        self.assertEqual(c.read_calendar_tasks(), ())


class DraftTests(unittest.TestCase):
    def test_correct_thread_recipient_headers(self):
        fake = FakeGmail(sample_threads()); c = connector(fake)
        rec = c.create_draft_reply("t-meeting", "Hello", idempotency_key="k1")
        msg, thread_id = decode_draft(fake, rec.draft_id)
        self.assertEqual(thread_id, "t-meeting")
        self.assertEqual(msg["To"], "office-kumar@dept.example")
        self.assertEqual(msg["Subject"], "Re: Project discussion")
        self.assertEqual(msg["In-Reply-To"], "<m2@mail.example>")
        self.assertIn("<m2@mail.example>", msg["References"])

    def test_owner_replied_last_goes_to_sender(self):
        fake = FakeGmail(sample_threads())
        rec = connector(fake).create_draft_reply("t-replied", "Hi", idempotency_key="k2")
        self.assertEqual(decode_draft(fake, rec.draft_id)[0]["To"], "ravi@student.example")

    def test_duplicate_key_one_draft(self):
        fake = FakeGmail(sample_threads()); c = connector(fake)
        a = c.create_draft_reply("t-deadline", "Hi", idempotency_key="same")
        b = c.create_draft_reply("t-deadline", "Hi again", idempotency_key="same")
        self.assertEqual(a.draft_id, b.draft_id)
        self.assertEqual(sum(1 for x in fake.calls if x[0] == "drafts.create"), 1)

    def test_duplicate_protection_survives_restart(self):
        fake = FakeGmail(sample_threads()); c = connector(fake)
        c.create_draft_reply("t-deadline", "Hi", idempotency_key="k")
        GmailConnector(c._session, service=fake, draft_log_path=c._draft_log_path, sleep=lambda s: None).create_draft_reply("t-deadline", "Hi", idempotency_key="k")
        self.assertEqual(len(fake.draft_store), 1)

    def test_transient_error_retried(self):
        fake = FakeGmail(sample_threads()); c = connector(fake); fake.fail_next = [503]
        c.create_draft_reply("t-deadline", "Hi", idempotency_key="k")
        self.assertEqual(len(fake.draft_store), 1)

    def test_read_only_scope_cannot_draft(self):
        with self.assertRaisesRegex(DraftError, "gmail.compose"):
            connector(FakeGmail(sample_threads()), scopes=READ_ONLY_SCOPES).create_draft_reply("t-deadline", "Hi", idempotency_key="k")

    def test_no_recipient_or_empty_body(self):
        fake = FakeGmail({"t": [make_message("m", "t", "Prof <%s>" % OWNER, "own", "x")]})
        with self.assertRaises(DraftError):
            connector(fake).create_draft_reply("t", "Hi", idempotency_key="k")
        with self.assertRaises(DraftError):
            connector(FakeGmail(sample_threads())).create_draft_reply("t-deadline", "  ", idempotency_key="k")

    def test_deleted_draft_detected(self):
        fake = FakeGmail(sample_threads()); c = connector(fake)
        rec = c.create_draft_reply("t-deadline", "Hi", idempotency_key="k")
        del fake.draft_store[rec.draft_id]
        self.assertEqual(c.draft_status("k").status, "missing")

    def test_header_injection_neutralised(self):
        fake = FakeGmail({"t": [make_message("m", "t", "a@x.example", "Hi\r\nBcc: evil@x.example", "body")]})
        rec = connector(fake).create_draft_reply("t", "Hello", idempotency_key="k")
        self.assertIsNone(decode_draft(fake, rec.draft_id)[0]["Bcc"])


class NoSendTests(unittest.TestCase):
    def test_no_source_calls_send_endpoint(self):
        for path in (Path(__file__).resolve().parents[1] / "src").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\.send\s*\(", text), path.name)

    def test_connector_has_no_send_method(self):
        self.assertFalse(any("send" in n.lower() for n in dir(GmailConnector) if not n.startswith("__")))

    def test_flow_never_touches_send(self):
        fake = FakeGmail(sample_threads()); c = connector(fake)
        c.read_inbox_threads(); c.create_draft_reply("t-deadline", "Hi", idempotency_key="k")
        self.assertFalse([x for x in fake.calls if "send" in x[0]])


if __name__ == "__main__":
    unittest.main()
