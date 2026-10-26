"""Incremental sync from Garmin Connect into Postgres.

Each run pages through activities newest-first and stops at the first page that
reaches past the re-check window and contains activities we already have. Activities inside the window
(default 14 days) are re-fetched so renames and edits made in Garmin are picked up,
and activities deleted in Garmin within the window are deleted here too.

    python -m app.sync                 # same as the dashboard button
    python -m app.sync --recheck-days 100000   # re-check everything
"""

import argparse
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from .garmin import GarminClient
from .models import Activity, SyncRun, is_running_type

log = logging.getLogger(__name__)

PAGE_SIZE = 99
RECHECK_DAYS = 14
LOCK_ID = 7_412_001  # arbitrary constant for pg_try_advisory_lock

# column -> key in the Garmin activity search response
FIELDS = {
    "activity_name": "activityName",
    "distance_m": "distance",
    "duration_s": "duration",
    "moving_duration_s": "movingDuration",
    "elevation_gain_m": "elevationGain",
    "avg_speed_mps": "averageSpeed",
    "avg_hr": "averageHR",
    "max_hr": "maxHR",
    "calories": "calories",
    "training_load": "activityTrainingLoad",
    "aerobic_te": "aerobicTrainingEffect",
    "anaerobic_te": "anaerobicTrainingEffect",
    "avg_cadence_spm": "averageRunningCadenceInStepsPerMinute",
    "avg_power_w": "avgPower",
    "vo2max": "vO2MaxValue",
    "location_name": "locationName",
}


class SyncInProgress(Exception):
    pass


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def to_values(a: dict) -> dict:
    type_key = (a.get("activityType") or {}).get("typeKey")
    return {
        "activity_id": a["activityId"],
        "activity_type": type_key,
        "is_running": is_running_type(type_key),
        "start_time_local": _parse_time(a.get("startTimeLocal")),
        "start_time_gmt": _parse_time(a.get("startTimeGMT")),
        **{col: a.get(key) for col, key in FIELDS.items()},
        "raw": a,
    }


def sync_activities(engine: Engine, client: GarminClient, recheck_days: int = RECHECK_DAYS,
                    page_size: int = PAGE_SIZE, pause_s: float = 0.3) -> SyncRun:
    """Run one sync. Raises SyncInProgress if another sync holds the lock."""
    with engine.connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}).scalar():
            raise SyncInProgress()
        try:
            with Session(engine, expire_on_commit=False) as session:
                return _run(session, client, recheck_days, page_size, pause_s)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
            lock_conn.commit()


def _run(session: Session, client: GarminClient, recheck_days: int, page_size: int, pause_s: float) -> SyncRun:
    run = SyncRun(status="running")
    session.add(run)
    session.commit()
    try:
        known = set(session.scalars(select(Activity.activity_id)))
        cutoff = datetime.now() - timedelta(days=recheck_days)
        seen_in_window: set[int] = set()
        fetched_any = False
        start = 0
        while True:
            page = client.search_activities(start=start, limit=page_size, sortOrder="desc")
            if not page:
                break
            fetched_any = True
            page_has_known = False
            oldest = None
            for a in page:
                values = to_values(a)
                started = values["start_time_local"]
                oldest = started if oldest is None or started < oldest else oldest
                in_window = started >= cutoff
                if in_window:
                    seen_in_window.add(values["activity_id"])
                if values["activity_id"] not in known:
                    session.add(Activity(**values))
                    known.add(values["activity_id"])
                    run.added += 1
                    continue
                page_has_known = True
                if in_window:
                    existing = session.get(Activity, values["activity_id"])
                    if existing.raw != a:
                        for col, value in values.items():
                            setattr(existing, col, value)
                        run.updated += 1
            session.commit()
            log.info("page at %d: %d activities, %d added so far", start, len(page), run.added)
            # New activities only ever appear at the top, so once we're past the window
            # and into already-synced history there's nothing more to fetch.
            if len(page) < page_size or (page_has_known and oldest < cutoff):
                break
            start += page_size
            time.sleep(pause_s)

        # Every activity in the window was fetched above, so anything missing was deleted in Garmin.
        if fetched_any:
            result = session.execute(
                delete(Activity).where(Activity.start_time_local >= cutoff,
                                       Activity.activity_id.not_in(seen_in_window or {-1}))
            )
            run.deleted = result.rowcount or 0

        run.status = "success"
    except Exception as e:
        session.rollback()
        run.status = "error"
        run.error = str(e)[:2000]
        raise
    finally:
        run.finished_at = func.now()
        session.add(run)
        session.commit()
    return run


def main(argv: list[str] | None = None) -> None:
    from .config import get_settings
    from .db import get_engine

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recheck-days", type=int, default=RECHECK_DAYS)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run = sync_activities(get_engine(), GarminClient(get_settings().garmin_api_key), recheck_days=args.recheck_days)
    print(f"{run.status}: +{run.added} added, {run.updated} updated, {run.deleted} deleted")


if __name__ == "__main__":
    main()
