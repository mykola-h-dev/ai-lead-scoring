"""Validate and normalise a lead submitted through the web form."""

import re
from datetime import datetime, timezone

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9 ()\-.]{7,20}$")
SERVICES = {"Garden design", "Lawn care", "Patio & paving", "Tree surgery", "Other"}
MAX_MESSAGE = 1000


class ValidationError(ValueError):
    """Raised with a dict of field -> error message."""

    def __init__(self, errors):
        super().__init__("Invalid lead")
        self.errors = errors


def clean_text(value, limit=200):
    """Strip whitespace and control characters, collapse spaces, cap length."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def validate_lead(form):
    """Return a clean lead dict or raise ValidationError.

    `form` is a dict of submitted fields. A hidden "website" field acts as a
    honeypot: real visitors never fill it, simple spam bots usually do.
    """
    if clean_text(form.get("website")):
        raise ValidationError({"form": "Submission rejected."})

    lead = {
        "name": clean_text(form.get("name"), 100),
        "email": clean_text(form.get("email"), 254).lower(),
        "phone": clean_text(form.get("phone"), 30),
        "service": clean_text(form.get("service"), 50),
        "message": clean_text(form.get("message"), MAX_MESSAGE),
    }
    errors = {}
    if len(lead["name"]) < 2:
        errors["name"] = "Please enter your name."
    if not EMAIL_RE.match(lead["email"]):
        errors["email"] = "Please enter a valid email address."
    if lead["phone"] and not PHONE_RE.match(lead["phone"]):
        errors["phone"] = "Please enter a valid phone number or leave it empty."
    if lead["service"] not in SERVICES:
        errors["service"] = "Please choose a service."
    if len(lead["message"]) < 10:
        errors["message"] = "Please tell us a little more (at least 10 characters)."
    if errors:
        raise ValidationError(errors)

    lead["received_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return lead
