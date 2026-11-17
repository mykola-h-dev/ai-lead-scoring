"""Offline tests: python -m unittest discover tests

Includes a fake Apps Script web app running on localhost, so the Google Sheet
connector and the retry logic are exercised without any Google account.
"""

import json
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.notify import DemoNotifier
from app.pipeline import LeadPipeline
from app.sheets import AppsScriptSheet, CsvSheet

GOOD_LEAD = {"name": "Jordan Example", "email": "Jordan@Example.com", "phone": "+1 555 0100",
             "service": "Lawn care", "message": "Weekly lawn care for a small back garden."}


class FakeAppsScript(BaseHTTPRequestHandler):
    """Mimics apps_script/Code.gs; fails the first `fail_times` requests with HTTP 500."""
    emails, fail_times, calls = set(), 0, 0

    def do_POST(self):
        cls = type(self)
        cls.calls += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if cls.calls <= cls.fail_times:
            return self._reply(500, {"status": "error", "message": "temporary"})
        if body.get("token") != "secret":
            return self._reply(200, {"status": "error", "message": "unauthorized"})
        email = body["lead"]["email"]
        if email in cls.emails:
            return self._reply(200, {"status": "duplicate"})
        cls.emails.add(email)
        self._reply(200, {"status": "added", "row": len(cls.emails) + 1})

    def _reply(self, code, data):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class CsvPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.outbox = self.tmp / "outbox.log"
        self.pipeline = LeadPipeline(CsvSheet(self.tmp / "leads.csv"), [DemoNotifier(self.outbox)], retry_delay=0)

    def test_valid_lead_is_saved_and_alert_logged(self):
        status, body = self.pipeline.process(GOOD_LEAD)
        self.assertEqual(status, 201)
        self.assertEqual(body["row"], 2)
        self.assertIn("jordan@example.com", (self.tmp / "leads.csv").read_text())
        self.assertIn("New lead: Jordan Example", self.outbox.read_text())

    def test_duplicate_email_is_skipped(self):
        self.pipeline.process(GOOD_LEAD)
        status, body = self.pipeline.process({**GOOD_LEAD, "email": "jordan@EXAMPLE.com"})
        self.assertTrue(body["duplicate"])
        self.assertEqual((self.tmp / "leads.csv").read_text().count("jordan@example.com"), 1)

    def test_invalid_fields_are_reported(self):
        status, body = self.pipeline.process({"name": "J", "email": "not-an-email", "service": "Nope", "message": "hi"})
        self.assertEqual(status, 400)
        self.assertEqual(set(body["errors"]), {"name", "email", "service", "message"})

    def test_honeypot_rejects_bots(self):
        status, _ = self.pipeline.process({**GOOD_LEAD, "website": "http://spam.example"})
        self.assertEqual(status, 400)

    def test_formula_injection_is_neutralised(self):
        self.pipeline.process({**GOOD_LEAD, "message": "=HYPERLINK(\"http://x\") please call me"})
        self.assertIn("'=HYPERLINK", (self.tmp / "leads.csv").read_text())

    def test_failed_alert_does_not_lose_the_lead(self):
        class BrokenNotifier:
            name = "Broken"

            def send(self, lead):
                raise ConnectionError("down")

        self.pipeline.notifiers = [BrokenNotifier()]
        status, body = self.pipeline.process(GOOD_LEAD)
        self.assertEqual(status, 201)
        self.assertEqual(body["warnings"], ["Broken alert failed"])


class AppsScriptConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAppsScript)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/exec"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeAppsScript.emails, FakeAppsScript.calls, FakeAppsScript.fail_times = set(), 0, 0

    def make_pipeline(self, secret="secret"):
        outbox = pathlib.Path(tempfile.mkdtemp()) / "outbox.log"
        return LeadPipeline(AppsScriptSheet(self.url, secret), [DemoNotifier(outbox)], retry_delay=0)

    def test_retries_transient_errors_then_succeeds(self):
        FakeAppsScript.fail_times = 2
        status, body = self.make_pipeline().process(GOOD_LEAD)
        self.assertEqual(status, 201)
        self.assertEqual(FakeAppsScript.calls, 3)

    def test_duplicate_detected_by_sheet(self):
        pipeline = self.make_pipeline()
        pipeline.process(GOOD_LEAD)
        _, body = pipeline.process(GOOD_LEAD)
        self.assertTrue(body["duplicate"])

    def test_wrong_secret_is_not_retried(self):
        status, _ = self.make_pipeline(secret="wrong").process(GOOD_LEAD)
        self.assertEqual(status, 503)
        self.assertEqual(FakeAppsScript.calls, 1)


if __name__ == "__main__":
    unittest.main()
