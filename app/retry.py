"""Small retry helper with exponential backoff for flaky network calls."""

import logging
import time

log = logging.getLogger("lead-to-sheet")


class PermanentError(Exception):
    """An error that retrying will not fix (e.g. bad credentials, 4xx)."""


def with_retry(func, *args, attempts=3, base_delay=1.0, label="call", **kwargs):
    """Call func(*args, **kwargs), retrying on errors with 1s, 2s, 4s... delays.

    PermanentError is raised immediately without retrying. After the last
    attempt the original exception is re-raised.
    """
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except PermanentError:
            raise
        except Exception as exc:  # noqa: BLE001 - we log and retry any transient error
            if attempt == attempts:
                log.error("%s failed after %d attempts: %s", label, attempts, exc)
                raise
            delay = base_delay * 2 ** (attempt - 1)
            log.warning("%s failed (attempt %d/%d): %s - retrying in %.0fs", label, attempt, attempts, exc, delay)
            time.sleep(delay)
