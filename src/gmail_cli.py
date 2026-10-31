"""Small command-line helper for first-time authorization and account checks.

  python src/gmail_cli.py verify            read-only OAuth + identity check
  python src/gmail_cli.py verify --drafts   also request the draft-write scope
  python src/gmail_cli.py logout            delete the locally stored token

It prints the verified Gmail address and scopes -- never tokens or secrets.
"""

from __future__ import annotations

import argparse
import sys

from connector_mode import PROFESSOR_EMAIL
from gmail_auth import (
    ARCHIVE_SCOPES, CALENDAR_SCOPES, DEFAULT_TOKEN_PATH, DRAFT_SCOPES, READ_ONLY_SCOPES, GmailAuthError, authorize,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gmail authorization helper (never sends email).")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="authorize and verify the Gmail account")
    verify.add_argument("--drafts", action="store_true", help="request draft-write scope too")
    verify.add_argument("--calendar", action="store_true", help="also request Google Calendar scopes")
    verify.add_argument("--archive", action="store_true", help="also request permission to archive resolved threads")
    verify.add_argument("--account", default=PROFESSOR_EMAIL)
    sub.add_parser("logout", help="delete the stored local token")
    args = parser.parse_args(argv)

    if args.command == "logout":
        if DEFAULT_TOKEN_PATH.exists():
            DEFAULT_TOKEN_PATH.unlink()
            print(f"Deleted {DEFAULT_TOKEN_PATH}. Also revoke access at https://myaccount.google.com/permissions if desired.")
        else:
            print("No stored token found.")
        return 0

    try:
        session = authorize(args.account, scopes=(DRAFT_SCOPES if args.drafts else READ_ONLY_SCOPES) + (CALENDAR_SCOPES if args.calendar else ()) + (ARCHIVE_SCOPES if args.archive else ()))
    except GmailAuthError as error:
        print(f"AUTHORIZATION FAILED: {error}", file=sys.stderr)
        return 1
    print(f"Verified Gmail account: {session.verified_email}")
    print("Scopes: " + ", ".join(s.rsplit("/", 1)[-1] for s in session.scopes))
    print("Sending email is not implemented and cannot be performed by this app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
