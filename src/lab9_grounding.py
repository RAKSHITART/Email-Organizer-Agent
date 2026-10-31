"""Lab 9 connector-backed evidence grounding for per-thread Lab 8 candidates."""

from __future__ import annotations

from dataclasses import dataclass
import re

from lab3_skills import LockedPlan
from lab4_memory import StoredHistory
from lab5_connector import ConnectorInboxThread, GovernedConnector
from lab8_swarm import SwarmRun, WorkerResult


@dataclass(frozen=True)
class CandidateOutput:
    """A candidate whose factual claims must be grounded for one inbox thread."""

    thread_id: str
    claims: tuple[str, ...]
    locked_plan_id: str


@dataclass(frozen=True)
class EvidenceSource:
    """A traceable, in-scope source associated with exactly one candidate thread."""

    source_type: str
    source_id: str
    target_thread_id: str
    matching_tags: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class EvidenceRecord:
    """Evidence supporting every claim in one grounded candidate."""

    target_thread_id: str
    locked_plan_id: str
    sources: tuple[EvidenceSource, ...]
    supported_claims: tuple[str, ...]


@dataclass(frozen=True)
class GroundingResult:
    candidate: CandidateOutput
    status: str
    evidence: EvidenceRecord | None
    rejection_reason: str | None = None


class EvidenceGrounder:
    """Ground candidates solely through the existing governed connector."""

    def __init__(self, connector: GovernedConnector) -> None:
        self._connector = connector

    def ground(self, candidate: CandidateOutput) -> GroundingResult:
        """Return grounded only when every candidate claim is supported in scope."""
        target = self._target_thread(candidate.thread_id)
        if target is None:
            return GroundingResult(
                candidate,
                "rejected",
                None,
                "No sanitized inbox thread exists for this candidate.",
            )
        if not candidate.claims:
            return GroundingResult(candidate, "rejected", None, "Candidate has no claims to ground.")

        sources = self._sources_for_thread(target)
        evidence_tokens = _tokens(" ".join(source.excerpt for source in sources))
        unsupported = [claim for claim in candidate.claims if not _claim_is_supported(claim, evidence_tokens)]
        if unsupported:
            return GroundingResult(
                candidate,
                "rejected",
                None,
                "Unsupported claim(s): " + "; ".join(unsupported),
            )

        return GroundingResult(
            candidate,
            "grounded",
            EvidenceRecord(
                target_thread_id=candidate.thread_id,
                locked_plan_id=candidate.locked_plan_id,
                sources=tuple(sources),
                supported_claims=candidate.claims,
            ),
        )

    def ground_swarm(self, swarm: SwarmRun, locked_plan: LockedPlan) -> tuple[GroundingResult, ...]:
        """Ground each Lab 8 worker output independently, preserving merge order."""
        return tuple(
            self.ground(_candidate_from_worker(worker, locked_plan))
            for worker in swarm.worker_results
        )

    def _target_thread(self, thread_id: str) -> ConnectorInboxThread | None:
        return next(
            (
                thread
                for thread in self._connector.read_inbox_threads().inbox_threads
                if thread.thread_id == thread_id
            ),
            None,
        )

    def _sources_for_thread(self, target: ConnectorInboxThread) -> list[EvidenceSource]:
        target_tags = {tag.casefold() for tag in target.labels}
        sources = [
            EvidenceSource(
                source_type="inbox_thread",
                source_id=target.thread_id,
                target_thread_id=target.thread_id,
                matching_tags=tuple(sorted(target_tags)),
                excerpt=" ".join((target.subject, *target.labels)),
            )
        ]

        history = StoredHistory.from_connector(self._connector)
        real_ids = {m.thread_id: m.message_id for m in self._connector.read_inbox_threads().history_messages if m.message_id}
        for message in history.messages:
            if message.thread_id in real_ids:
                # Real mailbox mail: evidence is ONLY the target thread's own messages
                # (Gmail system labels such as INBOX are shared by every thread).
                if message.thread_id != target.thread_id:
                    continue
                sources.append(EvidenceSource(
                    source_type="thread_history",
                    source_id=message.thread_id,
                    target_thread_id=target.thread_id,
                    matching_tags=("same-thread",),
                    excerpt=" ".join((message.subject, message.body)),
                ))
                continue
            matching_tags = _matching_tags(target_tags, message.tags)
            if matching_tags:
                sources.append(
                    EvidenceSource(
                        source_type="thread_history",
                        source_id=message.thread_id,
                        target_thread_id=target.thread_id,
                        matching_tags=matching_tags,
                        excerpt=" ".join((message.subject, message.body, *message.tags)),
                    )
                )

        for reply in self._connector.read_contacts_prior_replies().prior_replies:
            matching_tags = _matching_tags(target_tags, reply.tags)
            if matching_tags:
                sources.append(
                    EvidenceSource(
                        source_type="owner_prior_reply",
                        source_id=reply.reply_id,
                        target_thread_id=target.thread_id,
                        matching_tags=matching_tags,
                        excerpt=" ".join((reply.body, *reply.tags)),
                    )
                )
        return sources


def _candidate_from_worker(worker: WorkerResult, locked_plan: LockedPlan) -> CandidateOutput:
    claims = worker.claims or tuple(claim for claim in (worker.draft_reply, worker.action_item) if claim)
    return CandidateOutput(worker.thread_id, claims, locked_plan.blueprint_id)


def _matching_tags(target_tags: set[str], source_tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(target_tags & {tag.casefold() for tag in source_tags}))


def _claim_is_supported(claim: str, evidence_tokens: frozenset[str]) -> bool:
    semantic_tokens = _semantic_tokens(claim)
    return bool(semantic_tokens) and semantic_tokens <= evidence_tokens


def _semantic_tokens(text: str) -> frozenset[str]:
    ignored = {
        "a", "an", "and", "as", "draft", "for", "from", "in", "is", "message", "of", "on",
        "or", "proposed", "reply", "request", "review", "sanitized", "the", "this", "to", "topic",
    }
    return frozenset(token for token in _tokens(text) if token not in ignored)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.casefold()))
