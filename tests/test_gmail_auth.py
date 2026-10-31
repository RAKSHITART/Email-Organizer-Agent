"""Mocked OAuth tests: no real credentials, no network."""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmail_auth
from gmail_auth import (CALENDAR_SCOPES, DRAFT_SCOPES, READ_ONLY_SCOPES, GmailAccountMismatchError, GmailAuthError, authorize)
from fake_gmail import FakeGmail, OWNER, http_error


class Creds:
    def __init__(self, valid=True, expired=False, refresh_token="r", scopes=READ_ONLY_SCOPES, refresh_error=None):
        self.valid, self.expired, self.refresh_token = valid, expired, refresh_token
        self.scopes = list(scopes)
        self._err = refresh_error

    def refresh(self, request):
        if self._err:
            raise self._err
        self.valid, self.expired = True, False

    def to_json(self):
        return json.dumps({"token": "SECRET-TOKEN-VALUE"})


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.secret, self.token = self.dir / "credentials.json", self.dir / "token.json"

    def run_auth(self, fake, **kw):
        with mock.patch.object(gmail_auth, "build", return_value=fake):
            return authorize(OWNER, client_secret_path=self.secret, token_path=self.token, **kw)

    def flow(self, creds):
        f = mock.Mock(); f.run_local_server.return_value = creds
        return mock.patch.object(gmail_auth.InstalledAppFlow, "from_client_secrets_file", return_value=f)

    def test_missing_credentials_file(self):
        with self.assertRaisesRegex(GmailAuthError, "Missing OAuth client secret"):
            authorize(OWNER, client_secret_path=self.secret, token_path=self.token)

    def test_blank_expected_email(self):
        self.secret.write_text("{}")
        with self.assertRaises(GmailAuthError):
            authorize("  ", client_secret_path=self.secret, token_path=self.token)

    def test_success_persists_token_and_returns_identity(self):
        self.secret.write_text("{}")
        with self.flow(Creds()):
            session = self.run_auth(FakeGmail())
        self.assertEqual(session.verified_email, OWNER)
        self.assertTrue(self.token.exists())

    def test_wrong_account_rejected(self):
        self.secret.write_text("{}")
        with self.flow(Creds()), self.assertRaises(GmailAccountMismatchError):
            self.run_auth(FakeGmail(profile_email="someone.else@gmail.com"))

    def test_configured_address_alone_never_trusted(self):
        self.secret.write_text("{}"); self.token.write_text("{}")
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", return_value=Creds()):
            with self.assertRaises(GmailAccountMismatchError):
                self.run_auth(FakeGmail(profile_email="attacker@gmail.com"))

    def test_failed_authorization(self):
        self.secret.write_text("{}")
        f = mock.Mock(); f.run_local_server.side_effect = RuntimeError("denied")
        with mock.patch.object(gmail_auth.InstalledAppFlow, "from_client_secrets_file", return_value=f):
            with self.assertRaisesRegex(GmailAuthError, "consent failed"):
                self.run_auth(FakeGmail())

    def test_expired_token_refreshed(self):
        self.secret.write_text("{}"); self.token.write_text("{}")
        creds = Creds(valid=False, expired=True)
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", return_value=creds), mock.patch.object(gmail_auth, "Request"):
            self.run_auth(FakeGmail())
        self.assertTrue(creds.valid)

    def test_revoked_token(self):
        self.secret.write_text("{}"); self.token.write_text("{}")
        creds = Creds(valid=False, expired=True, refresh_error=RuntimeError("invalid_grant"))
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", return_value=creds), mock.patch.object(gmail_auth, "Request"):
            with self.assertRaisesRegex(GmailAuthError, "expired or revoked"):
                self.run_auth(FakeGmail())

    def test_insufficient_scope_forces_reconsent(self):
        self.secret.write_text("{}"); self.token.write_text("{}")
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", return_value=Creds(scopes=READ_ONLY_SCOPES)):
            with self.flow(Creds(scopes=DRAFT_SCOPES)) as f:
                session = self.run_auth(FakeGmail(), scopes=DRAFT_SCOPES)
        f.assert_called_once()
        self.assertEqual(session.scopes, DRAFT_SCOPES)

    def test_corrupt_token(self):
        self.secret.write_text("{}"); self.token.write_text("x")
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", side_effect=ValueError("bad")):
            with self.assertRaisesRegex(GmailAuthError, "corrupt"):
                self.run_auth(FakeGmail())

    def test_profile_api_error(self):
        self.secret.write_text("{}"); self.token.write_text("{}")
        fake = mock.Mock(); fake.users.return_value.getProfile.return_value.execute.side_effect = http_error(401)
        with mock.patch.object(gmail_auth.Credentials, "from_authorized_user_file", return_value=Creds()), mock.patch.object(gmail_auth, "build", return_value=fake):
            with self.assertRaises(GmailAuthError):
                authorize(OWNER, client_secret_path=self.secret, token_path=self.token)

    def test_scopes_never_include_gmail_send(self):
        allscopes = READ_ONLY_SCOPES + DRAFT_SCOPES + CALENDAR_SCOPES
        self.assertFalse(any(s.endswith("gmail.send") or s.endswith("mail.google.com/") or s.endswith("gmail.modify") for s in allscopes))
        self.assertEqual(READ_ONLY_SCOPES, ("https://www.googleapis.com/auth/gmail.readonly",))

    def test_token_value_not_in_errors(self):
        self.secret.write_text("{}")
        with self.flow(Creds()), self.assertRaises(GmailAccountMismatchError) as ctx:
            self.run_auth(FakeGmail(profile_email="x@gmail.com"))
        self.assertNotIn("SECRET-TOKEN-VALUE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
