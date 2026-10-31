"""Response-time and workload metrics computed only from real message timestamps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from statistics import median

OVERDUE_HOURS = 48  # a reply is "overdue" when the newest incoming mail has waited this long


@dataclass(frozen=True)
class Metrics:
    threads: int
    replied_pairs: int
    avg_response_hours: float | None
    median_response_hours: float | None
    awaiting_reply: int
    overdue: int
    overdue_thread_ids: tuple[str, ...]


def _parse(ts: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def compute_metrics(history, owner_email: str, now: datetime | None = None,
                    exclude_thread_ids: frozenset[str] = frozenset()) -> Metrics:
    now = now or datetime.now(timezone.utc)
    owner = owner_email.casefold()
    by_thread: dict[str, list] = {}
    for message in history:
        if _parse(message.timestamp) is not None:
            by_thread.setdefault(message.thread_id, []).append(message)

    gaps: list[float] = []
    awaiting = 0
    overdue_ids: list[str] = []
    for thread_id, messages in sorted(by_thread.items()):
        messages.sort(key=lambda m: _parse(m.timestamp))
        waiting_since: datetime | None = None
        for message in messages:
            when = _parse(message.timestamp)
            is_owner = parseaddr(message.sender)[1].casefold() == owner
            if is_owner and waiting_since is not None:
                gaps.append((when - waiting_since).total_seconds() / 3600)
                waiting_since = None
            elif not is_owner and waiting_since is None:
                waiting_since = when
        if waiting_since is not None and thread_id not in exclude_thread_ids:
            awaiting += 1
            if (now - waiting_since).total_seconds() / 3600 >= OVERDUE_HOURS:
                overdue_ids.append(thread_id)
    return Metrics(
        threads=len(by_thread),
        replied_pairs=len(gaps),
        avg_response_hours=round(sum(gaps) / len(gaps), 1) if gaps else None,
        median_response_hours=round(median(gaps), 1) if gaps else None,
        awaiting_reply=awaiting,
        overdue=len(overdue_ids),
        overdue_thread_ids=tuple(overdue_ids),
    )
