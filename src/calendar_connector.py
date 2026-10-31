"""Google Calendar access: free/busy check and an owner-confirmed event, nothing more.

* Reads only free/busy (``calendar.freebusy``) for the primary calendar.
* Creates an event only when the caller passes an explicit owner confirmation.
* Events have NO attendees and ``sendUpdates="none"``, so Google emails nobody.
* Times are parsed only when the email states BOTH an explicit date with a year
  and an explicit clock time; otherwise there is no proposal (nothing is guessed).
  Numeric dates are read day-first (15/10/2026 = 15 October 2026).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_auth import CALENDAR_SCOPES, GmailAuthError, GmailSession

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_MINUTES = 30
REMINDER_EMAIL_MINUTES = 24 * 60
REMINDER_POPUP_MINUTES = 30
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class CalendarError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProposedTime:
    start: datetime
    end: datetime
    source_text: str  # the exact text the time was read from


_DAY_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MONTH_FIRST = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s?(am|pm)\b", re.I)
_H24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def parse_proposed_time(text: str, tz_name: str = DEFAULT_TIMEZONE,
                        minutes: int = DEFAULT_MINUTES) -> ProposedTime | None:
    day = _find_date(text)
    clock = _find_clock(text)
    if day is None or clock is None:
        return None
    try:
        zone = ZoneInfo(tz_name)
        start = datetime(day[0].year, day[0].month, day[0].day, clock[0].hour, clock[0].minute, tzinfo=zone)
    except (ValueError, KeyError, OSError):
        return None
    return ProposedTime(start, start + timedelta(minutes=minutes), f"{day[1]} {clock[1]}")


def _find_date(text: str):
    for m in _DAY_FIRST.finditer(text):
        month = _MONTHS.get(m.group(2)[:3].lower())
        if month:
            return _mk(int(m.group(3)), month, int(m.group(1)), m.group(0))
    for m in _MONTH_FIRST.finditer(text):
        month = _MONTHS.get(m.group(1)[:3].lower())
        if month:
            return _mk(int(m.group(3)), month, int(m.group(2)), m.group(0))
    m = _NUMERIC.search(text)
    if m:
        return _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)), m.group(0))
    m = _ISO.search(text)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(0))
    return None


def _mk(year, month, day, source):
    try:
        return datetime(year, month, day), source
    except ValueError:
        return None


def _find_clock(text: str):
    m = _AMPM.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if m.group(3).lower() == "pm" else 0)
        return datetime(2000, 1, 1, hour, minute), m.group(0)
    m = _H24.search(text)
    if m:
        return datetime(2000, 1, 1, int(m.group(1)), int(m.group(2))), m.group(0)
    return None


class GoogleCalendar:
    def __init__(self, session: GmailSession, *, service: Any | None = None,
                 timezone: str = DEFAULT_TIMEZONE, sleep: Callable[[float], None] = time.sleep) -> None:
        if not set(CALENDAR_SCOPES).issubset(set(session.scopes)):
            raise GmailAuthError("Calendar scopes not granted; run: python src\\gmail_cli.py verify --drafts --calendar")
        self._service = service or build("calendar", "v3", credentials=session.credentials, cache_discovery=False)
        self._timezone = timezone
        self._sleep = sleep

    @property
    def timezone(self) -> str:
        return self._timezone

    def busy_periods(self, proposed: ProposedTime) -> list[tuple[str, str]]:
        body = {"timeMin": proposed.start.isoformat(), "timeMax": proposed.end.isoformat(),
                "timeZone": self._timezone, "items": [{"id": "primary"}]}
        result = self._call(lambda: self._service.freebusy().query(body=body).execute())
        busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
        return [(b.get("start", ""), b.get("end", "")) for b in busy]

    def busy_between(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Busy blocks (no titles: free/busy scope only) between two aware datetimes."""
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "timeZone": self._timezone, "items": [{"id": "primary"}]}
        result = self._call(lambda: self._service.freebusy().query(body=body).execute())
        out = []
        for b in result.get("calendars", {}).get("primary", {}).get("busy", []):
            try:
                out.append((datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(start.tzinfo),
                            datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(start.tzinfo)))
            except (KeyError, ValueError):
                continue
        return sorted(out)

    def overview(self, rules, days: int = 7, count: int = 5, now: datetime | None = None) -> dict:
        """Next ``days`` days: busy blocks and proposed free slots inside working hours."""
        zone = ZoneInfo(self._timezone)
        now = (now or datetime.now(zone)).astimezone(zone)
        end = datetime.combine(now.date() + timedelta(days=days), dtime(0, 0), tzinfo=zone)
        busy = self.busy_between(now, end)
        return {"busy": busy, "slots": free_slots(busy, now, days, rules, zone, count)}

    def create_event(self, summary: str, proposed: ProposedTime, *, owner_confirmed: bool,
                     idempotency_key: str, description: str = "") -> dict:
        """Create the event on the owner's own calendar with reminders (email 1 day before,
        popup 30 minutes before). No attendees, so nobody else is notified."""
        if owner_confirmed is not True:
            raise CalendarError("Explicit owner confirmation is required to create a calendar event.")
        event_id = "eoa" + hashlib.sha1(idempotency_key.encode()).hexdigest()  # base32hex-safe, stable
        body = {"id": event_id, "summary": summary[:200], "description": description[:2000],
                "start": {"dateTime": proposed.start.isoformat(), "timeZone": self._timezone},
                "end": {"dateTime": proposed.end.isoformat(), "timeZone": self._timezone},
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "email", "minutes": REMINDER_EMAIL_MINUTES},
                    {"method": "popup", "minutes": REMINDER_POPUP_MINUTES}]}}
        try:
            created = self._call(lambda: self._service.events().insert(
                calendarId="primary", body=body, sendUpdates="none").execute())
        except CalendarError as error:
            if "409" in str(error):  # same key already created this event
                return {"id": event_id, "htmlLink": "", "duplicate": True}
            raise
        return {"id": created.get("id", event_id), "htmlLink": created.get("htmlLink", ""), "duplicate": False}

    def delete_event(self, event_id: str) -> None:
        """Remove an event this app created (undo). Nobody is notified."""
        try:
            self._call(lambda: self._service.events().delete(
                calendarId="primary", eventId=event_id, sendUpdates="none").execute())
        except CalendarError as error:
            if "404" not in str(error) and "410" not in str(error):
                raise

    def _call(self, request: Callable[[], Any]) -> Any:
        delay = 1.0
        for attempt in range(4):
            try:
                return request()
            except HttpError as error:
                status = getattr(error.resp, "status", 0)
                if status in (429, 500, 502, 503, 504) and attempt < 3:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if status in (401, 403):
                    raise GmailAuthError(f"Calendar rejected the request ({status}): enable the Calendar API / re-authorize.") from error
                raise CalendarError(f"Calendar API error {status}: {error}") from error
        raise CalendarError("Calendar retries exhausted.")


def free_slots(busy, now: datetime, days: int, rules, zone, count: int = 5) -> list[tuple[datetime, datetime]]:
    """Free slots on weekdays inside the faculty's working hours (from rules, default 10-17).

    Pure function: slots start on :00/:30 boundaries, never overlap a busy block, and are
    only PROPOSALS for the owner to offer; nothing is booked.
    """
    step = timedelta(minutes=rules.slot_minutes)
    slots: list[tuple[datetime, datetime]] = []
    for offset in range(days + 1):
        per_day = 0
        day = now.date() + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        cursor = datetime.combine(day, dtime(rules.work_start_hour, 0), tzinfo=zone)
        closing = datetime.combine(day, dtime(rules.work_end_hour % 24, 0), tzinfo=zone)
        while cursor + step <= closing:
            candidate = (cursor, cursor + step)
            if candidate[0] > now and not any(b[0] < candidate[1] and candidate[0] < b[1] for b in busy):
                slots.append(candidate)
                per_day += 1
                if len(slots) >= count:
                    return slots
                if per_day >= 2:  # spread proposals over several days
                    break
            cursor += step
    return slots
