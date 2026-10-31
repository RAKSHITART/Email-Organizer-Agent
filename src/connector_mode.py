"""Explicit selection between FIXTURE mode and REAL Gmail mode.

The two modes never mix: fixture mode never imports Google libraries or reads
any mailbox; real mode never reads fixtures. The returned label is shown on the
dashboard so fixture data can never be mistaken for live email.
"""

from __future__ import annotations

from pathlib import Path

from lab5_connector import GovernedConnector, LocalSession

PROFESSOR_EMAIL = "prof.bharati.patil.kletech@gmail.com"
FIXTURE_EMAIL = "faculty.example@university.test"
MODES = ("fixture", "gmail")


def build_connector(
    mode: str,
    expected_email: str = PROFESSOR_EMAIL,
    *,
    allow_drafts: bool = False,
    extra_scopes: tuple[str, ...] = (),
    client_secret_path: Path | None = None,
    token_path: Path | None = None,
):
    """Return (connector, mode_label). Raises on unknown mode or failed auth."""
    if mode == "fixture":
        connector = GovernedConnector(LocalSession(FIXTURE_EMAIL, "local-demo-token"))
        return connector, "FIXTURE MODE - sanitized sample data, NOT a live mailbox"
    if mode == "gmail":
        from gmail_auth import DEFAULT_CLIENT_SECRET_PATH, DEFAULT_TOKEN_PATH, DRAFT_SCOPES, READ_ONLY_SCOPES, authorize
        from gmail_connector import GmailConnector

        session = authorize(
            expected_email,
            scopes=(DRAFT_SCOPES if allow_drafts else READ_ONLY_SCOPES) + tuple(extra_scopes),
            client_secret_path=client_secret_path or DEFAULT_CLIENT_SECRET_PATH,
            token_path=token_path or DEFAULT_TOKEN_PATH,
        )
        connector = GmailConnector(session)
        suffix = "drafts enabled, sending disabled" if allow_drafts else "read-only"
        return connector, f"LIVE GMAIL - {session.verified_email} ({suffix})"
    raise ValueError(f"Unknown mode {mode!r}; choose one of {MODES}.")
