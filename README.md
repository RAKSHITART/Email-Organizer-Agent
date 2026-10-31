# Email Organizer Agent

An agentic AI assistant that runs on a real Gmail inbox. It triages incoming mail, writes grounded replies,
extracts action items, checks your Google Calendar for meeting requests, and asks for **one approval click**
before saving anything. **It never sends email.**

Tested live against a real Google account: inbox reading, draft replies saved to Gmail, Google Calendar
scheduling with reminders, thread archiving, and the MCP server.

## What it does

| Stage | The agent does this automatically | You do this |
|---|---|---|
| **Triage** | Ranks every thread: deadline, meeting, student, admin, routine, automated. Detects urgency wording and priority headers, and sets aside newsletters and security alerts. | Look at the ranked inbox and the daily briefing. |
| **Reply** | Writes a draft that quotes only facts found in the email, using your standard replies. | Edit if you want, click **Approve**. |
| **Save** | Saves the reply as a **Gmail draft** in the correct thread, with the right recipient and reply headers. | Press Send yourself in Gmail. |
| **Meetings** | Reads the stated date and time, checks free/busy on your calendar, then confirms the time or offers your free times in the draft. | Approve. The meeting is added with reminders (email 1 day before, popup 30 minutes before). Nobody is invited. |
| **Follow-up** | Tracks unanswered mail, overdue replies, action items and your own commitments. | Set reply-later dates; mark items done. |

Also included: a Gmail-style dashboard, one-line thread summaries, bulk approval, undo, an audit trail with
reply log, response-time insights, optional auto-refresh, and a read-only MCP server.

## Design

- **Governed connector.** All mailbox and calendar access goes through one typed connector. Fixture mode and live Gmail mode are strictly separate, and a failed login never falls back to sample data.
- **Node graph with parallel workers.** Each thread runs through its own pipeline (classify, prioritise, draft, extract actions, follow-up, calendar), and threads are processed in parallel and isolated from each other.
- **Evidence grounding.** A reply is only offered if every fact in it is supported by that thread's own messages.
- **Approval, versioning and rollback.** Saving a draft needs an explicit owner decision, is versioned, and can be rolled back or undone.
- **Acceptance gate.** The live mode starts only after the built-in acceptance suite passes.
- **Deterministic rules, no language model.** Classification and drafting are fixed rules, so behaviour is reproducible and auditable.

## Safety rules (enforced in code and tests)

- No email is ever sent. No code path calls a Gmail send endpoint, and a test scans the source for it.
- A draft is saved only after your approval, and only if it is grounded in the email.
- A calendar event is created only after you approve the reply, never for a time that has already passed, and never with attendees.
- Email text is untrusted data and can never instruct the agent.
- Every decision, draft and calendar action is written to an audit trail.
- The signed-in Gmail address is verified with Google, not trusted from configuration.

**Permissions note:** Google's `gmail.compose` scope (needed for drafts) technically also allows sending. There is no
draft-only scope. This app never uses that ability. Keep `token.json` private and revoke access at
<https://myaccount.google.com/permissions> when you are done.

## Quick start (Windows PowerShell)

```powershell
git clone https://github.com/<your-username>/<repo>.git
cd <repo>
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt

python src\lab14_dashboard.py --serve  # demo with sample data, no Google account needed
```
Open <http://127.0.0.1:8765>. The banner reads **FIXTURE MODE**.

## Connect your Gmail

### 1. Google Cloud setup (one time)
1. Create a project at <https://console.cloud.google.com/>.
2. **APIs & Services > Library:** enable **Gmail API** and **Google Calendar API**.
3. **OAuth consent screen:** External, Testing. Add the scopes `gmail.readonly`, `gmail.compose`, `gmail.modify`, `calendar.freebusy`, `calendar.events`. Add your Gmail address under **Test users**.
4. **Credentials > Create credentials > OAuth client ID > Desktop app**, then download the JSON.
5. Save it as `C:\Users\<you>\.email_organizer_agent\credentials.json` (outside the repo).

Google shows "hasn't verified this app": choose **Advanced > Go to ... (unsafe)**. Tokens last 7 days in Testing mode; run `logout`, then `verify` again.

### 2. Choose the mailbox
The expected account defaults to `PROFESSOR_EMAIL` in `src/connector_mode.py`. Change it there, or pass `--account you@gmail.com`.

### 3. Authorize and run
```powershell
python src\gmail_cli.py verify --account you@gmail.com --drafts --calendar --archive
python src\lab14_dashboard.py --mode gmail --account you@gmail.com --drafts --calendar --archive --poll 10 --serve
```
The banner should read `LIVE GMAIL - you@gmail.com (...)`.

| Flag | Effect |
|---|---|
| `--drafts` | Allows saving approved replies as Gmail drafts |
| `--calendar` | Availability check, and approved meetings added to your calendar |
| `--archive` | Archive resolved threads (reversible) |
| `--poll N` | Refresh the inbox every N minutes |
| `--timezone` | IANA timezone for events (default `Asia/Kolkata`) |

Other commands:
```powershell
python src\email_agent.py organize-inbox --outputs "triaged list, draft replies"   # read-only report
python src\mcp_server.py                                                           # read-only MCP server
python src\gmail_cli.py logout                                                     # delete the local token
```

## Configuration (optional)

- `~\.email_organizer_agent\rules.json`: priority phrases, student/admin/collaborator/vendor domains, working hours.
- `~\.email_organizer_agent\standard_responses.json`: your own reply library (replaces the 12 starter replies).

Both live in your home folder and are never committed.

## Tests

No credentials needed; Gmail and Calendar are replaced by in-memory fakes.
```powershell
python -m unittest discover -s tests
```

## Project layout

```
src/
  gmail_auth.py          OAuth and account verification
  gmail_connector.py     the only code that talks to Gmail
  calendar_connector.py  free/busy, confirmed events, reminders
  mailbox_nodes.py       classification, priority, drafts, action items
  draft_publisher.py     approval-gated draft saving
  lab14_dashboard.py     dashboard server and owner actions
  gmail_ui.py            Gmail-style web interface
  mcp_server.py          read-only MCP server
  email_agent.py         command-line report
  lab1_*.py ... lab13_*.py   agent, memory, graph, swarm, grounding, governance modules
tests/                   fakes and all test suites
fixtures/                sample data for demo mode
```

## Limitations

- Rules are tuned for faculty mail in English; other roles need their own rules and reply library.
- Only the INBOX is read.
- An email with none of the keywords is classed as routine.
- Drafts use your standard replies, not the tone of your past mail.

## Privacy

Mail is processed on your own machine. The only network traffic is to Google's Gmail and Calendar APIs.
Never commit `credentials.json`, `token.json` or anything under `.email_organizer_agent`.

## License

MIT. See [LICENSE](LICENSE).
