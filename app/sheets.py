"""Where leads are stored: a Google Sheet (via Apps Script Web App) or a local CSV.

Why Apps Script instead of the Sheets API directly? The Sheets API needs a
service-account JWT signed with RS256, which requires a crypto library. An
Apps Script Web App bound to the sheet needs only an HTTPS POST, is free, and
the client keeps full control of it in their own Google account.
See apps_script/Code.gs for the script to paste into the sheet.

Both connectors share the same interface:
    append(lead) -> {"status": "added", "row": n} or {"status": "duplicate"}
Duplicates are detected by email address.
"""

import csv
import threading

import requests

from .retry import PermanentError

COLUMNS = ["received_at", "name", "email", "phone", "service", "message", "source",
           "score", "intent", "urgency", "budget", "summary", "scored_by"]


def safe_cell(value):
    """Stop spreadsheet formula injection: text starting with = + - @ is stored as plain text."""
    value = str(value or "")
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


class CsvSheet:
    """Demo / fallback storage: appends rows to a local CSV file."""

    name = "Local CSV (demo mode)"

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()  # the server is threaded

    def _existing_emails(self):
        if not self.path.exists():
            return set()
        with self.path.open(newline="", encoding="utf-8") as fh:
            return {row["email"].lower() for row in csv.DictReader(fh)}

    def _upgrade_header(self):
        """Older CSVs had fewer columns: rewrite them once with the current header."""
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames == COLUMNS:
                return
            rows = list(reader)
        with self.path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def rows(self):
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def append(self, lead):
        with self._lock:
            if self.path.exists():
                self._upgrade_header()
            if lead["email"] in self._existing_emails():
                return {"status": "duplicate"}
            is_new_file = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=COLUMNS)
                if is_new_file:
                    writer.writeheader()
                # phone is already validated (digits, spaces, +), so it is written as is
                writer.writerow({col: lead.get(col, "") if col == "phone" else safe_cell(lead.get(col))
                                 for col in COLUMNS})
            with self.path.open(encoding="utf-8") as fh:
                row_number = sum(1 for _ in fh)  # includes header, like a sheet row number
            return {"status": "added", "row": row_number}


class AppsScriptSheet:
    """Google Sheet storage through the Apps Script Web App in apps_script/Code.gs."""

    name = "Google Sheet (Apps Script)"

    def __init__(self, webapp_url, shared_secret, timeout=20):
        self.url = webapp_url
        self.secret = shared_secret
        self.timeout = timeout

    def append(self, lead):
        payload = {"action": "append", "token": self.secret, "lead": {c: lead.get(c, "") for c in COLUMNS}}
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        if resp.status_code >= 500:
            raise RuntimeError(f"Sheet web app returned {resp.status_code}")  # transient: retry
        try:
            data = resp.json()
        except ValueError as exc:
            raise PermanentError("Sheet web app did not return JSON - check the deployment URL and access") from exc
        if data.get("status") == "error":
            raise PermanentError(f"Sheet web app error: {data.get('message')}")
        return data


class MirroredSheet:
    """Google Sheet as the source of truth plus a local CSV copy that feeds the dashboard."""

    def __init__(self, primary, mirror):
        self.primary, self.mirror = primary, mirror
        self.name = f"{primary.name} + local copy"

    def append(self, lead):
        result = self.primary.append(lead)
        if result.get("status") == "added":
            try:
                self.mirror.append(lead)
            except OSError:
                pass  # the dashboard copy is best-effort; the sheet already has the row
        return result

    def rows(self):
        return self.mirror.rows()


def make_sheet(config):
    """Pick the Google Sheet connector if configured, otherwise the CSV demo connector."""
    if config.SHEETS_WEBAPP_URL:
        sheet = AppsScriptSheet(config.SHEETS_WEBAPP_URL, config.SHEETS_SHARED_SECRET)
        return MirroredSheet(sheet, CsvSheet(config.CSV_PATH)) if config.LOCAL_COPY else sheet
    return CsvSheet(config.CSV_PATH)
