import io, sys, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_cli
from gmail_auth import GmailAuthError, GmailSession, READ_ONLY_SCOPES
from fake_gmail import OWNER


class CliTests(unittest.TestCase):
    def test_verify_prints_identity(self):
        out = io.StringIO()
        with mock.patch.object(gmail_cli, "authorize", return_value=GmailSession(object(), OWNER, READ_ONLY_SCOPES)), redirect_stdout(out):
            self.assertEqual(gmail_cli.main(["verify"]), 0)
        self.assertIn(OWNER, out.getvalue())

    def test_failed_verify_nonzero(self):
        err = io.StringIO()
        with mock.patch.object(gmail_cli, "authorize", side_effect=GmailAuthError("nope")), redirect_stderr(err):
            self.assertEqual(gmail_cli.main(["verify"]), 1)


if __name__ == "__main__":
    unittest.main()
