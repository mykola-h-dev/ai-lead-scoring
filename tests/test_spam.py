"""Spam protection: honeypot, disposable emails, per-IP rate limit."""

import os
import pathlib
import tempfile
import unittest

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app.notify import DemoNotifier  # noqa: E402
from app.pipeline import LeadPipeline  # noqa: E402
from app.sheets import CsvSheet  # noqa: E402
from app.spam import RateLimiter, is_disposable, load_domains  # noqa: E402

LEAD = {"name": "Jordan Example", "email": "jordan@example.com", "service": "Lawn care",
        "message": "Weekly lawn care for a small back garden."}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class DisposableTests(unittest.TestCase):
    def test_known_and_sub_domains(self):
        self.assertTrue(is_disposable("x@mailinator.com"))
        self.assertTrue(is_disposable("x@inbox.mailinator.com"))
        self.assertFalse(is_disposable("x@example.com"))
        self.assertFalse(is_disposable("x@notmailinator.com"))

    def test_extra_domains_file(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "extra.txt"
        path.write_text("# comment\nthrowaway.example\n", encoding="utf-8")
        extra = load_domains(path)
        self.assertTrue(is_disposable("a@throwaway.example", extra))
        self.assertEqual(load_domains(None), frozenset())


class RateLimiterTests(unittest.TestCase):
    def test_sliding_window(self):
        clock = FakeClock()
        limiter = RateLimiter(max_events=2, window=60, clock=clock)
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertFalse(limiter.allow("1.2.3.4"))
        self.assertTrue(limiter.allow("5.6.7.8"))  # other visitors unaffected
        clock.now = 61
        self.assertTrue(limiter.allow("1.2.3.4"))

    def test_zero_disables(self):
        limiter = RateLimiter(max_events=0)
        self.assertTrue(all(limiter.allow("x") for _ in range(50)))


class PipelineSpamTests(unittest.TestCase):
    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.csv = tmp / "leads.csv"
        self.pipeline = LeadPipeline(CsvSheet(self.csv), [DemoNotifier(tmp / "outbox.log")], retry_delay=0,
                                     rate_limiter=RateLimiter(2, 600), use_ai=False)

    def test_disposable_email_rejected(self):
        status, body = self.pipeline.process({**LEAD, "email": "bot@yopmail.com"}, client_ip="9.9.9.9")
        self.assertEqual(status, 400)
        self.assertIn("permanent email", body["errors"]["email"])
        self.assertFalse(self.csv.exists())

    def test_honeypot_saves_nothing(self):
        status, _ = self.pipeline.process({**LEAD, "website": "http://spam.example"})
        self.assertEqual(status, 400)
        self.assertFalse(self.csv.exists())

    def test_rate_limit_returns_429(self):
        statuses = [self.pipeline.process({**LEAD, "email": f"p{i}@example.com"}, client_ip="9.9.9.9")[0]
                    for i in range(3)]
        self.assertEqual(statuses, [201, 201, 429])


if __name__ == "__main__":
    unittest.main()
