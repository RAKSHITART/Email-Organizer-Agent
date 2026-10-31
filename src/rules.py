"""Faculty-defined rules (proposal: "a small set of faculty-defined rules").

Optional file ``~/.email_organizer_agent/rules.json``; every key is optional:
{
  "priority_phrases": ["exam season", "NAAC"],          # raise matching mail to priority 95
  "student_domains": ["student.kletech.ac.in"],
  "admin_domains": ["kletech.ac.in"],
  "collaborator_domains": ["iisc.ac.in"],
  "vendor_domains": ["vendor.example"],
  "working_hours": {"start": 10, "end": 17},            # used only to propose free calendar slots
  "slot_minutes": 30
}
Nothing here is guessed: with no file, sender category falls back to content keywords
and slots use the documented defaults below.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

USER_RULES_PATH = Path.home() / ".email_organizer_agent" / "rules.json"


@dataclass(frozen=True)
class Rules:
    priority_phrases: tuple[str, ...] = ()
    student_domains: tuple[str, ...] = ()
    admin_domains: tuple[str, ...] = ()
    collaborator_domains: tuple[str, ...] = ()
    vendor_domains: tuple[str, ...] = ()
    work_start_hour: int = 10
    work_end_hour: int = 17
    slot_minutes: int = 30


def load_rules(path: Path | None = None) -> Rules:
    path = path or USER_RULES_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        hours = raw.get("working_hours", {})
        def tup(key):
            return tuple(str(v).casefold().strip() for v in raw.get(key, []) if str(v).strip())
        start, end = int(hours.get("start", 10)), int(hours.get("end", 17))
        minutes = int(raw.get("slot_minutes", 30))
        if not (0 <= start < end <= 24 and 5 <= minutes <= 240):
            return Rules()
        return Rules(tup("priority_phrases"), tup("student_domains"), tup("admin_domains"),
                     tup("collaborator_domains"), tup("vendor_domains"), start, end, minutes)
    except (OSError, ValueError, TypeError, AttributeError):
        return Rules()
