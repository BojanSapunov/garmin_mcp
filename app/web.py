"""Shared pieces for the web pages: templates and their filters, the local "today", and the fetch-only guard."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from .config import get_settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def today() -> date:
    """Today in the user's time zone (the server runs in UTC)."""
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def require_fetch(request: Request) -> None:
    """For state-changing API calls: only our own fetch() sends this header, which blocks cross-site form posts."""
    if request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(400, "Bad request")


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "–"
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _fmt_pace(seconds_per_km: float | None) -> str:
    if not seconds_per_km:
        return "–"
    s = round(seconds_per_km)
    return f"{s // 60}:{s % 60:02d}"


def _fmt_type(type_key: str | None) -> str:
    return (type_key or "unknown").replace("_", " ").capitalize()


templates.env.filters.update(duration=_fmt_duration, pace=_fmt_pace, activity_type=_fmt_type)
templates.env.globals.update(today=today)
