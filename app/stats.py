"""Aggregations for the dashboard."""

import re
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Activity, SyncRun

# How many buckets each period shows
PERIODS = {
    "week": ("week", 16, "1 week"),
    "month": ("month", 12, "1 month"),
    "year": ("year", None, "1 year"),  # None = since the first activity
}
# Applied after stripping Garmin's version suffixes (_ws, _v2, ...)
TYPE_ALIASES = {
    "resort_skiing_snowboarding": "resort_skiing",
    "backcountry_skiing_snowboarding": "backcountry_skiing",
}
_VERSION_SUFFIX = re.compile(r"_(ws|v\d+)$")
MAX_OTHER_TYPES = 4  # other types beyond this fold into "other" (keeps to the validated palette slots 2-5)


def summary(session: Session, days: int = 30) -> dict:
    row = session.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE is_running)                       AS runs,
          COALESCE(SUM(distance_m) FILTER (WHERE is_running), 0)    AS run_m,
          COALESCE(SUM(duration_s) FILTER (WHERE is_running), 0)    AS run_s,
          COALESCE(SUM(elevation_gain_m) FILTER (WHERE is_running), 0) AS run_elev,
          AVG(avg_hr) FILTER (WHERE is_running)                     AS run_hr,
          MAX(distance_m) FILTER (WHERE is_running)                 AS longest_m,
          COUNT(*) FILTER (WHERE NOT is_running)                    AS other_count,
          COALESCE(SUM(duration_s) FILTER (WHERE NOT is_running), 0) AS other_s
        FROM activities WHERE start_time_local >= now() - make_interval(days => :days)
    """), {"days": days}).mappings().one()
    run_km = row["run_m"] / 1000
    return {
        "days": days,
        "runs": row["runs"],
        "run_km": round(run_km, 1),
        "run_hours": round(row["run_s"] / 3600, 1),
        "run_pace_s_per_km": round(row["run_s"] / run_km) if run_km else None,
        "run_elevation_m": round(row["run_elev"]),
        "run_avg_hr": round(row["run_hr"]) if row["run_hr"] else None,
        "longest_run_km": round(row["longest_m"] / 1000, 1) if row["longest_m"] else None,
        "other_count": row["other_count"],
        "other_hours": round(row["other_s"] / 3600, 1),
    }


def distance_series(session: Session, period: str) -> dict:
    """Distance per period: running as one series, other activities by type."""
    unit, count, step = PERIODS[period]
    if count is None:
        first = session.scalar(select(Activity.start_time_local).order_by(Activity.start_time_local).limit(1))
        start_expr = "date_trunc(:unit, CAST(:first AS timestamp))"
        params = {"first": first or datetime.now()}
    else:
        start_expr = f"date_trunc(:unit, now()) - interval '{count - 1} {unit}'"
        params = {}
    params["unit"] = unit

    rows = session.execute(text(f"""
        WITH buckets AS (
          SELECT generate_series({start_expr}, date_trunc(:unit, now()), interval '{step}') AS bucket
        )
        SELECT b.bucket,
               CASE WHEN a.is_running THEN 'running' ELSE a.activity_type END AS kind,
               COALESCE(SUM(a.distance_m), 0) / 1000.0 AS km
        FROM buckets b
        LEFT JOIN activities a ON date_trunc(:unit, a.start_time_local) = b.bucket
        GROUP BY b.bucket, kind
        ORDER BY b.bucket
    """), params).all()

    buckets = sorted({r.bucket for r in rows})
    index = {b: i for i, b in enumerate(buckets)}
    running = [0.0] * len(buckets)
    other: dict[str, list[float]] = {}
    for r in rows:
        if r.kind is None:
            continue
        if r.kind == "running":
            running[index[r.bucket]] += r.km
        else:
            other.setdefault(canonical_type(r.kind), [0.0] * len(buckets))[index[r.bucket]] += r.km

    # Order other types by their all-time distance so each type keeps its colour across periods
    ranking = [t for t in other_type_ranking(session) if t in other]
    shown = ranking[:MAX_OTHER_TYPES]
    series = [{"type": t, "km": [round(v, 1) for v in other[t]]} for t in shown]
    rest = [t for t in other if t not in shown and any(other[t])]  # skip types without distance (e.g. yoga)
    if rest:
        series.append({"type": "other", "km": [round(sum(other[t][i] for t in rest), 1)
                                              for i in range(len(buckets))]})
    return {
        "period": period,
        "labels": [_label(b, unit) for b in buckets],
        "running_km": [round(v, 1) for v in running],
        "other": series,
    }


def canonical_type(type_key: str) -> str:
    """Merge type keys Garmin has renamed over the years, so one sport is one series."""
    base = _VERSION_SUFFIX.sub("", type_key)
    return TYPE_ALIASES.get(base, base)


def other_type_ranking(session: Session) -> list[str]:
    totals: dict[str, float] = {}
    for type_key, km in session.execute(text("""
        SELECT activity_type, SUM(distance_m) FROM activities WHERE NOT is_running AND COALESCE(distance_m, 0) > 0
        GROUP BY activity_type
    """)):
        totals[canonical_type(type_key)] = totals.get(canonical_type(type_key), 0) + km
    return sorted(totals, key=totals.get, reverse=True)


def _label(bucket: datetime, unit: str) -> str:
    if unit == "week":
        return bucket.strftime("%d %b")
    if unit == "month":
        return bucket.strftime("%b %y")
    return bucket.strftime("%Y")


def recent_activities(session: Session, days: int = 31) -> list[Activity]:
    since = datetime.now() - timedelta(days=days)
    return list(session.scalars(
        select(Activity).where(Activity.start_time_local >= since).order_by(Activity.start_time_local.desc())
    ))


def last_sync(session: Session) -> SyncRun | None:
    return session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
