"""HTTP server for the lead form demo (Python standard library only).

Routes:
  GET  /                  -> sample "request a quote" page with the lead form
  GET  /dashboard         -> leads dashboard (counts by score / source, table, CSV export)
  GET  /static/...        -> CSS / JS
  GET  /api/status        -> which sheet, notifiers, scoring and auto-reply modes are active
  GET  /api/leads         -> stored leads (+ counts and auto-reply drafts), from the local CSV copy
  GET  /api/leads.csv     -> CSV export (?score=hot&source=google to filter)
  POST /api/leads         -> submit a lead (JSON or regular form encoding)

The dashboard and the leads API are protected with HTTP Basic auth when
DASHBOARD_PASSWORD is set. Run:  python -m app.server
"""

import base64
import hmac
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import config, llm
from .autoreply import AutoReplier
from .crm import make_crm
from .dashboard import filter_rows, summarise, to_csv
from .notify import make_notifiers
from .pipeline import LeadPipeline
from .sheets import make_sheet
from .spam import RateLimiter, load_domains

STATIC_DIR = config.ROOT / "static"
MAX_BODY = 20_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lead-to-sheet")


def build_pipeline(cfg=config):
    sheet = make_sheet(cfg)
    smtp = (cfg.SMTP_HOST, cfg.SMTP_PORT, cfg.SMTP_USER, cfg.SMTP_PASSWORD) if cfg.SMTP_HOST else None
    replier = AutoReplier(cfg.AUTOREPLY_STORE, cfg.OUTBOX_LOG, mode=cfg.AUTO_REPLY_MODE, smtp=smtp,
                          sender=cfg.AUTO_REPLY_FROM or cfg.ALERT_EMAIL_FROM)
    return LeadPipeline(
        sheet, make_notifiers(cfg), business=cfg.BUSINESS_NAME,
        rate_limiter=RateLimiter(cfg.RATE_LIMIT_MAX, cfg.RATE_LIMIT_WINDOW),
        extra_disposable=load_domains(cfg.DISPOSABLE_DOMAINS_FILE),
        auto_replier=replier, crm=make_crm(cfg))


PIPELINE = build_pipeline()


def parse_body(handler):
    """Read the request body as JSON or URL-encoded form data into a flat dict."""
    length = min(int(handler.headers.get("Content-Length", 0) or 0), MAX_BODY)
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    if "application/json" in handler.headers.get("Content-Type", ""):
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    return {key: values[0] for key, values in parse_qs(raw).items()}


def status_info(pipeline=None):
    pipeline = pipeline or PIPELINE
    replier = pipeline.auto_replier
    return {
        "sheet": pipeline.sheet.name,
        "notifiers": [n.name for n in pipeline.notifiers],
        "scoring": f"AI ({llm.provider_name()})" if llm.is_available() else "rules (offline)",
        "auto_reply": "off" if not replier else ("send" if replier.sends_email else replier.mode),
        "crm": pipeline.crm.name if pipeline.crm else "off",
        "dashboard": hasattr(pipeline.sheet, "rows"),
    }


def _int(value, default):
    try:
        return max(1, min(int(value), 5000))
    except (TypeError, ValueError):
        return default


class LeadHandler(BaseHTTPRequestHandler):
    server_version = "LeadToSheet"

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        if path in ("/", "/index.html"):
            return self._send_file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            target = (STATIC_DIR / path[len("/static/"):]).resolve()
            if STATIC_DIR in target.parents and target.is_file():
                return self._send_file(target)
        if path == "/api/status":
            return self._send_json(status_info())
        if path in ("/dashboard", "/api/leads", "/api/leads.csv"):
            if not self._authorised():
                return self._ask_for_auth()
            if path == "/dashboard":
                return self._send_file(STATIC_DIR / "dashboard.html")
            rows = filter_rows(self._rows(), query.get("score"), query.get("source"))
            if path == "/api/leads.csv":
                return self._send_bytes(to_csv(rows).encode("utf-8"), "text/csv; charset=utf-8",
                                        {"Content-Disposition": 'attachment; filename="leads.csv"'})
            drafts = PIPELINE.auto_replier.drafts_by_email() if PIPELINE.auto_replier else {}
            leads = [{**r, "auto_reply": drafts.get(r.get("email", "").lower())} for r in reversed(rows)]
            return self._send_json({"summary": summarise(rows), "leads": leads[:_int(query.get("limit"), 200)]})
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/leads":
            return self._send_json({"error": "Not found"}, status=404)
        try:
            form = parse_body(self)
        except (ValueError, json.JSONDecodeError):
            return self._send_json({"ok": False, "errors": {"form": "Invalid request body."}}, status=400)
        status, body = PIPELINE.process(form, client_ip=self._client_ip())
        self._send_json(body, status=status)

    # --- helpers -------------------------------------------------------
    def _client_ip(self):
        if config.TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    @staticmethod
    def _rows():
        """Leads from the local CSV (demo) or the local copy kept next to a Google Sheet."""
        sheet = PIPELINE.sheet
        return sheet.rows() if hasattr(sheet, "rows") else []

    def _authorised(self):
        if not config.DASHBOARD_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            _, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(password, config.DASHBOARD_PASSWORD)

    def _ask_for_auth(self):
        self._send_bytes(b'{"error": "Authentication required"}', "application/json", status=401,
                         headers={"WWW-Authenticate": 'Basic realm="Leads dashboard"'})

    def _send_file(self, path):
        self._send_bytes(path.read_bytes(), mimetypes.guess_type(str(path))[0] or "application/octet-stream")

    def _send_json(self, data, status=200):
        self._send_bytes(json.dumps(data).encode("utf-8"), "application/json", status=status)

    def _send_bytes(self, body, content_type, headers=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


def main():
    info = status_info()
    for key in ("sheet", "notifiers", "scoring", "auto_reply", "crm"):
        log.info("%s: %s", key, info[key])
    if config.HOST not in ("127.0.0.1", "localhost") and not config.DASHBOARD_PASSWORD:
        log.warning("Dashboard is open to the network - set DASHBOARD_PASSWORD")
    server = ThreadingHTTPServer((config.HOST, config.PORT), LeadHandler)
    log.info("Open http://%s:%d (form) and http://%s:%d/dashboard", config.HOST, config.PORT, config.HOST, config.PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
