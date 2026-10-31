"""Owner-only local state: reply-later dates, edited drafts, saved-draft and calendar records.

Everything here is something the mailbox owner explicitly chose (a date she
picked, text she edited). Nothing is inferred. Stored as JSON under the user's
home folder (outside the repo); ``path=None`` keeps state in memory (fixture/tests).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

MAX_DRAFT_CHARS = 20_000


class OwnerStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._data: dict[str, dict] = {"reply_later": {}, "edits": {}, "drafts": {}, "events": {}, "done": {}, "archived": {}}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                for key in self._data:
                    self._data[key] = dict(loaded.get(key, {}))
            except (json.JSONDecodeError, OSError):
                pass  # unreadable state is treated as empty, never fabricated

    # reply later -------------------------------------------------------------
    def set_reply_later(self, thread_id: str, due_iso: str, message_id: str = "") -> None:
        date.fromisoformat(due_iso)  # ValueError on bad dates
        self._data["reply_later"][thread_id] = {"due": due_iso, "message_id": message_id}
        self._save()

    def clear_reply_later(self, thread_id: str) -> None:
        self._data["reply_later"].pop(thread_id, None)
        self._save()

    def reply_later(self) -> dict[str, dict]:
        return dict(self._data["reply_later"])

    # edited drafts -----------------------------------------------------------
    def set_edit(self, thread_id: str, message_id: str, text: str) -> None:
        if len(text) > MAX_DRAFT_CHARS:
            raise ValueError("Draft is too long.")
        self._data["edits"][thread_id] = {"message_id": message_id, "text": text}
        self._save()

    def get_edit(self, thread_id: str, message_id: str) -> str | None:
        entry = self._data["edits"].get(thread_id)
        return entry["text"] if entry and entry.get("message_id") == message_id else None

    def clear_edit(self, thread_id: str) -> None:
        self._data["edits"].pop(thread_id, None)
        self._save()

    # saved drafts / calendar events -----------------------------------------
    def set_draft(self, thread_id: str, message_id: str, draft_id: str, subject: str = "", to: str = "") -> None:
        from datetime import datetime, timezone
        self._data["drafts"][thread_id] = {"message_id": message_id, "draft_id": draft_id, "subject": subject,
                                            "to": to, "saved_at": datetime.now(timezone.utc).isoformat()}
        self._save()

    def draft_for(self, thread_id: str, message_id: str) -> str | None:
        entry = self._data["drafts"].get(thread_id)
        return entry["draft_id"] if entry and entry.get("message_id") == message_id else None

    def draft_log(self) -> list[dict]:
        return sorted(({"thread_id": t, **v} for t, v in self._data["drafts"].items()),
                      key=lambda e: e.get("saved_at", ""), reverse=True)

    # action items done (owner-confirmed only) ----------------------------------
    def mark_done(self, key: str) -> None:
        from datetime import datetime, timezone
        self._data["done"][key] = datetime.now(timezone.utc).isoformat()
        self._save()

    def unmark_done(self, key: str) -> None:
        self._data["done"].pop(key, None)
        self._save()

    def is_done(self, key: str) -> bool:
        return key in self._data["done"]

    # archived by the app (so it can be undone) ---------------------------------
    def set_archived(self, thread_id: str, subject: str, sender: str) -> None:
        self._data["archived"][thread_id] = {"subject": subject, "sender": sender}
        self._save()

    def clear_archived(self, thread_id: str) -> None:
        self._data["archived"].pop(thread_id, None)
        self._save()

    def archived(self) -> dict[str, dict]:
        return dict(self._data["archived"])

    def saved_draft_count(self) -> int:
        return len(self._data["drafts"])

    def set_event(self, thread_id: str, event_id: str, link: str, start_iso: str = "", summary: str = "") -> None:
        self._data["events"][thread_id] = {"event_id": event_id, "link": link, "start": start_iso, "summary": summary}
        self._save()

    def clear_event(self, thread_id: str) -> None:
        self._data["events"].pop(thread_id, None)
        self._save()

    def events(self) -> dict[str, dict]:
        return dict(self._data["events"])

    def clear_draft(self, thread_id: str) -> None:
        self._data["drafts"].pop(thread_id, None)
        self._save()

    def event_for(self, thread_id: str) -> dict | None:
        return self._data["events"].get(thread_id)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
