"""Garmin daily summaries (calories burned, steps, resting HR), cached in daily_summaries."""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .garmin import GarminClient
from .models import DailySummary

log = logging.getLogger(__name__)
CHUNK_DAYS = 28
REFRESH_AFTER = timedelta(hours=1)  # recent days keep changing until the day is over


def ensure_daily_summaries(session: Session, client: GarminClient, start: date, end: date, today: date) -> None:
    """Fetch days in [start, min(end, today)] that are missing, or recent and stale. Garmin errors are logged
    and the cached data is used as is, so a page never fails because of this."""
    end = min(end, today)
    if start > end:
        return
    cached = {s.date: s for s in session.scalars(
        select(DailySummary).where(DailySummary.date >= start, DailySummary.date <= end))}
    now = datetime.now(UTC)
    needed = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    needed = [d for d in needed if d not in cached
              or (d >= today - timedelta(days=1) and now - cached[d].fetched_at > REFRESH_AFTER)]
    if not needed:
        return
    chunk_start = needed[0]
    while chunk_start <= needed[-1]:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), needed[-1])
        try:
            rows = client.daily_summaries(chunk_start, chunk_end)
        except Exception as e:  # noqa: BLE001 - optional data; keep the page working
            log.warning("daily summaries %s..%s failed: %s", chunk_start, chunk_end, e)
            return
        for row in rows:
            summary = parse_daily_summary(row)
            if summary:
                session.merge(summary)
        session.commit()
        chunk_start = chunk_end + timedelta(days=1)


def parse_daily_summary(row: dict) -> DailySummary | None:
    try:
        day = date.fromisoformat(row["calendarDate"])
    except (KeyError, ValueError):
        return None
    calories = row.get("calories") or {}
    return DailySummary(
        date=day,
        burned_total=calories.get("burnedTotal"),
        burned_active=calories.get("burnedActive"),
        burned_resting=calories.get("burnedResting"),
        steps=((row.get("movement") or {}).get("steps") or {}).get("value"),
        resting_hr=(row.get("heartRate") or {}).get("restingValue"),
        fetched_at=datetime.now(UTC),
    )
