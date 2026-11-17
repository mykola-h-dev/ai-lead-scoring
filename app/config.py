"""Settings loaded from environment variables, with an optional local .env file."""

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_dotenv(path=ROOT / ".env"):
    """Load KEY=VALUE lines from the project's .env file (if present) into os.environ.

    Existing environment variables win, so you can override values on the command line.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def env(name, default=""):
    return os.environ.get(name, default).strip()


# Google Sheet via Apps Script Web App (see apps_script/Code.gs). Empty = CSV demo mode.
SHEETS_WEBAPP_URL = env("SHEETS_WEBAPP_URL")
SHEETS_SHARED_SECRET = env("SHEETS_SHARED_SECRET")
CSV_PATH = ROOT / env("CSV_PATH", "data/leads.csv")

# Notifications. Empty = demo mode (message written to data/outbox.log instead).
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL")
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
ALERT_EMAIL_FROM = env("ALERT_EMAIL_FROM")
ALERT_EMAIL_TO = env("ALERT_EMAIL_TO")
OUTBOX_LOG = ROOT / "data" / "outbox.log"

HOST = env("HOST", "127.0.0.1")
PORT = int(env("PORT", "8001"))
