"""Fixture-mode HTTP checks (no credentials): Gmail-style page, CSRF token required on POST."""
import socket, sys, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab14_dashboard import create_demo, serve


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class DashboardHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        threading.Thread(target=serve, args=(create_demo(), cls.port), daemon=True).start()
        time.sleep(0.4)
        cls.base = f"http://127.0.0.1:{cls.port}/"

    def test_get_shows_mode_banner_and_token(self):
        page = urllib.request.urlopen(self.base).read().decode()
        self.assertIn("FIXTURE MODE", page)
        self.assertIn('name="csrf"', page)

    def test_post_without_token_is_forbidden(self):
        data = urllib.parse.urlencode({"action": "approve", "thread_id": "sample-thread-001"}).encode()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(urllib.request.Request(self.base, data=data))
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
