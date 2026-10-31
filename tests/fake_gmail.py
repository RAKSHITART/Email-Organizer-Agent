"""In-memory stand-ins for the Gmail and Calendar API clients (no network, no credentials)."""

import base64
import sys
import tempfile
from pathlib import Path

from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import response_library, rules  # noqa: E402

# Keep tests independent of any real rules/library files on the developer's machine.
_EMPTY = Path(tempfile.mkdtemp()) / "none.json"
rules.USER_RULES_PATH = _EMPTY
response_library.USER_LIBRARY_PATH = _EMPTY

OWNER = "prof.bharati.patil.kletech@gmail.com"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_message(mid, tid, sender, subject, body, *, to=OWNER, labels=("INBOX",), html=False,
                 extra_headers=(), date="Mon, 05 Oct 2026 10:00:00 +0000"):
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to},
               {"name": "Subject", "value": subject}, {"name": "Date", "value": date},
               {"name": "Message-ID", "value": f"<{mid}@mail.example>"}]
    headers += [{"name": k, "value": v} for k, v in extra_headers]
    return {"id": mid, "threadId": tid, "labelIds": list(labels),
            "payload": {"mimeType": "text/html" if html else "text/plain", "headers": headers,
                        "body": {"data": b64(body)}}}


def http_error(status):
    class R(dict):
        pass
    resp = R(status=status)
    resp.status = status
    resp.reason = "err"
    return HttpError(resp, b'{"error":"x"}')


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    def __init__(self, threads=None, profile_email=OWNER, sent=None, page_size_limit=None):
        self.thread_store = threads or {}
        self.sent = sent or []
        self.profile_email = profile_email
        self.calls = []
        self.draft_store = {}
        self.fail_next = []
        self.page_size_limit = page_size_limit

    def users(self):
        return self

    def getProfile(self, userId):
        return _Exec(lambda: {"emailAddress": self.profile_email})

    def threads(self):
        return _Threads(self)

    def messages(self):
        return _Messages(self)

    def drafts(self):
        return _Drafts(self)

    def _maybe_fail(self):
        if self.fail_next:
            raise http_error(self.fail_next.pop(0))


class _Threads:
    def __init__(self, g):
        self.g = g

    def list(self, userId, labelIds, maxResults, pageToken=None):
        def run():
            self.g._maybe_fail()
            self.g.calls.append(("threads.list", pageToken))
            ids = [t for t, msgs in self.g.thread_store.items() if any(labelIds[0] in m.get("labelIds", []) for m in msgs)]
            start = int(pageToken or 0)
            size = self.g.page_size_limit or maxResults
            out = {"threads": [{"id": t} for t in ids[start:start + size]]}
            if start + size < len(ids):
                out["nextPageToken"] = str(start + size)
            return out
        return _Exec(run)

    def modify(self, userId, id, body):
        def run():
            self.g.calls.append(("threads.modify", id, body))
            for m in self.g.thread_store[id]:
                labels = set(m.get("labelIds", []))
                labels |= set(body.get("addLabelIds", []))
                labels -= set(body.get("removeLabelIds", []))
                m["labelIds"] = sorted(labels)
            return {"id": id}
        return _Exec(run)

    def get(self, userId, id, format):
        def run():
            self.g._maybe_fail()
            self.g.calls.append(("threads.get", id))
            return {"id": id, "messages": self.g.thread_store[id], "snippet": "snip"}
        return _Exec(run)


class _Messages:
    def __init__(self, g):
        self.g = g

    def list(self, userId, labelIds, maxResults):
        return _Exec(lambda: {"messages": [{"id": m["id"]} for m in self.g.sent][:maxResults]})

    def get(self, userId, id, format):
        return _Exec(lambda: next(m for m in self.g.sent if m["id"] == id))

    def send(self, *a, **k):
        self.g.calls.append(("messages.send", None))
        raise AssertionError("messages.send must never be called")


class _Drafts:
    def __init__(self, g):
        self.g = g

    def create(self, userId, body):
        def run():
            self.g._maybe_fail()
            did = f"draft-{len(self.g.draft_store) + 1}"
            self.g.draft_store[did] = body
            self.g.calls.append(("drafts.create", did))
            return {"id": did, "message": {"id": "m-" + did}}
        return _Exec(run)

    def get(self, userId, id):
        def run():
            if id not in self.g.draft_store:
                raise http_error(404)
            return {"id": id}
        return _Exec(run)

    def delete(self, userId, id):
        def run():
            if id not in self.g.draft_store:
                raise http_error(404)
            del self.g.draft_store[id]
            self.g.calls.append(("drafts.delete", id))
            return {}
        return _Exec(run)

    def send(self, *a, **k):
        self.g.calls.append(("drafts.send", None))
        raise AssertionError("drafts.send must never be called")


class FakeCalendar:
    def __init__(self, busy=None):
        self.busy = busy or []
        self.deleted = []
        self.event_store = {}
        self.calls = []

    def freebusy(self):
        return self

    def query(self, body):
        self.calls.append(("freebusy", body))
        return _Exec(lambda: {"calendars": {"primary": {"busy": self.busy}}})

    def events(self):
        return self

    def delete(self, calendarId, eventId, sendUpdates):
        def run():
            self.calls.append(("delete", eventId, sendUpdates))
            self.event_store.pop(eventId, None)
            self.deleted.append(eventId)
            return {}
        return _Exec(run)

    def insert(self, calendarId, body, sendUpdates):
        def run():
            self.calls.append(("insert", sendUpdates, body))
            if body["id"] in self.event_store:
                raise http_error(409)
            self.event_store[body["id"]] = body
            return {"id": body["id"], "htmlLink": "https://calendar.example/e"}
        return _Exec(run)


def sample_threads():
    return {
        "t-deadline": [make_message(
            "m1", "t-deadline", "Asha Rao <asha@student.example>", "Assignment 2 deadline",
            "Dear Sir, the submission is due on 15 October 2026. Please confirm whether I can submit by Friday.",
            labels=("INBOX", "UNREAD"))],
        "t-meeting": [make_message(
            "m2", "t-meeting", "Dr Kumar <kumar@dept.example>", "Project discussion",
            "Can we meet on 20 October 2026 at 3 pm to discuss the review?",
            extra_headers=[("Reply-To", "Dr Kumar Office <office-kumar@dept.example>")])],
        "t-news": [make_message(
            "m3", "t-news", "Campus News <no-reply@campus.example>", "Weekly newsletter",
            "<html><body><p>Hello <b>faculty</b></p><script>x()</script><p>Events this week</p></body></html>",
            html=True, extra_headers=[("List-Id", "<news.campus.example>")])],
        "t-replied": [
            make_message("m4", "t-replied", "Ravi <ravi@student.example>", "Lab marks", "Please share my lab marks.",
                         date="Mon, 05 Oct 2026 08:00:00 +0000"),
            make_message("m5", "t-replied", f"Prof <{OWNER}>", "Re: Lab marks", "Will share soon.",
                         to="ravi@student.example", labels=("INBOX", "SENT"), date="Mon, 05 Oct 2026 12:00:00 +0000"),
        ],
    }
