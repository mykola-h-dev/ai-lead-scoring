"""Auto-reply drafts: template, AI (mocked), fail-over, and never sending unless asked."""

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app import llm  # noqa: E402
from app.autoreply import AutoReplier, draft_reply, template_reply  # noqa: E402
from app.notify import DemoNotifier  # noqa: E402
from app.pipeline import LeadPipeline  # noqa: E402
from app.sheets import CsvSheet  # noqa: E402

LEAD = {"name": "priya example", "email": "priya@example.com", "phone": "", "service": "Tree surgery",
        "message": "A branch is leaning on the fence, urgent please. Budget £800.",
        "received_at": "2026-11-17 10:00:00 UTC"}
SCORING = {"score": "hot", "intent": "quote_request", "urgency": "high", "budget": "£800"}
AI_BODY = ("Hi Priya,\n\nThanks for letting us know about the branch leaning on your fence. We understand "
           "it is urgent, and a member of the team will reply within one business day. If you can, reply "
           "with a photo so our arborist can take a first look.\n\nThe Greenleaf Landscaping team")


class DraftTests(unittest.TestCase):
    def test_template_is_personalised(self):
        body = template_reply(LEAD, SCORING, "Greenleaf Landscaping")
        self.assertTrue(body.startswith("Hi Priya,"))
        self.assertIn("tree surgery", body)
        self.assertIn("urgent", body)
        self.assertIn("(£800)", body)
        self.assertIn("photo of the tree", body)

    def test_offline_uses_template(self):
        draft = draft_reply(LEAD, SCORING, "Greenleaf Landscaping")
        self.assertEqual(draft["drafted_by"], "template")
        self.assertEqual(draft["to"], "priya@example.com")
        self.assertIn("Priya", draft["subject"])

    def test_ai_draft_used_when_valid(self):
        with mock.patch("app.llm.generate", return_value=AI_BODY), \
                mock.patch("app.llm.provider_name", return_value="openai"):
            draft = draft_reply(LEAD, SCORING, "Greenleaf Landscaping", use_ai=True)
        self.assertEqual((draft["body"], draft["drafted_by"]), (AI_BODY, "ai (openai)"))

    def test_ai_draft_with_price_or_error_falls_back(self):
        for kwargs in ({"return_value": AI_BODY + " It will cost £300."},
                       {"side_effect": llm.LLMError("timeout")}):
            with mock.patch("app.llm.generate", **kwargs):
                draft = draft_reply(LEAD, SCORING, "Greenleaf Landscaping", use_ai=True)
            self.assertEqual(draft["drafted_by"], "template (AI unavailable)")


class AutoReplierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store, self.outbox = self.tmp / "replies.jsonl", self.tmp / "outbox.log"
        self.draft = draft_reply(LEAD, SCORING, "Greenleaf Landscaping")

    def test_draft_mode_stores_and_never_sends(self):
        replier = AutoReplier(self.store, self.outbox, mode="draft", smtp=("smtp.example", 587, "u", "p"))
        with mock.patch("smtplib.SMTP") as smtp:
            self.assertEqual(replier.handle(LEAD, self.draft), "drafted")
        smtp.assert_not_called()
        self.assertIn("auto-reply draft (not sent)", self.outbox.read_text())
        self.assertEqual(json.loads(self.store.read_text())["status"], "drafted")
        self.assertIn("priya@example.com", replier.drafts_by_email())

    def test_send_mode_needs_smtp(self):
        replier = AutoReplier(self.store, self.outbox, mode="send", smtp=None)
        with mock.patch("smtplib.SMTP") as smtp:
            self.assertEqual(replier.handle(LEAD, self.draft), "drafted")
        smtp.assert_not_called()

    def test_send_mode_with_smtp_sends(self):
        replier = AutoReplier(self.store, self.outbox, mode="send", smtp=("smtp.example", 587, "u", "p"),
                              sender="hello@greenleaf.example")
        with mock.patch("smtplib.SMTP") as smtp:
            self.assertEqual(replier.handle(LEAD, self.draft), "sent")
        sent = smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        self.assertEqual(sent["To"], "priya@example.com")

    def test_off_mode_and_unknown_mode(self):
        self.assertEqual(AutoReplier(self.store, self.outbox, mode="off").handle(LEAD, self.draft), "off")
        self.assertEqual(AutoReplier(self.store, self.outbox, mode="typo").mode, "draft")

    def test_pipeline_drafts_reply_but_not_for_sales_pitches(self):
        replier = AutoReplier(self.store, self.outbox)
        pipeline = LeadPipeline(CsvSheet(self.tmp / "leads.csv"), [DemoNotifier(self.outbox)], retry_delay=0,
                                auto_replier=replier, use_ai=False)
        _, body = pipeline.process({**LEAD, "phone": ""})
        self.assertEqual(body["auto_reply"], "drafted")
        _, body = pipeline.process({**LEAD, "email": "agency@example.com",
                                    "message": "We are an SEO marketing agency, can we talk about backlinks?"})
        self.assertEqual(body["auto_reply"], "skipped")


if __name__ == "__main__":
    unittest.main()
