"""Optional CRM push: create the lead as a HubSpot contact.

OFF by default. Enabled only when HUBSPOT_ACCESS_TOKEN (a HubSpot private-app
token with crm.objects.contacts.write scope) is set in the environment.
Written against HubSpot's public CRM v3 contacts API; tested with a mock only.
"""

import requests

from .retry import PermanentError

HUBSPOT_CONTACTS_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
HUBSPOT_SCORE_TO_STATUS = {"hot": "OPEN_DEAL", "warm": "IN_PROGRESS", "cold": "NEW"}


class HubSpotCRM:
    name = "HubSpot"

    def __init__(self, token, url=HUBSPOT_CONTACTS_URL, timeout=15):
        self.token, self.url, self.timeout = token, url, timeout

    @staticmethod
    def properties(lead):
        first, _, last = lead["name"].partition(" ")
        props = {"email": lead["email"], "firstname": first, "lastname": last,
                 "phone": lead.get("phone", ""), "lifecyclestage": "lead",
                 "hs_lead_status": HUBSPOT_SCORE_TO_STATUS.get(lead.get("score"), "NEW")}
        return {k: v for k, v in props.items() if v}

    def push(self, lead):
        resp = requests.post(self.url, json={"properties": self.properties(lead)},
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=self.timeout)
        if resp.status_code == 409:  # contact with this email already exists
            return "exists"
        if resp.status_code in (401, 403):
            raise PermanentError(f"HubSpot rejected the token ({resp.status_code})")
        if 400 <= resp.status_code < 500:
            raise PermanentError(f"HubSpot rejected the contact ({resp.status_code})")
        resp.raise_for_status()
        return "created"


def make_crm(config):
    return HubSpotCRM(config.HUBSPOT_ACCESS_TOKEN) if config.HUBSPOT_ACCESS_TOKEN else None
