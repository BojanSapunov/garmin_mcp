"""Database tables."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

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
