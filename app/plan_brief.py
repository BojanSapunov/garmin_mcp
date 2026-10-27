"""Briefs for generating a new plan.

build_brief() writes everything a planner needs (recent training, the goal, the exact JSON format) as one
prompt. Today it's copied into Claude and the JSON answer is imported on the Plans page. To generate inside
the app later, send the same brief to the Claude API and pass the reply to plans.PlanIn / import_plan
with source="generated"; nothing else changes.
"""

import json
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Activity
from .plans import PlanIn


class PlanRequest(BaseModel):
    kind: str = Field(pattern="^(training|nutrition)$")
    goal: str = Field(min_length=3, max_length=500)  # e.g. "Sub-3:15 at Vienna Marathon"
    start_date: date
    end_date: date
    race_date: date | None = None
    weekly_km_min: int | None = Field(default=None, ge=0, le=300)
    weekly_km_max: int | None = Field(default=None, ge=0, le=300)
    run_days_per_week: int | None = Field(default=None, ge=1, le=7)
    strength_days_per_week: int | None = Field(default=None, ge=0, le=7)
    weight_kg: float | None = Field(default=None, ge=30, le=200)
    target_weight_kg: float | None = Field(default=None, ge=30, le=200)
    height_cm: int | None = Field(default=None, ge=100, le=230)
    notes: str = Field(default="", max_length=2000)  # preferences, injuries, diet style


def training_summary(session: Session, today: date, weeks: int = 12) -> dict:
    since = today - timedelta(weeks=weeks)
    week = func.date_trunc("week", Activity.start_time_local)
    weekly = session.execute(
        select(week, func.sum(Activity.distance_m), func.count(), func.max(Activity.distance_m))
        .where(Activity.is_running, Activity.start_time_local >= datetime.combine(since, time.min))
        .group_by(week).order_by(week)
    ).all()
    races = session.scalars(
        select(Activity).where(Activity.is_running, Activity.distance_m > 9500,
                               Activity.start_time_local >= datetime.combine(today - timedelta(days=730), time.min),
                               Activity.raw["eventType"]["typeKey"].astext == "race")
        .order_by(Activity.start_time_local.desc()).limit(8)
    )
    return {
        "weeks": [{"week_of": w.date().isoformat(), "km": round((km or 0) / 1000, 1), "runs": n,
                   "longest_km": round((longest or 0) / 1000, 1)} for w, km, n, longest in weekly],
        "recent_races": [{"date": r.start_time_local.date().isoformat(), "name": r.activity_name,
                          "km": round((r.distance_m or 0) / 1000, 2), "time_s": round(r.duration_s or 0),
                          "avg_hr": r.avg_hr} for r in races],
    }


def build_brief(session: Session, req: PlanRequest, today: date) -> str:
    summary = training_summary(session, today)
    schema = json.dumps(PlanIn.model_json_schema(), indent=1)
    lines = [
        f"Create a {req.kind} plan as JSON.",
        "",
        "## Goal and constraints",
        f"- Goal: {req.goal}",
        f"- Plan dates: {req.start_date} to {req.end_date}" + (f"; race on {req.race_date}" if req.race_date else ""),
    ]
    if req.weekly_km_min or req.weekly_km_max:
        lines.append(f"- Weekly running volume: {req.weekly_km_min or '?'}–{req.weekly_km_max or '?'} km, "
                     "lower in down weeks and the taper")
    if req.run_days_per_week:
        lines.append(f"- Running days per week: {req.run_days_per_week}")
    if req.strength_days_per_week is not None:
        lines.append(f"- Strength sessions per week: {req.strength_days_per_week}")
    if req.weight_kg:
        height = f", height {req.height_cm} cm" if req.height_cm else ""
        lines.append(f"- Current weight: {req.weight_kg} kg{height}")
    if req.target_weight_kg:
        lines.append(f"- Target weight: {req.target_weight_kg} kg")
    if req.notes:
        lines.append(f"- Notes: {req.notes}")
    lines += [
        "",
        f"## Recent training (last 12 weeks, from Garmin, as of {today})",
        "```json",
        json.dumps(summary, indent=1),
        "```",
        "",
        "## Output format",
        "Reply with only one JSON object that validates against this JSON Schema, one entry per calendar day "
        "from the start date to the end date (include rest days).",
        "- Training plans: set planned_km on every day (0 for rest), category one of "
        f"{', '.join(c for c in ('easy', 'recovery', 'workout', 'long', 'rest', 'race'))}, "
        "a short title with the session (e.g. '5 × 2 km @ 4:15'), strength like 'A' or 'B (light)' when planned, "
        "and notes for paces or purpose. Put the pace zones in meta.paces.",
        "- Nutrition plans: set kcal on every day, protein_g, category matching the training day type, and meals "
        "(name, items, kcal, protein_g). Put start_weight_kg, target_weight_kg and deficit_end (YYYY-MM-DD) in meta.",
        "```json",
        schema,
        "```",
    ]
    return "\n".join(lines)
