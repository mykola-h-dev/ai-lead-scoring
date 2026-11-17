"""Read-side helpers for the leads dashboard: counts and CSV export."""

import csv
import io
from collections import Counter

from .sheets import COLUMNS, safe_cell

SCORE_ORDER = ("hot", "warm", "cold")


def summarise(rows):
    """Counts for the dashboard cards and tables."""
    by_score = Counter(r.get("score") or "unscored" for r in rows)
    return {
        "total": len(rows),
        "by_score": {s: by_score.get(s, 0) for s in (*SCORE_ORDER, "unscored") if s != "unscored" or by_score.get(s)},
        "by_source": dict(Counter(r.get("source") or "website" for r in rows).most_common()),
        "by_service": dict(Counter(r.get("service") or "-" for r in rows).most_common()),
        "by_urgency": dict(Counter(r.get("urgency") or "-" for r in rows).most_common()),
    }


def filter_rows(rows, score=None, source=None):
    return [r for r in rows
            if (not score or r.get("score") == score) and (not source or r.get("source") == source)]


def to_csv(rows):
    """CSV export with the formula-injection guard applied to every cell."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: safe_cell(row.get(c, "")) if c != "phone" else row.get(c, "") for c in COLUMNS})
    return buf.getvalue()
