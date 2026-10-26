import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import stats
from app.main import app
from app.sync import sync_activities
from conftest import FakeGarmin, make_activity, session_cookie


@pytest.fixture
def loaded(engine):
    acts = [make_activity(i, days_ago=i * 3, km=10) for i in range(1, 40)]
    acts += [make_activity(1000 + i, days_ago=i * 5, type_key=t, km=20)
             for i, t in enumerate(["cycling", "walking", "hiking", "swimming", "rowing", "cycling"], 1)]
    sync_activities(engine, FakeGarmin(acts), page_size=50, pause_s=0)
    return engine


def client(email: str | None = None) -> TestClient:
    c = TestClient(app)
    if email:
        c.cookies.set("garmin_session", session_cookie({"email": email}))
    return c


def test_pages_require_sign_in(engine):
    r = client().get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client().get("/api/stats/distance").status_code == 401


def test_other_google_accounts_are_rejected(engine):
    assert client("someone@else.com").get("/api/stats/distance").status_code == 401


def test_dashboard_renders_for_owner(loaded):
    r = client("me@example.com").get("/")
    assert r.status_code == 200
    assert "Refresh from Garmin" in r.text and "Activity 1" in r.text


def test_sync_requires_fetch_header(engine):
    assert client("me@example.com").post("/api/sync").status_code == 400


@pytest.mark.parametrize("period,buckets", [("week", 16), ("month", 12)])
def test_distance_series(loaded, period, buckets):
    data = client("me@example.com").get(f"/api/stats/distance?period={period}").json()
    assert len(data["labels"]) == len(data["running_km"]) == buckets
    # 4 named other types plus the folded "other" bucket, every series aligned to the labels
    assert [s["type"] for s in data["other"]][-1] == "other" and len(data["other"]) == 5
    assert all(len(s["km"]) == buckets for s in data["other"])


def test_yearly_series_covers_all_running(loaded):
    data = client("me@example.com").get("/api/stats/distance?period=year").json()
    assert round(sum(data["running_km"])) == 39 * 10


def test_summary_last_30_days(loaded):
    with Session(loaded) as s:
        summary = stats.summary(s)
    assert summary["runs"] == 9  # runs every 3 days: days 3..27
    assert summary["run_km"] == 90.0
    assert summary["run_pace_s_per_km"] == 300


@pytest.mark.parametrize("raw,expected", [
    ("resort_skiing_snowboarding_ws", "resort_skiing"), ("resort_skiing", "resort_skiing"),
    ("stand_up_paddleboarding_v2", "stand_up_paddleboarding"), ("cycling", "cycling"),
])
def test_type_keys_are_merged(raw, expected):
    assert stats.canonical_type(raw) == expected
