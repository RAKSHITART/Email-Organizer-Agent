"""Faculty standard-response library (chapter 3: 10-15 common questions).

Starter entries are neutral, non-committal templates: they never promise dates,
marks, approvals or meetings. The faculty member should edit them. To use her own
library, create ``~/.email_organizer_agent/standard_responses.json`` as a list of
{"id","title","keywords":[...],"body"} objects; it replaces the starters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

USER_LIBRARY_PATH = Path.home() / ".email_organizer_agent" / "standard_responses.json"


@dataclass(frozen=True)
class StandardResponse:
    id: str
    title: str
    keywords: tuple[str, ...]
    body: str


def _r(i, title, keywords, body):
    return StandardResponse(i, title, tuple(keywords), body)


STARTER_LIBRARY: tuple[StandardResponse, ...] = (
    _r("extension", "Assignment / deadline extension request", ["extension", "extend", "more time", "postpone"],
       "I have received your request for additional time. I will review it and let you know my decision."),
    _r("submission", "Submission confirmation", ["submitted", "submission", "uploaded", "attached"],
       "I have received your submission and will review it."),
    _r("marks", "Marks / results query", ["marks", "result", "grade", "score", "revaluation"],
       "I have noted your query about marks. I will check the records and respond."),
    _r("attendance", "Attendance query", ["attendance", "absent", "shortage"],
       "I have noted your attendance query. I will check the attendance records and respond."),
    _r("meeting", "Meeting request", ["meeting", "meet", "appointment", "discuss in person"],
       "I have noted your meeting request."),
    _r("project", "Project guidance", ["project", "guide", "guidance", "proposal", "thesis"],
       "I have noted your project query. I will review it and get back to you."),
    _r("leave", "Leave request", ["leave", "medical", "permission"],
       "I have received your request. I will review it and respond."),
    _r("recommendation", "Recommendation letter request", ["recommendation", "reference letter", "lor"],
       "I have received your request for a recommendation letter. I will review the details and respond."),
    _r("internship", "Internship / NOC request", ["internship", "noc", "no objection"],
       "I have noted your request. I will review it and respond."),
    _r("exam", "Exam schedule / syllabus query", ["syllabus", "exam schedule", "timetable", "viva"],
       "I have noted your query. I will check the details and respond."),
    _r("doubt", "Doubt / clarification", ["doubt", "clarification", "clarify", "question", "explain"],
       "I have noted your question and will respond with a clarification."),
    _r("thanks", "Thank-you / acknowledgement", ["thank you", "thanks", "grateful"],
       "Thank you for your message."),
)


def load_library(path: Path | None = None) -> tuple[StandardResponse, ...]:
    """User library if present and valid; otherwise the starter templates."""
    path = path or USER_LIBRARY_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = tuple(
            StandardResponse(str(e["id"]), str(e["title"]),
                             tuple(str(k).casefold() for k in e["keywords"]), str(e["body"]))
            for e in raw
        )
        return entries or STARTER_LIBRARY
    except (OSError, ValueError, KeyError, TypeError):
        return STARTER_LIBRARY


def match(library: tuple[StandardResponse, ...], text: str) -> StandardResponse | None:
    """Deterministic: the entry with most keyword hits wins; ties go to library order."""
    lowered = text.casefold()
    best, best_hits = None, 0
    for entry in library:
        hits = sum(1 for k in entry.keywords if re.search(r"\b" + re.escape(k.casefold()) + r"\b", lowered))
        if hits > best_hits:
            best, best_hits = entry, hits
    return best
