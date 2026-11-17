"""Builds n8n/lead-to-sheet-alert.workflow.json (an importable n8n workflow).

The JSON is generated from this script so the embedded JavaScript stays readable.
Run: python n8n/build_workflow.py
"""

import json
import pathlib

OUT = pathlib.Path(__file__).with_name("lead-to-sheet-alert.workflow.json")
SHEET_ID = "REPLACE_WITH_YOUR_SPREADSHEET_ID"

VALIDATE_JS = r"""
// Validate and normalise the submitted lead (same rules as the Python app).
const body = $json.body || {};
const clean = (v, max = 200) => String(v ?? '').replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
const lead = {
  name: clean(body.name, 100),
  email: clean(body.email, 254).toLowerCase(),
  phone: clean(body.phone, 30),
  service: clean(body.service, 50),
  message: clean(body.message, 1000),
  received_at: new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC',
};
const errors = {};
if (clean(body.website)) errors.form = 'Submission rejected.';          // honeypot
if (lead.name.length < 2) errors.name = 'Please enter your name.';
if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(lead.email)) errors.email = 'Please enter a valid email address.';
if (lead.phone && !/^\+?[0-9 ()\-.]{7,20}$/.test(lead.phone)) errors.phone = 'Please enter a valid phone number.';
if (lead.message.length < 10) errors.message = 'Please tell us a little more.';
return [{ json: { ...lead, valid: Object.keys(errors).length === 0, errors } }];
""".strip()

LEAD = "$('Validate lead').first().json"


def node(name, node_type, version, position, parameters, **extra):
    return {"parameters": parameters, "name": name, "type": node_type, "typeVersion": version,
            "position": position, **extra}


def bool_condition(left, operation):
    """IF node (v2) condition on a single value."""
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
        "conditions": [{"id": f"cond-{operation}", "leftValue": left, "rightValue": "",
                        "operator": {"type": "string" if operation != "true" else "boolean",
                                     "operation": operation, "singleValue": True}}],
        "combinator": "and",
    }


def respond(name, position, code, body_expr):
    return node(name, "n8n-nodes-base.respondToWebhook", 1.1, position, {
        "respondWith": "json", "responseBody": body_expr, "options": {"responseCode": code}})


def sheet_ref():
    return {
        "documentId": {"__rl": True, "mode": "id", "value": SHEET_ID},
        "sheetName": {"__rl": True, "mode": "name", "value": "Leads"},
    }


RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}

nodes = [
    node("Lead form webhook", "n8n-nodes-base.webhook", 2, [0, 300],
         {"httpMethod": "POST", "path": "new-lead", "responseMode": "responseNode", "options": {}},
         webhookId="new-lead"),
    node("Validate lead", "n8n-nodes-base.code", 2, [220, 300], {"jsCode": VALIDATE_JS}),
    node("Is valid?", "n8n-nodes-base.if", 2, [440, 300],
         {"conditions": bool_condition("={{ $json.valid }}", "true"), "options": {}}),
    respond("Respond 400 (invalid)", [660, 480], 400, "={{ { ok: false, errors: $json.errors } }}"),
    node("Find existing email", "n8n-nodes-base.googleSheets", 4.5, [660, 200], {
        **sheet_ref(),
        "filtersUI": {"values": [{"lookupColumn": "Email", "lookupValue": "={{ $json.email }}"}]},
        "options": {}}, alwaysOutputData=True, **RETRY),
    node("Already in sheet?", "n8n-nodes-base.if", 2, [880, 200],
         {"conditions": bool_condition("={{ $json.Email }}", "notEmpty"), "options": {}}),
    respond("Respond 200 (duplicate)", [1100, 80], 200,
            "={{ { ok: true, duplicate: true, message: 'Thanks! We already have your request.' } }}"),
    node("Append lead to sheet", "n8n-nodes-base.googleSheets", 4.5, [1100, 300], {
        "operation": "append", **sheet_ref(),
        "columns": {
            "mappingMode": "defineBelow",
            "value": {
                "Received": f"={{{{ {LEAD}.received_at }}}}",
                "Name": f"={{{{ {LEAD}.name }}}}",
                "Email": f"={{{{ {LEAD}.email }}}}",
                "Phone": f"={{{{ {LEAD}.phone }}}}",
                "Service": f"={{{{ {LEAD}.service }}}}",
                "Message": f"={{{{ {LEAD}.message }}}}",
            },
            "matchingColumns": [],
            "schema": [{"id": c, "displayName": c, "required": False, "defaultMatch": False,
                        "display": True, "type": "string", "canBeUsedToMatch": True}
                       for c in ["Received", "Name", "Email", "Phone", "Service", "Message"]],
        },
        "options": {}}, **RETRY),
    node("Slack alert", "n8n-nodes-base.slack", 2.2, [1320, 300], {
        "select": "channel",
        "channelId": {"__rl": True, "mode": "name", "value": "#leads"},
        "text": (f"=:seedling: New lead: *{{{{ {LEAD}.name }}}}* ({{{{ {LEAD}.service }}}})\n"
                 f"Email: {{{{ {LEAD}.email }}}} | Phone: {{{{ {LEAD}.phone || 'not given' }}}}\n"
                 f"{{{{ {LEAD}.message }}}}"),
        "otherOptions": {}}, onError="continueRegularOutput", **RETRY),
    respond("Respond 201 (saved)", [1540, 300], 201,
            "={{ { ok: true, duplicate: false, message: 'Thanks! We will get back to you within one business day.' } }}"),
]


def link(target, index=0):
    return {"node": target, "type": "main", "index": index}


connections = {
    "Lead form webhook": {"main": [[link("Validate lead")]]},
    "Validate lead": {"main": [[link("Is valid?")]]},
    "Is valid?": {"main": [[link("Find existing email")], [link("Respond 400 (invalid)")]]},
    "Find existing email": {"main": [[link("Already in sheet?")]]},
    "Already in sheet?": {"main": [[link("Respond 200 (duplicate)")], [link("Append lead to sheet")]]},
    "Append lead to sheet": {"main": [[link("Slack alert")]]},
    "Slack alert": {"main": [[link("Respond 201 (saved)")]]},
}

workflow = {
    "name": "Lead form -> Google Sheets -> Slack alert",
    "nodes": [{**n, "id": f"node-{i + 1:02d}"} for i, n in enumerate(nodes)],
    "connections": connections,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
    "active": False,
}

if __name__ == "__main__":
    OUT.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
