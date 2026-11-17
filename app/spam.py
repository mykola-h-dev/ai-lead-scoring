"""Spam protection: disposable-email check and a per-IP rate limiter.

The honeypot field lives in validation.py. These checks are deliberately
simple and dependency-free; for heavy abuse put a CAPTCHA/Turnstile in front.
"""

import threading
import time
from collections import defaultdict, deque

# A short built-in list of well-known throwaway-inbox domains. Extend it, or load a
# maintained list (e.g. the open-source "disposable-email-domains" project) into
# DISPOSABLE_DOMAINS_FILE, one domain per line.
DISPOSABLE_DOMAINS = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net sharklasers.com 10minutemail.com
10minutemail.net tempmail.com temp-mail.org tempmailo.com yopmail.com yopmail.fr
trashmail.com getnada.com dispostable.com maildrop.cc throwawaymail.com fakeinbox.com
mailnesia.com mintemail.com mohmal.com emailondeck.com spamgourmet.com tempr.email
discard.email burnermail.io moakt.com mytemp.email tempinbox.com
""".split())


def load_domains(path):
    """Extra domains from a text file (one per line, # comments allowed)."""
    if not path or not path.exists():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(l.strip().lower() for l in lines if l.strip() and not l.startswith("#"))


def is_disposable(email, extra=frozenset()):
    domain = email.rsplit("@", 1)[-1].lower()
    # also catch subdomains such as "abc.mailinator.com"
    candidates = {domain} | {".".join(domain.split(".")[i:]) for i in range(1, domain.count("."))}
    return bool(candidates & (DISPOSABLE_DOMAINS | extra))


class RateLimiter:
    """Allow at most `max_events` per key in a sliding window of `window` seconds."""

    def __init__(self, max_events=5, window=600, clock=time.monotonic):
        self.max_events, self.window, self.clock = max_events, window, clock
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        if self.max_events <= 0:  # 0 disables the limit
            return True
        now = self.clock()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.max_events:
                return False
            events.append(now)
            if len(self._events) > 10_000:  # keep memory bounded
                for stale in [k for k, v in self._events.items() if not v]:
                    del self._events[stale]
            return True
