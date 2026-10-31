# Repository Guidelines

## Source of Truth and Scope

`Chapter3_Email_Organizer_Agent.docx` is the controlling worklet. Implement its 14 labs in order; do not replace its architecture, add unrequested technology, or skip an acceptance gate. The product triages email, drafts replies, extracts actions and meeting requests, and tracks follow-ups. It never sends or confirms anything autonomously.

## Required Architecture

The apniLeap AURA Coordinator owns run state, the locked plan, budgets, and audit log. The node graph is: Ingestion & Classification -> Priority & Urgency -> Draft Reply -> Action-Item Extraction -> Follow-up / Reminder -> Calendar & Task Sync -> Faculty Review -> Archive & Logging. The coordinator oversees every node; validation failure regenerates only the failing work within a bounded retry budget, then escalates to the mailbox owner.

All data-facing agents must use one authenticated, typed connector; no agent accesses sources directly. Its read surfaces are inbox/threads, contacts/prior replies, and calendar/task list. Keep run state separate from stored history.

## Workflow, Evidence, and Approval

Lock the blueprint before work begins. Ground every per-thread output in actual thread history and the owner's past replies, attaching an in-scope evidence record; reject ungrounded candidates. Validation is a hard gate before owner review: no send without approval, every draft cites thread context, priority follows owner rules, and mailbox data remains in the account. The mailbox owner may approve, edit, discard, or override and is final authority. Only approved results may be versioned and published with a full, rollback-capable audit trail.

Do not mark action items complete without faculty or task-owner confirmation, confirm meetings without faculty approval, or let a "reply later" item expire without a reminder. Enforce faculty-only inbox access.

## Lab Delivery Order

Lab order: L1 clarify-plan-revise loop (ask at least two questions for vague requests); L2 typed tools; L3 reusable typed planning/format skills; L4 state, tagged retrieval, and similarity retrieval; L5 connector; L6 governed runtime/audit/checkpoints; L7 executable graph with bounded retry; L8 parallel fan-out/merge; L9 evidence; L10 targeted repair; L11 acceptance-suite build gate; L12 confidentiality, approval, evaluation, versioning, rollback; L13 pluggable domain specialists; L14 deployed dashboard, audit trail, and real-case demo.

## Repository Practices

This checkout has no source, manifest, tests, or Git history. Do not infer frameworks, commands, tests, or conventions. When authorized, begin with Lab 1 only. Add project rules after they are chosen. Keep secrets and mailbox data out of version control; use sanitized fixtures and configuration examples.


## Real Gmail Mode (added after Lab 14)

Real mailbox access lives only in `src/gmail_auth.py`, `src/gmail_connector.py` and `src/calendar_connector.py`, behind the typed connector surface. Fixture and real mode are separate (`src/connector_mode.py`); real mode never falls back to fixtures. Sending is not implemented and must not be added: only Gmail drafts, only after owner approval and publication (`src/draft_publisher.py`); calendar events only after explicit owner confirmation, without attendees. The MCP server is read-only. Credentials live in `~/.email_organizer_agent/`, never in the repo. Tests stay credential-free (`tests/fake_gmail.py`).

Additional rules: archiving uses `gmail.modify` only to add/remove the INBOX label (reversible). Action items are never completed without owner confirmation. `gmail.compose` technically permits sending; the code must never call any send endpoint.
