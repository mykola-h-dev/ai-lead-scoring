"""Optional HubSpot push, tested with a mocked HTTP call (no HubSpot account used)."""

import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app.crm import HubSpotCRM, make_crm  # noqa: E402
from app.notify import DemoNotifier  # noqa: E402
from app.pipeline import LeadPipeline  # noqa: E402
from app.retry import PermanentError  # noqa: E402
from app.sheets import CsvSheet  # noqa: E402

LEAD = {"name": "Jordan Q Example", "email": "jordan@example.com", "phone": "+1 555 0100",
        "service": "Lawn care", "message": "Weekly lawn care for a small back garden.", "score": "hot"}


def response(code):
    return types.SimpleNamespace(status_code=code, raise_for_status=lambda: None)


class HubSpotTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(make_crm(types.SimpleNamespace(HUBSPOT_ACCESS_TOKEN="")))
        self.assertIsInstance(make_crm(types.SimpleNamespace(HUBSPOT_ACCESS_TOKEN="test-token")), HubSpotCRM)

    def test_properties(self):
        props = HubSpotCRM.properties(LEAD)
        self.assertEqual((props["firstname"], props["lastname"]), ("Jordan", "Q Example"))
        self.assertEqual(props["hs_lead_status"], "OPEN_DEAL")

    def test_create_and_exists(self):
        crm = HubSpotCRM("test-token")
        with mock.patch("requests.post", return_value=response(201)) as post:
            self.assertEqual(crm.push(LEAD), "created")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(post.call_args.kwargs["json"]["properties"]["email"], "jordan@example.com")
        with mock.patch("requests.post", return_value=response(409)):
            self.assertEqual(crm.push(LEAD), "exists")

    def test_bad_token_is_permanent(self):
        with mock.patch("requests.post", return_value=response(401)):
            with self.assertRaises(PermanentError):
                HubSpotCRM("bad").push(LEAD)

    def test_crm_failure_does_not_lose_lead(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        pipeline = LeadPipeline(CsvSheet(tmp / "leads.csv"), [DemoNotifier(tmp / "o.log")], retry_delay=0,
                                crm=HubSpotCRM("bad"), use_ai=False)
        with mock.patch("requests.post", return_value=response(401)):
            status, body = pipeline.process(LEAD)
        self.assertEqual(status, 201)
        self.assertEqual((body["crm"], body["warnings"]), ("failed", ["HubSpot push failed"]))


if __name__ == "__main__":
    unittest.main()
