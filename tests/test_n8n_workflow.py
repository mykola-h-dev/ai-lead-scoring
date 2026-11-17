"""The n8n workflow JSON: structure, AI scoring step, and its JavaScript run under Node.js.

The JS tests are skipped when `node` is not installed.
"""

import json
import os
import pathlib
import shutil
import subprocess
import unittest

for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)

from app.scoring import rule_score  # noqa: E402

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / "n8n" / "lead-to-sheet-alert.workflow.json"
NODE = shutil.which("node")

SAMPLES = [
    {"name": "Priya Example", "email": "p@example.com", "service": "Tree surgery", "phone": "+44 7700 900123",
     "message": "A large branch fell in the storm - can you give me a quote and come this week? Budget around £800."},
    {"name": "Sam Example", "email": "s@example.com", "service": "Patio & paving", "phone": "",
     "message": "How much would a small sandstone patio cost? Thinking of doing it next month."},
    {"name": "Lee Example", "email": "l@example.com", "service": "Other", "phone": "",
     "message": "Just browsing ideas for now."},
    {"name": "Robin Example", "email": "r@example.com", "service": "Patio & paving", "phone": "",
     "message": "Patio repair needed urgently, several slabs are loose and dangerous."},
    {"name": "Agency", "email": "a@example.com", "service": "Other", "phone": "1",
     "message": "We are an SEO marketing agency offering backlinks, $500 per month, reply ASAP."},
]


def load():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def code_of(workflow, name):
    return next(n for n in workflow["nodes"] if n["name"] == name)["parameters"]["jsCode"]


def run_code_node(js, json_input, lead):
    """Run an n8n Code node body with minimal $json / $() stubs and return its output."""
    script = (
        f"const $json = {json.dumps(json_input)};\n"
        f"const __lead = {json.dumps(lead)};\n"
        "const $ = (name) => ({ first: () => ({ json: __lead }) });\n"
        f"const out = (() => {{\n{js}\n}})();\n"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class WorkflowStructureTests(unittest.TestCase):
    def test_connections_point_to_existing_nodes_and_all_nodes_are_reachable(self):
        wf = load()
        names = {n["name"] for n in wf["nodes"]}
        reached, todo = set(), ["Lead form webhook"]
        while todo:
            current = todo.pop()
            reached.add(current)
            for branch in wf["connections"].get(current, {}).get("main", []):
                for link in branch:
                    self.assertIn(link["node"], names)
                    if link["node"] not in reached:
                        todo.append(link["node"])
        self.assertEqual(reached, names)

    def test_ai_step_present_and_fails_over(self):
        wf = load()
        ai = next(n for n in wf["nodes"] if n["name"] == "AI lead scoring (Gemini)")
        self.assertEqual(ai["onError"], "continueRegularOutput")
        self.assertEqual(ai["parameters"]["genericAuthType"], "httpHeaderAuth")
        self.assertEqual(wf["connections"]["AI lead scoring (Gemini)"]["main"][0][0]["node"],
                         "Parse score (AI or rules)")

    def test_no_secrets_in_workflow(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for marker in ("AIza", "sk-", "xoxb-", "hooks.slack.com/services/T"):
            self.assertNotIn(marker, text)

    def test_json_is_up_to_date_with_builder(self):
        builder = WORKFLOW.parent / "build_workflow.py"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        code = (f"import runpy, json; ns = runpy.run_path({str(builder)!r}); "
                "print(json.dumps(ns['workflow'], indent=2))")
        out = subprocess.run(["python3", "-c", code], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(json.loads(out.stdout), load())


@unittest.skipUnless(NODE, "node is not installed")
class WorkflowJavaScriptTests(unittest.TestCase):
    def test_every_code_node_is_valid_javascript(self):
        for node in load()["nodes"]:
            if "jsCode" in node["parameters"]:
                wrapped = f"(async () => {{\n{node['parameters']['jsCode']}\n}})"
                result = subprocess.run([NODE, "--check", "-"], input=wrapped, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{node['name']}: {result.stderr}")

    def test_rules_in_n8n_match_python_rules(self):
        js = code_of(load(), "Parse score (AI or rules)")
        for lead in SAMPLES:
            js_result = run_code_node(js, {}, lead)[0]["json"]  # empty input = AI node produced nothing
            py_result = rule_score(lead)
            for key in ("score", "intent", "urgency", "budget", "summary"):
                self.assertEqual(js_result[key], py_result[key], f"{key} for {lead['name']}")

    def test_ai_reply_is_parsed_and_bad_reply_falls_back(self):
        js = code_of(load(), "Parse score (AI or rules)")
        good = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            {"score": "warm", "intent": "quote_request", "urgency": "medium", "budget": "",
             "summary": "Patio price for next month."})}]}}]}
        out = run_code_node(js, good, SAMPLES[1])[0]["json"]
        self.assertEqual((out["score"], out["scored_by"]), ("warm", "ai (gemini)"))
        bad = {"candidates": [{"content": {"parts": [{"text": "{\"score\": \"boiling\"}"}]}}]}
        out = run_code_node(js, bad, SAMPLES[1])[0]["json"]
        self.assertEqual(out["scored_by"], "rules (AI unavailable)")

    def test_validate_node_rejects_disposable_and_keeps_source(self):
        js = code_of(load(), "Validate lead")
        body = {"name": "Jo Example", "email": "jo@mailinator.com", "service": "Lawn care",
                "message": "Weekly lawn care please.", "utm_source": "Google"}
        out = run_code_node(js, {"body": body}, {})[0]["json"]
        self.assertFalse(out["valid"])
        self.assertIn("permanent", out["errors"]["email"])
        self.assertEqual(out["source"], "google")


if __name__ == "__main__":
    unittest.main()
