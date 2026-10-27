"""Period presets for filters: resolve a preset or a custom range to inclusive start/end dates."""

from dataclasses import dataclass
from datetime import date, timedelta

PRESETS = {
    "7d": "Last 7 days",
    "week": "This week",
    "30d": "Last 30 days",
    "month": "This month",
    "last_month": "Last month",
    "90d": "Last 3 months",
    "year": "This year",
    "last_year": "Last year",
    "all": "All time",
    "custom": "Custom",
}
DEFAULT = "30d"


@dataclass(frozen=True)
class Period:
    key: str
    start: date | None  # None = since the first activity
    end: date

    @property
    def label(self) -> str:
        if self.key == "custom":
            return f"{self.start:%d %b %Y} – {self.end:%d %b %Y}" if self.start else f"Until {self.end:%d %b %Y}"
        return PRESETS[self.key]


def resolve(key: str | None, start: date | None, end: date | None, today: date) -> Period:
    if key == "custom" or (key is None and (start or end)):
        end = end or today
        if start and start > end:
            start, end = end, start
        return Period("custom", start, end)
    key = key if key in PRESETS else DEFAULT
    first_of_month = today.replace(day=1)
    match key:
        case "7d":
            return Period(key, today - timedelta(days=6), today)
        case "week":
            return Period(key, today - timedelta(days=today.weekday()), today)
        case "30d":
            return Period(key, today - timedelta(days=29), today)
        case "month":
            return Period(key, first_of_month, today)
        case "last_month":
            last_end = first_of_month - timedelta(days=1)
            return Period(key, last_end.replace(day=1), last_end)
        case "90d":
            return Period(key, today - timedelta(days=89), today)
        case "year":
            return Period(key, today.replace(month=1, day=1), today)
        case "last_year":
            return Period(key, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
        case _:
            return Period("all", None, today)
