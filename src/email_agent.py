"""Command-line form of the proposal's prompt command.

  python src/email_agent.py organize-inbox --folders "inbox" ^
      --priority-rules "exam season, NAAC" --standard-replies faq_library.json ^
      --outputs "triaged list, draft replies, action items, meeting requests" ^
      --constraints "no auto-send, faculty approval required, audit log of all replies"

Prints a report only. It reads the inbox (read-only scope), saves nothing to Gmail and
cannot send: the guardrails are enforced in code regardless of --constraints.
--priority-rules phrases are matched literally in email text and lift matches to priority 95.
Only the INBOX folder is read. --standard-replies takes the JSON format described in response_library.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from connector_mode import PROFESSOR_EMAIL, build_connector
from lab3_skills import PlanInputs, plan
from lab8_swarm import ParallelSwarmController
from lab9_grounding import EvidenceGrounder
from mailbox_nodes import analyze, make_graph_factory
from response_library import StandardResponse, load_library
from rules import Rules, load_rules

OUTPUTS = ("triaged list", "draft replies", "action items", "meeting requests")


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def run_report(connector, rules: Rules, library, outputs=OUTPUTS) -> str:
    live_plan = plan(PlanInputs("Organize inbox", "Authenticated INBOX", "Deadlines first", ("report",)))
    swarm = ParallelSwarmController(connector, live_plan, max_workers=2,
                                    graph_factory=make_graph_factory(rules, library)).run_parallel()
    grounded = {r.candidate.thread_id: r for r in EvidenceGrounder(connector).ground_swarm(swarm, live_plan)}
    owner = connector.authenticated_principal.casefold()
    snapshot = connector.read_inbox_threads()
    info = {t.thread_id: t for t in snapshot.inbox_threads}
    ranked = sorted(swarm.worker_results, key=lambda w: -(w.priority or 0))
    lines = [f"Account: {connector.authenticated_principal}",
             "Guardrails enforced: no auto-send, faculty approval required, audit log in the dashboard."]
    if "triaged list" in outputs:
        lines.append("\n== Triaged list ==")
        for w in ranked:
            t = info[w.thread_id]
            found = analyze(t, [m for m in snapshot.history_messages if m.thread_id == w.thread_id], owner, rules, w.classification)
            flag = " [check urgency]" if found.uncertain else ""
            lines.append(f"{w.priority:>3}  {w.classification:<9} {found.sender_category:<12} {t.sender} | {t.subject}{flag}")
    if "draft replies" in outputs:
        lines.append("\n== Draft replies (review queue; nothing is saved or sent) ==")
        for w in ranked:
            if w.draft_reply and grounded[w.thread_id].status == "grounded":
                lines.append(f"--- {info[w.thread_id].subject}\n{w.draft_reply}")
    if "action items" in outputs:
        lines.append("\n== Action items ==")
        for w in ranked:
            for item in analyze(info[w.thread_id], [m for m in snapshot.history_messages if m.thread_id == w.thread_id],
                                owner, rules, w.classification).action_items:
                lines.append(f"- [{info[w.thread_id].subject}] {item}")
    if "meeting requests" in outputs:
        lines.append("\n== Meeting requests (unconfirmed) ==")
        for w in ranked:
            for proposal in w.calendar_proposals if w.classification == "meeting" else ():
                lines.append(f"- {proposal}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="email-agent", description="Read-only inbox report; never sends.")
    sub = parser.add_subparsers(dest="command", required=True)
    org = sub.add_parser("organize-inbox")
    org.add_argument("--folders", default="inbox")
    org.add_argument("--priority-rules", default="")
    org.add_argument("--standard-replies", default="")
    org.add_argument("--outputs", default=", ".join(OUTPUTS))
    org.add_argument("--constraints", default="no auto-send, faculty approval required, audit log of all replies")
    org.add_argument("--account", default=PROFESSOR_EMAIL)
    args = parser.parse_args(argv)

    if [f for f in _split(args.folders) if f.casefold() != "inbox"]:
        print("Note: only the INBOX folder is read; other folders are ignored.", file=sys.stderr)
    outputs = tuple(o for o in (x.casefold() for x in _split(args.outputs)) if o in OUTPUTS)
    rules = load_rules()
    extra = tuple(p.casefold() for p in _split(args.priority_rules))
    rules = replace(rules, priority_phrases=rules.priority_phrases + extra)
    library = load_library(Path(args.standard_replies)) if args.standard_replies else load_library()
    connector, label = build_connector("gmail", args.account)
    print(label, file=sys.stderr)
    print(run_report(connector, rules, library, outputs or OUTPUTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
