"""All activities: filter by period, type and text; every row opens the detail page."""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import auth, periods
from .db import get_session
from .models import Activity
from .web import templates, today

router = APIRouter()
PAGE_SIZE = 50
KINDS = {"all": "All types", "running": "Running", "other": "Other"}


def _parse_date(value: str) -> date | None:
    """Lenient: the form always sends the date fields, empty unless a custom range is chosen."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _filters(period: periods.Period, kind: str, q: str):
    conditions = [Activity.start_time_local < datetime.combine(period.end + timedelta(days=1), time.min)]
    if period.start:
        conditions.append(Activity.start_time_local >= datetime.combine(period.start, time.min))
    if kind == "running":
        conditions.append(Activity.is_running)
    elif kind == "other":
        conditions.append(~Activity.is_running)
    if q:
        like = f"%{q}%"
        conditions.append(or_(Activity.activity_name.ilike(like), Activity.location_name.ilike(like),
                              Activity.activity_type.ilike(like)))
    return conditions


def period_totals(session: Session, conditions) -> dict:
    row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Activity.distance_m), 0),
            func.coalesce(func.sum(Activity.duration_s), 0),
            func.coalesce(func.sum(Activity.distance_m).filter(Activity.is_running), 0),
            func.coalesce(func.sum(Activity.duration_s).filter(Activity.is_running), 0),
            func.count().filter(Activity.is_running),
            func.coalesce(func.sum(Activity.elevation_gain_m), 0),
        ).where(*conditions)
    ).one()
    count, dist, secs, run_dist, run_secs, runs, elevation = row
    return {
        "count": count,
        "km": round(dist / 1000, 1),
        "hours": round(secs / 3600, 1),
        "runs": runs,
        "run_km": round(run_dist / 1000, 1),
        "run_pace_s_km": round(run_secs / (run_dist / 1000)) if run_dist else None,
        "elevation_m": round(elevation),
    }


@router.get("/activities", response_class=HTMLResponse)
def activities_page(request: Request, period: str | None = None, start: str = "", end: str = "",
                    kind: str = "all", q: str = "", page: int = 1,
                    _: str = Depends(auth.require_page_user), session: Session = Depends(get_session)):
    resolved = periods.resolve(period, _parse_date(start), _parse_date(end), today())
    kind = kind if kind in KINDS else "all"
    q = q.strip()[:100]
    page = max(1, page)
    conditions = _filters(resolved, kind, q)
    rows = list(session.scalars(
        select(Activity).where(*conditions).order_by(Activity.start_time_local.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)
    ))
    return templates.TemplateResponse(request, "activities.html", {
        "period": resolved,
        "presets": periods.PRESETS,
        "kinds": KINDS,
        "kind": kind,
        "q": q,
        "page": page,
        "has_more": len(rows) > PAGE_SIZE,
        "activities": rows[:PAGE_SIZE],
        "totals": period_totals(session, conditions),
    })
