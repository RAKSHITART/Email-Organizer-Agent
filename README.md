# Email Organizer Agent (Chapter 3 worklet)

A Gmail-style dashboard that triages the faculty inbox, drafts grounded replies,
extracts action items, tracks follow-ups and meeting requests, and puts every
result in front of the mailbox owner for approval.
**It never sends email.** The most it does is save a Gmail **draft** after the owner approves.

## Run it (Windows PowerShell)

```powershell
cd path\to\Email-Organizer-Agent
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\gmail_cli.py verify --drafts --calendar --archive   # one-time consent (browser opens)
python src\lab14_dashboard.py --mode gmail --drafts --calendar --archive --poll 10 --serve
```

Open <http://127.0.0.1:8765>. Banner must read `LIVE GMAIL - prof.bharati.patil.kletech@gmail.com (...)`.
Every flag is optional: `--drafts` (save drafts), `--calendar` (availability + events), `--archive` (archive resolved threads, reversible), `--poll N` (auto-refresh every N minutes). Leave a flag out to run with fewer permissions. Fixture demo: `python src\lab14_dashboard.py --serve`.

## What you get

| Chapter-3 feature | Where |
|---|---|
| Triaged inbox ranked by urgency | Inbox tabs (Deadline / Meeting / Student / Admin / Routine / Automated) |
| Draft-reply queue + owner approval | **Draft replies** view; open a thread, edit, **Approve & save to Gmail drafts** |
| Standard-response library (12 starters) | Used inside drafts; edit via `~\.email_organizer_agent\standard_responses.json` |
| Action-item list | **Action items** view |
| Follow-up digest, "reply later" | **Reply later** view; set a date inside any thread |
| Response-time metrics | **Insights** view (computed from real timestamps) |
| Meeting requests + calendar | **Meeting requests** view; availability check and *owner-confirmed* event (`--calendar`) |
| Sender category (student/admin/collaborator/vendor/other) | Thread page; set your domain lists in `rules.json` (below), otherwise guessed from content and labelled as such |
| Urgency: deadline mentions, escalation language, priority markers | Priority 95 for "urgent/ASAP/escalation", `Importance: high`, or your own phrases; uncertain cases show **Check urgency** |
| Action items + your own commitments | **Action items** view; completion only with your confirmation |
| Propose calendar slots | **Calendar** view and "Add my free slots to the reply" on meeting threads |
| Archive resolved items | "Archive thread" (needs `--archive`), undo in **Archived** view |
| Reply log, approval history | **Audit trail** view |
| Runs continuously | `--poll N`; each refresh is audited with its trigger |
| Prompt command | `python src\email_agent.py organize-inbox --priority-rules "..." --standard-replies faq.json` (report only) |
| MCP-style governed connector | `src\gmail_connector.py`; plus a read-only MCP server (below) |

"Approve & save" performs three recorded steps: owner approval, publication (versioning), Gmail draft.
Saving twice for the same incoming message gives one draft. Rollback removes draft permission.

## Google Cloud setup (one time)

1. <https://console.cloud.google.com/> -> new project `email-organizer-agent`.
2. **APIs & Services -> Library**: enable **Gmail API** and (for `--calendar`) **Google Calendar API**.
3. **OAuth consent screen** (External, Testing). Scopes: `gmail.readonly`, `gmail.compose`, and for calendar `calendar.freebusy`, `calendar.events`. **Test users**: add `prof.bharati.patil.kletech@gmail.com`.
4. **Credentials -> Create credentials -> OAuth client ID -> Desktop app -> Download JSON.**
5. Save it as `C:\Users\<you>\.email_organizer_agent\credentials.json`. Never share it or `token.json`.

Expect "Google hasn't verified this app": **Advanced -> Go to ... (unsafe)**. Tokens expire after 7 days in Testing mode:
run `python src\gmail_cli.py logout` then `verify` again. Adding calendar later triggers a new consent screen automatically.

## Calendar rules

The app only proposes a time when the email states an explicit **date with year** and a **clock time**
(numeric dates are read day-first, 15/10/2026 = 15 October). "Tuesday at 3 pm" produces no proposal; nothing is guessed.
Default timezone `Asia/Kolkata` (change with `--timezone`). Events have **no attendees** and send **no invitations**.
Events are created only after you tick the confirmation box.

## MCP server (read-only)

```powershell
python src\mcp_server.py
```
Stdio MCP server with three tools: `list_inbox`, `get_thread`, `triage_summary`. It has no draft, send, delete or calendar tools.
Client config example (any MCP client):
```json
{"mcpServers": {"email-organizer": {"command": "C:\\path\\to\\.venv\\Scripts\\python.exe", "args": ["C:\\path\\to\\src\\mcp_server.py"]}}}
```
Email content in tool results is flagged as untrusted data.

## Deterministic behaviour (no AI model is called)

Rules live in `src\mailbox_nodes.py`: keyword classification (automated > deadline > meeting > student > admin > routine),
priority 100/90/70/50/30/10, drafts = greeting + matching standard response + quoted facts. Dates and requests are quoted verbatim, never invented.
Email text is data only. An accuracy limit: a message with none of the keywords is `routine`; `no-reply` senders and mailing lists are `automated`.

## Tests (no credentials needed)

```powershell
python -m unittest discover -s tests
```

## Troubleshooting

| Message | Fix |
|---|---|
| `Missing OAuth client secret` | Put `credentials.json` at the path above. |
| `access_denied` | Add the account as a **Test user**. |
| `GmailAccountMismatchError` | `python src\gmail_cli.py logout`, then `verify` and pick the right account. |
| `Token refresh failed` | `logout` then `verify` again. |
| `requires the gmail.compose scope` | Start with `--drafts`; run `verify --drafts`. |
| `Calendar scopes not granted` | Run `verify --drafts --calendar` and enable the Calendar API. |
| `Calendar rejected the request (403)` | Calendar API not enabled in the Cloud project. |
| Port busy | Add `--port 8766`. |


## Optional rules file

`C:\Users\<you>\.email_organizer_agent\rules.json` (every key optional):
```json
{"priority_phrases": ["exam season", "NAAC"], "student_domains": ["student.kletech.ac.in"],
 "admin_domains": ["kletech.ac.in"], "collaborator_domains": [], "vendor_domains": [],
 "working_hours": {"start": 10, "end": 17}, "slot_minutes": 30}
```
Working hours only affect proposed calendar slots (weekdays). The **Rules & replies** view shows what is active.

## About permissions (read this)

`gmail.compose` is Google's draft permission, and Google defines it as "manage drafts **and send** emails". There is no
draft-only Gmail permission. This app never calls any send endpoint (tests scan the source for that), but the permission
itself would technically allow it, so protect `token.json` and revoke access at <https://myaccount.google.com/permissions> when finished.
`gmail.modify` (`--archive`) is used only to remove or restore the INBOX label.


## How a meeting request flows (agentic, but you stay in control)

1. A mail with a full date and time arrives, e.g. "meet on 5 November 2026 at 3 pm".
2. The agent **automatically** checks your Google Calendar (free/busy only) and writes the reply for you:
   *free* -> it confirms the time in the draft; *busy* -> it says so and offers times you are free (taken from **your** calendar, never from the sender);
   *no time stated* -> it offers your free times; *time already passed* -> nothing is confirmed.
3. You read the draft and click **Approve reply & schedule meeting**. That one click saves the reply to Gmail Drafts **and**
   puts the meeting on your calendar with reminders: **email 1 day before** and **popup 30 minutes before** (Google Calendar delivers them to you).
   Nobody is invited and Google sends nobody anything. Before you approve, nothing is written to your calendar.
4. You send the reply yourself from Gmail. **Undo** deletes the draft and the event.

## Extras

Today's briefing strip, one-line thread summaries, **Approve & save all ready replies** (Draft replies view, one click), Undo, upcoming-meetings list,
auto-refresh (`--poll`), read-only MCP server, rules file, standard replies, audit trail with reply log.
