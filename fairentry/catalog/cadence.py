"""Cadence -> max age. A field is 'due' for a ticker if it's missing or older
than its cadence window. Used to refresh only what's due (runtime budget).
"""
from __future__ import annotations

from datetime import datetime, timezone

CADENCE_HOURS = {
    "twice_daily": 8,
    "daily": 24,
    "weekly": 24 * 7,
    "filing_based": 24 * 7,     # re-check weekly; parser dedups by filing
    "event_based": 24,
}


def max_age_hours(field: dict) -> float:
    return field.get("freshness_limit_h") or CADENCE_HOURS.get(field["cadence"], 24)


def is_due(fetched_at: str | None, field: dict, now: datetime | None = None) -> bool:
    if not fetched_at:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        age_h = (now - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
    except ValueError:
        return True
    return age_h >= max_age_hours(field)
