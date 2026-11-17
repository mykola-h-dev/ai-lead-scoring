"""The lead workflow: spam checks -> validate -> AI/rule scoring -> save -> alert -> auto-reply -> CRM.

A lead is never lost because of a downstream problem: it is saved first, and
alert, auto-reply and CRM failures are reported back as warnings only.
"""

import logging
from functools import partial

from .autoreply import draft_reply
from .retry import with_retry
from .scoring import score_lead
from .spam import is_disposable
from .validation import ValidationError, validate_lead

log = logging.getLogger("lead-to-sheet")

RATE_LIMITED = {"ok": False, "errors": {"form": "Too many requests from your network. Please try again later."}}


class LeadPipeline:
    def __init__(self, sheet, notifiers, retry_delay=1.0, *, business="Greenleaf Landscaping",
                 rate_limiter=None, extra_disposable=frozenset(), auto_replier=None, crm=None, use_ai=None):
        self.sheet = sheet
        self.notifiers = notifiers
        self.retry_delay = retry_delay
        self.business = business
        self.rate_limiter = rate_limiter
        self.disposable_check = partial(is_disposable, extra=extra_disposable)
        self.auto_replier = auto_replier
        self.crm = crm
        self.use_ai = use_ai  # None = decide from environment keys

    def process(self, form, client_ip="unknown"):
        """Return (http_status, response_dict) for a submitted form."""
        if self.rate_limiter and not self.rate_limiter.allow(client_ip):
            log.warning("Rate limit hit for %s", client_ip)
            return 429, RATE_LIMITED
        try:
            lead = validate_lead(form, disposable_check=self.disposable_check)
        except ValidationError as exc:
            return 400, {"ok": False, "errors": exc.errors}

        lead.update(score_lead(lead, self.business, use_ai=self.use_ai))

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
        auto_reply = self._auto_reply(lead, warnings)
        crm = self._push_crm(lead, warnings)
        log.info("Lead saved (row %s) from %s, score=%s by %s", saved.get("row"), lead["email"],
                 lead["score"], lead["scored_by"])
        # The visitor-facing response deliberately does not include the internal score.
        return 201, {"ok": True, "duplicate": False, "row": saved.get("row"), "warnings": warnings,
                     "auto_reply": auto_reply, "crm": crm,
                     "message": f"Thanks {lead['name'].split()[0]}! We'll get back to you within one business day."}

    def _notify_all(self, lead):
        warnings = []
        for notifier in self.notifiers:
            try:
                with_retry(notifier.send, lead, base_delay=self.retry_delay, label=f"{notifier.name} alert")
            except Exception:  # noqa: BLE001
                warnings.append(f"{notifier.name} alert failed")
        return warnings

    def _auto_reply(self, lead, warnings):
        if not self.auto_replier or lead["intent"] == "not_a_customer":
            return "skipped"
        try:
            draft = draft_reply(lead, lead, self.business, use_ai=self.use_ai)
            return self.auto_replier.handle(lead, draft)
        except Exception:  # noqa: BLE001
            log.exception("Auto-reply failed for %s", lead["email"])
            warnings.append("Auto-reply failed")
            return "failed"

    def _push_crm(self, lead, warnings):
        if not self.crm:
            return "off"
        try:
            return with_retry(self.crm.push, lead, base_delay=self.retry_delay, label=f"{self.crm.name} push")
        except Exception:  # noqa: BLE001
            warnings.append(f"{self.crm.name} push failed")
            return "failed"
