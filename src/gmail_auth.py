"""Real Google OAuth 2.0 (installed-app flow) authorization and identity check.

This module is the ONLY place that performs OAuth. It never fabricates a
credential, never trusts a configured email address, and always verifies the
*actually authenticated* Gmail profile against the mailbox owner this run is
supposed to operate on. Nothing here can send or confirm email; it only
proves who is signed in and hands back verified, scoped credentials.

Scopes start read-only (``gmail.readonly``). Draft creation requires the
caller to explicitly request ``DRAFT_SCOPES``, which re-triggers consent if
the stored token does not already carry that scope.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

READ_ONLY_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.readonly",)
DRAFT_SCOPES: tuple[str, ...] = READ_ONLY_SCOPES + (
    "https://www.googleapis.com/auth/gmail.compose",
)

CALENDAR_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar.freebusy",  # read busy/free only
    "https://www.googleapis.com/auth/calendar.events",    # create an event after owner confirmation
)

ARCHIVE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.modify",  # used ONLY to remove/restore the INBOX label
)

DEFAULT_CREDENTIALS_DIR = Path.home() / ".email_organizer_agent"
DEFAULT_CLIENT_SECRET_PATH = DEFAULT_CREDENTIALS_DIR / "credentials.json"
DEFAULT_TOKEN_PATH = DEFAULT_CREDENTIALS_DIR / "token.json"


class GmailAuthError(PermissionError):
    """Any OAuth failure: missing credentials file, expired/revoked token,
    insufficient scope, or a Google API error while verifying identity."""


class GmailAccountMismatchError(GmailAuthError):
    """The authenticated Gmail profile is not the required mailbox owner."""


@dataclass(frozen=True)
class GmailSession:
    """A verified, live Gmail OAuth session. Only ``authorize`` may construct one."""

    credentials: Credentials
    verified_email: str
    scopes: tuple[str, ...]


def authorize(
    expected_email: str,
    *,
    scopes: Iterable[str] = READ_ONLY_SCOPES,
    client_secret_path: Path = DEFAULT_CLIENT_SECRET_PATH,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> GmailSession:
    """Authorize (or refresh) a local OAuth session and verify its identity.

    Raises ``GmailAuthError`` for: missing client secret file, a corrupt
    stored token, a refresh/consent failure, or any Gmail API error while
    checking the profile. Raises ``GmailAccountMismatchError`` specifically
    when the signed-in account is not ``expected_email``. Never returns a
    session for the wrong account and never falls back to fixtures.
    """
    scopes = tuple(scopes)
    if not expected_email.strip():
        raise GmailAuthError("expected_email is required; refusing to authorize against no one.")
    if not client_secret_path.exists():
        raise GmailAuthError(
            f"Missing OAuth client secret at {client_secret_path}. Download it from "
            "Google Cloud Console (APIs & Services > Credentials > OAuth client ID, "
            "type 'Desktop app') and save it at that exact path. See README.md."
        )

    credentials = _load_or_refresh(scopes, client_secret_path, token_path)

    try:
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
    except HttpError as error:
        raise GmailAuthError(f"Gmail API profile check failed: {error}") from error

    authenticated_email = str(profile.get("emailAddress", "")).strip()
    if not authenticated_email:
        raise GmailAuthError("Gmail profile check returned no emailAddress; cannot verify identity.")
    if authenticated_email.casefold() != expected_email.strip().casefold():
        raise GmailAccountMismatchError(
            f"Authenticated Gmail account is '{authenticated_email}', but this run requires "
            f"'{expected_email}'. Sign out of the wrong Google account in the browser used for "
            f"consent, or delete {token_path} and re-run to choose the correct account."
        )
    return GmailSession(credentials=credentials, verified_email=authenticated_email, scopes=scopes)


def _load_or_refresh(scopes: tuple[str, ...], client_secret_path: Path, token_path: Path) -> Credentials:
    credentials: Credentials | None = None

    if token_path.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), list(scopes))
        except (ValueError, json.JSONDecodeError) as error:
            raise GmailAuthError(
                f"Stored token at {token_path} is corrupt or unreadable ({error}). "
                f"Delete it and re-run to re-authorize."
            ) from error

    if credentials is not None and not set(scopes).issubset(set(credentials.scopes or ())):
        # Stored token lacks a scope this call needs (e.g. draft-write after a
        # read-only session) -- force a fresh consent instead of silently
        # operating with insufficient permission.
        credentials = None

    if credentials is None or not credentials.valid:
        if credentials is not None and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception as error:  # noqa: BLE001 - normalize every refresh failure
                raise GmailAuthError(
                    f"Token refresh failed (expired or revoked): {error}. "
                    f"Delete {token_path} and re-run to re-authorize."
                ) from error
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), list(scopes))
                credentials = flow.run_local_server(port=0)
            except Exception as error:  # noqa: BLE001 - normalize every consent failure
                raise GmailAuthError(f"OAuth consent failed: {error}") from error
        _persist_token(credentials, token_path)

    return credentials


def _persist_token(credentials: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner read/write only
    except OSError:
        pass  # best-effort on platforms (e.g. some Windows filesystems) without POSIX modes
