import base64
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Configure before any app module reads settings (environment beats .env).
_local = Path(__file__).resolve().parent.parent / ".pgdata"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql+psycopg://postgres@/garmin_test?host={_local}")
os.environ.update(APP_ENV="local", AUTH_DEV_BYPASS="false", ALLOWED_EMAIL="me@example.com",
                  SESSION_SECRET="test-secret-" + "x" * 32, GARMIN_API_KEY="test")

from itsdangerous import TimestampSigner  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, get_engine  # noqa: E402


@pytest.fixture
def engine():
    eng = get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE activities, sync_runs"))


def make_activity(activity_id: int, days_ago: float, type_key: str = "running", km: float = 10,
                  name: str | None = None) -> dict:
    start = datetime.now() - timedelta(days=days_ago)
    return {
        "activityId": activity_id,
        "activityName": name or f"Activity {activity_id}",
        "activityType": {"typeKey": type_key},
        "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
        "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
        "distance": km * 1000,
        "duration": km * 300,
        "averageHR": 140,
    }


class FakeGarmin:
    """Serves a newest-first activity list the way the search endpoint does."""

    def __init__(self, activities: list[dict]):
        self.activities = activities
        self.requests = 0

    def search_activities(self, start: int = 0, limit: int = 99, **_):
        self.requests += 1
        ordered = sorted(self.activities, key=lambda a: a["startTimeLocal"], reverse=True)
        return ordered[start:start + limit]


def session_cookie(data: dict) -> str:
    """A cookie in the format Starlette's SessionMiddleware signs and reads."""
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(os.environ["SESSION_SECRET"]).sign(payload).decode()
