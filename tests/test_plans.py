from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_app import client

from app import periods, plans, wellness
from app.models import DailySummary, Plan
from app.sync import sync_activities
from conftest import FakeGarmin, make_activity

TODAY = date.today()


def training_json(start: date, km=(10, 0, 16)) -> dict:
    cats = ["easy", "rest", "workout"]
    return {"kind": "training", "name": "Test block", "race_date": (start + timedelta(days=30)).isoformat(),
            "days": [{"date": (start + timedelta(days=i)).isoformat(), "title": f"Day {i}", "category": cats[i % 3],
                      "planned_km": km[i % 3], **({"strength": "A"} if i == 0 else {})} for i in range(6)]}


# ---------- periods ----------

@pytest.mark.parametrize("key,start,end", [
    ("7d", date(2026, 10, 21), date(2026, 10, 27)),
    ("week", date(2026, 10, 26), date(2026, 10, 27)),
    ("month", date(2026, 10, 1), date(2026, 10, 27)),
    ("last_month", date(2026, 9, 1), date(2026, 9, 30)),
    ("last_year", date(2025, 1, 1), date(2025, 12, 31)),
    ("all", None, date(2026, 10, 27)),
    ("nonsense", date(2026, 9, 28), date(2026, 10, 27)),  # falls back to last 30 days
])
def test_period_presets(key, start, end):
    p = periods.resolve(key, None, None, date(2026, 10, 27))
    assert (p.start, p.end) == (start, end)


def test_custom_period_swaps_reversed_dates():
    p = periods.resolve("custom", date(2026, 10, 10), date(2026, 10, 1), date(2026, 10, 27))
    assert (p.key, p.start, p.end) == ("custom", date(2026, 10, 1), date(2026, 10, 10))


# ---------- activities page ----------

@pytest.fixture
def history(engine):
    acts = [make_activity(1, 1, km=10, name="Morning run"), make_activity(2, 40, km=20, name="Skopje long run"),
            make_activity(3, 2, type_key="cycling", km=30, name="Bike")]
    sync_activities(engine, FakeGarmin(acts), page_size=50, pause_s=0)
    return engine


def test_activities_filters(history):
    c = client("me@example.com")
    assert "Morning run" in c.get("/activities").text and "Skopje long run" not in c.get("/activities").text
    page = c.get("/activities?period=all&kind=running&start=&end=").text  # empty dates as the form sends them
    assert "Skopje long run" in page and "Bike" not in page
    assert "Bike" in c.get("/activities?period=all&q=bik").text
    assert c.get("/activities", follow_redirects=False).status_code == 200
    assert client().get("/activities", follow_redirects=False).status_code == 303


# ---------- import ----------

def test_plan_import_validates():
    with pytest.raises(ValidationError, match="only once"):
        plans.PlanIn.model_validate({**training_json(TODAY), "days": training_json(TODAY)["days"][:1] * 2})
    with pytest.raises(ValidationError, match="planned_km"):
        plans.PlanIn.model_validate({"kind": "training", "name": "x", "days": [{"date": "2026-10-27", "title": "t"}]})
    with pytest.raises(ValidationError, match="kcal"):
        plans.PlanIn.model_validate({"kind": "nutrition", "name": "x", "days": [{"date": "2026-10-27", "title": "t"}]})


def test_import_api_and_reimport_replaces(engine):
    c = client("me@example.com")
    headers = {"X-Requested-With": "fetch"}
    first = c.post("/api/plans", json=training_json(TODAY), headers=headers).json()
    second = c.post("/api/plans", json=training_json(TODAY), headers=headers).json()
    assert first["ok"] and second["ok"] and second["days"] == 6
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Plan)) == 1  # same name and kind: replaced
    bad = c.post("/api/plans", json={"kind": "training", "name": "x", "days": []}, headers=headers)
    assert bad.status_code == 422 and bad.json()["errors"]
    assert c.post("/api/plans", json=training_json(TODAY)).status_code == 400  # missing fetch header


# ---------- training comparison ----------

@pytest.mark.parametrize("planned,cat,actual,offset,expected", [
    (10, "easy", 10, -1, "done"), (10, "easy", 6, -1, "partial"), (10, "easy", 2, -1, "missed"),
    (0, "rest", 0, -1, "done"), (0, "rest", 8, -1, "extra"), (10, "easy", 0, 0, "today"),
    (10, "easy", 9, 0, "done"), (10, "easy", 0, 1, "upcoming"),
])
def test_day_status(planned, cat, actual, offset, expected):
    assert plans.day_status(planned, cat, actual, TODAY + timedelta(days=offset), TODAY) == expected


def test_training_comparison_matches_runs_and_strength(engine):
    start = TODAY - timedelta(days=3)
    acts = [make_activity(10, 3, km=10), make_activity(11, 1, km=8),
            make_activity(12, 3, type_key="strength_training", km=0)]
    acts[0]["startTimeLocal"] = acts[2]["startTimeLocal"] = f"{start} 08:00:00"
    acts[1]["startTimeLocal"] = f"{start + timedelta(days=2)} 08:00:00"
    sync_activities(engine, FakeGarmin(acts), page_size=50, pause_s=0)
    with Session(engine) as s:
        plan = plans.import_plan(s, plans.PlanIn.model_validate(training_json(start)))
        cmp = plans.training_comparison(s, plan, TODAY)
    by_date = {x["day"].date: x for x in cmp["days"]}
    assert by_date[start]["status"] == "done" and by_date[start]["strength_done"]
    assert by_date[start + timedelta(days=1)]["status"] == "done"  # rest day
    assert by_date[start + timedelta(days=2)]["status"] == "partial"  # 8 of 16 km
    assert by_date[TODAY]["status"] == "today"
    assert cmp["planned_to_date"] == 26 and cmp["actual_to_date"] == 18  # today not counted before running
    assert cmp["days_to_race"] == 27


# ---------- nutrition ----------

def nutrition_json(start: date) -> dict:
    return {"kind": "nutrition", "name": "Cut", "meta": {"start_weight_kg": 65, "target_weight_kg": 63,
                                                        "deficit_end": (start + timedelta(days=20)).isoformat()},
            "days": [{"date": (start + timedelta(days=i)).isoformat(), "title": "Easy", "kcal": 2000,
                      "meals": [{"name": "Lunch", "items": "Chicken, rice", "kcal": 600}]} for i in range(30)]}


def test_nutrition_comparison(engine):
    start = TODAY - timedelta(days=10)
    with Session(engine) as s:
        plan = plans.import_plan(s, plans.PlanIn.model_validate(nutrition_json(start)))
        for i in range(11):  # burns for the 10 past days and today
            s.add(DailySummary(date=start + timedelta(days=i), burned_total=2500))
        s.commit()
        plans.record_weight(s, TODAY - timedelta(days=1), 64.0)
        plans.record_weight(s, TODAY, 63.8)
        cmp = plans.nutrition_comparison(s, plan, TODAY)
    assert cmp["days_with_burn"] == 10  # today's burn is incomplete, so not counted
    assert cmp["cumulative_deficit_kcal"] == 5000 and cmp["estimated_loss_kg"] == 0.65
    assert cmp["avg7_weight"] == 63.9 and cmp["to_go_kg"] == 0.9
    assert cmp["expected_today"] == 64.0  # halfway along a 20-day path from 65 to 63


def test_nutrition_page_and_weight_api(engine, monkeypatch):
    import app.routes_plans

    class NoGarmin:
        def __init__(self, *_):
            pass

        def daily_summaries(self, start, end):
            raise RuntimeError("Garmin down")  # the page must still work

    monkeypatch.setattr(app.routes_plans, "GarminClient", NoGarmin)
    with Session(engine) as s:
        plan = plans.import_plan(s, plans.PlanIn.model_validate(nutrition_json(TODAY - timedelta(days=2))))
        plan_id = plan.id
    c = client("me@example.com")
    headers = {"X-Requested-With": "fetch"}
    saved = c.post("/api/weight", json={"date": TODAY.isoformat(), "weight_kg": 64.2}, headers=headers)
    assert saved.status_code == 200
    future = (TODAY + timedelta(days=1)).isoformat()
    assert c.post("/api/weight", json={"date": future, "weight_kg": 64}, headers=headers).status_code == 422
    page = c.get(f"/plans/{plan_id}")
    assert page.status_code == 200 and "Chicken, rice" in page.text and "64.2 kg" in page.text


def test_daily_summaries_are_cached(engine):
    class Fake:
        calls = 0

        def daily_summaries(self, start, end):
            Fake.calls += 1
            days = (end - start).days + 1
            return [{"calendarDate": (start + timedelta(days=i)).isoformat(), "calories": {"burnedTotal": 2400 + i},
                     "heartRate": {"restingValue": 45}} for i in range(days)]

    start = TODAY - timedelta(days=40)
    with Session(engine) as s:
        wellness.ensure_daily_summaries(s, Fake(), start, TODAY, TODAY)
        assert Fake.calls == 2  # 41 days in 28-day chunks
        assert s.get(DailySummary, start).resting_hr == 45
        wellness.ensure_daily_summaries(s, Fake(), start, TODAY - timedelta(days=5), TODAY)
        assert Fake.calls == 2  # older days are cached


# ---------- dashboard, brief ----------

def test_dashboard_shows_todays_plan(engine):
    with Session(engine) as s:
        plans.import_plan(s, plans.PlanIn.model_validate(training_json(TODAY)))
    page = client("me@example.com").get("/").text
    assert "Today" in page and "Day 0" in page and "Strength A" in page


def test_brief_includes_history_goal_and_schema(history):
    c = client("me@example.com")
    req = {"kind": "training", "goal": "Sub-3:15 marathon", "start_date": TODAY.isoformat(),
           "end_date": (TODAY + timedelta(days=60)).isoformat(), "weekly_km_min": 90, "weekly_km_max": 110}
    brief = c.post("/api/plans/brief", json=req, headers={"X-Requested-With": "fetch"}).json()["brief"]
    assert "Sub-3:15 marathon" in brief and "90–110 km" in brief
    assert '"planned_km"' in brief and '"week_of"' in brief  # import schema and training history
    bad = {**req, "end_date": (TODAY - timedelta(days=1)).isoformat()}
    assert c.post("/api/plans/brief", json=bad, headers={"X-Requested-With": "fetch"}).status_code == 422
