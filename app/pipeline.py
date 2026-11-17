"""The lead workflow: validate -> save to sheet -> notify.

A lead is never lost because of a notification problem: it is saved first,
and alert failures are reported back as warnings.
"""

import logging

from .retry import with_retry
from .validation import ValidationError, validate_lead

log = logging.getLogger("lead-to-sheet")


class LeadPipeline:
    def __init__(self, sheet, notifiers, retry_delay=1.0):
        self.sheet = sheet
        self.notifiers = notifiers
        self.retry_delay = retry_delay

    def process(self, form):
        """Return (http_status, response_dict) for a submitted form."""
        try:
            lead = validate_lead(form)
        except ValidationError as exc:
            return 400, {"ok": False, "errors": exc.errors}

        try:
            saved = with_retry(self.sheet.append, lead, base_delay=self.retry_delay, label="Sheet append")
        except Exception:  # noqa: BLE001 - reported to the visitor without internals
            log.exception("Could not save lead from %s", lead["email"])
            return 503, {"ok": False, "errors": {"form": "We could not save your request. Please try again shortly."}}

        if saved.get("status") == "duplicate":
            log.info("Duplicate lead ignored: %s", lead["email"])
            return 200, {"ok": True, "duplicate": True,
                         "message": "Thanks! We already have your request and will be in touch soon."}

        warnings = self._notify_all(lead)
        log.info("Lead saved (row %s) from %s", saved.get("row"), lead["email"])
        return 201, {"ok": True, "duplicate": False, "row": saved.get("row"), "warnings": warnings,
                     "message": f"Thanks {lead['name'].split()[0]}! We'll get back to you within one business day."}

    def _notify_all(self, lead):
        warnings = []
        for notifier in self.notifiers:
            try:
                with_retry(notifier.send, lead, base_delay=self.retry_delay, label=f"{notifier.name} alert")
            except Exception:  # noqa: BLE001
                warnings.append(f"{notifier.name} alert failed")
        return warnings
