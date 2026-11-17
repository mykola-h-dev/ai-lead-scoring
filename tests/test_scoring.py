"""Lead scoring: offline rules, AI path (mocked) and fail-over to rules."""

import os
import unittest
from unittest import mock

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app import llm  # noqa: E402
from app.scoring import find_budget, rule_score, score_lead  # noqa: E402

HOT = {"name": "Priya Example", "service": "Tree surgery", "phone": "+44 7700 900123",
       "message": "A large branch fell in the storm and is leaning on the fence - can you give me a quote "
                  "and come this week? Budget around £800."}
WARM = {"name": "Sam Example", "service": "Patio & paving", "phone": "",
        "message": "How much would a small sandstone patio cost? Thinking of doing it next month."}
COLD = {"name": "Lee Example", "service": "Other", "phone": "", "message": "Just browsing ideas for now."}
PITCH = {"name": "Agency Example", "service": "Other", "phone": "+1 555 0100",
         "message": "We are an SEO marketing agency and can get you backlinks, quote us ASAP for $500 per month."}


class RuleScoringTests(unittest.TestCase):
    def test_hot_warm_cold(self):
        self.assertEqual(rule_score(HOT)["score"], "hot")
        self.assertEqual(rule_score(WARM)["score"], "warm")
        self.assertEqual(rule_score(COLD)["score"], "cold")

    def test_fields_of_hot_lead(self):
        result = rule_score(HOT)
        self.assertEqual(result["urgency"], "high")
        self.assertEqual(result["intent"], "quote_request")
        self.assertEqual(result["budget"], "£800")
        self.assertEqual(result["scored_by"], "rules")
        self.assertIn("Tree surgery: wants a quote", result["summary"])

    def test_sales_pitch_is_cold_even_with_budget_and_urgency(self):
        result = rule_score(PITCH)
        self.assertEqual((result["score"], result["intent"]), ("cold", "not_a_customer"))

    def test_urgent_service_need_is_at_least_warm(self):
        lead = {"service": "Patio & paving", "phone": "",
                "message": "Patio repair needed urgently, several slabs are loose and dangerous."}
        result = rule_score(lead)
        self.assertEqual((result["intent"], result["urgency"], result["score"]), ("service_request", "high", "warm"))

    def test_budget_formats(self):
        self.assertEqual(find_budget("about $1,500 total"), "$1,500")
        self.assertEqual(find_budget("up to 2k pounds"), "2k pounds")
        self.assertEqual(find_budget("€ 900 max"), "€ 900")
        self.assertEqual(find_budget("4 x 5 m patio"), "")

    def test_offline_without_keys_uses_rules(self):
        self.assertFalse(llm.is_available())
        self.assertEqual(score_lead(HOT)["scored_by"], "rules")


class AIScoringTests(unittest.TestCase):
    def ai(self, reply=None, error=None):
        target = mock.patch("app.llm.generate_json", side_effect=error, return_value=reply)
        with target, mock.patch("app.llm.provider_name", return_value="gemini"):
            return score_lead(WARM, "Greenleaf", use_ai=True)

    def test_valid_ai_reply_is_used(self):
        result = self.ai({"score": "Warm", "intent": "quote_request", "urgency": "medium",
                          "budget": "", "summary": "Wants a sandstone patio price for next month."})
        self.assertEqual(result["score"], "warm")
        self.assertEqual(result["scored_by"], "ai (gemini)")
        self.assertEqual(result["summary"], "Wants a sandstone patio price for next month.")

    def test_invented_budget_is_dropped(self):
        result = self.ai({"score": "hot", "intent": "quote_request", "urgency": "medium",
                          "budget": "£5,000", "summary": "Patio quote."})
        self.assertEqual(result["budget"], "")

    def test_invalid_ai_values_fall_back_to_rules(self):
        result = self.ai({"score": "very hot", "intent": "x", "urgency": "y", "summary": "s"})
        self.assertEqual(result["scored_by"], "rules (AI unavailable)")
        self.assertEqual(result["score"], rule_score(WARM)["score"])

    def test_api_error_falls_back_to_rules(self):
        result = self.ai(error=llm.LLMError("Gemini API error 503"))
        self.assertEqual(result["scored_by"], "rules (AI unavailable)")

    def test_generate_json_parses_fenced_reply(self):
        with mock.patch("app.llm.generate", return_value='```json\n{"score": "hot"}\n```'):
            self.assertEqual(llm.generate_json("s", "u"), {"score": "hot"})
        with mock.patch("app.llm.generate", return_value="no json here"):
            with self.assertRaises(llm.LLMError):
                llm.generate_json("s", "u")

    def test_generate_without_key_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.generate("s", "u")


if __name__ == "__main__":
    unittest.main()
