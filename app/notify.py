"""Send a "new lead" alert to Slack and/or email, or log it in demo mode."""

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import requests

from .retry import PermanentError


def format_alert(lead):
    """Plain-text alert used for Slack, email and the demo outbox."""
    phone = lead["phone"] or "not given"
    return (
        f"New lead: {lead['name']} ({lead['service']})\n"
        f"Email: {lead['email']} | Phone: {phone}\n"
        f"Message: {lead['message']}\n"
        f"Received: {lead['received_at']}"
    )


class SlackNotifier:
    name = "Slack"

    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def send(self, lead):
        resp = requests.post(self.webhook_url, json={"text": format_alert(lead)}, timeout=15)
        if 400 <= resp.status_code < 500:
            raise PermanentError(f"Slack rejected the message ({resp.status_code})")
        resp.raise_for_status()


class EmailNotifier:
    name = "Email"

    def __init__(self, host, port, user, password, sender, recipient):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.sender, self.recipient = sender, recipient

    def send(self, lead):
        msg = EmailMessage()
        msg["Subject"] = f"New lead: {lead['name']} - {lead['service']}"
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Reply-To"] = lead["email"]
        msg.set_content(format_alert(lead))
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


class DemoNotifier:
    """Writes the alert that WOULD be sent to data/outbox.log."""

    name = "Demo outbox (data/outbox.log)"

    def __init__(self, log_path):
        self.log_path = log_path

    def send(self, lead):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"--- would send at {stamp} ---\n{format_alert(lead)}\n\n")


def make_notifiers(config):
    """Return every configured notifier; fall back to the demo outbox if none."""
    notifiers = []
    if config.SLACK_WEBHOOK_URL:
        notifiers.append(SlackNotifier(config.SLACK_WEBHOOK_URL))
    if config.SMTP_HOST and config.ALERT_EMAIL_TO:
        notifiers.append(EmailNotifier(
            config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD,
            config.ALERT_EMAIL_FROM or config.SMTP_USER, config.ALERT_EMAIL_TO,
        ))
    return notifiers or [DemoNotifier(config.OUTBOX_LOG)]
