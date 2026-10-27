"""Database tables."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def is_running_type(type_key: str | None) -> bool:
    """Road, trail, treadmill, track, indoor, virtual and ultra runs all count as running."""
    t = (type_key or "").lower()
    return "running" in t or t.endswith("_run") or t == "run"


class Activity(Base):
    __tablename__ = "activities"

    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    activity_type: Mapped[str | None] = mapped_column(String(64), index=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    activity_name: Mapped[str | None] = mapped_column(Text)
    start_time_local: Mapped[datetime] = mapped_column(DateTime, index=True)
    start_time_gmt: Mapped[datetime | None] = mapped_column(DateTime)
    distance_m: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    moving_duration_s: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    avg_speed_mps: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[float | None] = mapped_column(Float)
    max_hr: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float | None] = mapped_column(Float)
    training_load: Mapped[float | None] = mapped_column(Float)
    aerobic_te: Mapped[float | None] = mapped_column(Float)
    anaerobic_te: Mapped[float | None] = mapped_column(Float)
    avg_cadence_spm: Mapped[float | None] = mapped_column(Float)
    avg_power_w: Mapped[float | None] = mapped_column(Float)
    vo2max: Mapped[float | None] = mapped_column(Float)
    location_name: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)  # full Garmin record
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                onupdate=func.now())


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | success | error
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ActivityDetails(Base):
    """Parsed FIT data for one activity, fetched from Garmin the first time it's opened."""

    __tablename__ = "activity_details"

    activity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("activities.activity_id", ondelete="CASCADE"), primary_key=True)
    parser_version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)  # series, km splits and laps (see app/fit.py)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plan(Base):
    """A training or nutrition plan, imported from JSON (see app/plans.py for the format)."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # training | nutrition
    name: Mapped[str] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    race_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    source: Mapped[str] = mapped_column(String(16), default="import")  # import | generated
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # paces, targets, rules
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    days: Mapped[list["PlanDay"]] = relationship(back_populates="plan", cascade="all, delete-orphan",
                                                 order_by="PlanDay.date")


class PlanDay(Base):
    __tablename__ = "plan_days"
    __table_args__ = (UniqueConstraint("plan_id", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16))  # easy | workout | long | rest | race | recovery | ...
    planned_km: Mapped[float | None] = mapped_column(Float)  # training plans
    kcal: Mapped[int | None] = mapped_column(Integer)  # nutrition plans
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # notes, strength, meals, protein
    plan: Mapped[Plan] = relationship(back_populates="days")


class WeightEntry(Base):
    __tablename__ = "weight_entries"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    weight_kg: Mapped[float] = mapped_column(Float)


class DailySummary(Base):
    """Garmin's daily totals (calories burned, steps, resting HR), cached for the nutrition comparison."""

    __tablename__ = "daily_summaries"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    burned_total: Mapped[int | None] = mapped_column(Integer)
    burned_active: Mapped[int | None] = mapped_column(Integer)
    burned_resting: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[int | None] = mapped_column(Integer)
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
