"""Training and nutrition plans: the JSON import format, importing, and plan-vs-actual comparisons.

A plan is a list of days. The same format is used for plans written by hand, converted from documents,
or generated (see app/plan_brief.py), so every source goes through PlanIn validation.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, DailySummary, Plan, PlanDay, WeightEntry

KCAL_PER_KG = 7700  # energy in 1 kg of body fat, for the deficit estimate
TRAINING_CATEGORIES = ("easy", "recovery", "workout", "long", "rest", "race", "strength", "cross")


# ---------- import format ----------

class MealIn(BaseModel):
    name: str
    items: str
    kcal: int | None = None
    protein_g: int | None = None


class PlanDayIn(BaseModel):
    date: date
    title: str = Field(min_length=1, max_length=300)
    category: str = "easy"
    planned_km: float | None = Field(default=None, ge=0, le=300)  # training plans
    strength: str | None = None  # e.g. "A", "B (light)"
    kcal: int | None = Field(default=None, ge=0, le=10000)  # nutrition plans
    protein_g: int | None = None
    notes: str | None = None
    meals: list[MealIn] = []


class PlanIn(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["training", "nutrition"]
    name: str = Field(min_length=1, max_length=200)
    goal: str | None = None
    race_date: date | None = None
    meta: dict = {}  # free-form: paces, target weight, rules shown on the plan page
    days: list[PlanDayIn] = Field(min_length=1, max_length=400)

    @field_validator("days")
    @classmethod
    def _sorted_unique(cls, days: list[PlanDayIn]) -> list[PlanDayIn]:
        dates = [d.date for d in days]
        if len(set(dates)) != len(dates):
            raise ValueError("each date may appear only once")
        return sorted(days, key=lambda d: d.date)

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "training" and self.days and all(d.planned_km is None for d in self.days):
            raise ValueError("a training plan needs planned_km on its days")
        if self.kind == "nutrition" and self.days and all(d.kcal is None for d in self.days):
            raise ValueError("a nutrition plan needs kcal on its days")
        return self


def import_plan(session: Session, plan_in: PlanIn, source: str = "import") -> Plan:
    """Create the plan; an active plan of the same kind and name is replaced (so re-importing updates it)."""
    for old in session.scalars(select(Plan).where(Plan.kind == plan_in.kind, Plan.name == plan_in.name,
                                                   Plan.status == "active")):
        session.delete(old)
    plan = Plan(kind=plan_in.kind, name=plan_in.name, goal=plan_in.goal, race_date=plan_in.race_date,
                start_date=plan_in.days[0].date, end_date=plan_in.days[-1].date, status="active",
                source=source, meta=plan_in.meta)
    for d in plan_in.days:
        details = {k: v for k, v in {"strength": d.strength, "notes": d.notes, "protein_g": d.protein_g,
                                     "meals": [m.model_dump() for m in d.meals]}.items() if v}
        plan.days.append(PlanDay(date=d.date, title=d.title, category=d.category, planned_km=d.planned_km,
                                 kcal=d.kcal, details=details))
    session.add(plan)
    session.commit()
    return plan


def active_plans(session: Session) -> list[Plan]:
    return list(session.scalars(select(Plan).where(Plan.status == "active").order_by(Plan.start_date.desc())))


def plan_day(session: Session, kind: str, day: date) -> PlanDay | None:
    return session.scalar(
        select(PlanDay).join(Plan).where(Plan.kind == kind, Plan.status == "active", PlanDay.date == day)
        .order_by(Plan.created_at.desc()).limit(1)
    )


# ---------- training: plan vs actual ----------

def _activities_by_day(session: Session, start: date, end: date) -> dict[date, list[Activity]]:
    rows = session.scalars(select(Activity).where(
        Activity.start_time_local >= datetime.combine(start, time.min),
        Activity.start_time_local < datetime.combine(end + timedelta(days=1), time.min),
    ).order_by(Activity.start_time_local))
    out: dict[date, list[Activity]] = defaultdict(list)
    for a in rows:
        out[a.start_time_local.date()].append(a)
    return out


def day_status(planned_km: float | None, category: str, actual_km: float, day: date, today: date) -> str:
    """done | partial | missed | extra | today | upcoming"""
    if day > today:
        return "upcoming"
    if category == "rest" or not planned_km:
        return "extra" if actual_km >= 1 else "done"
    ratio = actual_km / planned_km
    if ratio >= 0.85:
        return "done"
    if day == today:
        return "today" if ratio < 0.5 else "partial"
    return "partial" if ratio >= 0.5 else "missed"


def training_comparison(session: Session, plan: Plan, today: date) -> dict:
    acts = _activities_by_day(session, plan.start_date, plan.end_date)
    days, weeks = [], {}
    for d in plan.days:
        day_acts = acts.get(d.date, [])
        run_km = sum((a.distance_m or 0) for a in day_acts if a.is_running) / 1000
        strength_planned = d.details.get("strength")
        strength_done = any(a.activity_type == "strength_training" for a in day_acts)
        status = day_status(d.planned_km, d.category, run_km, d.date, today)
        week_start = d.date - timedelta(days=d.date.weekday())
        days.append({"day": d, "activities": day_acts, "run_km": round(run_km, 1), "status": status,
                     "strength_planned": strength_planned, "strength_done": strength_done,
                     "week_start": week_start})
        w = weeks.setdefault(week_start, {"start": week_start, "planned_km": 0.0, "actual_km": 0.0,
                                          "sessions": 0, "sessions_done": 0, "future": True})
        w["planned_km"] += d.planned_km or 0
        w["actual_km"] += run_km
        if d.date <= today:
            w["future"] = False
            if d.category != "rest":
                w["sessions"] += 1
                w["sessions_done"] += status in ("done", "partial")
    # Today counts once you've run (otherwise the morning looks like a miss)
    past = [x for x in days if x["day"].date < today or (x["day"].date == today and x["run_km"] > 0)]
    planned_to_date = sum(x["day"].planned_km or 0 for x in past)
    actual_to_date = sum(x["run_km"] for x in past)
    week_rows = [{**w, "planned_km": round(w["planned_km"], 1), "actual_km": round(w["actual_km"], 1)}
                 for w in weeks.values()]
    return {
        "days": days,
        "weeks": week_rows,
        "weeks_chart": [{"week": w["start"].isoformat(), "planned_km": w["planned_km"],
                         "actual_km": None if w["future"] else w["actual_km"]} for w in week_rows],
        "planned_to_date": round(planned_to_date, 1),
        "actual_to_date": round(actual_to_date, 1),
        "compliance": round(100 * actual_to_date / planned_to_date) if planned_to_date else None,
        "missed": sum(x["status"] == "missed" for x in past),
        "days_to_race": (plan.race_date - today).days if plan.race_date else None,
    }


# ---------- nutrition: targets vs Garmin burn and weight ----------

def nutrition_comparison(session: Session, plan: Plan, today: date) -> dict:
    burns = {s.date: s for s in session.scalars(select(DailySummary).where(
        DailySummary.date >= plan.start_date, DailySummary.date <= plan.end_date))}
    weights = {w.date: w.weight_kg for w in session.scalars(select(WeightEntry).where(
        WeightEntry.date >= plan.start_date - timedelta(days=14), WeightEntry.date <= plan.end_date))}
    meta = plan.meta or {}
    start_w, target_w = meta.get("start_weight_kg"), meta.get("target_weight_kg")
    deficit_end = date.fromisoformat(meta["deficit_end"]) if meta.get("deficit_end") else plan.end_date

    def expected(d: date) -> float | None:  # straight line from start weight to target over the deficit phase
        if start_w is None or target_w is None:
            return None
        span = max(1, (deficit_end - plan.start_date).days)
        progress = min(1.0, max(0.0, (d - plan.start_date).days / span))
        return round(start_w + (target_w - start_w) * progress, 2)

    days, cumulative = [], 0
    for d in plan.days:
        burn = burns.get(d.date)
        burned = burn.burned_total if burn and d.date < today else None  # today's burn is still incomplete
        balance = burned - d.kcal if burned is not None and d.kcal else None
        if balance is not None:
            cumulative += balance
        days.append({"day": d, "burned": burned, "balance": balance, "weight": weights.get(d.date),
                     "expected_weight": expected(d.date), "is_today": d.date == today,
                     "week_start": d.date - timedelta(days=d.date.weekday())})
    recent = [w for d, w in sorted(weights.items()) if today - timedelta(days=6) <= d <= today]
    avg7 = round(sum(recent) / len(recent), 2) if recent else None
    return {
        "days": days,
        "weights": sorted(weights.items()),
        "chart": {"days": [x["day"].date.isoformat() for x in days],
                  "expected": [x["expected_weight"] for x in days],
                  "weights": [[d.isoformat(), w] for d, w in sorted(weights.items())]},
        "avg7_weight": avg7,
        "start_weight": start_w,
        "target_weight": target_w,
        "to_go_kg": round(avg7 - target_w, 1) if avg7 is not None and target_w is not None else None,
        "expected_today": expected(today),
        "cumulative_deficit_kcal": cumulative,
        "estimated_loss_kg": round(cumulative / KCAL_PER_KG, 2),
        "days_with_burn": sum(1 for x in days if x["balance"] is not None),
    }


def record_weight(session: Session, day: date, weight_kg: float) -> None:
    session.merge(WeightEntry(date=day, weight_kg=round(weight_kg, 2)))
    session.commit()
