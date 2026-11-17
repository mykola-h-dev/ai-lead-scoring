"""HTTP server for the lead form demo (Python standard library only).

Routes:
  GET  /                -> sample "request a quote" page with the lead form
  GET  /static/...      -> CSS / JS
  GET  /api/status      -> which sheet and notifiers are active
  GET  /api/leads       -> leads stored in the local CSV (demo mode only)
  POST /api/leads       -> submit a lead (JSON or regular form encoding)

Run:  python -m app.server
"""

import csv
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import config
from .notify import make_notifiers
from .pipeline import LeadPipeline
from .sheets import CsvSheet, make_sheet

STATIC_DIR = config.ROOT / "static"
MAX_BODY = 20_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lead-to-sheet")

SHEET = make_sheet(config)
PIPELINE = LeadPipeline(SHEET, make_notifiers(config))


def parse_body(handler):
    """Read the request body as JSON or URL-encoded form data into a flat dict."""
    length = min(int(handler.headers.get("Content-Length", 0) or 0), MAX_BODY)
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    if "application/json" in handler.headers.get("Content-Type", ""):
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    return {key: values[0] for key, values in parse_qs(raw).items()}


class LeadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            target = (STATIC_DIR / path[len("/static/"):]).resolve()
            if STATIC_DIR in target.parents and target.is_file():
                return self._send_file(target)
        if path == "/api/status":
            return self._send_json({"sheet": SHEET.name, "notifiers": [n.name for n in PIPELINE.notifiers]})
        if path == "/api/leads":
            return self._send_json({"leads": self._demo_leads()})
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/leads":
            return self._send_json({"error": "Not found"}, status=404)
        try:
            form = parse_body(self)
        except (ValueError, json.JSONDecodeError):
            return self._send_json({"ok": False, "errors": {"form": "Invalid request body."}}, status=400)
        status, body = PIPELINE.process(form)
        self._send_json(body, status=status)

    @staticmethod
    def _demo_leads():
        """Only expose stored leads when using the local demo CSV, never a real sheet."""
        if not isinstance(SHEET, CsvSheet) or not SHEET.path.exists():
            return []
        with SHEET.path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))[-10:]

    def _send_file(self, path):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


def main():
    log.info("Sheet: %s", SHEET.name)
    log.info("Notifications: %s", ", ".join(n.name for n in PIPELINE.notifiers))
    server = ThreadingHTTPServer((config.HOST, config.PORT), LeadHandler)
    log.info("Open http://%s:%d in your browser", config.HOST, config.PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
