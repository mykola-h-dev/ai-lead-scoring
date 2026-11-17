"""Dashboard counts, CSV export, storage upgrades and the HTTP endpoints end to end."""

import base64
import csv
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app import config, server  # noqa: E402
from app.autoreply import AutoReplier  # noqa: E402
from app.dashboard import filter_rows, summarise, to_csv  # noqa: E402
from app.notify import DemoNotifier  # noqa: E402
from app.pipeline import LeadPipeline  # noqa: E402
from app.sheets import COLUMNS, CsvSheet, MirroredSheet  # noqa: E402
from app.spam import RateLimiter  # noqa: E402

ROWS = [{"score": "hot", "source": "google", "service": "Tree surgery", "urgency": "high", "name": "=cmd"},
        {"score": "cold", "source": "website", "service": "Lawn care", "urgency": "low", "name": "B"},
        {"score": "hot", "source": "website", "service": "Lawn care", "urgency": "medium", "name": "C"}]


class DashboardHelperTests(unittest.TestCase):
    def test_summary_counts(self):
        s = summarise(ROWS)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_score"], {"hot": 2, "warm": 0, "cold": 1})
        self.assertEqual(s["by_source"], {"website": 2, "google": 1})
        self.assertEqual(s["by_service"]["Lawn care"], 2)

    def test_filter(self):
        self.assertEqual(len(filter_rows(ROWS, score="hot")), 2)
        self.assertEqual(len(filter_rows(ROWS, score="hot", source="google")), 1)

    def test_csv_export_has_header_and_formula_guard(self):
        rows = list(csv.DictReader(io.StringIO(to_csv(ROWS))))
        self.assertEqual(list(rows[0]), COLUMNS)
        self.assertEqual(rows[0]["name"], "'=cmd")


class StorageTests(unittest.TestCase):
    def test_old_csv_header_is_upgraded(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "leads.csv"
        path.write_text("received_at,name,email,phone,service,message\n"
                        "2026-11-01 09:00:00 UTC,Old Lead,old@example.com,,Lawn care,Old message here\n")
        sheet = CsvSheet(path)
        sheet.append({"received_at": "x", "name": "New", "email": "new@example.com", "phone": "",
                      "service": "Lawn care", "message": "m", "score": "warm"})
        rows = sheet.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Old Lead")
        self.assertEqual(rows[1]["score"], "warm")

    def test_mirrored_sheet_copies_only_added_rows(self):
        mirror = CsvSheet(pathlib.Path(tempfile.mkdtemp()) / "copy.csv")
        primary = mock.Mock(name="primary")
        primary.name = "Google Sheet"
        sheet = MirroredSheet(primary, mirror)
        lead = {"email": "a@example.com", "name": "A"}
        primary.append.return_value = {"status": "added", "row": 2}
        sheet.append(lead)
        primary.append.return_value = {"status": "duplicate"}
        sheet.append({**lead, "email": "b@example.com"})
        self.assertEqual([r["email"] for r in sheet.rows()], ["a@example.com"])


class HttpEndToEndTests(unittest.TestCase):
    """Starts the real handler on a free port with a temporary CSV store."""

    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.pipeline = LeadPipeline(
            CsvSheet(tmp / "leads.csv"), [DemoNotifier(tmp / "outbox.log")], retry_delay=0,
            rate_limiter=RateLimiter(3, 600), auto_replier=AutoReplier(tmp / "r.jsonl", tmp / "outbox.log"),
            use_ai=False)
        patcher = mock.patch.object(server, "PIPELINE", self.pipeline)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LeadHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def request(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers={
            "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def submit(self, email, message):
        return self.request("/api/leads", {"name": "Test Person", "email": email, "service": "Tree surgery",
                                           "message": message, "source": "google"})

    def test_submit_then_dashboard_api_and_csv(self):
        status, _, body = self.submit("t1@example.com", "Urgent: fallen tree, need a quote today. Budget £600.")
        self.assertEqual(status, 201, body)
        status, _, body = self.request("/api/leads")
        data = json.loads(body)
        self.assertEqual(data["summary"]["by_score"]["hot"], 1)
        self.assertEqual(data["summary"]["by_source"], {"google": 1})
        lead = data["leads"][0]
        self.assertEqual((lead["score"], lead["budget"]), ("hot", "£600"))
        self.assertEqual(lead["auto_reply"]["status"], "drafted")
        status, headers, body = self.request("/api/leads.csv?score=hot")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("t1@example.com", body.decode())
        status, _, body = self.request("/dashboard")
        self.assertIn(b"Leads dashboard", body)
        status, _, body = self.request("/api/status")
        self.assertEqual(json.loads(body)["scoring"], "rules (offline)")

    def test_rate_limit_over_http(self):
        codes = [self.submit(f"r{i}@example.com", "Please quote for lawn care weekly.")[0] for i in range(4)]
        self.assertEqual(codes, [201, 201, 201, 429])

    def test_dashboard_password(self):
        with mock.patch.object(config, "DASHBOARD_PASSWORD", "s3cret-demo"):
            self.assertEqual(self.request("/dashboard")[0], 401)
            self.assertEqual(self.request("/api/leads.csv")[0], 401)
            good = "Basic " + base64.b64encode(b"admin:s3cret-demo").decode()
            bad = "Basic " + base64.b64encode(b"admin:wrong").decode()
            self.assertEqual(self.request("/api/leads", headers={"Authorization": bad})[0], 401)
            self.assertEqual(self.request("/api/leads", headers={"Authorization": good})[0], 200)
            self.assertEqual(self.request("/")[0], 200)  # public form stays public


if __name__ == "__main__":
    unittest.main()
